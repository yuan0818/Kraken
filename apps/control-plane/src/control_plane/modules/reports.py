"""Read-only batch reports built from persisted execution facts."""

from collections import Counter
from uuid import UUID

from control_plane.infrastructure import db
from control_plane.modules import tasks
from platform_contracts.models import Verdict


def page(conn, task_id, page_number, page_size):
    total = db.one(
        conn,
        "SELECT count(*) AS total FROM runs WHERE (CAST(:task AS uuid) IS NULL OR task_id=CAST(:task AS uuid))",
        task=str(task_id) if task_id else None,
    )["total"]
    return {
        "total": total,
        "items": tasks.history(
            conn, task_id, limit=page_size, offset=(page_number - 1) * page_size
        ),
    }


def detail(conn, run_id):
    batch = tasks.batch(conn, run_id)
    cases = batch.pop("cases")
    # A preflight-blocked scheduled batch has a frozen case list without case runs.
    if not cases and batch["reason"]:
        planned = [UUID(cid) for cid in (batch["task_snapshot"] or {}).get("case_ids", [])]
        records = (
            {
                row["case_id"]: row
                for row in db.rows(
                    conn,
                    "SELECT id AS case_id,code,name AS case_name FROM cases WHERE id=ANY(:ids)",
                    ids=planned,
                )
            }
            if planned
            else {}
        )
        cases = [
            {**records.get(cid, {"case_id": cid, "code": "", "case_name": "用例"}), "attempts": []}
            for cid in planned
        ]
    items = []
    for case in cases:
        attempts = case["attempts"]
        latest = attempts[0] if attempts else {}
        first = attempts[-1] if attempts else {}
        state = latest.get("state", "not_started")
        if latest and tasks.retryable(batch, latest):
            state = "retry_wait"
        items.append(
            {
                **{key: case[key] for key in ("case_id", "code", "case_name")},
                "state": state,
                "first_verdict": first.get("verdict", "not_evaluated"),
                "verdict": latest.get("verdict", "not_evaluated"),
                "attempts": len(attempts),
                "attempt_id": latest.get("id"),
                "version_id": latest.get("version_id"),
                "reason": latest.get("reason") or (batch["reason"] if not attempts else ""),
            }
        )
    verdicts = Counter(item["verdict"] for item in items)
    summary = {
        "verdicts": {verdict: verdicts[verdict] for verdict in Verdict},
        "states": dict(Counter(item["state"] for item in items)),
        "first_pass": sum(item["first_verdict"] == "pass" for item in items),
        "retried": sum(item["attempts"] > 1 for item in items),
        "passed_after_retry": sum(
            item["attempts"] > 1 and item["first_verdict"] != "pass" and item["verdict"] == "pass"
            for item in items
        ),
        "attempts": sum(item["attempts"] for item in items),
    }
    return {**batch, "summary": summary, "cases": items}
