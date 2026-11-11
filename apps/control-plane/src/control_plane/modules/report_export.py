"""Render and archive a point-in-time batch report."""

import json
import os
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from control_plane.modules.service import TERMINAL
from platform_contracts.tasks import BatchReport

VERDICTS = {
    "pass": "通过",
    "fail": "失败",
    "blocked": "阻塞",
    "inconclusive": "结论不充分",
    "not_evaluated": "尚未评估",
}
STATES = {
    "not_started": "未执行",
    "queued": "排队中",
    "preparing": "准备中",
    "starting": "启动中",
    "running": "执行中",
    "retry_wait": "等待重试",
    "completed": "已结束",
    "blocked": "已阻塞",
    "cancel_pending": "取消中",
    "cancelled": "已取消",
    "needs_recovery": "待恢复",
}
MEDIA_TYPES = {"html": "text/html", "json": "application/json"}


def table(headers, rows):
    def cells(tag, values):
        return "".join(f"<{tag}>{escape(str(value))}</{tag}>" for value in values)

    body = "".join(f"<tr>{cells('td', row)}</tr>" for row in rows)
    return f"<table><thead><tr>{cells('th', headers)}</tr></thead><tbody>{body}</tbody></table>"


def render_html(report):
    summary = report["summary"]
    final = report["conclusions_are_final"]
    conclusions = table(
        VERDICTS.values(),
        [[summary["verdicts"].get(key, 0) for key in VERDICTS]],
    )
    states = summary["states"]
    execution = table(
        ("执行状态", "用例数"),
        [(STATES.get(key, key), count) for key, count in states.items()],
    )
    cases = table(
        (
            "编号",
            "用例",
            "执行状态",
            "首跑结论",
            "最终结论" if final else "最新结论",
            "尝试次数",
            "说明",
        ),
        [
            (
                case["code"],
                case["case_name"],
                STATES.get(case["state"], case["state"]),
                VERDICTS[case["first_verdict"]],
                VERDICTS[case["verdict"]],
                case["attempts"],
                case["reason"] or "—",
            )
            for case in report["cases"]
        ],
    )
    reason = f"<p>{escape(report['reason'])}</p>" if report["reason"] else ""
    task_name = escape(report["task_name"])
    state = escape(STATES.get(report["state"], report["state"]))
    trigger = {"scheduled": "定时", "manual": "手动", "legacy": "历史手动"}
    return f"""<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{report["batch_code"]} 测试报告</title>
<style>
body {{ max-width: 1100px; margin: 32px auto; padding: 0 16px; font: 15px/1.6 system-ui, sans-serif; color: #253047; }}
h2 {{ margin-top: 24px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ padding: 7px 10px; border: 1px solid #dce2eb; text-align: left; overflow-wrap: anywhere; }}
th {{ background: #f2f5fa; }}
td {{ white-space: pre-wrap; }}
tr {{ break-inside: avoid; }}
.table-wrap {{ overflow-x: auto; }}
@media print {{ body {{ margin: 0; padding: 0; }} .table-wrap {{ overflow: visible; }} }}
</style>
<h1>批次测试报告</h1>
<p>{task_name} · {report["batch_code"]}</p>
<p>{state} · {trigger.get(report["trigger"], "其他")}触发 · 已结束 {report["finished"]} / {report["total"]} 项</p>
<p>批次创建时间 {report["created_at"]} · 导出时间 {report["exported_at"]}（UTC）</p>
{reason}
<h2>结果汇总</h2>
<p>{"批次已结束，按最终结论汇总。" if final else "批次尚未结束，本报告记录导出时的最新结论。"}</p>
{conclusions}
<p>首跑通过 {summary["first_pass"]} 项 · 已重试 {summary["retried"]} 项 · 重试后通过 {summary["passed_after_retry"]} 项 · 共 {summary["attempts"]} 条尝试记录</p>
<h2>执行状态分布</h2>
{execution if states else "<p>本批次没有用例记录。</p>"}
<h2>用例结果（{len(report["cases"])}）</h2>
<div class="table-wrap">{cases if report["cases"] else "<p>本批次没有用例记录。</p>"}</div>
</html>
"""


def export(batch: BatchReport, format: str):
    now = datetime.now(timezone.utc)
    code = f"RUN-{str(batch.id)[:8].upper()}"
    snapshot = {
        **batch.model_dump(mode="json", exclude={"task_snapshot"}),
        "batch_code": code,
        "task_name": (batch.task_snapshot or {}).get("name") or "历史单用例执行",
        "exported_at": now.isoformat(timespec="seconds"),
        "conclusions_are_final": batch.state in TERMINAL,
    }
    document = (
        render_html(snapshot)
        if format == "html"
        else json.dumps(snapshot, ensure_ascii=False, indent=2)
    ).encode("utf-8")
    filename = f"{code}-{now:%Y%m%dT%H%M%SZ}-{uuid4().hex[:12]}.{format}"
    folder = Path(os.getenv("REPORT_EXPORT_DIR", ".runtime/report-exports")) / str(batch.id)
    folder.mkdir(parents=True, exist_ok=True)
    # The share only exposes completed files, even when several users export concurrently.
    with NamedTemporaryFile(dir=folder, prefix=".report-", delete=False) as temporary:
        path = Path(temporary.name)
        try:
            temporary.write(document)
            temporary.close()
            path.replace(folder / filename)
        finally:
            path.unlink(missing_ok=True)
    return filename, document
