import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from control_plane.entrypoints.api import app
from control_plane.infrastructure import db
from control_plane.modules import service, tasks
from fastapi.testclient import TestClient
from node_agent.devices import diagnostics
from platform_contracts.models import Command, DeviceDiagnostics, DeviceIncident, NodeEvent
from platform_contracts.tasks import CreateTask, SaveTask, TaskConfiguration


def test_device_clock_offset_filters_history_and_deduplicates(monkeypatch):
    device = Decimal("1794200000.123000")
    host = float(device) + 30 * 3600
    monkeypatch.setattr(diagnostics, "device_clock", lambda _: (device, host))

    def disconnected(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(diagnostics.subprocess, "Popen", disconnected)
    capture = diagnostics.IncidentCapture("physical")
    event = (
        " 1794200000.423000  2276  2400 I am_crash: "
        "[3100,0,fixture:main,0,java.lang.IllegalStateException,first, second,Main.java,14,0]"
    )
    capture.add(event.replace("0000.423000", "0000.022000"))
    capture.add(event)
    capture.add(event)
    capture.add(
        " 1794200000.623000  2276  2400 I am_anr: "
        "[0,3200,fixture,0,Input dispatching timed out, no focused window]"
    )
    result = capture.report()
    assert result.status == "partial"
    assert len(result.incidents) == 2
    crash, anr = result.incidents
    assert crash.kind == "crash" and crash.process == "fixture:main" and crash.pid == 3100
    assert crash.detail == "java.lang.IllegalStateException: first, second (Main.java:14)"
    assert crash.occurred_at.timestamp() == pytest.approx(host + 0.3, abs=0.001)
    assert anr.kind == "anr" and anr.detail.endswith(", no focused window")


def test_anomalies_survive_replay_cancellation_and_retry(database):
    client = TestClient(app)
    device = client.post(
        "/api/nodes/adb-inventory",
        json={"node_id": "lab", "serial": "phone", "state": "device", "artemis_state": "ready"},
    ).json()
    with database.begin() as conn:
        case = service.create_case(conn, "Stress")
        stable = service.add_version(
            conn,
            case["id"],
            {
                "title": "Stress",
                "target": "android_os",
                "steps": ["repeat taps"],
                "assertions": ["app is responsive"],
            },
        )
        service.set_stable(conn, case["id"], stable["id"])
        task = tasks.create(conn, CreateTask(name="Stress", request_id=uuid4()), "anonymous")
        task = tasks.save(
            conn,
            task["id"],
            SaveTask(
                expected_revision=task["revision"],
                name="Stress",
                case_ids=[case["id"]],
                configuration=TaskConfiguration(target_device_id=device["id"]),
            ),
            "anonymous",
        )
        run = tasks.trigger(conn, task["id"], uuid4(), task["revision"], "anonymous")
        assert service.schedule_one(conn)
        prepare = Command.model_validate(db.one(conn, "SELECT payload FROM commands")["payload"])
        generation = db.one(conn, "SELECT connection_generation FROM nodes WHERE id='lab'")[
            "connection_generation"
        ]

    def emit(cmd, seq, kind, **kwargs):
        event = NodeEvent(
            seq=seq,
            kind=kind,
            command_id=cmd.command_id,
            attempt_id=cmd.attempt_id,
            lease_generation=cmd.lease_generation,
            simulation=False,
            **kwargs,
        )
        with database.begin() as conn:
            assert service.ingest_event(conn, "lab", generation, event) == seq

    emit(prepare, 1, "ready")
    with database.connect() as conn:
        command = Command.model_validate(
            db.one(conn, "SELECT payload FROM commands WHERE payload->>'kind'='execute'")["payload"]
        )
    emit(command, 2, "accepted")
    stamp = datetime.now(timezone.utc)
    crash = DeviceIncident(
        id="a" * 64,
        kind="crash",
        device_at=stamp,
        occurred_at=stamp,
        process="fixture",
        pid=101,
        detail="test crash",
        raw="am_crash: raw fixture",
    )
    update = DeviceDiagnostics(status="collecting", serial="phone", incidents=[crash])
    with pytest.raises(service.Conflict, match="physical execution"):
        emit(command, 3, "diagnostics", diagnostics=update.model_copy(update={"serial": "wrong"}))
    emit(command, 3, "diagnostics", diagnostics=update)
    emit(command, 3, "diagnostics", diagnostics=update)
    with database.begin() as conn:
        service.cancel(conn, command.attempt_id)
        cancel = Command.model_validate(
            db.one(conn, "SELECT payload FROM commands WHERE payload->>'kind'='cancel'")["payload"]
        )
    anr = crash.model_copy(update={"id": "b" * 64, "kind": "anr", "detail": "test ANR"})
    final = DeviceDiagnostics(status="complete", serial="phone", incidents=[anr])
    emit(command, 4, "diagnostics", diagnostics=final)
    emit(command, 4, "diagnostics", diagnostics=final)
    emit(cancel, 5, "cancelled", cleanup="verified")
    with database.begin() as conn:
        retry = service.retry(conn, command.attempt_id, automated=True)
    attempts = client.get(f"/api/executions/{run['id']}").json()["cases"][0]["attempts"]
    assert attempts[0]["id"] == str(retry["id"]) and attempts[0]["diagnostics"] is None
    assert attempts[1]["state"] == "cancelled" and attempts[1]["verdict"] == "not_evaluated"
    assert attempts[1]["diagnostics"]["status"] == "complete"
    assert [i["kind"] for i in attempts[1]["diagnostics"]["incidents"]] == ["crash", "anr"]
    report_path = f"/api/attempts/{command.attempt_id}/report"
    upload_path = f"/api/nodes/lab/execution-reports/{command.command_id}"
    body = {"startup_log": "ModuleNotFoundError: missing gesture module"}

    assert client.get(report_path).json() is None
    assert client.put(upload_path, json=body).status_code == 204
    assert client.put(upload_path.replace("/lab/", "/other/"), json=body).status_code == 404
    assert client.put(upload_path, json=body).status_code == 204
    assert client.put(upload_path, json=body).status_code == 204
    assert client.get(report_path).json()["startup_log"] == body["startup_log"]
    assert client.get(f"/api/attempts/{retry['id']}/report").json() is None

    image = b"\xff\xd8\xfffixture"
    digest = hashlib.sha256(image).hexdigest()
    frame_upload = f"{upload_path}/frames/{digest}"
    assert client.put(frame_upload, content=image).status_code == 204
    assert client.put(frame_upload, content=image + b"bad").status_code == 422
    assert client.put(frame_upload, content=image).status_code == 204
    assert client.put(frame_upload, content=image).status_code == 204
    visual = {
        "visual_version": 1,
        "steps": [{"number": 1, "before": digest, "validation": "skipped"}],
    }
    assert client.put(upload_path, json=visual).status_code == 204
    assert client.put(upload_path, json=body).status_code == 204
    assert client.get(report_path).json()["steps"][0]["before"] == digest
    assert client.get(f"{report_path}/frames/{digest}").content == image
    assert client.get(f"/api/attempts/{retry['id']}/report/frames/{digest}").status_code == 404
