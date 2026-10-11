from __future__ import annotations

import argparse
import uuid

from pydantic import ValidationError

from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.commands.payloads import VerifyCandidatePayload
from promptpotter.application.diagnostics.verify import VerifyError, VerifyOutcome
from promptpotter.application.evidence.subjects import parse_subject
from promptpotter.application.views.render.primitives import fmt_ci
from promptpotter.config.logging import setup_logging
from promptpotter.domain.bench import BandedValue
from promptpotter.domain.paired_reading import READING_STATE_INFO
from promptpotter.domain.results import BankedSearchPointError
from promptpotter.infrastructure.store.stores import descend_store
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import open_stores, resolve_campaign_hint


def _level(level: BandedValue | None) -> str:
    if level is None:
        return "—"
    return f"{level.value:.3f} {fmt_ci(level.ci_lo, level.ci_hi, spec='{:.3f}')}"


async def cmd_verify(args: argparse.Namespace) -> CommandResult:
    setup_logging(style="full" if args.verbose else "cli")
    try:
        spec = parse_subject(args.subject)
    except ValueError as exc:
        raise SystemExit(f"ERROR: {exc}") from None
    if spec.kind != "candidate" or spec.lens or spec.samples:
        raise SystemExit(
            "ERROR: verify takes one searchpoint and no mask: "
            "`candidate:<campaign>/<cycle>/<candidate_id>[;in=<c::y~…>]`."
        )
    stores = descend_store(open_stores(args), spec.inside)

    try:
        dispatched = await CommandDispatcher(stores).dispatch_cycle_command(
            CommandCall(
                VerifyCandidatePayload(
                    campaign_id=resolve_campaign_hint(stores, spec.campaign_id),
                    cycle_id=spec.cycle_id,
                    candidate_id=spec.candidate_id,
                    samples=args.samples,
                    strategy=args.strategy,
                    seed=args.seed,
                ),
                uuid.uuid4().hex,
            ),
            expected_version=None,
        )
    except (VerifyError, BankedSearchPointError, ValidationError) as exc:
        raise SystemExit(f"ERROR: {exc}") from exc

    outcome = dispatched.result
    assert isinstance(outcome, VerifyOutcome), "a fresh key applies, and the applier answers one"
    reading = outcome.reading
    if reading is None:
        return CommandResult(
            human=(
                f"{spec.candidate_id}: every sample in the {outcome.dataset_name} search pool is "
                f"already measured for this config ({outcome.already_measured} total). "
                "Nothing to add."
            ),
        )

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
            f"{lift.measurand.key} {lift.estimate.value:+.3f} "
            f"[{lift.estimate.ci_lo:+.3f}, {lift.estimate.ci_hi:+.3f}]"
            for lift in (pair.headline, *pair.beside)
        )
        lines.append(f"  lift over C0, paired on {pair.coverage.scored} shared cells: {lifts}")
    return CommandResult(data=reading.model_dump(mode="json"), human="\n".join(lines))


__all__ = ["cmd_verify"]
