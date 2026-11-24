import importlib.util
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from node_agent.spool import Spool
from platform_contracts.execution_reports import ExecutionReport
from platform_contracts.models import NodeEvent

spec = importlib.util.spec_from_file_location(
    "migrate_node_journal", Path(__file__).resolve().parents[2] / "scripts/migrate_node_journal.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def legacy(path):
    command, attempt = uuid4(), uuid4()
    event = NodeEvent(
        seq=2, command_id=command, attempt_id=attempt, lease_generation=1, kind="ready"
    )
    with sqlite3.connect(path) as conn:
        conn.executescript(
            "CREATE TABLE commands(id TEXT,payload TEXT); CREATE TABLE events(seq INTEGER,payload TEXT,acked INTEGER); CREATE TABLE execution_report_outbox(command_id TEXT,payload TEXT);"
        )
        conn.execute(
            "INSERT INTO commands VALUES (?,?)",
            (
                str(command),
                json.dumps({"attempt_id": str(attempt), "node_id": "n", "serial": "SIM-001"}),
            ),
        )
        conn.execute("INSERT INTO events VALUES (?,?,0)", (2, event.model_dump_json()))
        conn.execute(
            "INSERT INTO execution_report_outbox VALUES (?,?)",
            (str(command), ExecutionReport(startup_log="evidence").model_dump_json()),
        )
    return event


def test_import_is_read_only_idempotent_and_keeps_pending_reports(tmp_path):
    source = tmp_path / "old.sqlite3"
    event = legacy(source)
    before = source.read_bytes()
    destination = tmp_path / "new"
    result = module.migrate(source, destination, "n")
    assert result["events"] == 1 and result["reports"] == 1
    assert source.read_bytes() == before
    spool = Spool(destination, "n")
    path, entry = spool.events()[0]
    assert entry["event"]["legacy_seq"] == event.seq
    assert len(list((destination / "reports").glob("*.json"))) == 1
    spool.acknowledge(path)
    assert module.migrate(source, destination, "n") == result
    assert not spool.events()  # Re-running migration cannot resurrect ACKed events.


def test_wrong_node_rejected_before_import(tmp_path):
    source = tmp_path / "old.sqlite3"
    legacy(source)
    with pytest.raises(ValueError, match="another node"):
        module.migrate(source, tmp_path / "new", "wrong")


def test_unmigrated_directory_requires_explicit_import(tmp_path):
    source = tmp_path / "old.sqlite3"
    legacy(source)
    with pytest.raises(ValueError, match="Legacy journal"):
        Spool(tmp_path, "n")
    module.migrate(source, tmp_path, "n")
    assert Spool(tmp_path, "n").events()
