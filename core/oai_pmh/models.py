"""Value objects used by the OAI-PMH catalogue."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class OAIHeader:
    work_id: int
    identifier_id: int
    identifier_type: str
    identifier_value: str
    pool_id: int
    modified_at: datetime
    entered_at: datetime
