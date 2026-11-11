from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from platform_contracts.models import CaseContent, DeviceDiagnostics, Verdict, WireModel


class TaskSchedule(WireModel):
    enabled: bool = False
    frequency: Literal["daily", "weekly"] = "daily"
    time: str = Field(default="02:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone: str = "Asia/Taipei"
    weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4], max_length=7)

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("请填写有效的 IANA 时区，例如 Asia/Taipei") from None
        return value

    @model_validator(mode="after")
    def valid_days(self):
        if any(day < 0 or day > 6 for day in self.weekdays) or len(set(self.weekdays)) != len(
            self.weekdays
        ):
            raise ValueError("星期必须唯一且在 0（周一）至 6（周日）之间")
        if self.frequency == "weekly" and not self.weekdays:
            raise ValueError("每周执行需至少选择一天")
        return self


class TaskConfiguration(WireModel):
    target_device_id: UUID | None = None
    model_config_id: UUID | None = None
    schedule: TaskSchedule = Field(default_factory=TaskSchedule)
    execution_order: Literal["parallel", "serial"] = "parallel"
    max_parallel: int = Field(default=4, ge=1, le=60)
    require_flash: bool = False
    flash_source: str = Field(default="", max_length=1000)
    max_retries: int = Field(default=0, ge=0, le=10)
    retry_interval_seconds: int = Field(default=30, ge=0, le=86400)

    @model_validator(mode="after")
    def flash_configuration(self):
        self.flash_source = self.flash_source.strip()
        if self.require_flash and not self.flash_source:
            raise ValueError("需要刷机时请填写内网刷机包来源")
        # Secrets belong to source credentials, not a reusable task definition.
        if (
            "://" in self.flash_source
            and "@" in self.flash_source.split("://", 1)[1].split("/", 1)[0]
        ):
            raise ValueError("刷机包来源不能包含账号或密码，请使用不带凭据的地址")
        return self


class CreateTask(WireModel):
    request_id: UUID
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        if not value.strip():
            raise ValueError("任务名称不能为空")
        return value.strip()


class SaveTask(WireModel):
    expected_revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    case_ids: list[UUID] = Field(default_factory=list, max_length=2000)
    configuration: TaskConfiguration

    @model_validator(mode="after")
    def valid_definition(self):
        self.name = self.name.strip()
        if not self.name or len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("任务名称不能为空，用例不能重复")
        if self.configuration.schedule.enabled and not self.case_ids:
            raise ValueError("请先添加用例，再启用定时执行")
        return self


class TriggerTask(WireModel):
    request_id: UUID
    expected_revision: int = Field(ge=1)


class TaskRecord(WireModel):
    id: UUID
    name: str
    description: str
    revision: int
    configuration: TaskConfiguration
    next_trigger_at: datetime | None
    created_at: datetime
    updated_at: datetime
    case_count: int


class TaskPage(WireModel):
    items: list[TaskRecord]
    total: int


class TaskCase(WireModel):
    id: UUID
    code: str
    name: str
    position: int
    latest_content: CaseContent | None
    stable_version_id: UUID | None
    stable_ready: bool


class TaskDetail(TaskRecord):
    cases: list[TaskCase]


class TaskAttempt(WireModel):
    id: UUID
    case_run_id: UUID
    number: int
    state: str
    version_id: UUID | None
    snapshot: CaseContent | None
    verdict: str
    cleanup: str
    evidence: str
    reason: str
    simulation: bool
    created_at: datetime
    eligible_at: datetime
    finished_at: datetime | None
    diagnostics: DeviceDiagnostics | None = None


class BatchCase(WireModel):
    case_id: UUID
    case_name: str
    code: str
    position: int
    attempts: list[TaskAttempt]


class ExecutionBatch(WireModel):
    id: UUID
    task_id: UUID | None
    task_revision: int | None
    task_snapshot: dict | None
    trigger: str
    trigger_at: datetime | None
    created_at: datetime
    state: str
    reason: str
    total: int
    finished: int
    cancel_requested: bool


class BatchDetail(ExecutionBatch):
    cases: list[BatchCase]


class ReportPage(WireModel):
    items: list[ExecutionBatch]
    total: int


class ReportCase(WireModel):
    case_id: UUID
    code: str
    case_name: str
    state: str
    first_verdict: Verdict
    verdict: Verdict
    attempts: int
    attempt_id: UUID | None
    version_id: UUID | None
    reason: str


class ReportSummary(WireModel):
    verdicts: dict[Verdict, int]
    states: dict[str, int]
    first_pass: int
    retried: int
    passed_after_retry: int
    attempts: int


class BatchReport(ExecutionBatch):
    summary: ReportSummary
    cases: list[ReportCase]
