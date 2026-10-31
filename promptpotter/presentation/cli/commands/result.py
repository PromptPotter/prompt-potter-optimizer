from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CommandResult:
    data: dict[str, Any] | None = None
    human: str | None = None
    # What the process exits: all a shell, a deploy script or a spawning server reads.
    exit_code: int = 0
