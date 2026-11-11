"""Collect bounded, attempt-scoped Android ActivityManager events."""

import hashlib
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from platform_contracts.models import MAX_DEVICE_INCIDENTS, DeviceDiagnostics, DeviceIncident

_EVENT = re.compile(r"^\s*(\d+\.\d+)\s+\d+\s+\d+\s+[VDIWEF]\s+(am_anr|am_crash)\s*:\s*\[(.*)\]\s*$")


def parse_event(line):
    match = _EVENT.match(line)
    if not match:
        return None
    stamp, tag, body = match.groups()
    first, second, process, _, detail = body.split(",", 4)
    # ActivityManagerService writes PID first for am_crash, despite its tag definition.
    pid = first if tag == "am_crash" else second
    if tag == "am_crash":
        # Newer Android appends a recoverable flag after the source line number.
        fields = detail.rsplit(",", 3)
        if len(fields) == 4 and fields[-1] in {"0", "1"} and fields[-2].lstrip("-").isdigit():
            detail = detail.rsplit(",", 1)[0]
        exception, remainder = detail.split(",", 1)
        message, file, number = remainder.rsplit(",", 2)
        location = f" ({file}:{number})" if file else ""
        detail = f"{exception}: {message}{location}"
    return Decimal(stamp), "anr" if tag == "am_anr" else "crash", int(pid), process, detail


def device_clock(serial):
    before = time.time()
    output = subprocess.check_output(
        ["adb", "-s", serial, "shell", "date", "+%s.%N"], stderr=subprocess.DEVNULL, timeout=4
    )
    after = time.time()
    epoch = Decimal(output.decode().strip())
    if not epoch.is_finite() or epoch <= 0:
        raise ValueError("Invalid device timestamp")
    return epoch, (before + after) / 2


class IncidentCapture:
    def __init__(self, serial, *, resumed=False):
        self.serial = serial
        self.process = None
        self.device_start = None
        self.host_start = 0.0
        self.monotonic_start = time.monotonic()
        self.buffer = b""
        self.incidents = {}
        self.reported = 0
        self.last_state = None
        self.truncated = False
        self.finished = False
        self.reason = "节点恢复后重新开始采集，中断期间可能缺少日志" if resumed else ""
        try:
            self.device_start, self.host_start = device_clock(serial)
            self.device_start = self.device_start.quantize(Decimal("0.000001"))
            self.monotonic_start = time.monotonic()
            self.process = subprocess.Popen(
                self.command(),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            os.set_blocking(self.process.stdout.fileno(), False)
        except (OSError, subprocess.SubprocessError, ValueError, InvalidOperation):
            self.close()
            self.reason = "无法读取设备时钟或启动 Logcat"

    def command(self, *, final=False):
        args = ["adb", "-s", self.serial, "logcat", "-b", "events"]
        if final:
            args.append("-d")
        return [
            *args,
            "-v",
            "epoch",
            "-v",
            "usec",
            "-v",
            "printable",
            "-T",
            f"{self.device_start:.6f}",
            "-s",
            "am_anr:I",
            "am_crash:I",
        ]

    def add(self, line, end=None):
        try:
            event = parse_event(line)
            if not event:
                if re.search(r"\s(?:am_anr|am_crash)\s*:", line):
                    self.reason = self.reason or "部分系统事件格式无法解析"
                return
            stamp, kind, pid, process, detail = event
            if stamp < self.device_start or (end is not None and stamp > end):
                return
            elapsed = float(stamp - self.device_start)
            if elapsed > time.monotonic() - self.monotonic_start + 3:
                self.reason = self.reason or "检测到设备时钟跳变"
                return
            key = hashlib.sha256(line.strip().encode()).hexdigest()
            if key in self.incidents:
                return
            if len(self.incidents) >= MAX_DEVICE_INCIDENTS:
                self.truncated = True
                return
            self.incidents[key] = DeviceIncident(
                id=key,
                kind=kind,
                pid=pid,
                process=process[:256],
                detail=detail[:1200],
                device_at=datetime.fromtimestamp(float(stamp), timezone.utc),
                occurred_at=datetime.fromtimestamp(self.host_start + elapsed, timezone.utc),
                raw=line.strip()[:2500],
            )
        except (ValueError, OverflowError, InvalidOperation):
            self.reason = self.reason or "部分系统事件格式无法解析"

    def drain(self):
        if self.process is None or self.finished:
            return
        try:
            chunk = os.read(self.process.stdout.fileno(), 65536)
        except BlockingIOError:
            return
        except OSError:
            chunk = b""
        if not chunk:
            self.reason = self.reason or "Logcat 连接中断"
            self.close()
            return
        *lines, self.buffer = (self.buffer + chunk).split(b"\n")
        for line in lines:
            self.add(line.decode(errors="replace"))

    def finish(self):
        if self.finished:
            return
        self.drain()
        if self.process is not None and self.process.poll() is not None:
            self.reason = self.reason or "Logcat 连接中断"
        self.finished = True
        self.close()
        if self.device_start is None:
            return
        try:
            end, host_end = device_clock(self.serial)
            if abs(float(end - self.device_start) - (host_end - self.host_start)) > 3:
                self.reason = self.reason or "检测到设备时钟跳变，结束时间无法可靠对齐"
                return
            output = subprocess.check_output(
                self.command(final=True), stderr=subprocess.DEVNULL, timeout=5
            )
            for line in output.decode(errors="replace").splitlines():
                self.add(line, end)
        except (OSError, subprocess.SubprocessError, ValueError, InvalidOperation):
            self.reason = self.reason or "无法读取执行结束时的设备日志"

    def close(self):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.process.stdout.close()
            self.process = None

    def report(self):
        if self.device_start is None:
            status = "unavailable"
        elif self.reason or self.truncated:
            status = "partial"
        else:
            status = "complete" if self.finished else "collecting"
        state = status, self.reason, self.truncated
        if self.reported == len(self.incidents) and self.last_state == state:
            return None
        return DeviceDiagnostics(
            serial=self.serial,
            status=status,
            reason=self.reason,
            incidents=list(self.incidents.values())[self.reported :],
            truncated=self.truncated,
        )

    def acknowledge(self, report):
        self.reported = len(self.incidents)
        self.last_state = report.status, report.reason, report.truncated
