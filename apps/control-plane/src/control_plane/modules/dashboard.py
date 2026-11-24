"""Aggregate persisted facts, without history pagination or report blobs."""

from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from control_plane.infrastructure import db
from control_plane.modules import tasks
from platform_contracts.models import Verdict

ZONE = ZoneInfo("Asia/Taipei")


def read(conn, days=7, mode="physical", task_id=None, *, now=None):
    now = now or datetime.now(timezone.utc)
    start = datetime.combine(now.astimezone(ZONE).date() - timedelta(days=days - 1), time(), ZONE)
    # Runs without attempts (preflight failures) still count. For legacy runs use
    # persisted attempt mode; task runs use their immutable execution configuration.
    runs = db.rows(
        conn,
        """SELECT r.id,r.created_at,r.blocked_reason,r.cancel_requested,r.task_snapshot
        FROM runs r WHERE r.created_at>=:start AND r.created_at<=:now
        AND (CAST(:task AS uuid) IS NULL OR r.task_id=CAST(:task AS uuid))
        AND (:mode='all' OR (:mode='physical') =
          CASE WHEN r.task_snapshot IS NOT NULL THEN
            NULLIF(r.task_snapshot->'configuration'->>'target_device_id','') IS NOT NULL
          ELSE EXISTS (SELECT 1 FROM case_runs cr JOIN attempts a ON a.case_run_id=cr.id
                       WHERE cr.run_id=r.id AND NOT a.simulation) END)
        ORDER BY r.created_at DESC,r.id DESC""",
        start=start,
        now=now,
        task=str(task_id) if task_id else None,
        mode=mode,
    )
    facts = (
        db.rows(
            conn,
            """SELECT cr.run_id,cr.case_id,c.code,c.name,a.id,a.state,a.verdict,a.cleanup,
          a.reason,a.finished_at,a.number,first.verdict AS first_verdict,
          stats.attempts,stats.anr,stats.crash,stats.complete,stats.diagnostic_total,stats.truncated
        FROM case_runs cr JOIN cases c ON c.id=cr.case_id
        JOIN LATERAL (SELECT id,state,verdict,cleanup,reason,finished_at,number
          FROM attempts WHERE case_run_id=cr.id ORDER BY number DESC LIMIT 1) a ON true
        JOIN LATERAL (SELECT verdict FROM attempts WHERE case_run_id=cr.id
          ORDER BY number LIMIT 1) first ON true
        JOIN LATERAL (SELECT count(*) AS attempts,
          count(*) FILTER (WHERE diagnostics->>'status'='complete') AS complete,
          count(*) FILTER (WHERE NOT simulation) AS diagnostic_total,
          count(*) FILTER (WHERE diagnostics->>'truncated'='true') AS truncated,
          COALESCE(sum((SELECT count(*) FROM jsonb_array_elements(
            COALESCE(diagnostics->'incidents','[]'::jsonb)) e WHERE e->>'kind'='anr')),0) AS anr,
          COALESCE(sum((SELECT count(*) FROM jsonb_array_elements(
            COALESCE(diagnostics->'incidents','[]'::jsonb)) e WHERE e->>'kind'='crash')),0) AS crash
          FROM attempts WHERE case_run_id=cr.id) stats ON true
        WHERE cr.run_id=ANY(:ids) ORDER BY cr.position,cr.id""",
            ids=[r["id"] for r in runs],
        )
        if runs
        else []
    )
    by_run = defaultdict(list)
    for fact in facts:
        by_run[fact["run_id"]].append(fact)
    verdicts = Counter({v.value: 0 for v in Verdict})
    states = Counter()
    trend = {
        (start.date() + timedelta(days=i)).isoformat(): {
            "date": start.date() + timedelta(days=i),
            "total": 0,
            "passed": 0,
            "evaluated": 0,
        }
        for i in range(days)
    }
    issues, recent = [], []
    for run in runs:
        items = by_run[run["id"]]
        state = tasks.run_state(run, items)
        states[state] += 1
        total = len(items) or len((run["task_snapshot"] or {}).get("case_ids", []))
        passed = sum(a["verdict"] == "pass" for a in items)
        bucket = trend[run["created_at"].astimezone(ZONE).date().isoformat()]
        bucket["total"] += total
        bucket["passed"] += passed
        bucket["evaluated"] += sum(a["verdict"] != "not_evaluated" for a in items)
        verdicts.update(a["verdict"] for a in items)
        verdicts["not_evaluated"] += total - len(items)
        name = (run["task_snapshot"] or {}).get("name") or f"RUN-{str(run['id'])[:8].upper()}"
        if len(recent) < 8:
            recent.append(
                dict(
                    id=run["id"],
                    name=name,
                    created_at=run["created_at"],
                    state=state,
                    total=total,
                    passed=passed,
                )
            )
        if run["blocked_reason"] and len(issues) < 10:
            issues.append(
                dict(
                    run_id=run["id"],
                    name=name,
                    state=state,
                    verdict="not_evaluated",
                    reason=run["blocked_reason"],
                )
            )
        for item in items:
            if len(issues) >= 10:
                break
            if (
                item["verdict"] in {"fail", "blocked", "inconclusive"}
                or item["state"] == "needs_recovery"
            ):
                issues.append(
                    dict(
                        run_id=run["id"],
                        case_id=item["case_id"],
                        attempt_id=item["id"],
                        name=f"{item['code']} · {item['name']}",
                        state="retry_wait" if tasks.retryable(run, item) else item["state"],
                        verdict=item["verdict"],
                        reason=item["reason"] or "尚未上报具体原因，请查看执行报告。",
                    )
                )
    evaluated = sum(verdicts.values()) - verdicts["not_evaluated"]
    return dict(
        generated_at=now,
        start_date=start.date(),
        timezone=str(ZONE),
        batches=len(runs),
        batch_states=dict(states),
        cases=sum(verdicts.values()),
        verdicts=dict(verdicts),
        evaluated=evaluated,
        pass_rate=round(verdicts["pass"] / evaluated * 100, 1) if evaluated else None,
        first_pass=sum(a["first_verdict"] == "pass" for a in facts),
        retried=sum(a["attempts"] > 1 for a in facts),
        attempts=sum(a["attempts"] for a in facts),
        anr=sum(a["anr"] for a in facts),
        crash=sum(a["crash"] for a in facts),
        diagnostics_complete=sum(a["complete"] for a in facts),
        diagnostics_total=sum(a["diagnostic_total"] for a in facts),
        diagnostics_truncated=sum(a["truncated"] for a in facts),
        trend=list(trend.values()),
        issues=issues,
        recent=recent,
    )
