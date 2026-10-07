"""``cmd_verify`` — re-score one campaign candidate on MORE search cells. Not a cycle or a fork: no round id, and the pass
is banked on the candidate's own cycle ledger."""

from __future__ import annotations

import argparse
import logging

from promptpotter.application.diagnostics.verify import VerifyError, verify_candidate
from promptpotter.application.evidence.subjects import parse_subject
from promptpotter.config.logging import setup_logging
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.domain.bench import BandedValue
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.infrastructure.store.stores import build_stores, descend_store
from promptpotter.presentation.cli.commands._shared import (
    CommandResult,
    get_verbose,
    identity_from_args,
    resolve_campaign_hint,
)

logger = logging.getLogger("promptpotter.presentation.cli")


def _level(level: BandedValue | None) -> str:
    if level is None:
        return "—"
    if level.ci_lo is None or level.ci_hi is None:
        return f"{level.value:.3f}"
    return f"{level.value:.3f} [{level.ci_lo:.3f}, {level.ci_hi:.3f}]"


async def cmd_verify(args: argparse.Namespace) -> CommandResult:
    """Re-score a campaign candidate on N unseen cells; bank the pass and print its reading."""

    setup_logging(style="full" if get_verbose() else "cli")
    identity = identity_from_args(args)
    try:
        spec = parse_subject(args.subject)
    except ValueError as exc:
        raise SystemExit(f"ERROR: {exc}") from None
    if spec.kind != "candidate" or spec.lens or spec.samples:
        raise SystemExit(
            "ERROR: verify takes one searchpoint and no mask: "
            "`candidate:<campaign>/<cycle>/<candidate_id>[;in=<c::y~…>]`."
        )
    stores = descend_store(build_stores(identity, projects_root=DEFAULT_PROJECTS_ROOT), spec.inside)
    hop = CycleHop(
        campaign_id=resolve_campaign_hint(stores, spec.campaign_id), cycle_id=spec.cycle_id
    )

    try:
        outcome = await verify_candidate(
            stores=stores,
            identity=identity,
            hop=hop,
            candidate_id=spec.candidate_id,
            samples=args.samples,
            strategy=args.strategy,
            seed=args.seed,
            log=logger.info if get_verbose() else None,
        )
    except VerifyError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc

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
        f"{reading.n_recorded} cells → {_level(reading.fresh.accuracy)} on {reading.n_fresh} "
        f"unseen ({reading.strategy}){verdict}",
        f"  composite {_level(reading.recorded.composite)} → {_level(reading.fresh.composite)}",
    ]
    if reading.lift.accuracy is not None:
        lines.append(
            f"  lift over C0, paired on {reading.n_shared} shared cells: "
            f"accuracy {_level(reading.lift.accuracy)}, composite {_level(reading.lift.composite)}"
        )
    return CommandResult(data=reading.model_dump(mode="json"), human="\n".join(lines))


__all__ = ["cmd_verify"]
