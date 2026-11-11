import asyncio
import json
from contextlib import suppress
from uuid import uuid4

import httpx
from node_agent.adapters.execution_report import (
    _paths,
    _validation,
    collect_execution_report,
    upload_execution_reports,
)
from node_agent.spool import Spool
from platform_contracts.execution_reports import ExecutionReport


def test_preserves_startup_exception_without_credentials(tmp_path):
    attempt = uuid4()
    trace = tmp_path / str(attempt)
    trace.mkdir()
    (trace / "stdout.log").write_text(
        "Authorization: Bearer fixture-key\nModuleNotFoundError: missing gesture module\n"
    )
    report = collect_execution_report(None, attempt, traces_dir=tmp_path)
    assert "ModuleNotFoundError" in report.startup_log
    assert "fixture-key" not in report.startup_log
    assert collect_execution_report(None, uuid4(), traces_dir=tmp_path) is None


def test_reports_observed_steps_and_artemis_note():
    attempt = uuid4()

    class Native:
        def get(self, path):
            assert path == f"/api/sessions/{attempt}/steps"
            return [
                {
                    "step_number": 1,
                    "action_taken": [{"action": "tap", "target_resource_id": "settings:id/wifi"}],
                    "last_execution_result": {"status": "success"},
                    "operator_raw_thinking": "private reasoning",
                    "generic_tools": [
                        {"name": "tap", "status": "success", "payload": "private payload"}
                    ],
                }
            ]

    report = collect_execution_report(
        Native(),
        attempt,
        status="completed",
        notes={"notes": {"case_result.md": "Observed Wi-Fi settings", "other": "private note"}},
    )
    assert report.case_result == "Observed Wi-Fi settings"
    assert report.steps[0].action == "tap settings:id/wifi"
    assert report.steps[0].tools == ["tap · success"]
    assert "private" not in report.model_dump_json()


def test_visual_paths_follow_artemis_coordinates_and_bypassed_is_not_passed():
    assert _paths({"action": "tap", "normalized_coordinates": [272, 484]}, {}) == [[[272, 484]]]
    assert _paths(
        {
            "action": "perform_gesture",
            "phases": [{"pointers": [{"path": [[500, 850], [500, 620]]}]}],
        },
        {},
    ) == [[[500, 850], [500, 620]]]
    assert _validation(
        {"result": "(True, <ValidationErrorCategory.PIXEL_BYPASSED: 'pixel_bypassed'>, '')"}
    ) == ("skipped", "")
    assert _validation(
        {
            "result": "(False, <ValidationErrorCategory.TARGET_SHIFTED: 'target_shifted'>, 'Target moved')"
        }
    ) == ("failed", "Target moved")
    assert _validation({"result": "(True, <ValidationErrorCategory.NONE: 'none'>, '')"}) == (
        "passed",
        "",
    )
    assert _validation({"status": "success"}) == (None, "")


def test_report_outbox_upload_uses_its_original_command(tmp_path, monkeypatch):
    spool = Spool(tmp_path, "test")
    command, attempt = str(uuid4()), str(uuid4())
    spool.report(command, attempt, ExecutionReport(startup_log="missing gesture module"))

    async def run():
        uploaded = asyncio.Event()

        def handle(request):
            assert request.url.path == f"/api/nodes/test/execution-reports/{command}"
            assert json.loads(request.content)["startup_log"] == "missing gesture module"
            uploaded.set()
            return httpx.Response(204)

        real_client = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs),
        )
        worker = asyncio.create_task(
            upload_execution_reports(spool, "http://control", "test", "http://native")
        )
        try:
            await asyncio.wait_for(uploaded.wait(), timeout=1)
            assert not list((tmp_path / "reports").glob("*.json"))
        finally:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker

    asyncio.run(run())
