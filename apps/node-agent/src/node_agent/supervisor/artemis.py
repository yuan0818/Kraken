"""One explicitly bound SN, one Artemis session per platform attempt."""

import argparse
import asyncio
import json
import logging
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from node_agent.adapters.artemis import Artemis, goal, verdict
from node_agent.adapters.execution_report import (
    collect_execution_report,
    upload_execution_reports,
)
from node_agent.devices.diagnostics import IncidentCapture
from node_agent.devices.lock import LocalLock
from node_agent.devices.observe import inspect_device
from node_agent.devices.remote import perform, warm_input
from node_agent.devices.video import video_transport
from node_agent.spool import Spool, state_directory
from node_agent.transport.client import RuntimeClient, platform_url
from platform_contracts.models import Command


class PhysicalWorker:
    def __init__(self, runtime, native, node, serial, control=None):
        self.runtime, self.native = runtime, native
        self.spool = runtime.spool
        self.jobs = {}
        self.handled = set()
        self.finished = set()
        self.started = set()
        self.node, self.serial = node, serial
        self.control = control
        self.captures = {}
        self.traces_dir = (
            Path(os.environ["ARTEMIS_TRACES_DIR"])
            if os.getenv("ARTEMIS_TRACES_DIR")
            else Path(os.environ["ARTEMIS_ROOT"]) / "traces"
            if os.getenv("ARTEMIS_ROOT")
            else None
        )

    def start_diagnostics(self, cmd, *, resumed=False):
        self.captures[str(cmd.attempt_id)] = (cmd, IncidentCapture(self.serial, resumed=resumed))

    def drain_diagnostics(self):
        for cmd, capture in self.captures.values():
            capture.drain()
            if report := capture.report():
                self.spool.event(cmd, "diagnostics", diagnostics=report)
                capture.acknowledge(report)

    def close(self):
        for _, capture in self.captures.values():
            capture.close()

    def save(self, attempt, name, value):
        self.spool.save(attempt, name, value)

    def collect_report(self, attempt_id, **values):
        try:
            return collect_execution_report(
                self.native, attempt_id, traces_dir=self.traces_dir, **values
            )
        except (OSError, ValueError, TypeError) as exc:
            logging.warning("Execution evidence could not be collected (%s)", type(exc).__name__)
            return None

    def finish(
        self,
        cmd,
        kind,
        *,
        clean=True,
        evidence="missing",
        result="blocked",
        why="",
        execution_report=None,
    ):
        attempt = str(cmd.attempt_id)
        job = self.jobs.get(attempt, {})
        original = job.get("execution_command")
        report_command = (
            Command.model_validate(original) if original else cmd if cmd.kind == "execute" else None
        )
        entry = self.captures.get(attempt)
        if execution_report is None and report_command:
            execution_report = self.collect_report(cmd.attempt_id)
        if execution_report is not None and report_command:
            self.spool.report(report_command.command_id, cmd.attempt_id, execution_report)
        if entry:
            execute_cmd, capture = entry
            capture.finish()
            if report := capture.report():
                self.spool.event(execute_cmd, "diagnostics", diagnostics=report)
        self.spool.event(
            cmd,
            kind,
            cleanup="verified" if clean else "failed",
            evidence=evidence,
            verdict=result,
            reason=why,
        )
        self.finished.add(attempt)
        self.captures.pop(attempt, None)

    def recover(self, jobs):
        self.jobs = {str(job["attempt_id"]): job for job in jobs if job["serial"] == self.serial}

    def process(self, job, device):
        cmd = Command.model_validate(job["command"])
        if cmd.node_id != self.node or cmd.simulation or cmd.serial != self.serial:
            raise ValueError("Command does not match this physical node")
        if cmd.command_id in self.handled or str(cmd.attempt_id) in self.finished:
            return
        if cmd.kind == "execute" and job["launch_intent_at"]:
            return
        if job["state"] == "needs_recovery":
            return
        if cmd.kind == "cancel":
            self.runtime.claim(cmd)
            if job["launch_intent_at"] is None:
                self.finish(cmd, "cancelled", result="not_evaluated", why="启动前取消")
            self.handled.add(cmd.command_id)
            return
        available = device and device["state"] == "device" and device.get("is_busy") is False
        if not available or cmd.expires_at <= datetime.now(timezone.utc):
            self.finish(
                cmd,
                "rejected",
                clean=bool(available),
                why="设备离线、已占用或授权过期",
            )
            return
        if cmd.kind == "prepare":
            self.runtime.claim(cmd)
            self.spool.event(cmd, "ready")
            self.handled.add(cmd.command_id)
            return
        if not cmd.content or not cmd.version_id:
            raise ValueError("Execution command has no frozen case version")
        model = None
        if cmd.execution_model is not None:
            model = self.native.model_override(cmd, self.control)
        grant = self.runtime.claim(cmd)
        job["launch_intent_at"] = grant["launch_intent_at"]
        if not grant["start"]:
            return
        self.handled.add(cmd.command_id)
        self.save(cmd.attempt_id, "command", cmd.model_dump(mode="json"))
        self.start_diagnostics(cmd)
        reply = self.native.submit(cmd, model) if model is not None else self.native.submit(cmd)
        self.save(cmd.attempt_id, "submission", reply)
        if reply.get("status") == "rejected":
            self.finish(cmd, "rejected", why=str(reply.get("error") or "Artemis 拒绝启动")[:300])

    def poll(self, job):
        cmd = Command.model_validate(job["command"])
        attempt = str(cmd.attempt_id)
        cancelling = cmd.kind == "cancel"
        deadline = datetime.fromisoformat(job["launch_intent_at"]).timestamp() + 120
        if attempt not in self.captures:
            original = job.get("execution_command")
            if original:
                self.start_diagnostics(Command.model_validate(original), resumed=True)
        terminal_kind = "rejected" if cancelling else "finished"
        native = self.native.session(attempt)
        if not native:
            if time.time() > deadline:
                self.finish(
                    cmd,
                    terminal_kind,
                    clean=False,
                    why="启动已获许可，但无法确认 Artemis 会话，保留租约等待恢复",
                )
            return
        info = native.get("device_info") or {}
        if isinstance(info, str):
            info = json.loads(info)
        if info.get("device_id") != self.serial or native.get("session_id") != attempt:
            if not info.get("device_id") and time.time() < deadline:
                return
            self.finish(cmd, terminal_kind, clean=False, why="Artemis 会话设备身份无法核实")
            return
        if not cancelling and native.get("initial_goal") != goal(cmd):
            self.finish(cmd, terminal_kind, clean=False, why="Artemis 会话内容与本次授权不一致")
            return
        status = native["status"]
        if not cancelling and job["state"] == "starting" and attempt not in self.started:
            self.spool.event(cmd, "started")
            self.started.add(attempt)
        if status not in {"completed", "failed", "cancelled", "stopped", "aborted"}:
            if cancelling:
                self.native.stop(attempt)
            return
        device = self.native.device(self.serial)
        if not device or device["state"] != "device":
            self.finish(
                cmd, terminal_kind, clean=False, why=f"会话 {attempt} 已结束，手机尚未恢复在线"
            )
            return
        if device.get("is_busy") is not False:
            return
        self.save(attempt, "session", native)
        if cancelling:
            self.finish(
                cmd,
                "cancelled",
                result="not_evaluated",
                why=f"已核对 Artemis 会话 {attempt} 结束",
                execution_report=self.collect_report(cmd.attempt_id, status=status),
            )
            return
        checks = self.native.get(f"/api/sessions/{attempt}/checks")
        notes = self.native.get(f"/api/sessions/{attempt}/notes")
        self.save(attempt, "checks", checks)
        self.save(attempt, "notes", notes)
        result, evidence, why = verdict(cmd, status, checks, notes)
        receipt = self.spool.directory / "attempts" / attempt / "submission.json"
        if cmd.execution_model is not None and not receipt.is_file():
            result, evidence = "inconclusive", "partial"
            why = "Artemis 启动回执缺失，无法核实本次模型版本"
        self.finish(
            cmd,
            "finished",
            result=result,
            evidence=evidence,
            why=f"Artemis {attempt} · {self.serial} · {why}",
            execution_report=self.collect_report(
                cmd.attempt_id, status=status, checks=checks, notes=notes
            ),
        )

    def tick(self, device):
        self.drain_diagnostics()
        for attempt, job in list(self.jobs.items()):
            if attempt in self.finished or not job["command"] or job["state"] == "needs_recovery":
                continue
            try:
                if self.runtime.online:
                    self.process(job, device)
                if job["launch_intent_at"] and attempt not in self.finished:
                    self.poll(job)
            except (httpx.HTTPError, OSError, ValueError, RuntimeError) as exc:
                logging.warning("Reconciling attempt %s (%s)", attempt, type(exc).__name__)


async def run_worker(serial):
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, asyncio.current_task().cancel)
    node = os.environ["NODE_ID"]
    base = platform_url()
    directory = state_directory()
    directory.mkdir(parents=True, exist_ok=True)
    owner = LocalLock(directory / "runtime.lock")
    spool = Spool(directory, node, serial)
    try:
        with (
            httpx.Client(
                base_url=os.environ.get("ARTEMIS_URL", "http://127.0.0.1:8000"),
                timeout=10,
                trust_env=False,
            ) as engine,
            httpx.Client(base_url=base, timeout=5, trust_env=False) as control,
        ):
            runtime = RuntimeClient(control, node, spool)
            native = Artemis(engine)
            worker = PhysicalWorker(runtime, native, node, serial, control)
            if os.getenv("ARTEMIS_PYTHON") and os.getenv("ARTEMIS_ROOT"):
                warm_input(serial)
            next_refresh = 0.0

            def cycle():
                nonlocal next_refresh
                try:
                    device = native.device(serial)
                except httpx.HTTPError:
                    device = None
                if time.monotonic() >= next_refresh:
                    next_refresh = time.monotonic() + 1
                    runtime.online = False
                    try:
                        observation = inspect_device(node, serial)
                        # Register as unavailable before fetching authoritative runtime state.
                        control.post(
                            "/api/nodes/adb-inventory",
                            json={**observation.model_dump(), "artemis_state": "unavailable"},
                        ).raise_for_status()
                        worker.recover(runtime.refresh())
                        state = (
                            "ready"
                            if device
                            and device["state"] == "device"
                            and device.get("is_busy") is False
                            else "busy"
                        )
                        control.post(
                            "/api/nodes/adb-inventory",
                            json={**observation.model_dump(), "artemis_state": state},
                        ).raise_for_status()
                    except (httpx.HTTPError, ValueError) as exc:
                        runtime.online = False
                        logging.warning(
                            "Control-plane synchronization deferred (%s)", type(exc).__name__
                        )
                worker.tick(device)
                if not runtime.online:
                    return False
                try:
                    action = control.get(f"/api/nodes/{node}/remote/{serial}")
                    action.raise_for_status()
                    action = action.json()
                    if action:
                        try:
                            if action["payload"]["kind"] != "frame":
                                current = native.device(serial)
                                if not current or current.get("is_busy") is not False:
                                    raise RuntimeError("Artemis 正在占用设备")
                            result = perform(serial, action["payload"])
                        except Exception as exc:
                            result = {"error": str(exc)[:300]}
                        control.post(
                            f"/api/nodes/{node}/remote/{serial}/{action['id']}",
                            json={"result": result},
                        ).raise_for_status()
                    return bool(action)
                except httpx.HTTPError:
                    runtime.online = False
                    return False

            async def supervise():
                while True:
                    work = asyncio.create_task(asyncio.to_thread(cycle))
                    try:
                        handled = await asyncio.shield(work)
                    except asyncio.CancelledError:
                        await asyncio.gather(work, return_exceptions=True)
                        raise
                    await asyncio.sleep(0.02 if handled else 0.15)

            try:
                async with asyncio.TaskGroup() as group:
                    group.create_task(supervise())
                    group.create_task(
                        upload_execution_reports(spool, base, node, str(engine.base_url))
                    )
                    group.create_task(video_transport(base, node, serial))
            finally:
                worker.close()
    finally:
        owner.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run_worker(parser.parse_args().serial))


if __name__ == "__main__":
    main()
