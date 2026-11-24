import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import UUID, uuid4

import pytest
from control_plane.entrypoints.api import app
from control_plane.infrastructure import db, secrets
from control_plane.modules import model_configs, service, tasks
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from platform_contracts.models import NodeEvent

pytestmark = pytest.mark.integration
KEY = "test-private-model-key-never-echo"
BODY = {
    "model_id": "local/model-a",
    "name": "内网模型",
    "api_schema": "openai",
    "url": "http://127.0.0.1:9999/v1",
    "api_key": KEY,
    "provider": "Internal",
    "context_window": 32768,
    "thinking_effort": "default",
}


@pytest.fixture
def client(database, tmp_path, monkeypatch):
    path = tmp_path / "master.key"
    path.write_bytes(Fernet.generate_key())
    path.chmod(0o600)
    monkeypatch.setenv("PLATFORM_SECRET_KEY_FILE", str(path))
    return TestClient(app, client=("127.0.0.1", 50000))


def create(client, **overrides):
    response = client.post("/api/models", json={**BODY, **overrides})
    assert response.status_code == 201, response.text
    assert KEY not in response.text
    return response.json()


def update(client, model, **overrides):
    fields = {k: model[k] for k in model_configs.PUBLIC_FIELDS}
    return client.put(
        f"/api/models/{model['id']}",
        json={**fields, "expected_revision": model["revision"], **overrides},
    )


def test_credentials_are_encrypted_and_history_never_returns_them(client, database):
    model = create(client)
    assert model["has_key"] and "api_key" not in model
    with database.connect() as conn:
        row = db.one(conn, "SELECT ciphertext FROM model_credentials")
        assert KEY not in row["ciphertext"] and secrets.decrypt(row["ciphertext"]) == KEY
        assert KEY not in str(db.rows(conn, "SELECT * FROM model_config_versions"))
        assert KEY not in str(db.rows(conn, "SELECT * FROM audit"))
    response = update(client, model, name="新名称")
    assert response.status_code == 200
    latest = response.json()
    assert latest["revision"] == 2 and latest["has_key"]
    history = client.get(f"/api/models/{model['id']}/versions")
    assert KEY not in history.text and history.json()[1]["name"] == "内网模型"
    with database.begin() as conn, pytest.raises(Exception, match="immutable"):
        db.execute(conn, "UPDATE model_config_versions SET definition='{}'")


def test_rotate_keep_clear_and_destination_change_require_explicit_key(client, database):
    model = create(client)
    assert update(client, model, url="https://another.example/v1").status_code == 409
    next_model = update(client, model, api_key="new-key").json()
    assert next_model["revision"] == 2
    third = update(client, next_model, api_key="", url="https://another.example/v1").json()
    assert not third["has_key"]
    with database.connect() as conn:
        values = db.rows(conn, "SELECT ciphertext FROM model_credentials ORDER BY created_at")
        assert [secrets.decrypt(v["ciphertext"]) for v in values] == [KEY, "new-key"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"url": "ftp://example.org"},
        {"url": f"https://user:{KEY}@example.org"},
        {"url": f"https://example.org?key={KEY}"},
        {"context_window": 1},
        {"api_key": KEY + "\n"},
        {"url": "https://example.org/v1/chat/completions"},
        {"api_schema": "anthropic", "thinking_effort": "xhigh"},
    ],
)
def test_validation_never_echoes_key_or_url_credentials(client, overrides):
    response = client.post("/api/models", json={**BODY, **overrides})
    assert response.status_code == 422 and KEY not in response.text
    assert not client.get("/api/models").json()


def test_concurrent_model_edit_compare_and_swap(client):
    model = create(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda n: update(client, model, name=f"编辑 {n}").status_code, range(2))
        )
    assert sorted(results) == [200, 409]
    assert len(client.get(f"/api/models/{model['id']}/versions").json()) == 2


def prepared_task(client, model, target=None):
    case = client.post("/api/cases", json={"name": "模型任务用例"}).json()
    version = client.post(
        f"/api/cases/{case['id']}/versions",
        json={
            "title": "模型测试",
            "target": "app",
            "steps": ["打开应用"],
            "assertions": ["界面可见"],
            "required_tags": ["adb"] if target else ["simulator"],
        },
    ).json()
    assert (
        client.put(
            f"/api/cases/{case['id']}/stable",
            json={"version_id": version["id"], "expected_version_id": None},
        ).status_code
        == 200
    )
    task = client.post("/api/tasks", json={"name": "模型绑定", "request_id": str(uuid4())}).json()
    response = client.put(
        f"/api/tasks/{task['id']}",
        json={
            "name": task["name"],
            "expected_revision": task["revision"],
            "case_ids": [case["id"]],
            "configuration": {"model_config_id": model["id"], "target_device_id": target},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def trigger(client, task):
    return client.post(
        f"/api/tasks/{task['id']}/execute",
        json={"expected_revision": task["revision"], "request_id": str(uuid4())},
    )


def test_batch_freezes_model_and_secret_version_new_runs_use_current(client, database):
    model = create(client)
    task = prepared_task(client, model)
    first = trigger(client, task).json()
    second_model = update(client, model, name="更新配置", api_key="rotated-key").json()
    second = trigger(client, task).json()
    assert first["task_snapshot"]["model"]["revision"] == 1
    assert second["task_snapshot"]["model"]["revision"] == second_model["revision"] == 2
    assert (
        client.get(f"/api/executions/{first['id']}").json()["task_snapshot"]["model"]["name"]
        == model["name"]
    )
    with database.connect() as conn:
        row = db.one(
            conn,
            "SELECT c.ciphertext FROM runs r JOIN model_config_versions v ON (r.model_config_id,r.model_revision)=(v.config_id,v.revision) JOIN model_credentials c ON c.id=v.credential_id WHERE r.id=:id",
            id=first["id"],
        )
        assert secrets.decrypt(row["ciphertext"]) == KEY
        for table in ["test_tasks", "task_revisions", "runs", "commands", "audit"]:
            assert KEY not in str(db.rows(conn, f"SELECT * FROM {table}"))


def test_disabled_model_blocks_new_trigger_but_keeps_batch_snapshot(client, database):
    model = create(client)
    task = prepared_task(client, model)
    first = trigger(client, task).json()
    result = client.put(
        f"/api/models/{model['id']}/availability", json={"enabled": False, "expected_revision": 1}
    )
    assert result.status_code == 200 and result.json()["revision"] == 2
    assert trigger(client, task).status_code == 409
    assert (
        client.get(f"/api/executions/{first['id']}").json()["task_snapshot"]["model"]["revision"]
        == 1
    )
    with database.begin() as conn:
        batch = tasks.trigger(
            conn,
            UUID(task["id"]),
            uuid4(),
            task["revision"],
            "scheduler",
            scheduled_at=datetime.now(timezone.utc),
        )
        assert batch["state"] == "blocked" and "停用" in batch["reason"] and not batch["cases"]


def test_physical_execution_redeems_only_its_frozen_model_version(client, database, monkeypatch):

    node = client.post(
        "/api/nodes/adb-inventory",
        json={"node_id": "lab", "serial": "physical", "state": "device", "artemis_state": "ready"},
    )
    task = prepared_task(client, create(client), target=node.json()["id"])
    assert trigger(client, task).status_code == 201
    model = client.get("/api/models").json()[0]
    assert update(client, model, model_id="local/model-b", api_key="rotated-key").status_code == 200

    with database.begin() as conn:
        assert service.schedule_one(conn)
        prepare = db.one(conn, "SELECT payload FROM commands")["payload"]
        gen = db.one(conn, "SELECT connection_generation FROM nodes WHERE id='lab'")[
            "connection_generation"
        ]
        service.ingest_event(
            conn,
            "lab",
            gen,
            NodeEvent(
                seq=1,
                kind="ready",
                simulation=False,
                **{
                    field: prepare[field]
                    for field in ("command_id", "attempt_id", "lease_generation")
                },
            ),
        )
        cmd = db.one(conn, "SELECT payload FROM commands WHERE payload->>'kind'='execute'")[
            "payload"
        ]
        assert cmd["execution_model"] == {"config_id": model["id"], "revision": 1}
        assert KEY not in json.dumps(cmd)
        db.execute(conn, "UPDATE commands SET dispatch_ready=true")
    route = f"/api/nodes/lab/execution-model/{cmd['command_id']}"
    assert (
        client.post(
            route.replace("/lab/", "/other/"),
        ).status_code
        == 404
    )
    response = client.post(route)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["api_key"] == KEY
    assert response.json()["model_id"] == "local/model-a"
    with database.begin() as conn:
        service.cancel(conn, UUID(cmd["attempt_id"]))
    assert client.post(route).status_code == 404


@pytest.fixture
def endpoint():
    received, response = [], {"status": 200, "body": {"choices": [{"message": {"content": "OK"}}]}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            received.append(
                {
                    "path": self.path,
                    "headers": self.headers,
                    "body": json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                }
            )
            self.send_response(response["status"])
            self.send_header("Location", "/redirected")
            self.end_headers()
            self.wfile.write(json.dumps(response["body"]).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", received, response
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.parametrize("schema", ["openai", "anthropic"])
def test_real_http_protocol_and_sanitized_probe_history(client, endpoint, schema):
    url, received, response = endpoint
    if schema == "anthropic":
        response["body"] = {"content": [{"type": "text", "text": "OK"}]}
    model = create(client, api_schema=schema, url=url + "/v1", thinking_effort="low")
    checked = client.post(f"/api/models/{model['id']}/check", json={"expected_revision": 1})
    assert checked.status_code == 200 and checked.json()["status"] == "passed"
    assert checked.json()["location"] == "control_plane" and KEY not in checked.text
    request = received[0]
    assert request["body"]["model"] == BODY["model_id"]
    if schema == "openai":
        assert request["path"] == "/v1/chat/completions"
        assert request["headers"]["Authorization"] == "Bearer " + KEY
        assert request["body"]["reasoning_effort"] == "low"
    else:
        assert request["path"] == "/v1/messages" and request["headers"]["x-api-key"] == KEY
        assert request["body"]["output_config"] == {"effort": "low"}
        assert request["body"]["thinking"] == {"type": "adaptive"}
    assert client.get("/api/models").json()[0]["last_check"]["status"] == "passed"
    response.update(status=400, body={"error": KEY})
    failed = client.post(f"/api/models/{model['id']}/check", json={"expected_revision": 1})
    assert failed.json()["status"] == "failed" and KEY not in failed.text
    assert len(received) == 2  # Never retry by silently dropping the effort parameter.


def test_probe_does_not_follow_redirect_and_rejects_invalid_shape(client, endpoint):
    url, received, response = endpoint
    model = create(client, url=url + "/v1")
    response.update(status=302)
    result = client.post(f"/api/models/{model['id']}/check", json={"expected_revision": 1}).json()
    assert result["status"] == "failed" and len(received) == 1
    response.update(status=200, body={"choices": [{"message": {"content": None}}]})
    result = client.post(f"/api/models/{model['id']}/check", json={"expected_revision": 1}).json()
    assert result["status"] == "failed"


def test_missing_master_key_rolls_back_and_wrong_key_cannot_probe(client, monkeypatch, tmp_path):
    model = create(client)
    monkeypatch.delenv("PLATFORM_SECRET_KEY_FILE")
    result = client.post("/api/models", json=BODY)
    assert result.status_code == 503 and KEY not in result.text
    assert len(client.get("/api/models").json()) == 1
    key = tmp_path / "wrong.key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    monkeypatch.setenv("PLATFORM_SECRET_KEY_FILE", str(key))
    response = client.post(f"/api/models/{model['id']}/check", json={"expected_revision": 1})
    assert response.status_code == 503 and KEY not in response.text


def test_probe_result_stays_on_checked_revision_after_concurrent_edit(client, monkeypatch):
    model = create(client)

    def complete_old_probe(definition, key):
        assert update(client, model, name="检测期间编辑").status_code == 200
        return {"status": "passed", "detail": "控制面检测", "latency_ms": 1, "http_status": 200}

    monkeypatch.setattr(model_configs, "probe", complete_old_probe)
    result = client.post(f"/api/models/{model['id']}/check", json={"expected_revision": 1}).json()
    assert result["revision"] == 1
    latest = client.get("/api/models").json()[0]
    assert latest["revision"] == 2 and latest["last_check"] is None
