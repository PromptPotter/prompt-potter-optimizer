from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from promptpotter.domain.phases import StopOutcome


@dataclass
class CommandResult:
    data: dict[str, Any] | None = None
    human: str | None = None
    outcome: StopOutcome | None = None
