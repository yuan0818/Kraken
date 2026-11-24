import subprocess
import sys

from control_plane.entrypoints.api import app
from control_plane.infrastructure import db
from fastapi.testclient import TestClient
from node_agent.devices import remote


def setup(client, serial="physical"):
    device = client.post(
        "/api/nodes/adb-inventory",
        json={"node_id": "cli", "serial": serial, "state": "device", "artemis_state": "ready"},
    ).json()["id"]
    base = f"/api/devices/{device}/remote"
    token = client.post(base + "/lease", json={}).json()["token"]
    return device, base, {"X-Control-Token": token}


def test_cli_ownership_result_and_durable_paged_history(database):
    client = TestClient(app)
    did, base, headers = setup(client)
    command = "getprop ro.product.model | head -1\nprintf '中文'"
    assert (
        client.post(base + "/actions", json={"kind": "shell", "command": command}).status_code
        == 409
    )
    assert (
        client.post(
            base + "/actions", headers=headers, json={"kind": "shell", "command": "  "}
        ).status_code
        == 422
    )
    action = client.post(
        base + "/actions", headers=headers, json={"kind": "shell", "command": command}
    ).json()["id"]
    queued = client.get("/api/nodes/cli/remote/physical").json()
    assert queued["id"] == action and queued["payload"]["command"] == command
    output = {
        "stdout": "TEST_DEVICE\n中文",
        "stderr": "fixture error",
        "exit_code": 2,
        "error": "命令退出码为 2",
    }
    client.post(f"/api/nodes/cli/remote/physical/{action}", json={"result": output})
    with database.begin() as conn:
        db.execute(
            conn,
            "UPDATE remote_actions SET created_at=now()-interval '1 day' WHERE id=:id",
            id=action,
        )
    client.get("/api/nodes/cli/remote/physical")
    history = TestClient(app).get(base + "/commands?page_size=1").json()
    assert history["total"] == 1
    record = history["items"][0]
    assert record["result"] == output and record["state"] == "failed"
    assert record["started_at"] and record["finished_at"]
    assert "token_hash" not in record and "token" not in record
    assert client.get(base + "/commands?page=2&page_size=1").json()["items"] == []
    _, other, _ = setup(client, "another")
    assert client.get(other + "/commands").json()["total"] == 0


def test_expired_cli_is_recorded_and_not_replayed(database):
    client = TestClient(app)
    _, base, headers = setup(client)
    aid = client.post(
        base + "/actions", headers=headers, json={"kind": "shell", "command": "pwd"}
    ).json()["id"]
    with database.begin() as conn:
        db.execute(
            conn,
            "UPDATE remote_actions SET created_at=now()-interval '1 day',expires_at=now()-interval '1 day' WHERE id=:id",
            id=aid,
        )
    assert client.get("/api/nodes/cli/remote/physical").json() is None
    item = client.get(base + "/commands").json()["items"][0]
    assert item["state"] == "failed" and item["finished_at"]
    assert item["result"]["error"] == "操作已过期"


def run_fixture(monkeypatch, script, command="printf '中文'; echo test | cat"):
    popen = subprocess.Popen
    calls = []

    def start(args, **kwargs):
        calls.append(args)
        assert "shell" not in kwargs
        return popen([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(remote.subprocess, "Popen", start)
    result = remote.shell_command("fixture-sn", command)
    assert calls[0][:4] == ["adb", "-s", "fixture-sn", "shell"]
    assert calls[0][4].startswith("timeout -s KILL 15 sh -c ")
    return result


def test_cli_separates_streams_and_exit_code(monkeypatch):
    result = run_fixture(
        monkeypatch, "import sys; print('中文'); print('failure',file=sys.stderr); sys.exit(7)"
    )
    assert result["stdout"] == "中文\n" and result["stderr"] == "failure\n"
    assert result["exit_code"] == 7 and result["error"]


def test_cli_output_is_bounded(monkeypatch):
    result = run_fixture(monkeypatch, "import sys; sys.stdout.write('x'*500000)")
    assert result["truncated"] and len(result["stdout"].encode()) <= 131072


def test_cli_timeout_kills_local_adb_and_returns_result(monkeypatch):
    times = iter([0, 19])
    monkeypatch.setattr(remote.time, "monotonic", lambda: next(times, 19))
    result = run_fixture(monkeypatch, "import time; time.sleep(60)")
    assert result["timed_out"] and "超时" in result["error"]
