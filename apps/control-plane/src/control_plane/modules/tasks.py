"""Reusable test tasks, immutable execution batches, timers and bounded retries."""

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo

from control_plane.infrastructure.db import execute, one, rows
from control_plane.modules import model_configs, service
from platform_contracts.tasks import TaskConfiguration


def occurrences(schedule, around):
    zone = ZoneInfo(schedule["timezone"])
    local = around.astimezone(zone)
    hour, minute = map(int, schedule["time"].split(":"))
    for offset in range(-8, 9):
        day = local.date() + timedelta(days=offset)
        if schedule["frequency"] == "weekly" and day.weekday() not in schedule["weekdays"]:
            continue
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
        utc = candidate.astimezone(timezone.utc)
        # Skip nonexistent local times, and execute a repeated clock time only once (fold 0).
        if utc.astimezone(zone).replace(tzinfo=None) == candidate.replace(tzinfo=None):
            yield utc


def next_trigger(config, now):
    if not config["schedule"]["enabled"]:
        return None
    return min(t for t in occurrences(config["schedule"], now) if t > now)


def record_revision(conn, task, subject):
    definition = {key: task[key] for key in ("name", "description", "configuration")}
    definition["case_ids"] = [
        str(c["case_id"])
        for c in rows(
            conn,
            "SELECT case_id FROM task_cases WHERE task_id=:id ORDER BY position",
            id=task["id"],
        )
    ]
    execute(
        conn,
        "INSERT INTO task_revisions(task_id,revision,definition,created_by) VALUES (:id,:rev,CAST(:d AS jsonb),:s)",
        id=task["id"],
        rev=task["revision"],
        d=service.encode(definition),
        s=subject,
    )


def create(conn, body, subject):
    execute(
        conn,
        "SELECT pg_advisory_xact_lock(hashtextextended(:key,0))",
        key=f"task:{body.request_id}",
    )
    old = one(conn, "SELECT * FROM test_tasks WHERE request_id=:id", id=body.request_id)
    if old:
        original = one(
            conn,
            "SELECT definition FROM task_revisions WHERE task_id=:id AND revision=1",
            id=old["id"],
        )["definition"]
        if original["name"] != body.name or original["description"] != body.description:
            raise service.Conflict("request_id_conflict", "重复创建请求的内容不同")
        return detail(conn, old["id"])
    task = one(
        conn,
        """INSERT INTO test_tasks(id,request_id,name,description,configuration,created_by)
        VALUES (:id,:r,:n,:d,CAST(:c AS jsonb),:s) RETURNING *""",
        id=uuid4(),
        r=body.request_id,
        n=body.name,
        d=body.description,
        c=TaskConfiguration().model_dump_json(),
        s=subject,
    )
    record_revision(conn, task, subject)
    service.audit(conn, subject, "task.create", task["id"])
    return detail(conn, task["id"])


def list_tasks(conn, page, page_size, q, scheduled=False):
    query = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    return {
        "items": rows(
            conn,
            """SELECT t.id,t.name,t.description,t.revision,t.configuration,t.next_trigger_at,t.created_at,t.updated_at,
        (SELECT count(*) FROM task_cases c WHERE c.task_id=t.id) AS case_count FROM test_tasks t
        WHERE concat_ws(' ',t.name,t.description) ILIKE :q AND (NOT :scheduled OR t.configuration->'schedule'->>'enabled'='true') ORDER BY t.updated_at DESC,t.id DESC LIMIT :limit OFFSET :offset""",
            q=query,
            scheduled=scheduled,
            limit=page_size,
            offset=(page - 1) * page_size,
        ),
        "total": one(
            conn,
            "SELECT count(*) AS n FROM test_tasks WHERE concat_ws(' ',name,description) ILIKE :q AND (NOT :scheduled OR configuration->'schedule'->>'enabled'='true')",
            q=query,
            scheduled=scheduled,
        )["n"],
    }


def detail(conn, task_id):
    task = one(
        conn,
        "SELECT id,name,description,revision,configuration,next_trigger_at,created_at,updated_at FROM test_tasks WHERE id=:id",
        id=task_id,
    )
    if not task:
        raise service.Conflict("not_found", "测试任务不存在")
    cases = rows(
        conn,
        """SELECT c.id,c.code,c.name,tc.position,c.stable_version_id,v.content AS latest_content,
        (sv.id IS NOT NULL AND coalesce(sv.content->>'status','active')='active') AS stable_ready
        FROM task_cases tc JOIN cases c ON c.id=tc.case_id
        LEFT JOIN case_versions sv ON sv.id=c.stable_version_id
        LEFT JOIN LATERAL (SELECT content FROM case_versions WHERE case_id=c.id ORDER BY created_at DESC,id DESC LIMIT 1) v ON true
        WHERE tc.task_id=:id ORDER BY tc.position""",
        id=task_id,
    )
    return {**task, "cases": cases, "case_count": len(cases)}


def save(conn, task_id, body, subject, now=None):
    now = now or datetime.now(timezone.utc)
    old = one(conn, "SELECT * FROM test_tasks WHERE id=:id FOR UPDATE", id=task_id)
    if not old or old["revision"] != body.expected_revision:
        raise service.Conflict("task_changed", "任务已被修改，请刷新核对；当前输入未保存")
    previous = {
        c["case_id"]
        for c in rows(conn, "SELECT case_id FROM task_cases WHERE task_id=:id", id=task_id)
    }
    for cid in body.case_ids:
        case = one(
            conn,
            """SELECT c.id,v.content FROM cases c LEFT JOIN LATERAL
            (SELECT content FROM case_versions WHERE case_id=c.id ORDER BY created_at DESC,id DESC LIMIT 1) v ON true
            WHERE c.id=:id FOR SHARE OF c""",
            id=cid,
        )
        if not case or (
            cid not in previous
            and (not case["content"] or case["content"].get("status", "active") != "active")
        ):
            raise service.Conflict("case_not_active", "只能添加已启用的用例；请刷新用例选择列表")
    config = body.configuration.model_dump(mode="json")
    if config.get("target_device_id"):
        if not one(
            conn,
            "SELECT id FROM devices WHERE id=:id AND artemis_state IS NOT NULL",
            id=config["target_device_id"],
        ):
            raise service.Conflict("invalid_device", "请选择接入 Artemis 执行器的真机")
    if config.get("model_config_id") and config.get("model_config_id") != old["configuration"].get(
        "model_config_id"
    ):
        model_configs.snapshot(conn, config["model_config_id"])
    next_at = (
        old["next_trigger_at"]
        if old["configuration"]["schedule"] == config["schedule"]
        else next_trigger(config, now)
    )
    task = one(
        conn,
        """UPDATE test_tasks SET name=:n,description=:d,configuration=CAST(:c AS jsonb),
        revision=revision+1,next_trigger_at=:next,updated_at=clock_timestamp() WHERE id=:id RETURNING *""",
        id=task_id,
        n=body.name,
        d=body.description,
        c=service.encode(config),
        next=next_at,
    )
    execute(conn, "DELETE FROM task_cases WHERE task_id=:id", id=task_id)
    for position, cid in enumerate(body.case_ids):
        execute(
            conn,
            "INSERT INTO task_cases(task_id,case_id,position) VALUES (:id,:cid,:p)",
            id=task_id,
            cid=cid,
            p=position,
        )
    record_revision(conn, task, subject)
    service.audit(conn, subject, "task.save", task_id)
    return detail(conn, task_id)


def preflight(task):
    errors = []
    if not task["cases"]:
        errors.append("任务尚未添加用例")
    invalid = [c["code"] for c in task["cases"] if not c["stable_ready"]]
    if invalid:
        errors.append("以下用例尚无已启用的 Stable：" + "、".join(invalid))
    if task["configuration"]["require_flash"]:
        errors.append(
            "任务要求 Fastboot 全量刷机，当前节点尚未接入刷机适配器；不会跳过刷机直接测试"
        )
    return "；".join(errors)


def trigger(conn, task_id, request_id, expected_revision, subject, *, scheduled_at=None):
    execute(conn, "SELECT pg_advisory_xact_lock(hashtextextended(:key,0))", key=str(request_id))
    fp = hashlib.sha256(f"task:{task_id}:{expected_revision}:{scheduled_at}".encode()).hexdigest()
    old = one(conn, "SELECT id,request_fingerprint FROM runs WHERE request_id=:id", id=request_id)
    if old:
        if old["request_fingerprint"] != fp:
            raise service.Conflict("request_id_conflict", "重复执行请求的任务或配置版本不同")
        return batch(conn, old["id"])
    one(conn, "SELECT id FROM test_tasks WHERE id=:id FOR UPDATE", id=task_id)
    task = detail(conn, task_id)
    if task["revision"] != expected_revision:
        raise service.Conflict("task_changed", "任务配置已更新，请刷新后执行")
    reason = preflight(task)
    selected_model = None
    if task["configuration"].get("model_config_id"):
        try:
            selected_model = model_configs.snapshot(conn, task["configuration"]["model_config_id"])
        except service.Conflict as exc:
            reason = "；".join(filter(None, [reason, exc.detail]))
    if reason and scheduled_at is None:
        raise service.Conflict("task_not_ready", reason)
    snapshot = one(
        conn,
        "SELECT definition FROM task_revisions WHERE task_id=:id AND revision=:rev",
        id=task_id,
        rev=task["revision"],
    )["definition"]
    snapshot = {**snapshot, "model": selected_model}
    run_id = uuid4()
    execute(
        conn,
        """INSERT INTO runs(id,request_id,request_fingerprint,task_id,task_revision,task_snapshot,trigger,trigger_at,blocked_reason,model_config_id,model_revision)
        VALUES (:id,:request,:fp,:task,:rev,CAST(:snapshot AS jsonb),:trigger,:at,:reason,:model,:model_rev)""",
        id=run_id,
        request=request_id,
        fp=fp,
        task=task_id,
        rev=task["revision"],
        snapshot=service.encode(snapshot),
        trigger="scheduled" if scheduled_at else "manual",
        at=scheduled_at,
        reason=reason,
        model=selected_model["id"] if selected_model else None,
        model_rev=selected_model["revision"] if selected_model else None,
    )
    if not reason:
        for c in task["cases"]:
            cr = uuid4()
            execute(
                conn,
                "INSERT INTO case_runs(id,run_id,case_id,position) VALUES (:id,:r,:c,:p)",
                id=cr,
                r=run_id,
                c=c["id"],
                p=c["position"],
            )
            execute(
                conn,
                "INSERT INTO attempts(id,case_run_id,number,simulation) VALUES (:id,:cr,1,:sim)",
                id=uuid4(),
                cr=cr,
                sim=not task["configuration"].get("target_device_id"),
            )
    service.audit(conn, subject, "task.trigger", run_id)
    return batch(conn, run_id)


def latest_attempts(conn, run_id):
    return rows(
        conn,
        """SELECT a.* FROM case_runs cr JOIN LATERAL
        (SELECT * FROM attempts WHERE case_run_id=cr.id ORDER BY number DESC LIMIT 1) a ON true
        WHERE cr.run_id=:id ORDER BY cr.position""",
        id=run_id,
    )


def retryable(run, attempt):
    config = (run["task_snapshot"] or {}).get("configuration", {})
    return bool(
        not run["cancel_requested"]
        and attempt["state"] in {"completed", "blocked"}
        and attempt["verdict"] in {"fail", "blocked", "inconclusive"}
        and attempt["cleanup"] == "verified"
        and attempt["finished_at"]
        and attempt["number"] <= config.get("max_retries", 0)
    )


def run_state(run, attempts):
    if run["blocked_reason"]:
        return "blocked"
    if any(a["state"] == "needs_recovery" or a["cleanup"] == "failed" for a in attempts):
        return "needs_recovery"
    if any(a["state"] not in service.TERMINAL for a in attempts):
        if run["cancel_requested"]:
            return "cancel_pending"
        return "queued" if all(a["state"] == "queued" for a in attempts) else "running"
    if any(retryable(run, a) for a in attempts):
        return "retry_wait"
    if run["cancel_requested"]:
        return "cancelled"
    return "blocked" if any(a["state"] == "blocked" for a in attempts) else "completed"


def batch(conn, run_id, include_cases=True):
    run = one(conn, "SELECT * FROM runs WHERE id=:id", id=run_id)
    if not run:
        raise service.Conflict("not_found", "执行批次不存在")
    latest = latest_attempts(conn, run_id)
    result = {
        k: run[k]
        for k in (
            "id",
            "task_id",
            "task_revision",
            "task_snapshot",
            "trigger",
            "trigger_at",
            "created_at",
            "cancel_requested",
        )
    }
    result.update(
        state=run_state(run, latest),
        reason=run["blocked_reason"],
        total=len(latest) or len((run["task_snapshot"] or {}).get("case_ids", [])),
        finished=sum(a["state"] in service.TERMINAL and not retryable(run, a) for a in latest),
    )
    if include_cases:
        attempts_by_case = {}
        for attempt in rows(
            conn,
            """SELECT a.* FROM attempts a JOIN case_runs cr ON cr.id=a.case_run_id
            WHERE cr.run_id=:id ORDER BY a.number DESC""",
            id=run_id,
        ):
            attempts_by_case.setdefault(attempt["case_run_id"], []).append(attempt)
        result["cases"] = []
        for c in rows(
            conn,
            "SELECT cr.id,cr.case_id,cr.position,c.name AS case_name,c.code FROM case_runs cr JOIN cases c ON c.id=cr.case_id WHERE cr.run_id=:id ORDER BY cr.position",
            id=run_id,
        ):
            result["cases"].append(
                {k: c[k] for k in ("case_id", "case_name", "code", "position")}
                | {"attempts": attempts_by_case.get(c["id"], [])}
            )
    return result


def history(conn, task_id=None, *, limit=100, offset=0):
    ids = rows(
        conn,
        """SELECT id FROM runs WHERE (CAST(:task AS uuid) IS NULL OR task_id=CAST(:task AS uuid))
        ORDER BY created_at DESC,id DESC LIMIT :limit OFFSET :offset""",
        task=str(task_id) if task_id else None,
        limit=limit,
        offset=offset,
    )
    return [batch(conn, r["id"], False) for r in ids]


def cancel_batch(conn, run_id, subject):
    run = one(conn, "SELECT * FROM runs WHERE id=:id FOR UPDATE", id=run_id)
    if not run:
        raise service.Conflict("not_found", "执行批次不存在")
    if run_state(run, latest_attempts(conn, run_id)) in service.TERMINAL:
        return batch(conn, run_id)
    execute(conn, "UPDATE runs SET cancel_requested=true WHERE id=:id", id=run_id)
    for a in latest_attempts(conn, run_id):
        if a["state"] not in service.TERMINAL and a["state"] != "needs_recovery":
            service.cancel(conn, a["id"], subject)
    service.audit(conn, subject, "batch.cancel", run_id)
    return batch(conn, run_id)


def advance(conn, now=None):
    """One missed timer per task, plus safe retry creation. Called before device allocation."""
    now = now or datetime.now(timezone.utc)
    if not one(conn, "SELECT pg_try_advisory_xact_lock(421097) AS owned")["owned"]:
        return
    for task in rows(
        conn,
        "SELECT * FROM test_tasks WHERE next_trigger_at<=:now ORDER BY next_trigger_at FOR UPDATE SKIP LOCKED",
        now=now,
    ):
        scheduled_at = max(
            t
            for t in occurrences(task["configuration"]["schedule"], now)
            if task["next_trigger_at"] <= t <= now
        )
        request_id = uuid5(NAMESPACE_URL, f"artemis:task:{task['id']}:{scheduled_at.isoformat()}")
        trigger(
            conn, task["id"], request_id, task["revision"], "scheduler", scheduled_at=scheduled_at
        )
        execute(
            conn,
            "UPDATE test_tasks SET next_trigger_at=:next WHERE id=:id",
            id=task["id"],
            next=next_trigger(task["configuration"], now),
        )
    for run in rows(
        conn,
        """SELECT r.* FROM runs r WHERE task_id IS NOT NULL AND NOT cancel_requested
        AND EXISTS (SELECT 1 FROM case_runs cr JOIN attempts a ON a.case_run_id=cr.id
        WHERE cr.run_id=r.id AND a.state IN ('completed','blocked')) ORDER BY r.created_at FOR UPDATE OF r SKIP LOCKED""",
    ):
        for a in latest_attempts(conn, run["id"]):
            if not retryable(run, a):
                continue
            if one(
                conn,
                "SELECT id FROM leases WHERE attempt_id=:id AND released_at IS NULL",
                id=a["id"],
            ):
                continue
            due = a["finished_at"] + timedelta(
                seconds=run["task_snapshot"]["configuration"]["retry_interval_seconds"]
            )
            if due <= now:
                service.retry(conn, a["id"], automated=True)


def can_dispatch(conn, case_run_id):
    run = one(
        conn,
        "SELECT r.* FROM runs r JOIN case_runs cr ON cr.run_id=r.id WHERE cr.id=:id",
        id=case_run_id,
    )
    if run["cancel_requested"]:
        return False
    if not run["task_id"]:
        return True
    for earlier in rows(
        conn,
        "SELECT * FROM runs WHERE task_id=:task AND (created_at,id)<(:created,:id)",
        task=run["task_id"],
        created=run["created_at"],
        id=run["id"],
    ):
        if run_state(earlier, latest_attempts(conn, earlier["id"])) not in service.TERMINAL or one(
            conn,
            "SELECT l.id FROM leases l JOIN attempts a ON a.id=l.attempt_id JOIN case_runs cr ON cr.id=a.case_run_id WHERE cr.run_id=:id AND l.released_at IS NULL LIMIT 1",
            id=earlier["id"],
        ):
            return False
    config = run["task_snapshot"]["configuration"]
    current = latest_attempts(conn, run["id"])
    active = sum(a["state"] not in service.TERMINAL and a["state"] != "queued" for a in current)
    limit = 1 if config["execution_order"] == "serial" else config["max_parallel"]
    if active >= limit:
        return False
    if config["execution_order"] == "serial":
        return (
            next(
                (
                    a["case_run_id"]
                    for a in current
                    if a["state"] not in service.TERMINAL or retryable(run, a)
                ),
                None,
            )
            == case_run_id
        )
    return True
