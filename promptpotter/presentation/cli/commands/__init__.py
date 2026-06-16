"""Per-subcommand entry-point modules.

`campaign_runner.py` is the thin facade that wires the COMMANDS table and
`main()`. The per-command bodies live here.
"""

from __future__ import annotations

from promptpotter.presentation.cli.commands.compare import cmd_compare
from promptpotter.presentation.cli.commands.lifecycle import cmd_archive, cmd_delete, cmd_unarchive
from promptpotter.presentation.cli.commands.new import cmd_new
from promptpotter.presentation.cli.commands.reset import cmd_reset
from promptpotter.presentation.cli.commands.resume_command import cmd_resume
from promptpotter.presentation.cli.commands.sweep import cmd_sweep
from promptpotter.presentation.cli.commands.verify import cmd_verify

__all__ = [
    "cmd_archive",
    "cmd_compare",
    "cmd_delete",
    "cmd_new",
    "cmd_reset",
    "cmd_resume",
    "cmd_sweep",
    "cmd_unarchive",
    "cmd_verify",
]
