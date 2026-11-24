from subprocess import CompletedProcess
from uuid import uuid4

from control_plane.entrypoints.api import app
from control_plane.infrastructure import db
from control_plane.modules import service
from fastapi.testclient import TestClient
from node_agent.devices.observe import inspect_device
from platform_contracts.models import SubmitRun


def test_observation_and_loss_never_enter_simulated_execution_pool(database, monkeypatch):
    client = TestClient(app)

    serial, node = "sample-sn", "mac-observer"
    inventory = "List of devices attached\nsample-sn-extra device model:Wrong\n"

    def probe(argv, **kwargs):
        assert argv == ["adb", "devices", "-l"]
        return CompletedProcess(argv, 0, stdout=inventory)

    monkeypatch.setattr("node_agent.devices.observe.subprocess.run", probe)
    endpoint = "/api/nodes/adb-inventory"

    def current():
        return inspect_device(node, serial).model_dump()

    def report():
        response = client.post(endpoint, json=current())
        assert response.status_code == 200
        return response.json()

    assert client.post(endpoint, json=current()).status_code == 200
    device = report()
    assert device["adb_state"] == "absent"
    inventory += "sample-sn unauthorized\n"
    assert report()["adb_state"] == "unauthorized"
    inventory = inventory.replace(
        "sample-sn unauthorized", "sample-sn device product:Product model:Phone"
    )
    assert report()["id"] == device["id"]
    rows = client.get("/api/overview").json()["devices"]
    assert len(rows) == 1 and rows[0]["adb_state"] == "device" and rows[0]["adb_model"] == "Phone"
    assert client.post("/api/nodes/register", json={"node_id": node}).status_code == 409
    with database.begin() as conn:
        case = service.create_case(conn, "case")
        service.submit(conn, SubmitRun(case_id=case["id"], request_id=uuid4()))
        assert not service.schedule_one(conn)
        assert db.one(conn, "SELECT count(*) AS n FROM leases")["n"] == 0
    inventory = "List of devices attached\n"
    assert report()["adb_state"] == "absent"
    stored = client.get("/api/overview").json()["devices"][0]
    assert stored["id"] == device["id"] and stored["adb_model"] == "Phone"
