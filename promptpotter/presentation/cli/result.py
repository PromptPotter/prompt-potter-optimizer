"""Shared ``CommandResult`` type — what every ``cmd_*`` returns.

Kept in its own module so every command file (and ``main()``) can import it
without pulling in the full campaign_runner dispatch module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CommandResult:
    """What every ``cmd_*`` returns. ``main()`` picks JSON or human output.

    - ``data`` — machine-readable payload (printed under ``--json`` or when no
      ``human`` form exists, so machine-only commands can leave ``human=None``).
    - ``human`` — pre-rendered text for the interactive terminal. Multi-line OK.
    """

    data: dict[str, Any] | None = None
    human: str | None = None
