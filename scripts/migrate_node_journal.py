"""One-time, read-only legacy SQLite import. Stop old node processes before running."""

import argparse
import json
import shutil
import sqlite3
from contextlib import ExitStack
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from node_agent.devices.lock import LocalLock
from node_agent.spool import Spool, atomic_json
from platform_contracts.execution_reports import ExecutionReport
from platform_contracts.models import NodeEvent, NodeUpdate


def migrate(source, destination, node_id, serial=None):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file():
        raise ValueError("Legacy journal does not exist")
    destination.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        for path in [
            source.with_suffix(s) for s in (".worker.lock", ".supervisor.lock", ".transport.lock")
        ] + [destination / "runtime.lock"]:
            lock = LocalLock(path)
            stack.callback(lock.close)
        conn = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        stack.callback(conn.close)
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        metadata = (
            dict(conn.execute("SELECT key,value FROM metadata")) if "metadata" in tables else {}
        )
        if metadata.get("node_id", node_id) != node_id:
            raise ValueError("Legacy journal belongs to another node")
        commands = {
            r["id"]: json.loads(r["payload"]) for r in conn.execute("SELECT * FROM commands")
        }
        if any(
            c["node_id"] != node_id or (serial and c["serial"] != serial) for c in commands.values()
        ):
            raise ValueError("Legacy commands belong to another node or device")
        for key in ("serial", "physical_serial"):
            if metadata.get(key) and metadata[key] != serial:
                raise ValueError("Legacy device identity mismatch")
        # Validate all records before publishing any pending observations.
        events = []
        for row in conn.execute("SELECT seq,payload FROM events WHERE acked=0 ORDER BY seq"):
            event = NodeEvent.model_validate_json(row["payload"])
            events.append(
                NodeUpdate(
                    event_id=uuid5(NAMESPACE_URL, f"artemis-platform:{node_id}:legacy:{event.seq}"),
                    legacy_seq=event.seq,
                    **event.model_dump(exclude={"seq"}),
                )
            )
        reports = []
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "execution_report_outbox" in tables:
            for row in conn.execute("SELECT command_id,payload FROM execution_report_outbox"):
                command = commands[row["command_id"]]
                reports.append(
                    (
                        UUID(row["command_id"]),
                        UUID(command["attempt_id"]),
                        ExecutionReport.model_validate_json(row["payload"]),
                    )
                )
        spool = Spool(destination, node_id, serial, importing_legacy=True)
        marker = destination / "legacy-import.json"
        if marker.exists():
            previous = json.loads(marker.read_text())
            if previous["source"] != str(source):
                raise ValueError("Destination already imported another journal")
            return previous
        old_attempts = source.parent / "attempts"
        new_attempts = destination / "attempts"
        if old_attempts.exists() and old_attempts != new_attempts:
            for entry in old_attempts.iterdir():
                UUID(entry.name)
                if entry.is_dir():
                    shutil.copytree(entry, new_attempts / entry.name, dirs_exist_ok=True)
        for event in events:
            spool.enqueue(event, order=event.legacy_seq)
        for command, attempt, report in reports:
            spool.report(command, attempt, report)
        result = {
            "source": str(source),
            "node_id": node_id,
            "events": len(events),
            "reports": len(reports),
        }
        atomic_json(marker, result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--serial")
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.state_dir, args.node_id, args.serial)))


if __name__ == "__main__":
    main()
