from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from node_agent.adapters.artemis import Artemis, goal
from node_agent.spool import Spool
from node_agent.supervisor.artemis import PhysicalWorker
from platform_contracts.models import CaseContent, Command


def command():
    return Command(
        command_id=uuid4(),
        attempt_id=uuid4(),
        device_id=uuid4(),
        node_id="n",
        serial="real-sn",
        simulation=False,
        lease_generation=1,
        kind="execute",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        version_id=uuid4(),
        content=CaseContent(
            title="test", target="android_os", steps=["open"], assertions=["visible"]
        ),
    )


def worker(tmp_path, native, monkeypatch):
    runtime = SimpleNamespace(spool=Spool(tmp_path, "n", "real-sn"), online=True)
    runtime.claim = lambda cmd: {
        "start": True,
        "launch_intent_at": datetime.now(timezone.utc).isoformat(),
    }
    w = PhysicalWorker(runtime, native, "n", "real-sn")
    monkeypatch.setattr(w, "start_diagnostics", lambda *a, **kw: None)
    monkeypatch.setattr(w, "collect_report", lambda *a, **kw: None)
    return w


def job(cmd, intent=None):
    return {
        "attempt_id": str(cmd.attempt_id),
        "serial": cmd.serial,
        "state": "starting",
        "command": cmd.model_dump(mode="json"),
        "execution_command": cmd.model_dump(mode="json"),
        "launch_intent_at": intent,
    }


def test_lost_native_submit_response_restart_never_resubmits(tmp_path, monkeypatch):
    cmd = command()
    starts = []

    def submit(command):
        starts.append(command.attempt_id)
        raise httpx.ReadTimeout("response lost after engine started")

    native = SimpleNamespace(
        submit=submit,
        session=lambda _: {
            "device_info": {"device_id": cmd.serial},
            "session_id": str(cmd.attempt_id),
            "initial_goal": goal(cmd),
            "status": "running",
        },
    )
    first = worker(tmp_path, native, monkeypatch)
    current = job(cmd)
    first.recover([current])
    first.tick({"state": "device", "is_busy": False})
    assert current["launch_intent_at"]
    second = worker(tmp_path, native, monkeypatch)
    second.recover([current])
    second.tick({"state": "device", "is_busy": True})
    assert starts == [cmd.attempt_id]
    assert [e["event"]["kind"] for _, e in second.spool.events()] == ["started"]


def test_uncertain_launch_keeps_device_for_recovery(tmp_path, monkeypatch):
    cmd = command()

    def submit(_):
        raise AssertionError("must never resubmit after central admission")

    native = SimpleNamespace(submit=submit, session=lambda _: None)
    w = worker(tmp_path, native, monkeypatch)
    w.recover([job(cmd, (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat())])
    w.tick({"state": "device", "is_busy": False})
    event = w.spool.events()[-1][1]["event"]
    assert event["kind"] == "finished" and event["cleanup"] == "failed"
    assert event["verdict"] == "blocked"


def test_offline_node_cannot_start_unadmitted_work(tmp_path, monkeypatch):
    cmd = command()

    def submit(_):
        raise AssertionError("offline start")

    w = worker(tmp_path, SimpleNamespace(submit=submit), monkeypatch)
    w.runtime.online = False
    w.recover([job(cmd)])
    w.tick({"state": "device", "is_busy": False})
    assert not w.spool.events()


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422])
def test_http_admission_rejection_is_recorded_and_releases_idle_device(
    tmp_path, monkeypatch, status
):
    cmd = command()
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/run":
            return httpx.Response(
                status,
                json={
                    "detail": "Android device real-sn is locked. Unlock it and enter the home screen before running a task."
                },
            )
        if request.url.path.startswith("/api/sessions/"):
            return httpx.Response(404)
        return httpx.Response(
            200, json={"devices": [{"serial": cmd.serial, "state": "device", "is_busy": False}]}
        )

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(handler)
    ) as client:
        w = worker(tmp_path, Artemis(client), monkeypatch)
        w.recover([job(cmd)])
        w.tick({"state": "device", "is_busy": False})
        event = w.spool.events()[-1][1]["event"]
        assert event["kind"] == "rejected" and event["cleanup"] == "verified"
        assert event["verdict"] == "blocked"
        assert f"HTTP {status}" in event["reason"] and "锁屏" in event["reason"]
        assert w.rejected_submission(cmd.attempt_id)["http_status"] == status
        assert calls.count(("POST", "/api/run")) == 1


@pytest.mark.parametrize("status", [408, 500, 502, 503])
def test_ambiguous_admission_errors_still_require_session_reconciliation(status):
    with httpx.Client(
        base_url="http://localhost",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, json={"detail": "uncertain"})
        ),
    ) as client:
        with pytest.raises(httpx.HTTPStatusError):
            Artemis(client).submit(command())


def test_rejection_receipt_survives_restart_without_resubmission(tmp_path, monkeypatch):
    cmd = command()
    native = SimpleNamespace(
        session=lambda _: None,
        device=lambda _: {"state": "device", "is_busy": False},
        submit=lambda _: pytest.fail("must not submit a rejected attempt again"),
    )
    w = worker(tmp_path, native, monkeypatch)
    w.save(
        cmd.attempt_id,
        "submission",
        {"status": "rejected", "error": "执行引擎拒绝启动（HTTP 409）：设备锁屏"},
    )
    current = job(cmd, datetime.now(timezone.utc).isoformat())
    current["state"] = "needs_recovery"
    w.recover([current])
    w.tick({"state": "device", "is_busy": False})
    event = w.spool.events()[-1][1]["event"]
    assert event["kind"] == "rejected" and event["cleanup"] == "verified"


def test_rejection_receipt_does_not_release_an_existing_engine_session(tmp_path, monkeypatch):
    cmd = command()
    native = SimpleNamespace(
        session=lambda _: {"status": "running"},
        device=lambda _: {"state": "device", "is_busy": False},
    )
    w = worker(tmp_path, native, monkeypatch)
    w.save(cmd.attempt_id, "submission", {"status": "rejected", "error": "rejected"})
    current = job(cmd, datetime.now(timezone.utc).isoformat())
    current["state"] = "needs_recovery"
    w.recover([current])
    w.tick({"state": "device", "is_busy": False})
    assert not w.spool.events()


def test_rejected_model_validation_does_not_persist_credentials():
    key = "scoped-private-key"
    model = {
        "config_id": str(uuid4()),
        "revision": 1,
        "model_id": "test",
        "api_schema": "openai",
        "url": "http://localhost/v1",
        "context_window": 32768,
        "thinking_effort": "default",
        "api_key": key,
    }
    with httpx.Client(
        base_url="http://localhost",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                422, json={"detail": [{"msg": f"invalid {key}", "input": key}]}
            )
        ),
    ) as client:
        reply = Artemis(client).submit(command(), model)
    assert reply["status"] == "rejected"
    assert key not in str(reply) and "[REDACTED]" in reply["error"]
