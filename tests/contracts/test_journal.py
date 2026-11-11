from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from node_agent.spool import Spool
from platform_contracts.models import Command
from pydantic import ValidationError


def command(kind="prepare", **kw):
    values = dict(
        command_id=uuid4(),
        attempt_id=uuid4(),
        node_id="n",
        device_id=uuid4(),
        serial="SIM-001",
        lease_generation=1,
        kind=kind,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    values.update(kw)
    return Command(**values)


def test_pending_events_survive_restart_and_ack_loss(tmp_path):
    spool = Spool(tmp_path, "n")
    c = command()
    spool.event(c, "ready")
    reopened = Spool(tmp_path, "n")
    reopened.event(c, "ready")
    assert len(reopened.events()) == 1
    path, entry = reopened.events()[0]
    assert entry["event"]["kind"] == "ready"
    reopened.acknowledge(path)
    assert Spool(tmp_path, "n").events() == []


def test_events_keep_observed_order_and_ignore_partial_files(tmp_path):
    spool = Spool(tmp_path, "n")
    c = command()
    spool.event(c, "accepted")
    spool.event(c, "ready")
    (tmp_path / "events" / ".pending-interrupted").write_text("{")
    assert [entry["event"]["kind"] for _, entry in spool.events()] == ["accepted", "ready"]


def test_report_ack_does_not_delete_newer_evidence(tmp_path):
    import json

    from platform_contracts.execution_reports import ExecutionReport

    spool = Spool(tmp_path, "n")
    c = command()
    spool.report(c.command_id, c.attempt_id, ExecutionReport(startup_log="first"))
    path = next((tmp_path / "reports").glob("*.json"))
    uploading = json.loads(path.read_text())
    spool.report(c.command_id, c.attempt_id, ExecutionReport(startup_log="second"))
    spool.acknowledge(path, expected=uploading)
    assert json.loads(path.read_text())["payload"]["startup_log"] == "second"
    spool.acknowledge(path, expected=json.loads(path.read_text()))
    assert not path.exists()


def test_protocol_rejects_unknown_versions_and_fields():
    with pytest.raises(ValidationError):
        Command.model_validate({**command().model_dump(), "protocol_version": 2})
    with pytest.raises(ValidationError):
        Command.model_validate({**command().model_dump(), "shell": "rm -rf"})


def test_manifest_stays_in_workspace_and_expiry_has_timezone():
    from platform_contracts.models import ArtifactManifest

    with pytest.raises(ValidationError):
        ArtifactManifest(
            artifact_id=uuid4(),
            attempt_id=uuid4(),
            relative_path="/etc/example.txt",
            sha256="0" * 64,
            size_bytes=1,
            media_type="text/plain",
        )
    with pytest.raises(ValidationError):
        command(expires_at=datetime(2026, 10, 21))
