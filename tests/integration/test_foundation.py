from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from control_plane.entrypoints.api import app
from control_plane.entrypoints.publisher import publish_one
from control_plane.infrastructure import db
from control_plane.modules import service as s
from fastapi.testclient import TestClient
from node_agent.spool import Spool
from node_agent.supervisor.simulator import Simulator
from node_agent.transport.client import RuntimeClient
from platform_contracts.models import Command, NodeEvent, SubmitRun
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration
CONTENT = {
    "title": "V1",
    "target": "android_os",
    "steps": ["打开设置"],
    "assertions": ["显示设置"],
    "required_tags": ["simulator"],
}


def seed(engine):
    with engine.begin() as c:
        case = s.create_case(c, "settings")
        v = s.add_version(c, case["id"], CONTENT)
        s.set_stable(c, case["id"], v["id"])
        run = s.submit(
            c,
            SubmitRun(
                case_id=case["id"], request_id=uuid4(), artifacts={"firmware": "sha256:frozen"}
            ),
        )
        db.execute(
            c,
            "INSERT INTO nodes(id,token_hash,capabilities,last_seen,connection_generation) VALUES ('n','hash','[\"simulator\"]',now(),1)",
        )
        device = uuid4()
        db.execute(
            c,
            "INSERT INTO devices(id,node_id,serial,tags) VALUES (:id,'n','SIM-001','[\"simulator\"]')",
            id=device,
        )
        a = db.one(c, "SELECT * FROM attempts")
    return case, v, run, a, device


def event(engine, command, seq, kind, **kw):
    value = NodeEvent(
        command_id=command.command_id,
        attempt_id=command.attempt_id,
        lease_generation=command.lease_generation,
        seq=seq,
        kind=kind,
        **kw,
    )
    with engine.begin() as c:
        s.ingest_event(c, "n", 1, value)
    return value


def prepare(engine):
    with engine.begin() as c:
        assert s.schedule_one(c)
        return Command.model_validate(
            db.one(c, "SELECT payload FROM commands WHERE payload->>'kind'='prepare'")["payload"]
        )


def test_versions_immutable_and_stable_same_case(database):
    case, v, _, _, _ = seed(database)
    with pytest.raises(DBAPIError), database.begin() as c:
        db.execute(c, "UPDATE case_versions SET content='{}' WHERE id=:id", id=v["id"])
    with database.begin() as c:
        other = s.create_case(c, "other")
    with pytest.raises(DBAPIError), database.begin() as c:
        db.execute(c, "UPDATE cases SET stable_version_id=:v WHERE id=:c", v=v["id"], c=other["id"])
    with database.begin() as c:
        s.add_version(c, case["id"], {**CONTENT, "title": "V2"})
        assert (
            db.one(c, "SELECT stable_version_id FROM cases WHERE id=:id", id=case["id"])[
                "stable_version_id"
            ]
            == v["id"]
        )


def test_stable_resolved_at_authorization_and_snapshot_frozen(database):
    case, v, _, a, _ = seed(database)
    p = prepare(database)
    with database.begin() as c:
        new = s.add_version(
            c, case["id"], {**CONTENT, "title": "V2", "steps_markdown": "1. 打开**设置**"}
        )
        s.set_stable(c, case["id"], new["id"])
    event(database, p, 1, "ready")
    with database.begin() as c:
        snapshot = db.one(c, "SELECT * FROM attempts")["snapshot"]
        assert snapshot["title"] == "V2"
        assert snapshot["steps_markdown"] == "1. 打开**设置**"
        payload = db.one(c, "SELECT payload FROM commands WHERE payload->>'kind'='execute'")[
            "payload"
        ]
        assert payload["content"]["steps"] == CONTENT["steps"]
        assert "steps_markdown" not in payload["content"]
        s.set_stable(c, case["id"], v["id"])
    with pytest.raises(DBAPIError), database.begin() as c:
        db.execute(c, "UPDATE attempts SET snapshot='{}' WHERE id=:a", a=a["id"])
    with database.connect() as c:
        assert db.one(c, "SELECT snapshot FROM attempts")["snapshot"] == snapshot


def test_changed_constraints_cancel_before_execute(database):
    case, _, _, _, _ = seed(database)
    p = prepare(database)
    with database.begin() as c:
        v = s.add_version(c, case["id"], {**CONTENT, "required_tags": ["missing"]})
        s.set_stable(c, case["id"], v["id"])
    event(database, p, 1, "ready")
    with database.connect() as c:
        assert db.one(c, "SELECT state FROM attempts")["state"] == "cancel_pending"
        assert not db.one(c, "SELECT id FROM commands WHERE payload->>'kind'='execute'")
        assert db.one(c, "SELECT id FROM leases WHERE released_at IS NULL")


def test_parallel_scheduler_cannot_double_lease(database):
    case, _, _, _, device = seed(database)
    with database.begin() as c:
        s.submit(c, SubmitRun(case_id=case["id"], request_id=uuid4()))

    def tick(_):
        with database.begin() as c:
            return s.schedule_one(c)

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(tick, range(4))) == 1
    with database.connect() as c:
        assert db.one(c, "SELECT count(*) AS n FROM leases WHERE released_at IS NULL")["n"] == 1
        other = db.one(c, "SELECT id FROM attempts WHERE state='queued'")["id"]
    with pytest.raises(DBAPIError), database.begin() as c:
        db.execute(
            c,
            "INSERT INTO leases(id,device_id,attempt_id,generation) VALUES (:id,:d,:a,2)",
            id=uuid4(),
            d=device,
            a=other,
        )


def test_idempotent_submission_and_conflicting_reuse(database):
    case, _, _, _, _ = seed(database)
    r = SubmitRun(case_id=case["id"], request_id=uuid4())
    with database.begin() as c:
        first = s.submit(c, r)
        assert s.submit(c, r) == first
    with pytest.raises(s.Conflict, match="request_id_conflict"), database.begin() as c:
        s.submit(c, r.model_copy(update={"artifacts": {"different": "x"}}))


def test_duplicate_out_of_order_stale_and_unknown_events(database):
    seed(database)
    p = prepare(database)
    e = event(database, p, 1, "accepted")
    with database.begin() as c:
        assert s.ingest_event(c, "n", 1, e) == 1
    with pytest.raises(s.Conflict, match="out_of_order"):
        event(database, p, 3, "ready")
    with pytest.raises(s.Conflict, match="event_conflict"):
        event(database, p, 1, "ready")
    with pytest.raises(s.Conflict, match="stale_connection"), database.begin() as c:
        s.ingest_event(c, "n", 0, e)
    with pytest.raises(s.Conflict, match="unknown_command"):
        event(database, p.model_copy(update={"command_id": uuid4()}), 2, "accepted")
    with pytest.raises(s.Conflict, match="lease_mismatch"):
        event(database, p.model_copy(update={"lease_generation": 2}), 2, "ready")


def test_outbox_publish_confirmation_loss_reuses_command(database):
    seed(database)
    p = prepare(database)
    sent = []
    with pytest.raises(RuntimeError), database.begin() as c:
        publish_one(c, send=sent.append)
        raise RuntimeError("crash after broker confirm before database commit")
    with database.begin() as c:
        publish_one(c, send=sent.append)
    assert sent == [str(p.command_id), str(p.command_id)]


def test_cleanup_failure_preserves_lease_and_blocks_retry(database):
    seed(database)
    p = prepare(database)
    event(database, p, 1, "ready")
    with database.connect() as c:
        ex = Command.model_validate(
            db.one(c, "SELECT payload FROM commands WHERE payload->>'kind'='execute'")["payload"]
        )
    event(database, ex, 2, "finished", cleanup="failed", verdict="inconclusive")
    with database.begin() as c:
        assert db.one(c, "SELECT state FROM attempts")["state"] == "needs_recovery"
        assert db.one(c, "SELECT id FROM leases WHERE released_at IS NULL")
    with pytest.raises(s.Conflict, match="retry_not_safe"), database.begin() as c:
        s.retry(c, p.attempt_id)


def test_roundtrip_restart_retry_reads_new_stable(database, tmp_path):
    case, _, run, a, _ = seed(database)
    prepare(database)
    runtime = RuntimeClient(TestClient(app), "n", Spool(tmp_path, "n"))
    sim = Simulator(runtime, duration=0)

    def pump():
        for _ in range(5):
            with database.begin() as c:
                db.execute(c, "UPDATE commands SET dispatch_ready=true")
            sim.recover(runtime.refresh())
            sim.tick()

    try:
        pump()
        with database.begin() as c:
            assert db.one(c, "SELECT state FROM attempts")["state"] == "completed"
            v = s.add_version(c, case["id"], {**CONTENT, "title": "retry version"})
            s.set_stable(c, case["id"], v["id"])
            retry = s.retry(c, a["id"])
            assert s.schedule_one(c)
        pump()
        with database.connect() as c:
            assert (
                db.one(c, "SELECT version_id FROM attempts WHERE id=:id", id=retry["id"])[
                    "version_id"
                ]
                == v["id"]
            )
            assert db.one(c, "SELECT artifacts FROM runs WHERE id=:id", id=run["id"])[
                "artifacts"
            ] == {"firmware": "sha256:frozen"}
            assert db.one(c, "SELECT count(*) AS n FROM leases WHERE released_at IS NULL")["n"] == 0
        with database.connect() as c:
            assert (
                db.one(c, "SELECT count(*) AS n FROM commands WHERE launch_intent_at IS NOT NULL")[
                    "n"
                ]
                == 2
            )
    finally:
        sim.close()


def test_websocket_reconnect_after_ack_loss(database):
    seed(database)
    p = prepare(database)
    with database.begin() as c:
        db.execute(c, "UPDATE commands SET dispatch_ready=true")
    client = TestClient(app)

    ev = NodeEvent(
        seq=1, command_id=p.command_id, attempt_id=p.attempt_id, lease_generation=1, kind="accepted"
    ).model_dump(mode="json")
    with client.websocket_connect("/api/nodes/n/ws") as ws:
        ws.send_json({"type": "hello", "protocol_version": 1, "last_seq": 0})
        assert ws.receive_json()["ack_cursor"] == 0
        assert ws.receive_json()["type"] == "command"
        ws.send_json({"type": "event", "event": ev})
        assert ws.receive_json()["seq"] == 1
        # Client crashes before persisting the received ACK.
    with client.websocket_connect("/api/nodes/n/ws") as ws:
        ws.send_json({"type": "hello", "protocol_version": 1, "last_seq": 1})
        assert ws.receive_json()["ack_cursor"] == 1
        ws.send_json({"type": "event", "event": ev})
        assert ws.receive_json() == {"type": "event_ack", "seq": 1}
    with database.connect() as c:
        assert db.one(c, "SELECT count(*) AS n FROM node_events")["n"] == 1


def test_websocket_refuses_dispatch_after_control_cursor_rollback(database):
    seed(database)
    p = prepare(database)
    with database.begin() as conn:
        db.execute(conn, "UPDATE commands SET dispatch_ready=true")
    client = TestClient(app)

    with client.websocket_connect("/api/nodes/n/ws") as ws:
        ws.send_json({"type": "hello", "protocol_version": 1, "last_seq": 1, "acked_seq": 1})
        assert ws.receive_json()["error"] == "control_cursor_rollback"
    with database.connect() as conn:
        row = db.one(conn, "SELECT connection_generation, last_event_seq FROM nodes WHERE id='n'")
        assert row["connection_generation"] == 1 and row["last_event_seq"] == 0

    # A node with one persisted event and an ACK it never received can still resume.
    with client.websocket_connect("/api/nodes/n/ws") as ws:
        ws.send_json({"type": "hello", "protocol_version": 1, "last_seq": 1, "acked_seq": 0})
        assert ws.receive_json()["ack_cursor"] == 0
        assert ws.receive_json()["command"]["command_id"] == str(p.command_id)


def test_open_mode_nodes_register_without_credentials(database):
    client = TestClient(app)
    assert client.get("/api/me").json()["authentication"] == "open"
    assert (
        client.post(
            "/api/nodes/register", json={"node_id": "open-node", "device_serials": ["SIM-OPEN"]}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/nodes/register",
            json={"node_id": "n", "device_serials": ["real-device"]},
        ).status_code
        == 422
    )


def test_cancel_finish_race_does_not_poison_node_cursor(database):
    seed(database)
    p = prepare(database)
    event(database, p, 1, "ready")
    with database.begin() as c:
        ex = Command.model_validate(
            db.one(c, "SELECT payload FROM commands WHERE payload->>'kind'='execute'")["payload"]
        )
        s.cancel(c, p.attempt_id)
        cancel = Command.model_validate(
            db.one(c, "SELECT payload FROM commands WHERE payload->>'kind'='cancel'")["payload"]
        )
    event(database, ex, 2, "finished", cleanup="verified", verdict="inconclusive")
    event(database, cancel, 3, "cancelled", cleanup="verified")
    with database.connect() as c:
        assert db.one(c, "SELECT last_event_seq FROM nodes")["last_event_seq"] == 3
        assert db.one(c, "SELECT state FROM attempts")["state"] == "completed"


def test_lost_worker_handoff_is_republished_without_new_attempt(database):
    seed(database)
    p = prepare(database)
    sent = []
    with database.begin() as c:
        assert publish_one(c, send=sent.append)
        assert not publish_one(c, send=sent.append)
        db.execute(c, "UPDATE outbox SET published_at=now()-interval '31 seconds'")
        assert publish_one(c, send=sent.append)
        db.execute(c, "UPDATE commands SET dispatch_ready=true")
        db.execute(c, "UPDATE outbox SET published_at=now()-interval '31 seconds'")
        assert not publish_one(c, send=sent.append)
        assert db.one(c, "SELECT count(*) AS n FROM attempts")["n"] == 1
    assert sent == [str(p.command_id), str(p.command_id)]


def test_same_serial_on_two_nodes_never_aliases_lease(database):
    case, _, _, _, _ = seed(database)
    with database.begin() as c:
        db.execute(
            c,
            "INSERT INTO nodes(id,token_hash,capabilities,last_seen) VALUES ('n2','hash','[\"simulator\"]',now())",
        )
        db.execute(
            c,
            "INSERT INTO devices(id,node_id,serial,tags) VALUES (:id,'n2','SIM-001','[\"simulator\"]')",
            id=uuid4(),
        )
        s.submit(c, SubmitRun(case_id=case["id"], request_id=uuid4()))
        assert s.schedule_one(c)
    with database.begin() as c:
        assert s.schedule_one(c)
        commands = db.rows(c, "SELECT node_id,payload FROM commands")
        assert {cmd["node_id"] for cmd in commands} == {"n", "n2"}
        assert len({cmd["payload"]["device_id"] for cmd in commands}) == 2
