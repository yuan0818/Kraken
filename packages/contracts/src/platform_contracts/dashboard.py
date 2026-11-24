"""Dashboard read models. Rates use latest verdict per case in a batch."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel


class DashboardDay(BaseModel):
    date: date
    total: int = 0
    passed: int = 0
    evaluated: int = 0


class DashboardIssue(BaseModel):
    run_id: UUID
    case_id: UUID | None = None
    attempt_id: UUID | None = None
    name: str
    state: str
    verdict: str
    reason: str


class DashboardBatch(BaseModel):
    id: UUID
    name: str
    created_at: datetime
    state: str
    total: int
    passed: int


class Dashboard(BaseModel):
    generated_at: datetime
    start_date: date
    timezone: str
    batches: int
    batch_states: dict[str, int]
    cases: int
    verdicts: dict[str, int]
    evaluated: int
    pass_rate: float | None
    first_pass: int
    retried: int
    attempts: int
    anr: int
    crash: int
    diagnostics_complete: int
    diagnostics_total: int
    diagnostics_truncated: int
    trend: list[DashboardDay]
    issues: list[DashboardIssue]
    recent: list[DashboardBatch]
