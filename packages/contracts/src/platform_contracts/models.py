"""v1 wire contract. No engine, database or transport dependencies."""

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExecutionState(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    STARTING = "starting"
    RUNNING = "running"
    CANCEL_PENDING = "cancel_pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    NEEDS_RECOVERY = "needs_recovery"


class Verdict(StrEnum):
    NOT_EVALUATED = "not_evaluated"
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"
    INCONCLUSIVE = "inconclusive"


class CaseContent(WireModel):
    schema_version: Literal[1, 2] = 1
    title: str = Field(min_length=1, max_length=200)
    target: Literal["app", "android_os"]
    steps: list[str] = Field(min_length=1, max_length=200)
    assertions: list[str] = Field(min_length=1, max_length=200)
    steps_markdown: str | None = Field(default=None, min_length=1, max_length=64000)
    assertions_markdown: str | None = Field(default=None, min_length=1, max_length=64000)
    required_tags: list[str] = Field(default_factory=list, max_length=100)
    case_code: str = ""
    purpose: str = Field(default="", max_length=4000)
    preconditions: list[str] = Field(default_factory=list, max_length=100)
    module_id: UUID | None = None
    app_id: UUID | None = None
    android_version_ids: list[UUID] = Field(default_factory=list, max_length=100)
    app_version_ids: list[UUID] = Field(default_factory=list, max_length=100)
    device_model_ids: list[UUID] = Field(default_factory=list, max_length=100)
    chip_platform_ids: list[UUID] = Field(default_factory=list, max_length=100)
    level: Literal["P0", "P1", "P2", "P3"] = "P2"
    status: Literal["draft", "active", "disabled"] = "active"
    catalog_snapshot: dict[str, str] = Field(default_factory=dict)

    @model_serializer(mode="wrap")
    def omit_absent_markdown(self, handler):
        content = handler(self)
        if self.steps_markdown is None:
            content.pop("steps_markdown", None)
        if self.assertions_markdown is None:
            content.pop("assertions_markdown", None)
        return content

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value):
        if not value.strip():
            raise ValueError("版本标题不能为空")
        return value.strip()

    @field_validator("steps", "assertions", "preconditions", "required_tags")
    @classmethod
    def nonblank_lines(cls, value):
        if any(not line.strip() for line in value):
            raise ValueError("内容不能包含空白项")
        return [line.strip() for line in value]

    @model_validator(mode="after")
    def valid_metadata(self):
        if self.target == "app" and (self.chip_platform_ids or self.android_version_ids):
            raise ValueError("App 用例不适用 Android 版本或芯片平台")
        if self.target == "android_os" and (self.app_id or self.app_version_ids):
            raise ValueError("Android OS 用例不能关联 App")
        if self.schema_version == 2:
            if not self.purpose.strip() or not self.module_id:
                raise ValueError("请填写测试目的并选择模块")
            if self.target == "app" and not self.app_id:
                raise ValueError("App 用例必须选择 App")
        return self


class CreateCase(WireModel):
    name: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value):
        if not value.strip():
            raise ValueError("用例名称不能为空")
        return value.strip()


CatalogKind = Literal[
    "system_module",
    "app_module",
    "app_version",
    "application",
    "android_version",
    "device_model",
    "chip_platform",
]


class CatalogWrite(WireModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value):
        if not value.strip():
            raise ValueError("名称不能为空")
        return value.strip()


class CatalogCreate(CatalogWrite):
    kind: CatalogKind
    parent_id: UUID | None = None

    @model_validator(mode="after")
    def valid_parent(self):
        if (self.kind in {"app_module", "app_version"}) != (self.parent_id is not None):
            raise ValueError("App 模块和 App 版本需要指定所属 App")
        return self


class StableSelection(WireModel):
    version_id: UUID
    expected_version_id: UUID | None


class SubmitRun(WireModel):
    case_id: UUID
    request_id: UUID
    artifacts: dict[str, str] = Field(default_factory=dict)


class NodeRegistration(WireModel):
    protocol_version: Literal[1] = 1
    node_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    capabilities: list[Literal["simulator"]] = ["simulator"]
    device_serials: list[str] = Field(default_factory=lambda: ["SIM-001"], max_length=100)
    tags: list[str] = Field(default_factory=lambda: ["simulator"])


class AdbObservation(WireModel):
    node_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    serial: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$")
    state: Literal[
        "device", "absent", "offline", "unauthorized", "no_permissions", "other", "probe_error"
    ]
    model: str | None = Field(default=None, max_length=200)
    product: str | None = Field(default=None, max_length=200)
    artemis_state: Literal["ready", "busy", "unavailable"] | None = None

    @field_validator("serial")
    @classmethod
    def physical_serial(cls, value):
        if value.upper().startswith("SIM-"):
            raise ValueError("模拟设备不能作为 ADB 真机登记")
        return value


class ExecutionModelRef(WireModel):
    config_id: UUID
    revision: int = Field(ge=1)


class Command(WireModel):
    protocol_version: Literal[1] = 1
    command_id: UUID
    attempt_id: UUID
    node_id: str
    device_id: UUID
    serial: str
    lease_generation: int = Field(gt=0)
    kind: Literal["prepare", "execute", "cancel"]
    expires_at: AwareDatetime
    content: CaseContent | None = None
    version_id: UUID | None = None
    simulation: bool = True
    execution_model: ExecutionModelRef | None = None


MAX_DEVICE_INCIDENTS = 100


class DeviceIncident(WireModel):
    id: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: Literal["anr", "crash"]
    device_at: AwareDatetime
    occurred_at: AwareDatetime
    process: str = Field(min_length=1, max_length=256)
    pid: int = Field(ge=0)
    detail: str = Field(max_length=1200)
    raw: str = Field(max_length=2500)


class DeviceDiagnostics(WireModel):
    serial: str = Field(min_length=1, max_length=256)
    status: Literal["collecting", "complete", "partial", "unavailable"]
    reason: str = Field(default="", max_length=300)
    incidents: list[DeviceIncident] = Field(default_factory=list, max_length=MAX_DEVICE_INCIDENTS)
    truncated: bool = False


class AttemptEvent(WireModel):
    protocol_version: Literal[1] = 1
    command_id: UUID
    attempt_id: UUID
    lease_generation: int = Field(gt=0)
    kind: Literal[
        "accepted", "ready", "started", "finished", "cancelled", "rejected", "diagnostics"
    ]
    cleanup: Literal["pending", "verified", "failed"] = "pending"
    evidence: Literal["missing", "partial", "complete"] = "missing"
    verdict: Verdict = Verdict.NOT_EVALUATED
    reason: str = ""
    simulation: bool = True

    diagnostics: DeviceDiagnostics | None = None

    @model_validator(mode="after")
    def diagnostic_payload(self):
        if (self.kind == "diagnostics") != (self.diagnostics is not None):
            raise ValueError("Device diagnostics are required only for diagnostics events")
        return self


class NodeEvent(AttemptEvent):
    seq: int = Field(gt=0)


class NodeUpdate(AttemptEvent):
    event_id: UUID
    legacy_seq: int | None = Field(default=None, gt=0)


class OpenRuntime(WireModel):
    boot_id: UUID


class ArtifactManifest(WireModel):
    artifact_id: UUID
    attempt_id: UUID
    relative_path: str = Field(pattern=r"^[a-zA-Z0-9_/-]+\.[a-zA-Z0-9]+$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str

    @field_validator("relative_path")
    @classmethod
    def safe_relative_path(cls, value):
        from pathlib import PurePosixPath

        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Artifact path must remain inside the attempt workspace")
        return value


class ProtocolError(WireModel):
    error: Literal[
        "stale_connection",
        "out_of_order",
        "event_conflict",
        "unknown_command",
        "lease_mismatch",
        "expired",
        "unsupported_version",
        "invalid_transition",
    ]
    detail: str = ""
    expected_seq: int | None = None
