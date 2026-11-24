"""Durable pending observations and evidence files. Business state lives in the control plane."""

import json
import os
import tempfile
import time
from pathlib import Path
from threading import RLock
from uuid import uuid4, uuid5

from platform_contracts.models import NodeUpdate


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def state_directory():
    directory = os.getenv("NODE_STATE_DIR")
    if directory:
        return Path(directory)
    legacy = os.getenv("NODE_JOURNAL")
    if legacy:
        raise ValueError(
            "Migrate NODE_JOURNAL with scripts/migrate_node_journal.py and set NODE_STATE_DIR"
        )
    return Path(".runtime/node")


class Spool:
    def __init__(self, directory, node_id, serial=None, *, importing_legacy=False):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        if (
            not importing_legacy
            and any(self.directory.glob("*.sqlite*"))
            and not (self.directory / "legacy-import.json").is_file()
        ):
            raise ValueError(
                "Legacy journal found; import it with scripts/migrate_node_journal.py before starting"
            )
        expected = {"node_id": node_id, "serial": serial}
        identity = self.directory / "identity.json"
        if identity.exists():
            if json.loads(identity.read_text()) != expected:
                raise ValueError("Evidence directory belongs to another node or device")
        else:
            atomic_json(identity, expected)
        self.node_id = node_id
        self._lock = RLock()

    def save(self, attempt, name, value):
        atomic_json(self.directory / "attempts" / str(attempt) / f"{name}.json", value)

    def event(self, cmd, kind, **values):
        key = uuid4() if kind == "diagnostics" else uuid5(cmd.command_id, kind)
        event = NodeUpdate(
            event_id=key,
            command_id=cmd.command_id,
            attempt_id=cmd.attempt_id,
            lease_generation=cmd.lease_generation,
            simulation=cmd.simulation,
            kind=kind,
            **values,
        )
        self.enqueue(event)
        return event

    def enqueue(self, event, order=None):
        path = self.directory / "events" / f"{event.event_id}.json"
        if not path.exists():
            atomic_json(
                path,
                {
                    "order": order if order is not None else time.time_ns(),
                    "event": event.model_dump(mode="json"),
                },
            )

    def events(self):
        return sorted(
            [
                (path, json.loads(path.read_text()))
                for path in (self.directory / "events").glob("*.json")
            ],
            key=lambda entry: (entry[1]["order"], entry[0].name),
        )

    def acknowledge(self, path, expected=None):
        with self._lock:
            if expected is not None and (
                not path.exists() or json.loads(path.read_text()) != expected
            ):
                return
            path.unlink(missing_ok=True)
            sync_directory(path.parent)

    def report(self, command_id, attempt_id, report):
        with self._lock:
            atomic_json(
                self.directory / "reports" / f"{command_id}.json",
                {
                    "command_id": str(command_id),
                    "attempt_id": str(attempt_id),
                    "payload": report.model_dump(mode="json"),
                },
            )
