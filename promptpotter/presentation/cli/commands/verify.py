from __future__ import annotations

import argparse

from promptpotter.application.commands import measuring
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.views.render.primitives import fmt_ci
from promptpotter.config.logging import setup_logging
from promptpotter.domain.arm_reading import lift_band_text
from promptpotter.domain.bench import BandedValue
from promptpotter.domain.command_kinds import VerifyCandidatePayload
from promptpotter.domain.cycle_paths import Subject
from promptpotter.domain.paired_reading import READING_STATE_INFO
from promptpotter.domain.run_records import VERIFY_WORDS, VerifySaturated
from promptpotter.domain.verify_pass import VerifyReading
from promptpotter.presentation.cli.commands.launch import ctrl_c_stops_pass
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import open_stores
from promptpotter.shared.errors import BadRequestError, PayloadInvalidError


def _level(level: BandedValue | None) -> str:
    if level is None:
        return "—"
    return f"{level.value:.3f} {fmt_ci(level.ci_lo, level.ci_hi, spec='{:.3f}')}"


async def cmd_verify(args: argparse.Namespace) -> CommandResult:
    setup_logging(style="full" if args.verbose else "cli")
    spec = Subject.parse(args.subject)
    if spec.kind != "candidate" or spec.lens or spec.samples:
        raise BadRequestError(
            "verify takes one searchpoint and no mask: "
            "`candidate:<campaign>/<cycle>/<candidate_id>[;in=<c::y~…>]`."
        )
    stores = open_stores(args)
    fields = {
        **spec.command_fields,
        "candidate_id": spec.candidate_id,
        "samples": args.samples,
        "strategy": args.strategy,
        "seed": args.seed,
    }
    payload = PayloadInvalidError.validating(VerifyCandidatePayload, fields)
    call = CommandCall(payload)
    with ctrl_c_stops_pass(stores, call):
        dispatched = await CommandDispatcher(stores).answer(measuring.verify_candidate, call)

    graded = dispatched.result
    if isinstance(graded, VerifySaturated):
        said = VERIFY_WORDS["saturated"].format(**graded.model_dump())
        return CommandResult(human=f"{spec.candidate_id}: {said}")
    reading = graded.reading
    assert isinstance(reading, VerifyReading), "a graded verify pass reads under its scorer"

    verdict = {True: " — held", False: " — DROPPED", None: ""}[reading.held]
    lines = [
        f"{reading.label}: accuracy {_level(reading.recorded.accuracy)} on its round's "
        f"{reading.recorded.n} cells → {_level(reading.fresh.accuracy)} on {reading.fresh.n} "
        f"unseen ({reading.strategy}){verdict}",
        f"  composite {_level(reading.recorded.composite)} → {_level(reading.fresh.composite)}",
    ]
    pair = reading.vs_origin
    if pair.headline is None or pair.coverage is None:
        lines.append(f"  lift over C0: {READING_STATE_INFO[pair.state].sentence}")
    else:
        lifts = ", ".join(
            f"{lift.measurand.key} {lift_band_text(lift.estimate, lift.measurand.unit)}"
            for lift in (pair.headline, *pair.beside)
        )
        lines.append(f"  lift over C0, paired on {pair.coverage.scored} shared cells: {lifts}")
    return CommandResult(data=reading.model_dump(mode="json"), human="\n".join(lines))
