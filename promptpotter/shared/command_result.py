"""What a command handler answers its door: the payload, the terminal text, the exit code."""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass
class CommandResult:
    data: dict[str, Any] | None = None
    human: str | None = None
    # What the process exits: all a shell, a deploy script or a spawning server reads.
    exit_code: int = 0
