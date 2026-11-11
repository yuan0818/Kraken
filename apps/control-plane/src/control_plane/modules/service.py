"""Transactional application rules; transport and Celery never decide test verdicts."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from control_plane.infrastructure.db import execute, one, rows
from control_plane.modules import catalogs
from platform_contracts.models import MAX_DEVICE_INCIDENTS, Command, NodeEvent

TERMINAL = {"completed", "cancelled", "blocked"}


class Conflict(Exception):
    def __init__(self, code, detail=""):
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}")


def encode(value):
    return json.dumps(value, sort_keys=True, default=str)


def audit(conn, subject, action, resource, detail=None):
    execute(
        conn,
        "INSERT INTO audit(subject_id,action,resource_id,detail) VALUES (:s,:a,:r,CAST(:d AS jsonb))",
        s=subject,
        a=action,
        r=str(resource),
        d=encode(detail or {}),
    )


def create_case(conn, name, subject="anonymous", code=None):
    case_id = uuid4()
    for _ in range(100):
        result = one(
            conn,
            """INSERT INTO cases(id,name,code)
            VALUES (:id,:name,coalesce(:code,'TC-' || to_char(nextval('case_code_seq'),'FM000000')))
            ON CONFLICT DO NOTHING RETURNING *""",
            id=case_id,
            name=name,
            code=code,
        )
        if result:
            audit(conn, subject, "case.create", case_id)
            return dict(result)
        if code:
            raise Conflict("duplicate_case_code", "用例编号已存在，请使用其他编号")
    raise Conflict("case_code_allocation", "自动编号发生冲突，请指定编号后重试")


def add_version(conn, case_id, content, subject="anonymous"):
    case = one(conn, "SELECT * FROM cases WHERE id=:id FOR UPDATE", id=case_id)
    if not case:
        raise Conflict("not_found")
    content = catalogs.resolve_content(conn, content, case["code"])
    version_id = uuid4()
    execute(
        conn,
        """INSERT INTO case_versions(id,case_id,content,created_by)
        VALUES (:id,:case,CAST(:content AS jsonb),:subject)""",
        id=version_id,
        case=case_id,
        content=encode(content),
        subject=subject,
    )
    audit(conn, subject, "version.create", version_id)
    for entry_id in content["catalog_snapshot"]:
        execute(
            conn,
            "INSERT INTO case_version_catalog_refs(version_id,entry_id) VALUES (:v,:e)",
            v=version_id,
            e=entry_id,
        )
    return {"id": version_id, "case_id": case_id, "content": content}


_UNSET = object()


def set_stable(conn, case_id, version_id, subject="anonymous", *, expected_version_id=_UNSET):
    case = one(conn, "SELECT * FROM cases WHERE id=:id FOR UPDATE", id=case_id)
    if not case:
        raise Conflict("not_found")
    if expected_version_id is not _UNSET and case["stable_version_id"] != expected_version_id:
        raise Conflict("stable_changed", "Stable 已被其他操作修改，请刷新并核对后重新设置")
    if not one(
        conn, "SELECT id FROM case_versions WHERE id=:v AND case_id=:c", v=version_id, c=case_id
    ):
        raise Conflict("version_wrong_case")
    execute(conn, "UPDATE cases SET stable_version_id=:v WHERE id=:c", v=version_id, c=case_id)
    audit(conn, subject, "case.set_stable", case_id)
    return {"id": case_id, "stable_version_id": version_id}


def submit(conn, request, subject="anonymous"):
    fingerprint = hashlib.sha256(encode(request.model_dump(mode="json")).encode()).hexdigest()
    # Serializes concurrent retries of the same request, including before its row exists.
    execute(
        conn, "SELECT pg_advisory_xact_lock(hashtextextended(:key,0))", key=str(request.request_id)
    )
    existing = one(conn, "SELECT * FROM runs WHERE request_id=:r", r=request.request_id)
    if existing:
        if existing["request_fingerprint"] != fingerprint:
            raise Conflict("request_id_conflict")
        return {"id": existing["id"]}
    if not one(conn, "SELECT id FROM cases WHERE id=:id", id=request.case_id):
        raise Conflict("not_found")
    stable = one(
        conn,
        """SELECT v.content FROM cases c JOIN case_versions v ON v.id=c.stable_version_id
        WHERE c.id=:id""",
        id=request.case_id,
    )
    if stable and stable["content"].get("status", "active") != "active":
        raise Conflict("case_not_active", "当前 Stable 为草稿或停用状态，不能执行")
    run_id, case_run_id, attempt_id = uuid4(), uuid4(), uuid4()
    execute(
        conn,
        """INSERT INTO runs(id,request_id,request_fingerprint,artifacts)
        VALUES (:id,:req,:fp,CAST(:a AS jsonb))""",
        id=run_id,
        req=request.request_id,
        fp=fingerprint,
        a=encode(request.artifacts),
    )
    execute(
        conn,
        "INSERT INTO case_runs(id,run_id,case_id) VALUES (:id,:r,:c)",
        id=case_run_id,
        r=run_id,
        c=request.case_id,
    )
    execute(
        conn,
        "INSERT INTO attempts(id,case_run_id,number) VALUES (:id,:cr,1)",
        id=attempt_id,
        cr=case_run_id,
    )
    audit(conn, subject, "run.submit", run_id)
    return {"id": run_id}


def queue_command(conn, attempt_id, device, kind, version=None):
    execution = one(
        conn,
        """SELECT a.simulation,r.model_config_id,r.model_revision FROM attempts a
        JOIN case_runs cr ON cr.id=a.case_run_id JOIN runs r ON r.id=cr.run_id WHERE a.id=:id""",
        id=attempt_id,
    )
    execution_model = None
    if kind == "execute" and not execution["simulation"] and execution["model_config_id"]:
        execution_model = {
            "config_id": execution["model_config_id"],
            "revision": execution["model_revision"],
        }
    content = dict(version["content"]) if version else None
    if content is not None:
        content.pop("steps_markdown", None)
        content.pop("assertions_markdown", None)
    command = Command(
        command_id=uuid4(),
        attempt_id=attempt_id,
        node_id=device["node_id"],
        device_id=device["id"],
        serial=device["serial"],
        lease_generation=device["generation"],
        kind=kind,
        simulation=execution["simulation"],
        execution_model=execution_model,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        version_id=version["id"] if version else None,
        content=content,
    )
    execute(
        conn,
        """INSERT INTO commands(id,attempt_id,node_id,payload)
        VALUES (:id,:a,:n,CAST(:p AS jsonb))""",
        id=command.command_id,
        a=attempt_id,
        n=device["node_id"],
        p=command.model_dump_json(exclude={"execution_model"} if execution_model is None else None),
    )
    execute(
        conn, "INSERT INTO outbox(id,command_id) VALUES (:id,:c)", id=uuid4(), c=command.command_id
    )
    return command


def matches_device(content, tags):
    tags = set(tags)
    if not set(content.get("required_tags", [])) <= tags:
        return False
    for field, prefix in (
        ("android_version_ids", "android_version"),
        ("app_version_ids", "app_version"),
        ("device_model_ids", "device_model"),
        ("chip_platform_ids", "chip_platform"),
    ):
        values = content.get(field, [])
        if values and not any(f"{prefix}:{value}" in tags for value in values):
            return False
    return True


def schedule_one(conn):
    from control_plane.modules.tasks import can_dispatch

    # Transaction-scoped single-active gate: connection loss drops ownership.
    if not one(conn, "SELECT pg_try_advisory_xact_lock(421097) AS owned")["owned"]:
        return False
    attempts = rows(
        conn,
        """SELECT a.*,r.task_snapshot->'configuration'->>'target_device_id' AS target_device
        FROM attempts a JOIN case_runs cr ON cr.id=a.case_run_id JOIN runs r ON r.id=cr.run_id
        WHERE a.state='queued' AND a.eligible_at<=now() ORDER BY r.created_at,r.id,cr.position,a.created_at
        FOR UPDATE OF a SKIP LOCKED""",
    )
    device = attempt = None
    for candidate in attempts:
        if not can_dispatch(conn, candidate["case_run_id"]):
            continue
        version = one(
            conn,
            """SELECT v.content FROM case_runs cr JOIN cases c ON c.id=cr.case_id
            LEFT JOIN case_versions v ON v.id=c.stable_version_id WHERE cr.id=:id""",
            id=candidate["case_run_id"],
        )
        content = version["content"] if version else None
        if content and content.get("status", "active") != "active":
            execute(
                conn,
                """UPDATE attempts SET state='blocked',verdict='blocked',
                reason='Stable case is draft or disabled' WHERE id=:id""",
                id=candidate["id"],
            )
            return True
        candidates = rows(
            conn,
            """SELECT d.* FROM devices d JOIN nodes n ON n.id=d.node_id
            WHERE NOT d.quarantined AND d.remote_token IS NULL AND n.last_seen > now()-interval '15 seconds'
            AND ((:sim AND d.adb_state IS NULL AND n.capabilities @> '["simulator"]'::jsonb)
              OR (NOT :sim AND d.id=CAST(:target AS uuid) AND d.adb_state='device'
                  AND d.observed_at > now()-interval '15 seconds' AND d.artemis_state='ready'))
            AND NOT EXISTS (SELECT 1 FROM leases l WHERE l.device_id=d.id AND l.released_at IS NULL)
            ORDER BY d.serial FOR UPDATE OF d SKIP LOCKED""",
            sim=candidate["simulation"],
            target=candidate["target_device"],
        )
        device = next(
            (d for d in candidates if not content or matches_device(content, d["tags"])), None
        )
        if device:
            attempt = candidate
            break
    if not device:
        return False
    device = dict(device)
    device["generation"] += 1
    execute(
        conn,
        "UPDATE devices SET generation=:g WHERE id=:id",
        g=device["generation"],
        id=device["id"],
    )
    execute(
        conn,
        "INSERT INTO leases(id,device_id,attempt_id,generation) VALUES (:id,:d,:a,:g)",
        id=uuid4(),
        d=device["id"],
        a=attempt["id"],
        g=device["generation"],
    )
    execute(conn, "UPDATE attempts SET state='preparing' WHERE id=:id", id=attempt["id"])
    queue_command(conn, attempt["id"], device, "prepare")
    return True


def cancel(conn, attempt_id, subject="anonymous"):
    attempt = one(conn, "SELECT * FROM attempts WHERE id=:id FOR UPDATE", id=attempt_id)
    if not attempt:
        raise Conflict("not_found")
    if attempt["state"] in TERMINAL or attempt["state"] == "cancel_pending":
        return {"state": attempt["state"]}
    if attempt["state"] == "needs_recovery":
        raise Conflict("recovery_requires_reconciliation")
    lease = one(
        conn,
        """SELECT d.* FROM leases l JOIN devices d ON d.id=l.device_id
        WHERE l.attempt_id=:a AND l.released_at IS NULL""",
        a=attempt_id,
    )
    if not lease:
        execute(
            conn,
            "UPDATE attempts SET state='cancelled',cleanup='verified' WHERE id=:id",
            id=attempt_id,
        )
        return {"state": "cancelled"}
    execute(conn, "UPDATE attempts SET state='cancel_pending' WHERE id=:id", id=attempt_id)
    # Pending prepare/execute cannot be delivered after cancellation. Already accepted work must stop safely.
    execute(conn, "UPDATE commands SET retired=true WHERE attempt_id=:a", a=attempt_id)
    queue_command(conn, attempt_id, lease, "cancel")
    audit(conn, subject, "attempt.cancel", attempt_id)
    return {"state": "cancel_pending"}


def retry(conn, attempt_id, subject="anonymous", *, automated=False):
    attempt = one(conn, "SELECT * FROM attempts WHERE id=:id FOR UPDATE", id=attempt_id)
    if not attempt or attempt["state"] not in TERMINAL:
        raise Conflict("retry_not_safe")
    run = one(
        conn,
        "SELECT r.task_id FROM runs r JOIN case_runs cr ON cr.run_id=r.id WHERE cr.id=:id",
        id=attempt["case_run_id"],
    )
    if run["task_id"] and not automated:
        raise Conflict(
            "task_retry_policy",
            "任务用例按批次规则自动重试；需要再次执行时，请从测试任务创建新批次",
        )
    one(conn, "SELECT id FROM case_runs WHERE id=:id FOR UPDATE", id=attempt["case_run_id"])
    if one(
        conn, "SELECT id FROM leases WHERE attempt_id=:id AND released_at IS NULL", id=attempt_id
    ):
        raise Conflict("lease_still_held")
    latest = one(
        conn,
        "SELECT * FROM attempts WHERE case_run_id=:id ORDER BY number DESC LIMIT 1",
        id=attempt["case_run_id"],
    )
    if latest["id"] != attempt_id:
        raise Conflict("not_latest_attempt")
    if not run["task_id"]:
        execute(
            conn,
            "UPDATE runs SET cancel_requested=false WHERE id=(SELECT run_id FROM case_runs WHERE id=:id)",
            id=attempt["case_run_id"],
        )
    new_id = uuid4()
    execute(
        conn,
        "INSERT INTO attempts(id,case_run_id,number,simulation) VALUES (:id,:cr,:n,:sim)",
        sim=attempt["simulation"],
        id=new_id,
        cr=attempt["case_run_id"],
        n=attempt["number"] + 1,
    )
    audit(conn, subject, "attempt.retry", new_id)
    return {"id": new_id}


def ingest_event(conn, node_id, connection_generation, event: NodeEvent):
    node = one(conn, "SELECT * FROM nodes WHERE id=:id FOR UPDATE", id=node_id)
    if node["connection_generation"] != connection_generation:
        raise Conflict("stale_connection")
    payload = event.model_dump(
        mode="json", exclude={"diagnostics"} if event.diagnostics is None else None
    )
    if event.seq <= node["last_event_seq"]:
        existing = one(
            conn,
            "SELECT payload FROM node_events WHERE node_id=:n AND seq=:s",
            n=node_id,
            s=event.seq,
        )
        if not existing or existing["payload"] != payload:
            raise Conflict("event_conflict")
        return event.seq
    if event.seq != node["last_event_seq"] + 1:
        raise Conflict("out_of_order", str(node["last_event_seq"] + 1))
    cmd = one(
        conn, "SELECT * FROM commands WHERE id=:id AND node_id=:n", id=event.command_id, n=node_id
    )
    if not cmd or cmd["attempt_id"] != event.attempt_id:
        raise Conflict("unknown_command")
    attempt = one(conn, "SELECT * FROM attempts WHERE id=:a FOR UPDATE", a=event.attempt_id)
    if event.simulation != attempt["simulation"] or event.simulation != cmd["payload"].get(
        "simulation", True
    ):
        raise Conflict("invalid_transition", "Execution mode does not match the assigned attempt")
    lease = one(
        conn,
        """SELECT l.*,d.node_id,d.serial,d.tags FROM leases l JOIN devices d ON d.id=l.device_id
        WHERE l.attempt_id=:a AND l.generation=:g""",
        a=event.attempt_id,
        g=event.lease_generation,
    )
    if (
        not lease
        or lease["node_id"] != node_id
        or cmd["payload"]["lease_generation"] != event.lease_generation
    ):
        raise Conflict("lease_mismatch")
    kind = cmd["payload"]["kind"]
    allowed = {
        "prepare": {"accepted", "ready", "rejected"},
        "execute": {"accepted", "started", "finished", "rejected", "diagnostics"},
        "cancel": {"accepted", "cancelled", "rejected"},
    }
    if event.kind not in allowed[kind]:
        raise Conflict("invalid_transition")
    if event.kind == "diagnostics":
        if attempt["simulation"] or event.diagnostics.serial != lease["serial"]:
            raise Conflict(
                "invalid_transition", "Device incidents do not match the physical execution"
            )
        if (
            (not cmd["retired"] or attempt["state"] == "cancel_pending")
            and lease["released_at"] is None
            and attempt["state"] in {"starting", "running", "cancel_pending"}
        ):
            update = dict(payload["diagnostics"])
            previous = attempt["diagnostics"] or {}
            incidents = {i["id"]: i for i in previous.get("incidents", [])}
            for incident in update["incidents"]:
                incidents.setdefault(incident["id"], incident)
            update["truncated"] |= (
                previous.get("truncated", False) or len(incidents) > MAX_DEVICE_INCIDENTS
            )
            update["incidents"] = list(incidents.values())[:MAX_DEVICE_INCIDENTS]
            if update["truncated"] and update["status"] == "complete":
                update["status"] = "partial"
            execute(
                conn,
                "UPDATE attempts SET diagnostics=CAST(:d AS jsonb) WHERE id=:id",
                d=encode(update),
                id=attempt["id"],
            )
    elif event.kind == "accepted":
        execute(conn, "UPDATE commands SET accepted=true WHERE id=:id", id=event.command_id)
    elif event.kind == "ready" and attempt["state"] == "preparing":
        case = one(
            conn,
            """SELECT c.* FROM cases c JOIN case_runs cr ON cr.case_id=c.id
            WHERE cr.id=:cr FOR UPDATE OF c""",
            cr=attempt["case_run_id"],
        )
        version = one(conn, "SELECT * FROM case_versions WHERE id=:v", v=case["stable_version_id"])
        if (
            not version
            or version["content"].get("status", "active") != "active"
            or not matches_device(version["content"], lease["tags"])
        ):
            execute(
                conn,
                "UPDATE attempts SET reason='Stable missing, inactive or device constraints changed' WHERE id=:a",
                a=attempt["id"],
            )
            cancel(conn, attempt["id"])
        else:
            execute(
                conn,
                """UPDATE attempts SET version_id=:v,snapshot=CAST(:s AS jsonb),state='starting'
                WHERE id=:a""",
                v=version["id"],
                s=encode(version["content"]),
                a=attempt["id"],
            )
            device = {
                "id": lease["device_id"],
                "node_id": node_id,
                "serial": lease["serial"],
                "generation": lease["generation"],
            }
            queue_command(conn, attempt["id"], device, "execute", version)
    elif event.kind == "started" and attempt["state"] == "starting":
        execute(conn, "UPDATE attempts SET state='running' WHERE id=:a", a=attempt["id"])
    elif event.kind in {"finished", "cancelled", "rejected"}:
        if attempt["state"] in TERMINAL or lease["released_at"] is not None:
            # Cancellation can arrive after a completed result. Preserve the first terminal fact,
            # but acknowledge this historical event so the node cursor can continue advancing.
            if event.kind == "cancelled" or (event.kind == "rejected" and cmd["retired"]):
                execute(
                    conn,
                    "INSERT INTO node_events(node_id,seq,payload) VALUES (:n,:s,CAST(:p AS jsonb))",
                    n=node_id,
                    s=event.seq,
                    p=encode(payload),
                )
                execute(
                    conn,
                    "UPDATE nodes SET last_event_seq=:s,last_seen=now() WHERE id=:id",
                    s=event.seq,
                    id=node_id,
                )
                return event.seq
            raise Conflict("invalid_transition")
        if event.kind == "finished" and attempt["snapshot"] is None:
            raise Conflict("invalid_transition")
        if event.verdict == "pass" and (
            attempt["simulation"]
            or event.kind != "finished"
            or event.cleanup != "verified"
            or event.evidence != "complete"
        ):
            raise Conflict(
                "invalid_transition", "PASS requires verified real execution and complete evidence"
            )
        if event.kind == "rejected" and kind == "cancel":
            state = "needs_recovery"
        elif event.cleanup != "verified":
            state = "needs_recovery"
        else:
            state = {"finished": "completed", "cancelled": "cancelled", "rejected": "blocked"}[
                event.kind
            ]
        execute(
            conn,
            """UPDATE attempts SET state=:s,verdict=:v,cleanup=:c,evidence=:e,reason=:r WHERE id=:a""",
            s=state,
            v=event.verdict.value,
            c=event.cleanup,
            e=event.evidence,
            r=event.reason or attempt["reason"],
            a=attempt["id"],
        )
        if state in TERMINAL:
            execute(conn, "UPDATE leases SET released_at=now() WHERE id=:id", id=lease["id"])
            execute(conn, "UPDATE commands SET retired=true WHERE attempt_id=:a", a=attempt["id"])
    execute(
        conn,
        "INSERT INTO node_events(node_id,seq,payload) VALUES (:n,:s,CAST(:p AS jsonb))",
        n=node_id,
        s=event.seq,
        p=encode(payload),
    )
    execute(
        conn,
        "UPDATE nodes SET last_event_seq=:s,last_seen=now() WHERE id=:id",
        s=event.seq,
        id=node_id,
    )
    return event.seq


def overview(conn):
    return {
        "cases": rows(conn, "SELECT * FROM cases ORDER BY created_at DESC"),
        "catalogs": catalogs.list_entries(conn),
        "versions": rows(conn, "SELECT * FROM case_versions ORDER BY created_at DESC"),
        "nodes": rows(
            conn,
            "SELECT id,capabilities,last_seen,connection_generation,last_event_seq FROM nodes ORDER BY id",
        ),
        "devices": rows(
            conn,
            """SELECT d.*,d.remote_token IS NOT NULL AS remote_held,l.attempt_id FROM devices d LEFT JOIN leases l ON l.device_id=d.id
            AND l.released_at IS NULL ORDER BY d.node_id,d.serial""",
        ),
        "attempts": rows(
            conn,
            """SELECT a.*,cr.run_id,c.name AS case_name FROM attempts a
            JOIN case_runs cr ON cr.id=a.case_run_id JOIN cases c ON c.id=cr.case_id ORDER BY a.created_at DESC LIMIT 100""",
        ),
    }
