"""Report the ADB transport state of one explicitly enrolled serial."""

import argparse
import logging
import os
import subprocess
import time

import httpx
from node_agent.transport.client import platform_url
from platform_contracts.models import AdbObservation


def inspect_device(node_id, serial):
    fields = {"node_id": node_id, "serial": serial}
    try:
        result = subprocess.run(
            ["adb", "devices", "-l"], capture_output=True, text=True, timeout=5, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return AdbObservation(**fields, state="probe_error")
    if "List of devices attached" not in result.stdout.splitlines():
        return AdbObservation(**fields, state="probe_error")
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] != serial:
            continue
        state = parts[1]
        if parts[1:3] == ["no", "permissions"]:
            state = "no_permissions"
        elif state not in {"device", "offline", "unauthorized"}:
            state = "other"
        props = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
        return AdbObservation(
            **fields, state=state, model=props.get("model"), product=props.get("product")
        )
    return AdbObservation(**fields, state="absent")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    args = parser.parse_args()
    node_id = os.environ["NODE_ID"]
    url = platform_url() + "/api/nodes/adb-inventory"
    AdbObservation(node_id=node_id, serial=args.serial, state="absent")
    logging.basicConfig(level=logging.INFO)
    last = None
    with httpx.Client(timeout=5) as client:
        while True:
            observation = inspect_device(node_id, args.serial)
            try:
                reply = client.post(url, json=observation.model_dump())
                reply.raise_for_status()
                if observation.state != last:
                    logging.info("ADB %s: %s", args.serial, observation.state)
                last = observation.state
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise SystemExit(
                        f"Inventory rejected (HTTP {exc.response.status_code})"
                    ) from None
                logging.warning("Inventory API returned %s", exc.response.status_code)
            except httpx.RequestError as exc:
                logging.warning("Inventory API unavailable (%s)", type(exc).__name__)
            time.sleep(3)


if __name__ == "__main__":
    main()
