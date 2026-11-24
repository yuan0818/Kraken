from typing import Literal

from pydantic import Field, FiniteFloat

from platform_contracts.models import WireModel

MAX_QUALITY_MEDIA_BYTES = 32 * 1024 * 1024


class QualityEvent(WireModel):
    kind: str = Field(max_length=80)
    start_seconds: FiniteFloat = Field(ge=0)
    end_seconds: FiniteFloat = Field(ge=0)
    score: FiniteFloat | None = Field(default=None, ge=0, le=1)


class QualityCheck(WireModel):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    tool: Literal["check_visual_quality", "check_audio_quality"]
    criterion: str = Field(max_length=800)
    status: Literal["completed", "inconclusive", "unavailable", "error"]
    verdict: Literal["passed", "failed", "inconclusive"]
    detector: Literal["FrameGuard", "AudioGuard"]
    model_sha256: str = Field(default="", pattern=r"^([a-f0-9]{64})?$")
    policy: Literal["advisory", "enforced"]
    checks: list[str] = Field(max_length=10)
    events: list[QualityEvent] = Field(default_factory=list, max_length=300)
    reason: str = Field(default="", max_length=600)
    evidence_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    evidence_type: Literal["image/jpeg", "video/mp4"] | None = None
    evidence_available: bool = False
    start_seconds: FiniteFloat = Field(ge=0)
    end_seconds: FiniteFloat = Field(ge=0)
    sample_interval_seconds: FiniteFloat | None = None
    analyzed_seconds: FiniteFloat = Field(default=0, ge=0)
    threshold: FiniteFloat | None = None
    step_number: int | None = None
    created_at: FiniteFloat
