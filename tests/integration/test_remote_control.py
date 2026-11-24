from control_plane.entrypoints.api import app
from control_plane.infrastructure import db
from control_plane.modules import service
from fastapi.testclient import TestClient


def test_remote_owner_fencing_and_release(database):
    c = TestClient(app)

    body = {"node_id": "mac", "serial": "physical", "state": "device", "artemis_state": "ready"}
    did = c.post("/api/nodes/adb-inventory", json=body).json()["id"]
    base = f"/api/devices/{did}/remote"
    token = c.post(base + "/lease", json={}).json()["token"]
    assert c.post(base + "/lease", json={}).status_code == 409
    assert c.post(base + "/actions", json={"kind": "key"}).status_code == 409
    hdr = {"X-Control-Token": token}
    a = c.post(base + "/actions", json={"kind": "key", "key": "HOME"}, headers=hdr).json()["id"]
    assert c.post(base + "/lease", json={"close": True}, headers=hdr).status_code == 200
    assert c.get("/api/nodes/mac/remote/physical").json() is None
    assert c.get(base + "/actions/" + a).json()["state"] == "failed"
    assert c.get("/api/nodes/mac/remote/physical").json() is None
    assert not c.get("/api/overview").json()["devices"][0]["remote_held"]


def test_real_target_waits_for_remote_lease_and_does_not_fall_back(database):
    from uuid import UUID, uuid4

    from control_plane.modules import tasks
    from platform_contracts.tasks import CreateTask, SaveTask, TaskConfiguration

    c = TestClient(app)

    did = c.post(
        "/api/nodes/adb-inventory",
        json={"node_id": "mac", "serial": "real", "state": "device", "artemis_state": "ready"},
    ).json()["id"]
    c.post("/api/nodes/register", json={"node_id": "sim"})
    with database.begin() as conn:
        case = service.create_case(conn, "real case")
        ver = service.add_version(
            conn,
            case["id"],
            {"title": "case", "target": "android_os", "steps": ["read"], "assertions": ["check"]},
        )
        service.set_stable(conn, case["id"], ver["id"])
        task = tasks.create(conn, CreateTask(name="real", request_id=uuid4()), "anonymous")
        saved = tasks.save(
            conn,
            task["id"],
            SaveTask(
                expected_revision=1,
                name="real",
                case_ids=[case["id"]],
                configuration=TaskConfiguration(target_device_id=UUID(did)),
            ),
            "anonymous",
        )
        tasks.trigger(conn, task["id"], uuid4(), saved["revision"], "anonymous")
    c.post(f"/api/devices/{did}/remote/lease", json={})
    with database.begin() as conn:
        assert not service.schedule_one(conn)
        db.execute(
            conn, "UPDATE devices SET remote_token=NULL,remote_until=NULL WHERE id=:id", id=did
        )
        assert service.schedule_one(conn)
        a = db.one(conn, "SELECT simulation,state FROM attempts")
        assert a["simulation"] is False and a["state"] == "preparing"
        assert str(db.one(conn, "SELECT device_id FROM leases")["device_id"]) == did
        assert db.one(conn, "SELECT payload FROM commands")["payload"]["simulation"] is False


def test_input_bypasses_pending_frame_and_viewers_share_capture(database):
    c = TestClient(app)

    did = c.post(
        "/api/nodes/adb-inventory",
        json={"node_id": "fast", "serial": "real", "state": "device", "artemis_state": "ready"},
    ).json()["id"]
    base = f"/api/devices/{did}/remote"
    token = c.post(base + "/lease", json={}).json()["token"]
    frame = c.post(base + "/actions", json={"kind": "frame"}).json()["id"]
    assert c.post(base + "/actions", json={"kind": "frame"}).json()["id"] == frame
    key = c.post(
        base + "/actions", json={"kind": "key", "key": "BACK"}, headers={"X-Control-Token": token}
    )
    assert key.status_code == 200
    assert c.get("/api/nodes/fast/remote/real").json()["id"] == key.json()["id"]


def test_remote_swipe_preserves_hold_timing(database):
    client = TestClient(app)

    device = client.post(
        "/api/nodes/adb-inventory",
        json={"node_id": "mac", "serial": "real", "state": "device", "artemis_state": "ready"},
    ).json()
    base = f"/api/devices/{device['id']}/remote"
    token = client.post(base + "/lease", json={}).json()["token"]
    headers = {"X-Control-Token": token}
    swipe = {
        "kind": "swipe",
        "x": 0.5,
        "y": 0.99,
        "x2": 0.5,
        "y2": 0.68,
        "width": 576,
        "height": 1280,
        "duration_ms": 280,
        "hold_ms": 700,
    }
    assert (
        client.post(base + "/actions", json={**swipe, "hold_ms": 2001}, headers=headers).status_code
        == 422
    )
    action = client.post(base + "/actions", json=swipe, headers=headers)
    assert action.status_code == 200
    queued = client.get("/api/nodes/mac/remote/real").json()
    assert queued["id"] == action.json()["id"]
    assert queued["payload"]["duration_ms"] == 280
    assert queued["payload"]["hold_ms"] == 700
