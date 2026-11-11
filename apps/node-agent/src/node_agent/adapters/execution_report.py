import ast
import asyncio
import hashlib
import json
import logging
import math
import re
from pathlib import Path
from uuid import UUID

import httpx
from platform_contracts.execution_reports import (
    MAX_STEP_FRAME_BYTES,
    ExecutionReport,
    ExecutionStep,
    StepCheck,
)

_CREDENTIAL = re.compile(
    r"api[-_ ]?key|authorization|bearer\s|access[-_ ]?token|password|secret|cookie", re.I
)
_FRAME = re.compile(r"[0-9a-f]{64}")
_VALIDATION = re.compile(r"^\((True|False), <ValidationErrorCategory\.\w+: '([a-z_]+)'>, (.*)\)$")
_ACTIONS = {
    "tap": "点击",
    "click": "点击",
    "long_press": "长按",
    "long_press_on": "长按",
    "swipe": "滑动",
    "scroll": "滚动",
    "drag": "拖动",
    "drag_and_drop": "拖动",
    "perform_gesture": "执行手势",
    "input_text": "输入文字",
    "focus_and_input_text": "输入文字",
    "press_key": "按系统键",
    "press_home": "返回桌面",
    "press_back": "返回",
    "launch_app": "打开应用",
    "manage_app": "管理应用",
    "wait_for_delay": "等待",
    "report_task_status": "提交结果",
}
_DIRECTIONS = {"up": "向上", "down": "向下", "left": "向左", "right": "向右"}
_DECISIONS = {"passed", "failed", "skipped", "inconclusive", "unchecked"}


def _text(value, limit):
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return "\n".join(
        "[已隐藏凭证相关内容]" if _CREDENTIAL.search(line) else line for line in text.splitlines()
    )[:limit]


def _startup_log(traces_dir: Path | None, attempt: UUID):
    if traces_dir is None:
        return "", False
    root = traces_dir.resolve()
    file = (root / str(attempt) / "stdout.log").resolve()
    if not file.is_relative_to(root):
        return "", False
    try:
        with file.open("rb") as stream:
            size = stream.seek(0, 2)
            stream.seek(max(0, size - 16000))
            return _text(stream.read(16000).decode("utf-8", errors="replace"), 16000), size > 16000
    except OSError:
        return "", False


def _step(row):
    actions = row.get("action_taken") or []
    if not isinstance(actions, list):
        actions = [actions]
    labels = [
        " ".join(str(action[k]) for k in ("action", "target_resource_id") if action.get(k))
        if isinstance(action, dict)
        else str(action)
        for action in actions[:12]
    ]
    result = row.get("last_execution_result")
    timestamp = row.get("timestamp")
    return ExecutionStep(
        number=row["step_number"],
        timestamp=timestamp
        if isinstance(timestamp, (int, float)) and math.isfinite(timestamp)
        else None,
        action=_text(" · ".join(filter(None, labels)) or row.get("step_type"), 600),
        status=_text(result.get("status") if isinstance(result, dict) else result, 100),
        tools=[
            _text(f"{tool['name']} · {tool.get('status') or '—'}", 160)
            for tool in (row.get("generic_tools") or [])[:12]
            if isinstance(tool, dict) and tool.get("name")
        ],
    )


def _action_text(action):
    name = action.get("action", "")
    if not isinstance(name, str) or not name:
        return ""
    target = next(
        (
            action[k]
            for k in ("target_text", "target_description", "app_name", "key")
            if action.get(k)
        ),
        "",
    )
    direction = _DIRECTIONS.get(str(action.get("direction")), "")
    label = _ACTIONS.get(name, "执行操作")
    if name == "wait_for_delay" and isinstance(action.get("time_in_ms"), (int, float)):
        return f"等待 {action['time_in_ms'] / 1000:g} 秒"
    return f"{direction}{label} {target}".strip()


def _paths(action, metadata):
    def path(points):
        if (
            isinstance(points, list)
            and 1 <= len(points) <= 16
            and all(
                isinstance(p, (tuple, list))
                and len(p) == 2
                and all(isinstance(v, (int, float)) and 0 <= v <= 1000 for v in p)
                for p in points
            )
        ):
            return [points]
        return []

    if action.get("action") == "perform_gesture":
        return [
            normalized
            for phase in (action.get("phases") or [])
            if isinstance(phase, dict)
            for pointer in (phase.get("pointers") or [])
            if isinstance(pointer, dict)
            for normalized in path(pointer.get("path"))
        ][:8]
    coords = action.get("normalized_coordinates")
    if isinstance(coords, list) and len(coords) in (2, 4):
        return path([coords[i : i + 2] for i in range(0, len(coords), 2)])
    coords = action.get("coordinates")
    width, height = metadata.get("width"), metadata.get("height")
    if (
        action.get("coordinate_space") == "pixel"
        and isinstance(width, (int, float))
        and width > 0
        and isinstance(height, (int, float))
        and height > 0
        and isinstance(coords, list)
        and len(coords) in (2, 4)
        and all(isinstance(v, (int, float)) for v in coords)
    ):
        return path(
            [
                [coords[i] / width * 1000, coords[i + 1] / height * 1000]
                for i in range(0, len(coords), 2)
            ]
        )
    return []


def _validation(payload):
    result = payload.get("result")
    if not isinstance(result, str) or not (match := _VALIDATION.fullmatch(result)):
        return None, ""
    passed, category, message = match.groups()
    if category.endswith("_bypassed"):
        return "skipped", ""
    try:
        detail = ast.literal_eval(message[:2000])
    except (ValueError, SyntaxError):
        detail = ""
    return ("passed" if passed == "True" else "failed"), _text(detail, 500)


async def _native_json(native, path):
    result = await native.get(path)
    if result.status_code == 404:
        return None
    result.raise_for_status()
    return result.json()


async def _upload_frame(native, control, route, digest):
    image = bytearray()
    async with native.stream("GET", f"/api/images/{digest}") as result:
        if result.status_code == 404:
            return False
        result.raise_for_status()
        async for chunk in result.aiter_bytes():
            if len(image) + len(chunk) > MAX_STEP_FRAME_BYTES:
                return False
            image.extend(chunk)
    if not image.startswith(b"\xff\xd8\xff") or hashlib.sha256(image).hexdigest() != digest:
        return False
    response = await control.put(f"{route}/frames/{digest}", content=bytes(image))
    response.raise_for_status()
    return True


async def _visual_report(native, control, route, attempt, report):
    rows = await _native_json(native, f"/api/sessions/{attempt}/steps")
    if not isinstance(rows, list) or not rows:
        return report
    checks = await _native_json(native, f"/api/sessions/{attempt}/checks") or {}
    recorded = checks.get("records") or []
    frames = {}
    steps = []
    truncated = report.truncated and (len(report.checks) >= 40 or len(report.case_result) >= 16000)
    for row in rows:
        if (
            not isinstance(row, dict)
            or row.get("session_id") != attempt
            or not isinstance(row.get("step_number"), int)
        ):
            continue
        step = _step(row)
        actions = row.get("action_taken") or []
        actions = actions if isinstance(actions, list) else [actions]
        actions = [action for action in actions if isinstance(action, dict)]
        paths = [p for action in actions for p in _paths(action, row.get("extra_metadata") or {})]
        tools = row.get("generic_tools") or []
        validation, detail = None, ""
        for tool in reversed(tools):
            if not isinstance(tool, dict) or tool.get("name") not in {
                "safety_net_validation",
                "safety_net_pixel_validation",
            }:
                continue
            try:
                trace_id = UUID(tool.get("trace_id", ""))
            except (ValueError, TypeError):
                continue
            trace = await _native_json(native, f"/api/traces/{trace_id}")
            if (
                trace
                and trace.get("session_id") == attempt
                and trace.get("step_id") == row.get("step_id")
            ):
                payload = trace.get("payload")
                if isinstance(payload, dict):
                    validation, detail = _validation(payload)
                    if validation:
                        break
        refs = []
        for key in ("pre_image_name", "post_image_name"):
            digest = row.get(key)
            if not isinstance(digest, str) or not _FRAME.fullmatch(digest):
                refs.append(None)
                continue
            if digest not in frames:
                frames[digest] = await _upload_frame(native, control, route, digest)
            refs.append(digest if frames[digest] else None)
            truncated |= not frames[digest]
        assertions = [
            StepCheck(
                status=check["status"],
                criterion=_text(check.get("item_text"), 800),
                evidence=_text(check.get("evidence"), 1200),
            )
            for check in recorded
            if isinstance(check, dict)
            and row.get("step_id")
            and check.get("anchor_step_id") == row["step_id"]
            and check.get("kind") == "assert"
            and check.get("status") in _DECISIONS
        ]
        step.before, step.after = refs
        step.instruction = _text("；".join(filter(None, map(_action_text, actions))), 1200)
        step.observation = _text(row.get("summary"), 1200)
        step.paths = paths[:8]
        step.validation, step.validation_note = validation, detail
        step.assertions = assertions[:12]
        steps.append(step)
    if not steps:
        return report
    return report.model_copy(update={"visual_version": 1, "steps": steps, "truncated": truncated})


def collect_execution_report(
    native, attempt, *, status=None, checks=None, notes=None, traces_dir=None
):
    """Project session evidence without copying tool payloads, prompts or model reasoning."""
    if not status:
        log, truncated = _startup_log(traces_dir, attempt)
        return ExecutionReport(startup_log=log, truncated=truncated) if log else None

    rows = []
    steps_missing = False
    try:
        rows = native.get(f"/api/sessions/{attempt}/steps")
    except (httpx.HTTPError, ValueError):
        steps_missing = True
    rows = [
        row for row in rows if isinstance(row, dict) and isinstance(row.get("step_number"), int)
    ]
    checks = (checks or {}).get("records") or []
    case_result = (notes or {}).get("notes", {}).get("case_result.md") or ""
    return ExecutionReport(
        session_status=_text(status, 100),
        case_result=_text(case_result, 16000),
        checks=[
            _text(
                {
                    k: row[k]
                    for k in ("kind", "status", "expected", "actual", "reason", "evidence")
                    if k in row
                },
                1000,
            )
            for row in checks[:40]
            if isinstance(row, dict)
        ],
        steps=[_step(row) for row in rows[:100]],
        truncated=steps_missing or len(rows) > 100 or len(checks) > 40 or len(case_result) > 16000,
    )


async def upload_execution_reports(spool, base, node, native_base):
    async with (
        httpx.AsyncClient(base_url=base, timeout=5, trust_env=False) as client,
        httpx.AsyncClient(base_url=native_base, timeout=4, trust_env=False) as native,
    ):
        while True:
            for path in sorted((spool.directory / "reports").glob("*.json")):
                pending = json.loads(path.read_text())
                route = f"/api/nodes/{node}/execution-reports/{pending['command_id']}"
                try:
                    report = ExecutionReport.model_validate(pending["payload"])
                    response = await client.put(
                        route,
                        content=report.model_dump_json(),
                        headers={"Content-Type": "application/json"},
                    )
                    response.raise_for_status()
                    if report.session_status and not report.visual_version:
                        visual = await _visual_report(
                            native, client, route, pending["attempt_id"], report
                        )
                        if visual.visual_version:
                            response = await client.put(
                                route,
                                content=visual.model_dump_json(),
                                headers={"Content-Type": "application/json"},
                            )
                            response.raise_for_status()
                except (httpx.HTTPError, ValueError) as exc:
                    logging.warning("Execution report upload deferred (%s)", type(exc).__name__)
                else:
                    spool.acknowledge(path, expected=pending)
            await asyncio.sleep(2)
