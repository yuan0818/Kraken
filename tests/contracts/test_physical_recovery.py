from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
from node_agent.adapters.artemis import goal
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
