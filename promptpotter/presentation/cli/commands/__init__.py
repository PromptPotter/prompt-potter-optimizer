"""Per-subcommand entry-point modules.

`campaign_runner.py` is the thin facade that wires the COMMANDS table and
`main()`. The per-command bodies (``cmd_new``, ``cmd_resume``,
``cmd_sweep``, ``cmd_compare``, ``cmd_reset``) live here.
"""

from __future__ import annotations

from promptpotter.presentation.cli.commands.compare import cmd_compare
from promptpotter.presentation.cli.commands.new import cmd_new
from promptpotter.presentation.cli.commands.reset import cmd_reset
from promptpotter.presentation.cli.commands.resume import cmd_resume
from promptpotter.presentation.cli.commands.sweep import cmd_sweep
from promptpotter.presentation.cli.commands.verify import cmd_verify

__all__ = ["cmd_compare", "cmd_new", "cmd_reset", "cmd_resume", "cmd_sweep", "cmd_verify"]
