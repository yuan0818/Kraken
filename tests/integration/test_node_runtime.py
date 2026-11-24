"""Central admission, fencing and durable observations across process/ACK loss."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from control_plane.entrypoints.api import app
from control_plane.infrastructure import db
from fastapi.testclient import TestClient
from node_agent.spool import Spool
from node_agent.supervisor.simulator import Simulator
from node_agent.transport.client import RuntimeClient
from platform_contracts.models import Command, NodeUpdate
from test_foundation import event, prepare, seed

pytestmark = pytest.mark.integration


def runtime(tmp_path):
    return RuntimeClient(TestClient(app), "n", Spool(tmp_path, "n"))


def dispatch(database):
    with database.begin() as c:
        db.execute(c, "UPDATE commands SET dispatch_ready=true")


def executing(database, tmp_path):
    seed(database)
    p = prepare(database)
    dispatch(database)
    rt = runtime(tmp_path)
    rt.refresh()
    rt.claim(p)
    rt.spool.event(p, "ready")
    rt.refresh()
    dispatch(database)
    job = rt.refresh()[0]
    return rt, Command.model_validate(job["command"])


def test_admission_response_loss_and_concurrent_retries_grant_once(database, tmp_path):
    rt, cmd = executing(database, tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        grants = list(pool.map(lambda _: rt.claim(cmd), range(4)))
    assert sum(g["start"] for g in grants) == 1
    assert len({g["launch_intent_at"] for g in grants}) == 1
    restarted = runtime(tmp_path)
    jobs = restarted.refresh()
    assert jobs[0]["launch_intent_at"] == grants[0]["launch_intent_at"]
    assert restarted.claim(cmd)["start"] is False
    with pytest.raises(httpx.HTTPStatusError) as err:
        rt.claim(cmd)
    assert err.value.response.status_code == 409
    # An old boot cannot take back authority by repeating registration of that boot.
    response = rt.client.post("/api/nodes/n/runtime", json={"boot_id": str(rt.boot_id)})
    assert response.status_code == 409


def test_boot_open_retry_is_idempotent(database, tmp_path):
    seed(database)
    rt = runtime(tmp_path)
    body = {"boot_id": str(rt.boot_id)}
    first = rt.client.post("/api/nodes/n/runtime", json=body)
    second = rt.client.post("/api/nodes/n/runtime", json=body)
    assert first.json() == second.json()


def test_event_ack_loss_is_idempotent_and_payload_conflicts_rejected(database, tmp_path):
    rt, cmd = executing(database, tmp_path)
    rt.claim(cmd)
    observation = rt.spool.event(cmd, "started")
    response = rt.request(
        "POST", "/events", headers=rt.headers(), json=observation.model_dump(mode="json")
    )
    assert response["acknowledged"] == str(observation.event_id)
    # Deliberately leave the acknowledged file on disk (lost reply).
    restarted = runtime(tmp_path)
    assert restarted.refresh()[0]["state"] == "running"
    assert not restarted.spool.events()
    bad = observation.model_copy(update={"reason": "different fact"})
    with pytest.raises(httpx.HTTPStatusError) as err:
        restarted.request(
            "POST", "/events", headers=restarted.headers(), json=bad.model_dump(mode="json")
        )
    assert err.value.response.status_code == 409
    with database.connect() as c:
        assert (
            db.one(c, "SELECT count(*) AS n FROM node_events WHERE payload->>'kind'='started'")["n"]
            == 1
        )


def test_no_start_without_admission_and_wrong_lease_rejected(database, tmp_path):
    rt, cmd = executing(database, tmp_path)
    observation = NodeUpdate(
        event_id=uuid4(),
        command_id=cmd.command_id,
        attempt_id=cmd.attempt_id,
        lease_generation=cmd.lease_generation,
        kind="started",
    )
    with pytest.raises(httpx.HTTPStatusError):
        rt.request(
            "POST", "/events", headers=rt.headers(), json=observation.model_dump(mode="json")
        )
    rt.claim(cmd)
    bad = observation.model_copy(update={"lease_generation": cmd.lease_generation + 1})
    with pytest.raises(httpx.HTTPStatusError):
        rt.request("POST", "/events", headers=rt.headers(), json=bad.model_dump(mode="json"))
    with database.connect() as c:
        assert db.one(c, "SELECT state FROM attempts")["state"] == "starting"


def test_restart_reconstructs_central_job_and_offline_result_replays(database, tmp_path):
    rt, cmd = executing(database, tmp_path)
    rt.claim(cmd)
    # No local jobs/command database; reconstruct using control-plane snapshot only.
    restarted = runtime(tmp_path)
    sim = Simulator(restarted, duration=0)
    sim.recover(restarted.refresh())
    restarted.online = False
    try:
        sim.tick()
        assert [e["event"]["kind"] for _, e in restarted.spool.events()] == ["finished"]
        with database.connect() as c:
            assert db.one(c, "SELECT released_at FROM leases")["released_at"] is None
        assert restarted.refresh() == []
        with database.connect() as c:
            assert db.one(c, "SELECT state FROM attempts")["state"] == "completed"
            assert db.one(c, "SELECT released_at FROM leases")["released_at"] is not None
    finally:
        sim.close()
    assert not list(tmp_path.rglob("*.sqlite*"))


def test_legacy_ack_loss_and_unacknowledged_tail(database, tmp_path):
    seed(database)
    cmd = prepare(database)
    previous = event(database, cmd, 1, "accepted")
    rt = runtime(tmp_path)
    for old in [previous, previous.model_copy(update={"seq": 2, "kind": "ready"})]:
        rt.spool.enqueue(
            NodeUpdate(event_id=uuid4(), legacy_seq=old.seq, **old.model_dump(exclude={"seq"})),
            order=old.seq,
        )
    rt.refresh()
    assert not rt.spool.events()
    with database.connect() as c:
        assert db.one(c, "SELECT last_event_seq FROM nodes")["last_event_seq"] == 2
        assert db.one(c, "SELECT state FROM attempts")["state"] == "starting"
