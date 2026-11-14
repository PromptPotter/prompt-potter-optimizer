from __future__ import annotations

import argparse

from promptpotter.application.caller import Caller
from promptpotter.application.evidence.read import Evidence
from promptpotter.application.read_params import EvidenceParams
from promptpotter.application.views.evidence import evidence_lines
from promptpotter.presentation.cli.commands.result import CommandResult


def evidence_human(
    _params: EvidenceParams, ev: Evidence, args: argparse.Namespace, _caller: Caller
) -> CommandResult:
    return CommandResult(data=ev.model_dump(), human="\n".join(evidence_lines(ev, top=args.top)))
