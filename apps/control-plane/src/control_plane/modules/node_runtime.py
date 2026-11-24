"""Central admission and idempotent observation processing for database-free nodes."""

from datetime import datetime, timezone

from control_plane.infrastructure import db
from control_plane.modules import service
from platform_contracts.models import NodeEvent


def current(conn, node_id, generation):
    node = db.one(conn, "SELECT * FROM nodes WHERE id=:id FOR UPDATE", id=node_id)
    if not node or node["runtime_protocol"] != 2 or node["connection_generation"] != generation:
        raise service.Conflict("stale_connection", "Node runtime must reconnect and reconcile")
    return node


def open_runtime(conn, node_id, boot_id):
    node = db.one(conn, "SELECT * FROM nodes WHERE id=:id FOR UPDATE", id=node_id)
    if not node:
        raise service.Conflict("not_found", "Register node inventory first")
    previous = db.one(
        conn,
        "SELECT generation FROM node_runtime_sessions WHERE node_id=:n AND boot_id=:b",
        n=node_id,
        b=boot_id,
    )
    if previous:
        current(conn, node_id, previous["generation"])
        return {"generation": previous["generation"]}
    generation = node["connection_generation"] + 1
    db.execute(
        conn,
        "INSERT INTO node_runtime_sessions(node_id,boot_id,generation) VALUES (:n,:b,:g)",
        n=node_id,
        b=boot_id,
        g=generation,
    )
    db.execute(
        conn,
        "UPDATE nodes SET connection_generation=:g,runtime_protocol=2,last_seen=now() WHERE id=:n",
        n=node_id,
        g=generation,
    )
    db.execute(
        conn,
        "UPDATE devices SET artemis_state='unavailable' WHERE node_id=:n AND artemis_state IS NOT NULL",
        n=node_id,
    )
    return {"generation": generation}


def snapshot(conn, node_id, generation):
    current(conn, node_id, generation)
    db.execute(conn, "UPDATE nodes SET last_seen=now() WHERE id=:n", n=node_id)
    jobs = db.rows(
        conn,
        """SELECT a.id AS attempt_id,a.state,a.simulation,l.device_id,l.generation,
        d.serial,
        (SELECT payload FROM commands WHERE attempt_id=a.id AND payload->>'kind'='execute'
         ORDER BY created_at DESC LIMIT 1) AS execution_command
        FROM leases l JOIN devices d ON d.id=l.device_id JOIN attempts a ON a.id=l.attempt_id
        WHERE d.node_id=:n AND l.released_at IS NULL ORDER BY a.created_at,a.id""",
        n=node_id,
    )
    for job in jobs:
        kind = (
            "cancel"
            if job["state"] == "cancel_pending"
            else "prepare"
            if job["state"] == "preparing"
            else "execute"
        )
        command = db.one(
            conn,
            """SELECT payload,launch_intent_at FROM commands WHERE attempt_id=:a
            AND payload->>'kind'=:kind AND dispatch_ready AND NOT retired ORDER BY created_at DESC LIMIT 1""",
            a=job["attempt_id"],
            kind=kind,
        )
        job["command"] = command["payload"] if command else None
        execution = db.one(
            conn,
            "SELECT launch_intent_at FROM commands WHERE attempt_id=:a AND payload->>'kind'='execute' ORDER BY created_at DESC LIMIT 1",
            a=job["attempt_id"],
        )
        job["launch_intent_at"] = execution["launch_intent_at"] if execution else None
    return {"jobs": jobs}


def claim(conn, node_id, generation, command_id):
    current(conn, node_id, generation)
    command = db.one(
        conn, "SELECT * FROM commands WHERE id=:id AND node_id=:n", id=command_id, n=node_id
    )
    if not command:
        raise service.Conflict("unknown_command")
    attempt = db.one(
        conn, "SELECT * FROM attempts WHERE id=:id FOR UPDATE", id=command["attempt_id"]
    )
    lease = db.one(
        conn,
        """SELECT l.id FROM leases l JOIN devices d ON d.id=l.device_id
        WHERE l.attempt_id=:a AND l.released_at IS NULL AND d.node_id=:n
        AND l.generation=:g AND d.generation=l.generation AND d.serial=:sn""",
        a=attempt["id"],
        n=node_id,
        g=command["payload"]["lease_generation"],
        sn=command["payload"]["serial"],
    )
    kind = command["payload"]["kind"]
    permitted = {
        "prepare": {"preparing"},
        "execute": {"starting", "running"},
        "cancel": {"cancel_pending"},
    }
    if (
        not lease
        or command["retired"]
        or not command["dispatch_ready"]
        or attempt["state"] not in permitted[kind]
    ):
        raise service.Conflict("lease_mismatch", "Command is no longer executable")
    # A second call only permits observing the already-authorized session.
    if kind == "execute" and command["launch_intent_at"] is not None:
        return {"start": False, "launch_intent_at": command["launch_intent_at"]}
    if kind == "execute" and attempt["state"] != "starting":
        raise service.Conflict(
            "invalid_transition", "Cannot grant a new launch to a running attempt"
        )
    if datetime.fromisoformat(command["payload"]["expires_at"]) <= datetime.now(timezone.utc):
        raise service.Conflict("command_expired")
    db.execute(conn, "UPDATE commands SET accepted=true WHERE id=:id", id=command_id)
    if kind == "execute":
        result = db.one(
            conn,
            "UPDATE commands SET launch_intent_at=now() WHERE id=:id RETURNING launch_intent_at",
            id=command_id,
        )
        return {"start": True, **result}
    return {"start": False, "launch_intent_at": None}


def update(conn, node_id, generation, observation):
    node = current(conn, node_id, generation)
    payload = observation.model_dump(mode="json")
    previous = db.one(
        conn,
        "SELECT payload FROM node_runtime_events WHERE node_id=:n AND event_id=:id",
        n=node_id,
        id=observation.event_id,
    )
    if previous:
        if previous["payload"] != payload:
            raise service.Conflict("event_conflict")
        return {"acknowledged": str(observation.event_id)}
    fields = observation.model_dump(exclude={"event_id", "legacy_seq"})
    if observation.legacy_seq is not None and observation.legacy_seq <= node["last_event_seq"]:
        old = db.one(
            conn,
            "SELECT payload FROM node_events WHERE node_id=:n AND seq=:s",
            n=node_id,
            s=observation.legacy_seq,
        )
        original = NodeEvent(seq=observation.legacy_seq, **fields).model_dump(
            mode="json",
            exclude={"diagnostics"} if observation.diagnostics is None else None,
        )
        if not old or old["payload"] != original:
            raise service.Conflict("event_conflict", "Legacy journal does not match control plane")
    else:
        if (
            observation.legacy_seq is not None
            and observation.legacy_seq != node["last_event_seq"] + 1
        ):
            raise service.Conflict(
                "event_gap", "Import legacy events in sequence before new observations"
            )
        command = db.one(
            conn,
            "SELECT launch_intent_at FROM commands WHERE id=:id AND node_id=:n",
            id=observation.command_id,
            n=node_id,
        )
        if observation.kind in {"started", "finished"} and (
            not command or command["launch_intent_at"] is None
        ):
            raise service.Conflict(
                "invalid_transition", "Execution has no persisted launch admission"
            )
        event = NodeEvent(seq=node["last_event_seq"] + 1, **fields)
        service.ingest_event(conn, node_id, generation, event)
    db.execute(
        conn,
        "INSERT INTO node_runtime_events(node_id,event_id,payload) VALUES (:n,:id,CAST(:p AS jsonb))",
        n=node_id,
        id=observation.event_id,
        p=observation.model_dump_json(),
    )
    return {"acknowledged": str(observation.event_id)}
