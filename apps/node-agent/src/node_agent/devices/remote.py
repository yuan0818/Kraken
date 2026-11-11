import base64
import json
import os
import re
import secrets
import select
import socket
import struct
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path


def adb(serial, *args):
    return subprocess.run(
        ["adb", "-s", serial, *args], check=True, capture_output=True, timeout=10
    ).stdout


@contextmanager
def scrcpy_control(serial):
    # Each input has its own socket and bounded lifetime.
    ident = f"{secrets.randbelow(0x7FFFFFFF):08x}"
    remote = f"/data/local/tmp/platform-scrcpy-{ident}.jar"
    server = os.getenv("SCRCPY_SERVER_PATH")
    if not server:
        server = next(
            (
                str(p)
                for p in (
                    Path("/opt/homebrew/share/scrcpy/scrcpy-server"),
                    Path("/usr/share/scrcpy/scrcpy-server"),
                )
                if p.is_file()
            ),
            None,
        )
    if not server:
        raise RuntimeError("请配置 SCRCPY_SERVER_PATH，使用 scrcpy 5.0.1 服务端")
    port = None
    proc = None
    try:
        adb(serial, "push", server, remote)
        port = adb(serial, "forward", "tcp:0", f"localabstract:scrcpy_{ident}").decode().strip()
        command = f"CLASSPATH={remote} app_process / com.genymobile.scrcpy.Server 5.0.1 scid={ident} video=false audio=false control=true tunnel_forward=true send_dummy_byte=false send_device_meta=false clipboard_autosync=false cleanup=true"
        proc = subprocess.Popen(
            ["adb", "-s", serial, "shell", command],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.7)
        for _ in range(30):
            try:
                sock = socket.create_connection(("127.0.0.1", int(port)), timeout=0.2)
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("scrcpy 输入通道未建立")
        with sock:
            sock.settimeout(5)
            yield sock
    finally:
        try:
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            if port:
                adb(serial, "forward", "--remove", "tcp:" + port)
        finally:
            adb(serial, "shell", "rm", "-f", remote)


def paste(serial, text):
    with scrcpy_control(serial) as sock:
        data = text.encode("utf-8")
        sock.sendall(struct.pack(">BQBI", 9, 1, 1, len(data)) + data)
        ack = b""
        while len(ack) < 9:
            chunk = sock.recv(9 - len(ack))
            if not chunk:
                raise RuntimeError("文字输入通道断开")
            ack += chunk
        if ack != struct.pack(">BQ", 1, 1):
            raise RuntimeError("文字输入未确认")


def swipe_and_hold(serial, start, end, size, duration_ms, hold_ms):
    # scrcpy 5.0.1 INJECT_TOUCH_EVENT with the generic finger pointer ID.
    with scrcpy_control(serial) as sock:

        def touch(action, point):
            sock.sendall(
                struct.pack(
                    ">BBqiiHHHII", 2, action, -2, *point, *size, 0 if action == 1 else 0xFFFF, 0, 0
                )
            )

        point = start
        touch(0, point)
        try:
            steps = (duration_ms + 15) // 16
            started = time.monotonic()
            for step in range(1, steps + 1):
                fraction = step / steps
                deadline = started + fraction * duration_ms / 1000
                time.sleep(max(0, deadline - time.monotonic()))
                point = tuple(round(a + (b - a) * fraction) for a, b in zip(start, end))
                touch(2, point)
            time.sleep(hold_ms / 1000)
        finally:
            touch(1, point)
        # Let the server consume UP before tearing down the ADB tunnel.
        sock.shutdown(socket.SHUT_WR)
        sock.settimeout(2)
        while sock.recv(4096):
            pass


_bridges = {}


def warm_input(serial):
    python = os.getenv("ARTEMIS_PYTHON")
    root = os.getenv("ARTEMIS_ROOT")
    if not python or not root:
        raise RuntimeError("节点尚未配置 Artemis 的共享设备锁")
    bridge = _bridges.get(serial)
    if bridge is None or bridge.poll() is not None:
        bridge = subprocess.Popen(
            [python, "-u", str(Path(__file__).resolve()), serial],
            env={**os.environ, "PYTHONPATH": root},
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        _bridges[serial] = bridge
    return bridge


def perform(serial, payload):
    if payload["kind"] == "frame":
        return execute(serial, payload)
    bridge = warm_input(serial)
    try:
        bridge.stdin.write(json.dumps(payload).encode() + b"\n")
        bridge.stdin.flush()
        if not select.select([bridge.stdout], [], [], 20)[0]:
            raise RuntimeError("输入确认超时")
        line = bridge.stdout.readline()
        if not line:
            raise RuntimeError("输入通道已断开")
        result = json.loads(line)
        if result.get("error"):
            raise RuntimeError(result["error"])
        return result
    except (OSError, ValueError, RuntimeError):
        bridge.terminate()
        try:
            bridge.wait(timeout=2)
        except subprocess.TimeoutExpired:
            bridge.kill()
            bridge.wait()
        raise


def execute(serial, payload):
    kind = payload["kind"]
    if kind == "frame":
        captured_at = time.time()
        png = adb(serial, "exec-out", "screencap", "-p")
        if png[:8] != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError("画面采集失败")
        width, height = struct.unpack(">II", png[16:24])
        return {
            "png": base64.b64encode(png).decode(),
            "width": width,
            "height": height,
            "captured_at": captured_at,
        }
    if kind in ("tap", "swipe"):
        viewport = adb(serial, "shell", "dumpsys", "input").decode()
        match = re.search(
            r"Viewport INTERNAL: displayId=0,[^\n]*logicalFrame=\[0, 0, (\d+), (\d+)\]", viewport
        )
        if match:
            size = tuple(map(int, match.groups()))
        else:
            png = adb(serial, "exec-out", "screencap", "-p")
            size = struct.unpack(">II", png[16:24])
        width, height = payload["width"], payload["height"]
        if not width or not height or abs(size[0] / size[1] - width / height) > 0.015:
            raise RuntimeError("屏幕方向已变化，请等待新画面")
        start = tuple(round(payload[k] * (size[i] - 1)) for i, k in enumerate(("x", "y")))
        if kind == "tap":
            adb(serial, "shell", "input", "tap", *map(str, start))
        else:
            end = tuple(round(payload[k] * (size[i] - 1)) for i, k in enumerate(("x2", "y2")))
            duration = payload.get("duration_ms", 350)
            hold = payload.get("hold_ms", 0)
            if hold:
                swipe_and_hold(serial, start, end, size, duration, hold)
            else:
                adb(serial, "shell", "input", "swipe", *map(str, (*start, *end, duration)))
    elif kind == "text":
        paste(serial, payload["text"])
    elif kind == "key":
        adb(
            serial,
            "shell",
            "input",
            "keyevent",
            {
                "HOME": "3",
                "BACK": "4",
                "APP_SWITCH": "187",
                "VOLUME_UP": "24",
                "VOLUME_DOWN": "25",
                "WAKEUP": "224",
                "SLEEP": "223",
            }[payload["key"]],
        )
    return {"ok": True}


if __name__ == "__main__":
    from artemis.runtime.device_lock import DeviceExecutionLock

    for line in sys.stdin:
        lock = DeviceExecutionLock(
            device_id=sys.argv[1],
            description="平台远程控制",
            max_concurrency=0,
            concurrency_mode="per_device",
            ingress="platform_remote",
        )
        try:
            lock.acquire(blocking=False)
            result = execute(sys.argv[1], json.loads(line))
        except Exception as exc:
            result = {"error": str(exc)[:300]}
        finally:
            lock.release()
        print(json.dumps(result), flush=True)
