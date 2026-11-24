from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator

from platform_contracts.models import WireModel

ThinkingEffort = Literal["default", "none", "minimal", "low", "medium", "high", "xhigh", "max"]


class ModelDefinition(WireModel):
    model_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    api_schema: Literal["openai", "anthropic"]
    url: str = Field(min_length=1, max_length=2000)
    provider: str = Field(min_length=1, max_length=200)
    context_window: int = Field(ge=1024, le=10000000)
    thinking_effort: ThinkingEffort = "default"

    @field_validator("model_id", "name", "provider")
    @classmethod
    def nonempty(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("名称、ID 和供应商不能为空或包含控制字符")
        return value

    @field_validator("url")
    @classmethod
    def endpoint(cls, value):
        value = value.strip().rstrip("/")
        try:
            parsed = urlsplit(value)
            valid = parsed.scheme in {"http", "https"} and parsed.hostname and parsed.port != 0
        except ValueError:
            valid = False
        if not valid or any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value:
            raise ValueError("URL 必须是有效的 HTTP/HTTPS API 基础地址")
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("URL 不能包含账号、密码、查询参数或片段；凭据请填写 Key")
        if parsed.path.endswith(("/chat/completions", "/messages", "/responses")):
            raise ValueError(
                "请填写 API 基础地址，例如 https://service.example/v1，而非完整请求路径"
            )
        return value

    @model_validator(mode="after")
    def effort(self):
        allowed = {"default", "none", "low", "medium", "high"}
        allowed |= {"minimal", "xhigh"} if self.api_schema == "openai" else {"max"}
        if self.thinking_effort not in allowed:
            raise ValueError("当前 API schema 不支持所选思考强度")
        return self


class ModelCreate(ModelDefinition):
    api_key: SecretStr = Field(default=SecretStr(""), max_length=8192)

    @field_validator("api_key")
    @classmethod
    def key_header(cls, value):
        if any(ord(c) < 32 or ord(c) > 126 for c in value.get_secret_value()):
            raise ValueError("Key 不能包含换行或非 ASCII 字符")
        return value


class ModelUpdate(ModelCreate):
    expected_revision: int = Field(ge=1)
    # null/omitted retains the credential; empty explicitly removes it.
    api_key: SecretStr | None = Field(default=None, max_length=8192)

    @field_validator("api_key")
    @classmethod
    def key_header(cls, value):
        return ModelCreate.key_header(value) if value is not None else None


class ModelSelection(WireModel):
    expected_revision: int = Field(ge=1)


class ModelAvailability(ModelSelection):
    enabled: bool


class ModelCheck(WireModel):
    id: UUID
    config_id: UUID
    revision: int
    location: Literal["control_plane"]
    status: Literal["passed", "failed"]
    detail: str
    latency_ms: int
    http_status: int | None
    checked_at: datetime


class ModelRecord(ModelDefinition):
    id: UUID
    revision: int
    enabled: bool
    has_key: bool
    created_at: datetime
    updated_at: datetime
    last_check: ModelCheck | None = None


class ModelVersion(ModelDefinition):
    revision: int
    has_key: bool
    created_at: datetime
