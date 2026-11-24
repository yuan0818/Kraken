from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from platform_contracts.models import CaseContent, WireModel

ImportField = Literal[
    "code",
    "name",
    "target",
    "module",
    "app",
    "purpose",
    "preconditions",
    "steps",
    "assertions",
    "android_versions",
    "app_versions",
    "device_models",
    "chip_platforms",
    "level",
    "status",
    "required_tags",
    "title",
]


class ImportPreviewRequest(WireModel):
    request_id: UUID
    upload_id: UUID
    source_key: str = Field(min_length=1, max_length=160)
    sheet: str = Field(min_length=1, max_length=100)
    header_row: int = Field(default=1, ge=1, le=20)
    mapping: dict[ImportField, int]
    default_target: Literal["android_os", "app"] = "android_os"

    @field_validator("source_key")
    @classmethod
    def nonblank_source(cls, value):
        if not value.strip():
            raise ValueError("请填写固定的来源名称，后续导入使用同一个名称")
        return value.strip()

    @model_validator(mode="after")
    def valid_mapping(self):
        if not {"code", "module", "purpose", "steps", "assertions"} <= self.mapping.keys():
            raise ValueError("请映射编号、模块、测试目的、步骤和预期结果")
        indexes = list(self.mapping.values())
        if len(indexes) != len(set(indexes)) or any(i < 0 or i >= 50 for i in indexes):
            raise ValueError("每列只能映射一个字段，列索引范围为 0–49")
        return self


class ImportAcceptance(WireModel):
    rows: list[int] = Field(min_length=1, max_length=2000)
    acknowledged_conflicts: list[int] = Field(default_factory=list, max_length=2000)

    @model_validator(mode="after")
    def unique_rows(self):
        if len(set(self.rows)) != len(self.rows) or not set(self.acknowledged_conflicts) <= set(
            self.rows
        ):
            raise ValueError("采纳行不可重复，冲突确认必须属于选中行")
        self.rows.sort()
        self.acknowledged_conflicts = sorted(set(self.acknowledged_conflicts))
        return self


class ImportSheet(WireModel):
    name: str
    row_count: int
    sample_rows: list[list[str | int | float | bool | None]]


class ImportUpload(WireModel):
    id: UUID
    filename: str
    sha256: str
    sheets: list[ImportSheet]
    warnings: list[str]


class ImportRow(WireModel):
    row_number: int
    code: str
    name: str
    action: Literal["new", "update", "unchanged", "link_required", "conflict", "invalid"]
    errors: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    case_id: UUID | None = None
    expected_latest_version_id: UUID | None = None
    source_digest: str = ""
    previous_source_digest: str | None = None
    content: CaseContent | None = None
    before: CaseContent | None = None
    changed_fields: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class ImportBatch(WireModel):
    id: UUID
    state: str
    source: str
    source_identity: dict[str, Any]
    configuration: dict[str, Any]
    preview: list[ImportRow]
    result: dict[str, Any] | None
    created_at: datetime
