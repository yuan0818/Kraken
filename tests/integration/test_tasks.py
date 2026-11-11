from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from control_plane.entrypoints.api import app
from control_plane.infrastructure import db
from control_plane.modules import service, tasks
from fastapi.testclient import TestClient
from platform_contracts.models import Command, NodeEvent
from platform_contracts.tasks import SaveTask, TaskConfiguration

pytestmark = pytest.mark.integration
CONTENT = {
    "title": "设置",
    "target": "android_os",
    "steps": ["打开设置"],
    "assertions": ["显示设置"],
    "required_tags": ["simulator"],
}


@pytest.fixture
def client(database):
    return TestClient(app)


def case(client, code, stable=True, status="active"):
    c = client.post("/api/cases", json={"name": code, "code": code}).json()
    v = client.post(f"/api/cases/{c['id']}/versions", json={**CONTENT, "status": status}).json()
    if stable:
        assert (
            client.put(
                f"/api/cases/{c['id']}/stable",
                json={"version_id": v["id"], "expected_version_id": None},
            ).status_code
            == 200
        )
    return c, v


def create(client, name="每日回归", request_id=None):
    response = client.post(
        "/api/tasks", json={"name": name, "request_id": request_id or str(uuid4())}
    )
    assert response.status_code == 201, response.text
    return response.json()


def definition(task, ids, **configuration):
    return {
        "expected_revision": task["revision"],
        "name": task["name"],
        "description": task["description"],
        "case_ids": ids,
        "configuration": {**task["configuration"], **configuration},
    }


def save(client, task, ids, **configuration):
    response = client.put(f"/api/tasks/{task['id']}", json=definition(task, ids, **configuration))
    assert response.status_code == 200, response.text
    return response.json()


def run(client, task, request_id=None, status=201):
    response = client.post(
        f"/api/tasks/{task['id']}/execute",
        json={"request_id": request_id or str(uuid4()), "expected_revision": task["revision"]},
    )
    assert response.status_code == status, response.text
    return response.json()


def test_create_container_then_add_enabled_cases_and_preflight(client):
    request_id = str(uuid4())
    task = create(client, request_id=request_id)
    assert create(client, request_id=request_id)["id"] == task["id"]
    assert task["case_count"] == 0 and client.get("/api/executions").json() == []
    run(client, task, status=409)
    c, _ = case(client, "OS-001", stable=False)
    disabled, _ = case(client, "OS-DISABLED", status="disabled")
    task = save(client, task, [c["id"]])
    assert not task["cases"][0]["stable_ready"]
    assert "Stable" in run(client, task, status=409)["detail"]
    invalid = client.put(
        f"/api/tasks/{task['id']}", json=definition(task, [c["id"], disabled["id"]])
    )
    assert invalid.status_code == 409
    values = client.get("/api/cases", params={"status": "active", "page_size": 1}).json()
    assert values["total"] == 1 and values["items"][0]["id"] == c["id"]
    assert client.get("/api/tasks", params={"q": "每日"}).json()["total"] == 1


def test_multi_case_snapshot_order_idempotency_and_edit_conflict(client):
    c1, _ = case(client, "OS-A")
    c2, _ = case(client, "OS-B")
    task = save(client, create(client), [c2["id"], c1["id"]], max_retries=2, max_parallel=2)
    request_id = str(uuid4())
    batch = run(client, task, request_id)
    assert run(client, task, request_id)["id"] == batch["id"]
    assert [c["case_id"] for c in batch["cases"]] == [c2["id"], c1["id"]]
    assert all(c["attempts"][0]["snapshot"] is None for c in batch["cases"])
    old = task
    task = save(client, task, [c1["id"]], max_retries=0)
    assert client.put(f"/api/tasks/{task['id']}", json=definition(old, [])).status_code == 409
    run(client, old, status=409)
    record = client.get(f"/api/executions/{batch['id']}").json()
    assert record["total"] == 2 and record["task_snapshot"]["configuration"]["max_retries"] == 2
    assert len(client.get(f"/api/executions?task_id={task['id']}").json()) == 1


def test_concurrent_task_save_only_one_wins(client):
    task = create(client)
    payload = definition(task, [])

    def update(n):
        return (
            TestClient(app)
            .put(f"/api/tasks/{task['id']}", json={**payload, "description": str(n)})
            .status_code
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update, range(2))) == [200, 409]


def test_schedule_catchup_latest_only_disable_and_source_audit(client, database):
    c, _ = case(client, "OS-TIME")
    task = create(client)
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    schedule = {"enabled": True, "frequency": "daily", "time": "03:00", "timezone": "UTC"}
    with database.begin() as conn:
        tasks.save(
            conn,
            UUID(task["id"]),
            SaveTask.model_validate(definition(task, [c["id"]], schedule=schedule)),
            "anonymous",
            now=start,
        )
    assert client.get("/api/tasks?scheduled=true").json()["total"] == 1
    now = start + timedelta(days=4, hours=4)
    with database.begin() as conn:
        tasks.advance(conn, now)
        tasks.advance(conn, now)
    runs = client.get("/api/executions").json()
    assert len(runs) == 1 and runs[0]["trigger"] == "scheduled"
    assert datetime.fromisoformat(runs[0]["trigger_at"]) == start + timedelta(days=4, hours=3)
    task = client.get(f"/api/tasks/{task['id']}").json()
    assert datetime.fromisoformat(task["next_trigger_at"]) == start + timedelta(days=5, hours=3)
    task = save(client, task, [c["id"]], schedule={**schedule, "enabled": False})
    with database.begin() as conn:
        tasks.advance(conn, now + timedelta(days=3))
        assert (
            db.one(conn, "SELECT subject_id FROM audit WHERE action='task.trigger'")["subject_id"]
            == "scheduler"
        )
    assert len(client.get("/api/executions").json()) == 1
    assert task["next_trigger_at"] is None


def test_flash_configuration_persists_and_cannot_be_silently_skipped(client, database):
    c, _ = case(client, "OS-FLASH")
    task = create(client)
    assert (
        client.put(
            f"/api/tasks/{task['id']}", json=definition(task, [c["id"]], require_flash=True)
        ).status_code
        == 422
    )
    assert (
        client.put(
            f"/api/tasks/{task['id']}",
            json=definition(
                task, [c["id"]], require_flash=True, flash_source="ftp://user:secret@host/path"
            ),
        ).status_code
        == 422
    )
    task = save(
        client, task, [c["id"]], require_flash=True, flash_source="ftp://firmware.internal/releases"
    )
    assert "刷机" in run(client, task, status=409)["detail"]
    with database.begin() as conn:
        batch = tasks.trigger(
            conn,
            UUID(task["id"]),
            uuid4(),
            task["revision"],
            "scheduler",
            scheduled_at=datetime.now(timezone.utc),
        )
        assert batch["state"] == "blocked" and batch["total"] == 1 and not batch["cases"]
        assert db.one(conn, "SELECT count(*) AS n FROM attempts")["n"] == 0
    response = client.get(f"/api/reports/{batch['id']}")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["total"] == 1 and report["finished"] == 0
    assert report["summary"]["states"] == {"not_started": 1}
    assert report["summary"]["verdicts"]["not_evaluated"] == 1
    (item,) = report["cases"]
    assert item["case_id"] == c["id"] and item["code"] == "OS-FLASH"
    assert item["attempt_id"] is None and item["attempts"] == 0 and "刷机" in item["reason"]


def test_batch_report_tracks_case_outcomes_and_pages_history(client, database):
    c1, _ = case(client, "OS-REPORT-RETRY")
    c2, _ = case(client, "OS-REPORT-CANCEL")
    task = save(client, create(client), [c1["id"], c2["id"]], max_retries=1)
    batch = run(client, task)
    url = f"/api/reports/{batch['id']}"
    initial = batch["cases"][0]["attempts"][0]
    with database.begin() as conn:
        db.execute(
            conn,
            "UPDATE attempts SET state='completed',cleanup='verified',verdict='fail' WHERE id=:id",
            id=initial["id"],
        )
    waiting = client.get(url).json()
    assert waiting["summary"]["states"] == {"retry_wait": 1, "queued": 1}
    assert waiting["summary"]["verdicts"]["fail"] == 1
    assert waiting["summary"]["verdicts"]["not_evaluated"] == 1
    retry_id = uuid4()
    with database.begin() as conn:
        db.execute(
            conn,
            """INSERT INTO attempts(id,case_run_id,number,simulation,state,verdict,cleanup,evidence)
            VALUES (:id,:cr,2,false,'completed','pass','verified','complete')""",
            id=retry_id,
            cr=initial["case_run_id"],
        )
    assert client.post(f"/api/executions/{batch['id']}/cancel").status_code == 200
    report = client.get(url).json()
    assert (report["state"], report["finished"], report["total"]) == ("cancelled", 2, 2)
    assert report["summary"] == {
        "states": {"completed": 1, "cancelled": 1},
        "verdicts": {"not_evaluated": 1, "pass": 1, "fail": 0, "blocked": 0, "inconclusive": 0},
        "first_pass": 0,
        "retried": 1,
        "passed_after_retry": 1,
        "attempts": 3,
    }
    retry_case, cancelled_case = report["cases"]
    assert retry_case["case_id"] == c1["id"] and retry_case["attempt_id"] == str(retry_id)
    assert (retry_case["first_verdict"], retry_case["verdict"]) == ("fail", "pass")
    assert cancelled_case["case_id"] == c2["id"] and cancelled_case["state"] == "cancelled"
    second = run(client, task)
    run(client, save(client, create(client, "其他任务"), [c1["id"]]))
    for page, expected in [(1, [second["id"]]), (2, [batch["id"]]), (3, [])]:
        response = client.get(
            "/api/reports", params={"task_id": task["id"], "page": page, "page_size": 1}
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["total"] == 2 and [item["id"] for item in result["items"]] == expected


def test_batch_export_includes_every_case_and_archives_downloads(client, tmp_path, monkeypatch):
    monkeypatch.setenv("REPORT_EXPORT_DIR", str(tmp_path))
    cases = [case(client, f"OS-EXPORT-{index:02}")[0] for index in range(12)]
    name = "每周回归 <script>alert(1)</script>"
    task = save(client, create(client, name), [c["id"] for c in cases])
    batch = run(client, task)
    url = f"/api/reports/{batch['id']}"
    snapshot = client.get(url).json()
    html = client.post(f"{url}/export")
    result = client.post(f"{url}/export", params={"format": "json"})
    assert html.status_code == result.status_code == 200
    assert html.headers["content-type"].startswith("text/html")
    assert result.headers["content-type"] == "application/json"
    for response in (html, result):
        filename = response.headers["content-disposition"].split('"')[1]
        assert filename.startswith("RUN-")
        assert (tmp_path / batch["id"] / filename).read_bytes() == response.content
    assert "<h2>结果汇总</h2>" in html.text and "用例结果（12）" in html.text
    assert "批次尚未结束" in html.text and "最新结论" in html.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html.text
    assert "<script>" not in html.text
    for case_row in cases:
        assert case_row["code"] in html.text
    exported = result.json()
    assert exported["summary"] == snapshot["summary"] and exported["cases"] == snapshot["cases"]
    assert exported["task_name"] == name and not exported["conclusions_are_final"]
    assert "task_snapshot" not in exported
    assert client.post(f"{url}/export?format=csv").status_code == 422

    client.post(f"/api/executions/{batch['id']}/cancel").raise_for_status()
    final = client.post(f"{url}/export?format=json").json()
    assert final["conclusions_are_final"]
    assert final["summary"]["states"] == {"cancelled": 12}
    assert all(row["state"] == "cancelled" for row in final["cases"])


def node(database):
    with database.begin() as conn:
        db.execute(
            conn,
            "INSERT INTO nodes(id,token_hash,capabilities,last_seen,connection_generation) VALUES ('n','x','[\"simulator\"]',now(),1)",
        )
        for i in range(3):
            db.execute(
                conn,
                "INSERT INTO devices(id,node_id,serial,tags) VALUES (:id,'n',:s,'[\"simulator\"]')",
                id=uuid4(),
                s=f"SIM-{i}",
            )


def test_parallel_limit_same_task_batch_queue_and_stable_at_start(client, database):
    c1, v1 = case(client, "OS-ONE")
    c2, _ = case(client, "OS-TWO")
    task = save(client, create(client), [c1["id"], c2["id"]], max_parallel=1)
    first = run(client, task)
    second = run(client, task)
    node(database)
    with database.begin() as conn:
        assert service.schedule_one(conn)
        assert not service.schedule_one(conn)
        command = Command.model_validate(db.one(conn, "SELECT payload FROM commands")["payload"])
    version = client.post(
        f"/api/cases/{c1['id']}/versions", json={**CONTENT, "title": "执行前换 Stable"}
    ).json()
    client.put(
        f"/api/cases/{c1['id']}/stable",
        json={"version_id": version["id"], "expected_version_id": v1["id"]},
    )
    with database.begin() as conn:
        service.ingest_event(
            conn,
            "n",
            1,
            NodeEvent(
                command_id=command.command_id,
                attempt_id=command.attempt_id,
                lease_generation=command.lease_generation,
                seq=1,
                kind="ready",
            ),
        )
        assert (
            str(
                db.one(conn, "SELECT version_id FROM attempts WHERE id=:id", id=command.attempt_id)[
                    "version_id"
                ]
            )
            == version["id"]
        )
        second_case = db.one(
            conn,
            "SELECT id FROM case_runs WHERE run_id=:id ORDER BY position LIMIT 1",
            id=second["id"],
        )["id"]
        assert not tasks.can_dispatch(conn, second_case)
        # Completed first batch with released devices permits the next batch.
        db.execute(conn, "UPDATE leases SET released_at=now()")
        db.execute(
            conn,
            "UPDATE attempts SET state='completed',cleanup='verified',verdict='inconclusive' WHERE case_run_id IN (SELECT id FROM case_runs WHERE run_id=:id)",
            id=first["id"],
        )
        assert tasks.can_dispatch(conn, second_case)


def test_bounded_retry_interval_serial_order_and_cancel(client, database):
    c1, _ = case(client, "OS-RETRY")
    c2, _ = case(client, "OS-NEXT")
    task = save(
        client,
        create(client),
        [c1["id"], c2["id"]],
        execution_order="serial",
        max_retries=2,
        retry_interval_seconds=60,
    )
    batch = run(client, task)
    initial = batch["cases"][0]["attempts"][0]
    second_cr = batch["cases"][1]["attempts"][0]["case_run_id"]
    with database.begin() as conn:
        db.execute(
            conn,
            "UPDATE attempts SET state='completed',cleanup='verified',verdict='fail' WHERE id=:id",
            id=initial["id"],
        )
        end = db.one(conn, "SELECT finished_at FROM attempts WHERE id=:id", id=initial["id"])[
            "finished_at"
        ]
        tasks.advance(conn, end + timedelta(seconds=59))
        assert len(tasks.latest_attempts(conn, UUID(batch["id"]))) == 2
        assert not tasks.can_dispatch(conn, UUID(second_cr))
        tasks.advance(conn, end + timedelta(seconds=61))
        attempts = db.rows(
            conn,
            "SELECT * FROM attempts WHERE case_run_id=:id ORDER BY number",
            id=initial["case_run_id"],
        )
        assert len(attempts) == 2 and attempts[1]["snapshot"] is None
        db.execute(
            conn,
            "UPDATE attempts SET state='completed',cleanup='verified',verdict='fail' WHERE id=:id",
            id=attempts[1]["id"],
        )
        tasks.advance(conn, datetime.now(timezone.utc) + timedelta(seconds=61))
        latest = tasks.latest_attempts(conn, UUID(batch["id"]))[0]
        assert latest["number"] == 3
        db.execute(
            conn,
            "UPDATE attempts SET state='completed',cleanup='verified',verdict='fail' WHERE id=:id",
            id=latest["id"],
        )
        tasks.advance(conn, datetime.now(timezone.utc) + timedelta(days=1))
        assert tasks.latest_attempts(conn, UUID(batch["id"]))[0]["number"] == 3
        assert tasks.can_dispatch(conn, UUID(second_cr))
    response = client.post(f"/api/executions/{batch['id']}/cancel")
    assert response.status_code == 200 and response.json()["state"] == "cancelled"
    assert client.post(f"/api/attempts/{initial['id']}/retry").status_code == 409


@pytest.mark.parametrize(
    "state,cleanup,verdict",
    [
        ("needs_recovery", "verified", "fail"),
        ("cancelled", "verified", "inconclusive"),
        ("completed", "failed", "fail"),
    ],
)
def test_unknown_cancelled_or_unclean_does_not_retry(client, database, state, cleanup, verdict):
    c, _ = case(client, "OS-SAFE")
    task = save(client, create(client), [c["id"]], max_retries=2, retry_interval_seconds=0)
    batch = run(client, task)
    with database.begin() as conn:
        db.execute(
            conn,
            "UPDATE attempts SET state=:s,cleanup=:c,verdict=:v",
            s=state,
            c=cleanup,
            v=verdict,
        )
        tasks.advance(conn, datetime.now(timezone.utc) + timedelta(days=1))
        assert db.one(conn, "SELECT count(*) AS n FROM attempts")["n"] == 1
    assert client.get(f"/api/executions/{batch['id']}").status_code == 200


def test_timer_timezone_weekdays_and_dst_gap():
    config = TaskConfiguration.model_validate(
        {
            "schedule": {
                "enabled": True,
                "frequency": "weekly",
                "weekdays": [0],
                "time": "09:00",
                "timezone": "Asia/Taipei",
            }
        }
    ).model_dump(mode="json")
    assert tasks.next_trigger(config, datetime(2026, 10, 22, tzinfo=timezone.utc)) == datetime(
        2026, 10, 26, 1, tzinfo=timezone.utc
    )
    config["schedule"].update(frequency="daily", timezone="America/New_York", time="02:30")
    # 2024-03-10 02:30 did not exist in this zone.
    assert tasks.next_trigger(config, datetime(2024, 3, 10, 6, tzinfo=timezone.utc)) == datetime(
        2024, 3, 11, 6, 30, tzinfo=timezone.utc
    )


def test_parallel_batch_really_allocates_multiple_devices_and_terminal_cancel_is_noop(
    client, database
):
    cases = [case(client, f"OS-PAR-{i}")[0] for i in range(3)]
    task = save(client, create(client), [c["id"] for c in cases], max_parallel=2)
    batch = run(client, task)
    node(database)
    with database.begin() as conn:
        assert service.schedule_one(conn)
        assert service.schedule_one(conn)
        assert not service.schedule_one(conn)
        assert (
            db.one(
                conn, "SELECT count(DISTINCT device_id) AS n FROM leases WHERE released_at IS NULL"
            )["n"]
            == 2
        )
        db.execute(conn, "UPDATE leases SET released_at=now()")
        db.execute(
            conn, "UPDATE attempts SET state='completed',cleanup='verified',verdict='inconclusive'"
        )
    response = client.post(f"/api/executions/{batch['id']}/cancel")
    assert response.status_code == 200
    assert response.json()["state"] == "completed" and not response.json()["cancel_requested"]


def test_cancel_during_retry_wait_prevents_further_attempts(client, database):
    c, _ = case(client, "OS-CANCEL-RETRY")
    task = save(client, create(client), [c["id"]], max_retries=2, retry_interval_seconds=60)
    batch = run(client, task)
    with database.begin() as conn:
        db.execute(conn, "UPDATE attempts SET state='completed',cleanup='verified',verdict='fail'")
    assert client.get(f"/api/executions/{batch['id']}").json()["state"] == "retry_wait"
    assert client.post(f"/api/executions/{batch['id']}/cancel").json()["state"] == "cancelled"
    with database.begin() as conn:
        tasks.advance(conn, datetime.now(timezone.utc) + timedelta(days=1))
        assert db.one(conn, "SELECT count(*) AS n FROM attempts")["n"] == 1
