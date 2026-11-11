"""Central-state simulator, deliberately incapable of touching USB/ADB/Fastboot."""

import logging
import os
import time
from datetime import datetime, timezone

import httpx
from node_agent.config import simulation_registration
from node_agent.devices.lock import LocalLock
from node_agent.spool import Spool, state_directory
from node_agent.transport.client import RuntimeClient, platform_url
from platform_contracts.models import Command


class Simulator:
    def __init__(self, runtime, duration=5, cleanup_failure=False, registration=None):
        self.runtime, self.spool = runtime, runtime.spool
        self.duration, self.cleanup_failure = duration, cleanup_failure
        self.registration = registration
        self.jobs = {}
        self.locks = {}
        self.handled = set()
        self.finished = set()

    def recover(self, jobs):
        self.jobs = {str(job["attempt_id"]): job for job in jobs}

    def lock(self, device_id):
        if device_id not in self.locks:
            self.locks[device_id] = LocalLock(self.spool.directory / "locks" / device_id)

    def release(self, device_id):
        if device_id in self.locks:
            self.locks.pop(device_id).close()

    def tick(self):
        for attempt, job in list(self.jobs.items()):
            if attempt in self.finished or not job["command"] or job["state"] == "needs_recovery":
                continue
            cmd = Command.model_validate(job["command"])
            if not cmd.simulation or not cmd.serial.startswith("SIM-"):
                raise ValueError("non_simulated_device")
            if self.registration and (
                cmd.node_id != self.registration.node_id
                or cmd.serial not in self.registration.device_serials
            ):
                raise ValueError("device_not_in_node_inventory")
            if (
                cmd.kind != "cancel"
                and not job["launch_intent_at"]
                and cmd.expires_at <= datetime.now(timezone.utc)
            ):
                self.spool.event(cmd, "rejected", cleanup="verified", reason="expired")
                self.finished.add(attempt)
                self.release(str(cmd.device_id))
                continue
            device = str(cmd.device_id)
            self.lock(device)
            if self.runtime.online and cmd.command_id not in self.handled:
                if cmd.kind == "execute" and job["launch_intent_at"]:
                    grant = {"launch_intent_at": job["launch_intent_at"]}
                else:
                    grant = self.runtime.claim(cmd)
                self.handled.add(cmd.command_id)
                if cmd.kind == "prepare":
                    self.spool.event(cmd, "ready")
                elif cmd.kind == "execute":
                    job["launch_intent_at"] = grant["launch_intent_at"]
                    self.spool.event(cmd, "started")
                elif cmd.kind == "cancel":
                    self.spool.event(cmd, "cancelled", cleanup="verified")
                    self.finished.add(attempt)
                    self.release(device)
            if cmd.kind == "execute" and job["launch_intent_at"]:
                deadline = (
                    datetime.fromisoformat(job["launch_intent_at"]).timestamp() + self.duration
                )
                if time.time() >= deadline:
                    self.spool.event(
                        cmd,
                        "finished",
                        cleanup="failed" if self.cleanup_failure else "verified",
                        evidence="partial",
                        verdict="inconclusive",
                        reason="Simulation only; no App/OS assertion executed",
                    )
                    self.finished.add(attempt)
                    if not self.cleanup_failure:
                        self.release(device)

    def close(self):
        for lock in self.locks.values():
            lock.close()
        self.locks.clear()


def main():
    logging.basicConfig(level=logging.INFO)
    registration = simulation_registration()
    directory = state_directory()
    directory.mkdir(parents=True, exist_ok=True)
    owner = LocalLock(directory / "runtime.lock")
    try:
        spool = Spool(directory, registration.node_id)
        with httpx.Client(base_url=platform_url(), timeout=5, trust_env=False) as client:
            runtime = RuntimeClient(client, registration.node_id, spool)
            simulator = Simulator(
                runtime,
                duration=float(os.getenv("SIM_DURATION", "5")),
                cleanup_failure=os.getenv("SIM_CLEANUP_FAILURE") == "1",
                registration=registration,
            )
            registered = False
            try:
                while True:
                    try:
                        if not registered:
                            response = client.post(
                                "/api/nodes/register", json=registration.model_dump(mode="json")
                            )
                            response.raise_for_status()
                            registered = True
                        simulator.recover(runtime.refresh())
                    except (httpx.HTTPError, ValueError) as exc:
                        runtime.online = False
                        logging.warning(
                            "Control-plane synchronization deferred (%s)", type(exc).__name__
                        )
                    try:
                        simulator.tick()
                    except (httpx.HTTPError, OSError, ValueError, RuntimeError) as exc:
                        logging.warning(
                            "Simulator reconciliation deferred (%s)", type(exc).__name__
                        )
                    time.sleep(0.2)
            finally:
                simulator.close()
    finally:
        owner.close()


if __name__ == "__main__":
    main()
