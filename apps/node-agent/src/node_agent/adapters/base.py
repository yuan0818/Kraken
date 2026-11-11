"""Adapter boundary; implementation is scheduled in ENG-01 and FLA-02."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class ExecutionRef:
    attempt_id: UUID
    device_serial: str
    adb_endpoint: str
    lease_generation: int
    workspace: str


class ExecutionAdapter(Protocol):
    def capabilities(self) -> dict: ...
    def submit(self, execution: ExecutionRef, content: dict) -> str: ...
    def inspect(self, external_id: str) -> dict: ...
    def request_cancel(self, external_id: str) -> dict: ...


class UnverifiedHardwareAdapter:
    def capabilities(self):
        return {"available": False, "reason": "Hardware adapter not integrated in M0"}

    def submit(self, *args, **kwargs):
        raise RuntimeError("Hardware execution requires the validated ENG/FLA adapter")
