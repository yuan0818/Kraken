import hashlib
import secrets
from uuid import uuid4

from control_plane.infrastructure.db import execute, one
from control_plane.modules.service import Conflict, audit, encode


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def device(conn, did):
    d = one(conn, "SELECT * FROM devices WHERE id=:id FOR UPDATE", id=did)
    if not d or d["adb_state"] is None:
        raise Conflict("invalid_device", "请选择已接入的真机")
    return d


def lease(conn, did, token, subject, close=False):
    d = device(conn, did)
    if token:
        if not close and not one(
            conn, "SELECT id FROM devices WHERE id=:id AND remote_until>now()", id=did
        ):
            raise Conflict("expired_control", "控制会话已过期，请重新申请")
        if not secrets.compare_digest(d["remote_token"] or "", digest(token)):
            raise Conflict("invalid_control_token", "控制会话已失效")
        execute(
            conn,
            "UPDATE devices SET remote_until=now()+CAST(:duration AS interval) WHERE id=:id",
            id=did,
            duration="0 seconds" if close else "30 seconds",
        )
        return {"token": token}
    if d["remote_token"] or one(
        conn, "SELECT id FROM leases WHERE device_id=:id AND released_at IS NULL", id=did
    ):
        raise Conflict("device_busy", "设备正被任务或其他控制会话占用")
    fresh = one(
        conn,
        "SELECT id FROM devices WHERE id=:id AND adb_state='device' AND artemis_state='ready' AND observed_at>now()-interval '15 seconds'",
        id=did,
    )
    if not fresh:
        raise Conflict("device_not_ready", "等待设备与执行节点就绪")
    token = secrets.token_urlsafe(32)
    execute(
        conn,
        "UPDATE devices SET remote_token=:t,remote_until=now()+interval '30 seconds' WHERE id=:id",
        id=did,
        t=digest(token),
    )
    audit(conn, subject, "remote.acquire", did)
    return {"token": token}


def enqueue(conn, did, payload, token, subject):
    d = device(conn, did)
    if payload["kind"] != "frame":
        if not token or not secrets.compare_digest(d["remote_token"] or "", digest(token)):
            raise Conflict("invalid_control_token", "请先申请控制权")
        if not one(
            conn, "SELECT id FROM devices WHERE id=:id AND remote_until>now()", id=did
        ) or one(conn, "SELECT id FROM leases WHERE device_id=:id AND released_at IS NULL", id=did):
            raise Conflict("device_busy", "控制会话过期或设备被测试占用")
    pending = one(
        conn,
        "SELECT id,payload FROM remote_actions WHERE device_id=:id AND state IN ('queued','executing') AND expires_at>now() AND (payload->>'kind'<>'frame' OR :frame) ORDER BY created_at LIMIT 1",
        id=did,
        frame=payload["kind"] == "frame",
    )
    if pending:
        if payload["kind"] == "frame" and pending["payload"]["kind"] == "frame":
            return {"id": pending["id"]}
        raise Conflict("remote_pending", "上一条输入尚未完成")
    aid = uuid4()
    execute(
        conn,
        "INSERT INTO remote_actions(id,device_id,token_hash,payload) VALUES(:id,:d,:t,CAST(:p AS jsonb))",
        id=aid,
        d=did,
        t=digest(token) if token else None,
        p=encode(payload),
    )
    if payload["kind"] != "frame":
        audit(conn, subject, "remote." + payload["kind"], did)
    return {"id": aid}
