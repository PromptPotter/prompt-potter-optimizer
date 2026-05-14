"""Per-subcommand entry-point modules.

`campaign_runner.py` is the thin facade that wires the COMMANDS table and
`main()`. The per-command bodies (``cmd_optimize``, ``cmd_sweep``,
``cmd_compare``, plus the fresh-init shell ``_run_init_body``) live here.
"""

from __future__ import annotations

from promptpotter.presentation.cli.commands.compare import cmd_compare
from promptpotter.presentation.cli.commands.optimize import cmd_optimize
from promptpotter.presentation.cli.commands.sweep import cmd_sweep

__all__ = ["cmd_compare", "cmd_optimize", "cmd_sweep"]
