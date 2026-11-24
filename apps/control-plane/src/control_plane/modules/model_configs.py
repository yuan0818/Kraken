"""Model configuration versions and sanitized, explicit control-plane probes."""

import json
from time import monotonic
from uuid import uuid4

import httpx
from control_plane.infrastructure import db, secrets
from control_plane.modules import service
from platform_contracts.model_configs import ModelDefinition

PUBLIC_FIELDS = tuple(ModelDefinition.model_fields)


def get(conn, config_id, *, lock=False):
    config = db.one(
        conn,
        "SELECT * FROM model_configs WHERE id=:id" + (" FOR UPDATE" if lock else ""),
        id=config_id,
    )
    if not config:
        raise service.Conflict("model_not_found", "模型配置不存在")
    version = db.one(
        conn,
        "SELECT * FROM model_config_versions WHERE config_id=:id AND revision=:r",
        id=config_id,
        r=config["revision"],
    )
    return dict(config), dict(version)


def public(conn, config_id):
    config, version = get(conn, config_id)
    check = db.one(
        conn,
        "SELECT id,config_id,revision,location,status,detail,latency_ms,http_status,checked_at FROM model_checks WHERE config_id=:id AND revision=:r ORDER BY checked_at DESC LIMIT 1",
        id=config_id,
        r=config["revision"],
    )
    return {
        **config,
        **version["definition"],
        "has_key": version["credential_id"] is not None,
        "last_check": dict(check) if check else None,
    }


def listing(conn):
    return [
        public(conn, r["id"])
        for r in db.rows(conn, "SELECT id FROM model_configs ORDER BY updated_at DESC,id")
    ]


def versions(conn, config_id):
    get(conn, config_id)
    return [
        {
            **r["definition"],
            "revision": r["revision"],
            "has_key": r["credential_id"] is not None,
            "created_at": r["created_at"],
        }
        for r in db.rows(
            conn,
            "SELECT * FROM model_config_versions WHERE config_id=:id ORDER BY revision DESC",
            id=config_id,
        )
    ]


def save(conn, body, subject, config_id=None):
    definition = {k: getattr(body, k) for k in PUBLIC_FIELDS}
    credential_id = None
    if config_id is None:
        config_id, revision = uuid4(), 1
        db.execute(
            conn, "INSERT INTO model_configs(id,revision) VALUES (:id,:r)", id=config_id, r=revision
        )
    else:
        config, old = get(conn, config_id, lock=True)
        if config["revision"] != body.expected_revision:
            raise service.Conflict(
                "model_changed", "模型配置已被修改，请刷新后核对；当前输入未保存"
            )
        credential_id = old["credential_id"]
        revision = config["revision"] + 1
        if (
            body.api_key is None
            and (
                old["definition"]["url"] != definition["url"]
                or old["definition"]["api_schema"] != definition["api_schema"]
            )
            and credential_id
        ):
            raise service.Conflict(
                "model_key_destination_changed",
                "修改 URL 或 API schema 时请重新填写 Key，或明确清除 Key",
            )
        db.execute(
            conn,
            "UPDATE model_configs SET revision=:r,updated_at=clock_timestamp() WHERE id=:id",
            id=config_id,
            r=revision,
        )
    if body.api_key is not None:
        raw = body.api_key.get_secret_value()
        credential_id = None
        if raw:
            credential_id = uuid4()
            db.execute(
                conn,
                "INSERT INTO model_credentials(id,ciphertext) VALUES (:id,:c)",
                id=credential_id,
                c=secrets.encrypt(raw),
            )
    db.execute(
        conn,
        "INSERT INTO model_config_versions(config_id,revision,definition,credential_id,created_by) VALUES (:id,:r,CAST(:d AS jsonb),:key,:s)",
        id=config_id,
        r=revision,
        d=service.encode(definition),
        key=credential_id,
        s=subject,
    )
    service.audit(
        conn,
        subject,
        "model.save",
        config_id,
        {"revision": revision, "key_changed": body.api_key is not None},
    )
    return public(conn, config_id)


def availability(conn, config_id, body, subject):
    config, version = get(conn, config_id, lock=True)
    if config["revision"] != body.expected_revision:
        raise service.Conflict("model_changed", "模型配置已更新，请刷新后操作")
    # Status changes also advance the revision, so an editor cannot overwrite a newer status.
    if config["enabled"] != body.enabled:
        revision = config["revision"] + 1
        db.execute(
            conn,
            "INSERT INTO model_config_versions(config_id,revision,definition,credential_id,created_by) VALUES (:id,:r,CAST(:d AS jsonb),:key,:s)",
            id=config_id,
            r=revision,
            d=service.encode(version["definition"]),
            key=version["credential_id"],
            s=subject,
        )
        db.execute(
            conn,
            "UPDATE model_configs SET enabled=:e,revision=:r,updated_at=clock_timestamp() WHERE id=:id",
            id=config_id,
            e=body.enabled,
            r=revision,
        )
        service.audit(
            conn,
            subject,
            "model.enable" if body.enabled else "model.disable",
            config_id,
            {"revision": revision},
        )
    return public(conn, config_id)


def snapshot(conn, config_id):
    config, version = get(conn, config_id)
    if not config["enabled"]:
        raise service.Conflict("model_disabled", "所选模型已停用，请更换模型或重新启用")
    return {
        "id": str(config_id),
        "revision": config["revision"],
        **version["definition"],
        "has_key": version["credential_id"] is not None,
    }


def execution_model(conn, node_id, command_id):
    version = db.one(
        conn,
        """SELECT v.config_id,v.revision,v.definition,k.ciphertext FROM commands c
        JOIN attempts a ON a.id=c.attempt_id
        JOIN leases l ON l.attempt_id=a.id AND l.released_at IS NULL
        JOIN devices d ON d.id=l.device_id AND d.node_id=c.node_id AND d.generation=l.generation
        JOIN case_runs cr ON cr.id=a.case_run_id JOIN runs r ON r.id=cr.run_id
        JOIN model_config_versions v ON v.config_id=r.model_config_id AND v.revision=r.model_revision
        LEFT JOIN model_credentials k ON k.id=v.credential_id
        WHERE c.id=:id AND c.node_id=:node AND c.dispatch_ready AND NOT c.retired
          AND NOT a.simulation AND a.state='starting' AND c.payload->>'kind'='execute'
          AND (c.payload->>'expires_at')::timestamptz > now()
          AND (c.payload->>'lease_generation')::int=l.generation
          AND c.payload->'execution_model'->>'config_id'=v.config_id::text
          AND (c.payload->'execution_model'->>'revision')::int=v.revision
        FOR SHARE OF a,l,d""",
        id=command_id,
        node=node_id,
    )
    if not version:
        return None
    return {
        "config_id": str(version["config_id"]),
        "revision": version["revision"],
        **version["definition"],
        "api_key": secrets.decrypt(version["ciphertext"]) if version["ciphertext"] else "",
    }


def probe_input(conn, config_id, expected_revision):
    config, version = get(conn, config_id)
    if config["revision"] != expected_revision:
        raise service.Conflict("model_changed", "模型配置已更新，请刷新后检测")
    if not config["enabled"]:
        raise service.Conflict("model_disabled", "请先启用模型")
    key = ""
    if version["credential_id"]:
        encrypted = db.one(
            conn,
            "SELECT ciphertext FROM model_credentials WHERE id=:id",
            id=version["credential_id"],
        )
        key = secrets.decrypt(encrypted["ciphertext"])
    return version["definition"], key


def request_spec(definition, key):
    """Explicit parameters; unsupported effort is reported, never retried with it removed."""
    prompt = "Connectivity check. Reply with OK only."
    effort = definition["thinking_effort"]
    payload = {"model": definition["model_id"], "messages": [{"role": "user", "content": prompt}]}
    headers = {"Content-Type": "application/json"}
    base = definition["url"]
    if definition["api_schema"] == "openai":
        path = "/chat/completions"
        payload["max_completion_tokens"] = 512
        if key:
            headers["Authorization"] = f"Bearer {key}"
        if effort != "default":
            payload["reasoning_effort"] = effort
    else:
        # Anthropic accepts a host-only base URL or a base ending in /v1.
        path = "/messages" if base.endswith("/v1") else "/v1/messages"
        headers["anthropic-version"] = "2023-06-01"
        if key:
            headers["x-api-key"] = key
        payload["max_tokens"] = 512
        if effort == "none":
            payload["thinking"] = {"type": "disabled"}
        elif effort != "default":
            payload["thinking"] = {"type": "adaptive"}
            payload["output_config"] = {"effort": effort}
    return base + path, headers, payload


def probe(definition, key):
    start = monotonic()
    status, detail, http_status = "failed", "请求未完成", None
    try:
        url, headers, payload = request_spec(definition, key)
        # No redirects, environment proxy credentials, upstream body logging or automatic retries.
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
            with client.stream("POST", url, headers=headers, json=payload) as response:
                http_status = response.status_code
                if http_status != 200:
                    detail = (
                        f"服务返回 HTTP {http_status}；请核对地址、Key、模型 ID 与思考参数支持情况"
                    )
                else:
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        if monotonic() - start > 20 or len(content) + len(chunk) > 65536:
                            raise ValueError
                        content.extend(chunk)
                    data = json.loads(content)
                    if definition["api_schema"] == "openai":
                        answer = data["choices"][0]["message"]["content"]
                    else:
                        answer = "".join(
                            part.get("text", "")
                            for part in data["content"]
                            if part.get("type") == "text"
                        )
                    if not isinstance(answer, str) or not answer.strip():
                        raise ValueError
                    status, detail = (
                        "passed",
                        "控制面已收到有效文本响应；节点可达性、视觉能力和设备执行尚未验证",
                    )
    except httpx.TimeoutException:
        detail = "模型请求超时，请核对网络、服务响应时间和思考强度"
    except httpx.RequestError:
        detail = "模型连接失败，请核对控制面网络、地址和 TLS 配置"
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        detail = "响应未包含符合所选协议的有效文本，或超出检测大小/时间限制"
    return {
        "status": status,
        "detail": detail,
        "latency_ms": int((monotonic() - start) * 1000),
        "http_status": http_status,
    }


def save_probe(conn, config_id, revision, result, subject):
    check = db.one(
        conn,
        "INSERT INTO model_checks(id,config_id,revision,location,status,detail,latency_ms,http_status,created_by) VALUES (:id,:c,:r,'control_plane',:status,:detail,:latency_ms,:http_status,:s) RETURNING id,config_id,revision,location,status,detail,latency_ms,http_status,checked_at",
        id=uuid4(),
        c=config_id,
        r=revision,
        s=subject,
        **result,
    )
    service.audit(
        conn,
        subject,
        "model.check",
        config_id,
        {"revision": revision, "status": result["status"], "location": "control_plane"},
    )
    return dict(check)
