from typing import Annotated, Literal

from pydantic import Field, FiniteFloat

from platform_contracts.models import WireModel

FrameDigest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Coordinate = Annotated[float, Field(ge=0, le=1000)]
StepDecision = Literal["passed", "failed", "skipped", "inconclusive", "unchecked"]
MAX_STEP_FRAME_BYTES = 1024 * 1024


class StepCheck(WireModel):
    status: StepDecision
    criterion: str = Field(default="", max_length=800)
    evidence: str = Field(default="", max_length=1200)


class ExecutionStep(WireModel):
    number: int
    timestamp: FiniteFloat | None = None
    action: str = Field(default="", max_length=600)
    status: str = Field(default="", max_length=100)
    tools: list[Annotated[str, Field(max_length=160)]] = Field(default_factory=list, max_length=12)
    before: FrameDigest | None = None
    after: FrameDigest | None = None
    paths: list[
        Annotated[list[tuple[Coordinate, Coordinate]], Field(min_length=1, max_length=16)]
    ] = Field(default_factory=list, max_length=8)
    instruction: str = Field(default="", max_length=1200)
    observation: str = Field(default="", max_length=1200)
    validation: StepDecision | None = None
    validation_note: str = Field(default="", max_length=500)
    assertions: list[StepCheck] = Field(default_factory=list, max_length=12)


class ExecutionReport(WireModel):
    session_status: str = Field(default="", max_length=100)
    case_result: str = Field(default="", max_length=16000)
    checks: list[Annotated[str, Field(max_length=1000)]] = Field(
        default_factory=list, max_length=40
    )
    steps: list[ExecutionStep] = Field(default_factory=list)
    startup_log: str = Field(default="", max_length=16000)
    truncated: bool = False
    visual_version: Literal[1] | None = None
