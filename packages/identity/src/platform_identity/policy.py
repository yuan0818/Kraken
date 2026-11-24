"""Human access policy is separate from node machine authentication."""

import os

from pydantic import BaseModel


class Subject(BaseModel):
    id: str
    authentication: str
    role: str


def current_subject() -> Subject:
    if os.getenv("AUTH_MODE", "open") != "open":
        raise RuntimeError(
            "Custom authentication requires an identity adapter supplied by the deployer"
        )
    return Subject(id="anonymous", authentication="open", role="admin")


def authorize(subject: Subject, action: str) -> None:
    if subject.role != "admin":
        raise PermissionError(action)
