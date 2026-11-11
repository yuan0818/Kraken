"""Execute a frozen text case through the node's native Artemis service."""

import json

import httpx


def goal(cmd):
    c = cmd.content
    return "\n".join(
        [
            f"【平台测试】{c.case_code or c.title} · {c.purpose or c.title}",
            f"只操作 SN {cmd.serial}。按以下用例执行，前置条件不满足则记录 BLOCKED。",
            "【前置条件】",
            *c.preconditions,
            "【步骤】",
            *c.steps,
            "【预期结果】",
            *c.assertions,
            "在结束前保存 case_result JSON 笔记，包含 verdict（PASS/FAIL/BLOCKED/INCONCLUSIVE）、"
            "reason 和 assertions 数组。每项的 expected 原样引用对应预期，actual 和 evidence 写实测值与本次工具步骤。"
            "仅操作用例要求的内容。完成后恢复本次临时改动。",
        ]
    )


class Artemis:
    def __init__(self, client: httpx.Client):
        self.client = client

    def get(self, path):
        r = self.client.get(path)
        r.raise_for_status()
        return r.json()

    def device(self, serial):
        return next((d for d in self.get("/api/devices")["devices"] if d["serial"] == serial), None)

    def model_override(self, cmd, control):
        if cmd.execution_model is None:
            return None
        if self.client.base_url.host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("按任务指定模型要求 Artemis 与节点代理在同一主机")
        if self.get("/api/run/defaults").get("task_model_override") != 1:
            raise ValueError("节点 Artemis 版本不支持按任务指定模型")
        response = control.post(f"/api/nodes/{cmd.node_id}/execution-model/{cmd.command_id}")
        response.raise_for_status()
        model = response.json()
        if any(model.get(k) != v for k, v in cmd.execution_model.model_dump(mode="json").items()):
            raise ValueError("模型授权版本与本次执行不一致")
        return model

    def submit(self, cmd, model=None):
        payload = {
            "goal": goal(cmd),
            "session_id": str(cmd.attempt_id),
            "device_serial": cmd.serial,
            "profile": "pro",
            "verification_level": "final",
            "explorer_mode": "pro",
            "ingress": "python_sdk",
        }
        headers = {}
        if model is not None:
            payload["model_override"] = {
                field: model[field]
                for field in (
                    "config_id",
                    "revision",
                    "model_id",
                    "api_schema",
                    "url",
                    "context_window",
                    "thinking_effort",
                )
            }
            if model["api_key"]:
                headers["X-Artemis-Run-Model-Key"] = model["api_key"]
        response = self.client.post("/api/run", json=payload, headers=headers)
        response.raise_for_status()
        reply = response.json()
        if model is not None and reply.get("status") != "rejected":
            expected = cmd.execution_model.model_dump(mode="json")
            if not any(
                task.get("session_id") == str(cmd.attempt_id)
                and task.get("execution_model") == expected
                for task in reply.get("tasks", [])
            ):
                raise RuntimeError("Artemis 未确认本次执行的模型版本")
        return reply

    def session(self, attempt):
        response = self.client.get(f"/api/sessions/{attempt}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def stop(self, attempt):
        response = self.client.post("/api/stop", params={"session_id": str(attempt)})
        response.raise_for_status()


def verdict(cmd, status, checks, notes):
    result = notes.get("notes", {}).get("case_result.md", "")
    try:
        result = json.loads(result) if isinstance(result, str) else result
    except ValueError:
        result = {}
    result = result if isinstance(result, dict) else {}
    declared = str(result.get("verdict", "")).lower()
    counts = (checks.get("run_outcome") or {}).get("tests") or {}
    why = str(result.get("reason", ""))[:1800]
    if declared == "blocked":
        return "blocked", "partial", why
    if counts.get("failed") and any(
        r.get("kind") == "assert" and r.get("status") == "failed" and r.get("evidence")
        for r in checks.get("records", [])
    ):
        return "fail", "partial", why or "Artemis 检查到断言失败"
    items = result.get("assertions") or []
    covered = {
        i["expected"]
        for i in items
        if isinstance(i, dict)
        and isinstance(i.get("expected"), str)
        and i.get("actual")
        and i.get("evidence")
    }
    verified = any(
        r.get("kind") == "assert" and r.get("status") == "passed" and r.get("evidence")
        for r in checks.get("records", [])
    )
    if (
        status == "completed"
        and declared == "pass"
        and verified
        and counts.get("passed")
        and all(counts.get(k) == 0 for k in ("failed", "unchecked", "inconclusive"))
        and set(cmd.content.assertions) <= covered
    ):
        return "pass", "complete", why
    return "inconclusive", "partial", why or "Artemis 未提供完整断言证据"
