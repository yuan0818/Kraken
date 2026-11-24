import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from control_plane.entrypoints.api import app
from control_plane.infrastructure import db
from control_plane.modules import dashboard
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration
NOW = datetime(2026, 11, 20, 4, tzinfo=timezone.utc)


def seed(conn, verdicts=("blocked",), physical=True, at=NOW, reason="", task=None, recovery=False):
    rid, cid, crid = uuid4(), uuid4(), uuid4()
    snapshot = {
        "name": "测试任务",
        "configuration": {"target_device_id": str(uuid4()) if physical else None},
        "case_ids": [str(cid)],
    }
    db.execute(
        conn,
        "INSERT INTO runs(id,request_id,request_fingerprint,task_snapshot,created_at,blocked_reason,task_id) VALUES (:id,:request,'fixture',CAST(:snapshot AS jsonb),:at,:reason,:task)",
        id=rid,
        request=uuid4(),
        snapshot=json.dumps(snapshot),
        at=at,
        reason=reason,
        task=task,
    )
    if verdicts:
        db.execute(
            conn, "INSERT INTO cases(id,name,code) VALUES (:id,'用例',:code)", id=cid, code=str(cid)
        )
        db.execute(
            conn,
            "INSERT INTO case_runs(id,run_id,case_id) VALUES (:id,:run,:case)",
            id=crid,
            run=rid,
            case=cid,
        )
        for number, verdict in enumerate(verdicts, 1):
            db.execute(
                conn,
                """INSERT INTO attempts(id,case_run_id,number,state,verdict,cleanup,evidence,simulation,reason)
              VALUES (:id,:cr,:number,:state,:verdict,:cleanup,'complete',:sim,'设备不可用')""",
                id=uuid4(),
                cr=crid,
                number=number,
                state="needs_recovery" if recovery else "completed",
                verdict=verdict,
                cleanup="failed" if recovery else "verified",
                sim=not physical,
            )
    return rid


def test_dashboard_full_history_latest_verdict_and_filters(database):
    with database.begin() as conn:
        task = uuid4()
        db.execute(
            conn,
            "INSERT INTO test_tasks(id,request_id,name,configuration,created_by) VALUES (:id,:request,'范围测试','{}','anonymous')",
            id=task,
            request=uuid4(),
        )
        # More than the existing history endpoint's 100-row cap.
        for _ in range(105):
            seed(conn, ("pass",))
        run = seed(conn, ("fail", "pass"), task=task)
        seed(conn, ("blocked",), recovery=True)
        seed(conn, ("fail",), physical=False)
        seed(conn, ("fail",), at=NOW - timedelta(days=8))
        seed(conn, (), reason="执行模型不可用")
        # Event collection on an earlier retry must not disappear on latest success.
        db.execute(
            conn,
            """UPDATE attempts SET diagnostics=:diagnostics WHERE case_run_id IN
          (SELECT id FROM case_runs WHERE run_id=:run) AND number=1""",
            run=run,
            diagnostics=json.dumps(
                {
                    "status": "complete",
                    "truncated": True,
                    "incidents": [{"kind": "anr"}, {"kind": "crash"}],
                }
            ),
        )
        result = dashboard.read(conn, now=NOW)
        assert result["batches"] == 108 and result["cases"] == 108
        assert result["verdicts"] == {
            "pass": 106,
            "blocked": 1,
            "fail": 0,
            "inconclusive": 0,
            "not_evaluated": 1,
        }
        assert result["evaluated"] == 107 and result["pass_rate"] == 99.1
        assert result["first_pass"] == 105 and result["retried"] == 1
        assert result["attempts"] == 108
        assert (
            result["anr"]
            == result["crash"]
            == result["diagnostics_complete"]
            == result["diagnostics_truncated"]
            == 1
        )
        assert result["batch_states"]["needs_recovery"] == 1
        assert result["batch_states"]["blocked"] == 1
        assert len(result["recent"]) == 8 and len(result["trend"]) == 7
        assert sum(d["total"] for d in result["trend"]) == result["cases"]
        assert any(i["reason"] == "执行模型不可用" for i in result["issues"])
        assert dashboard.read(conn, mode="simulation", now=NOW)["batches"] == 1
        assert dashboard.read(conn, mode="all", now=NOW)["batches"] == 109
        selected = dashboard.read(conn, task_id=task, now=NOW)
        assert selected["batches"] == 1 and selected["pass_rate"] == 100
        assert selected["retried"] == 1 and selected["first_pass"] == 0


def test_dashboard_local_day_boundary_and_empty(database):
    with database.begin() as conn:
        empty = dashboard.read(conn, now=NOW)
        assert empty["pass_rate"] is None and empty["cases"] == 0
        # November 14 00:00 Taipei is November 13 16:00 UTC.
        boundary = datetime(2026, 11, 13, 16, tzinfo=timezone.utc)
        seed(conn, at=boundary)
        seed(conn, at=boundary - timedelta(microseconds=1))
        seed(conn, at=NOW + timedelta(seconds=1))
        result = dashboard.read(conn, now=NOW)
        assert result["batches"] == 1 and result["trend"][0]["total"] == 1
    client = TestClient(app)
    for query in ("days=0", "days=91", "mode=invalid", "task_id=invalid"):
        assert client.get(f"/api/dashboard?{query}").status_code == 422
    response = client.get("/api/dashboard?mode=all")
    assert response.status_code == 200, response.text
    assert len(response.json()["trend"]) == 7
