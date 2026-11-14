from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application.bench.node_context import restated_round
from promptpotter.application.bench.resume_and_fork.ab_replay import line_divergence
from promptpotter.application.bench.resume_and_fork.fork_siblings import ForkCut, cut_fork
from promptpotter.application.bench.round_analysis import mended_round
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.domain.arms import ArmState, Concluded, is_leader_eligible
from promptpotter.domain.cell_fold import repairable_holes
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.paired_reading import ArmKey, RoundArm
from promptpotter.domain.run_records import ForkDirection, ForkTrigger
from promptpotter.shared.identity import SYSTEM_PRINCIPAL
from promptpotter.shared.measurement_context import MeasurementRole

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.domain.results import RoundResult
    from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
        StandingRound,
        StandingRounds,
    )

logger = logging.getLogger(__name__)


def _holed(t: RoundResult, key: ArmKey, arm: ArmState) -> bool:
    return is_leader_eligible(arm) and repairable_holes(t.rows(key)) > 0


def _first_divergent_arm(t: RoundResult) -> ArmKey | None:
    return next((key for key, arm in t.panel.items() if _holed(t, key, arm)), None)


class RepairCut(NamedTuple):
    """The cut is an ARM, not a round: earlier siblings are untouched and stay on the line."""

    rounds: list[int]
    resume_at: int
    edge: RoundArm | None  # None ⇒ no arm before it, so the fork stands on its line's pick
    # Only what the BRANCH CARRIES (edge → end of the cut round); it regenerates the later ones.
    retirements: list[tuple[int, ArmKey]]


def repair_cut(prior: list[RoundResult]) -> RepairCut:
    rounds: list[int] = []
    resume_at, edge = 0, None
    retirements: list[tuple[int, ArmKey]] = []
    cutting = carrying = False
    for t in prior:
        diverged = _first_divergent_arm(t)
        if diverged is not None:
            rounds.append(t.round)
            if not cutting:
                resume_at, carrying = t.round + 1, True
        for key in t.panel:
            cutting = cutting or key == diverged
            if not cutting:
                edge = RoundArm(round=t.round, arm=key)
            elif carrying:
                retirements.append((t.round, key))
        carrying = False
    return RepairCut(rounds, resume_at, edge, retirements)


def _rebank_on_branch(
    cycle: Cycle,
    closes: Mapping[int, StandingRound],
    branch: CycleHop,
    rounds: list[RoundResult],
    retirements: list[tuple[int, ArmKey]],
) -> list[RoundResult]:
    session = cycle.session
    retired = {round_num for round_num, _ in retirements}
    restated: dict[int, RoundResult] = {}
    for t in rounds:
        if t.round not in retired:
            continue
        offset = session.store.campaigns.rebank_round(
            branch,
            restated_round(
                t,
                rounds,
                cycle.difficulty,
                lives=cycle.config.optimization.lives,
                close=closes[t.round].close,
                slot_of=session.bank.slot_of,
            ),
        )
        # The restatement MOVES the document's address; the retired one re-folds to the holed round.
        restated[t.round] = t.model_copy(update={"at_offset": offset})
    return [restated.get(t.round, t) for t in rounds]


async def repair_incomplete_rounds(
    prior: list[RoundResult], cycle: Cycle, dataset: list[Any]
) -> list[RoundResult]:
    """Writes nothing, to a ledger or to *prior*; an untouched round is the SAME object handed in."""

    session = cycle.session
    by_key = {s.key: s for s in dataset}
    corrected = list(prior)
    for slot, t in enumerate(prior):
        cells = dict(t.cells)
        folds = dict(t.folds)
        for key, arm in t.panel.items():
            rows = t.rows(key)
            if not isinstance(arm, Concluded) or not _holed(t, key, arm):
                continue
            # Round 0's walk is the origin's cells, the ones it never sent among them.
            attempted = (
                cycle.origin_cells(dataset)
                if t.round == 0
                else [by_key[r.key] for r in rows if r.key in by_key]
            )
            if not attempted:
                logger.warning(
                    "Round %d arm %s has unmeasured cells but none in this dataset to "
                    "re-measure — leaving it holed rather than guessing.",
                    t.round,
                    arm.label,
                )
                continue
            sp = cycle.points.measured(arm)
            missing = repairable_holes(rows)
            # ONE walk, whole: the archive never replays an errored cell, so it sends exactly the
            # holes, and the composite is the GATEWAY's over the full panel.
            scored = await score_search_point(
                sp,
                attempted,
                session,
                kind=MeasurementRole.REPAIR,
                sample_index=cycle.sample_index,
                measured=arm.individual.id,
            )
            if scored.stopped is not None:
                logger.warning(
                    "Round %d arm %s: re-scoring its cells stopped after %d/%d (%s) — "
                    "leaving it holed rather than reporting part of its panel as the whole.",
                    t.round,
                    arm.label,
                    len(scored.sheet),
                    len(attempted),
                    scored.stopped,
                )
                continue
            cells[key] = scored.sheet
            folds[key] = scored.scores
            logger.warning(
                "Repaired round %d arm %s: %d cell(s) had no measurement; "
                "re-measured %d, composite %s → %s",
                t.round,
                arm.label,
                missing,
                len(scored.sheet),
                None if (was := t.folds.get(key)) is None else was.composite_fitness,
                scored.scores.composite_fitness,
            )
        # The headline is the head arm's own: re-keying its rows moves it, never a second write.
        if cells != t.cells:
            corrected[slot] = mended_round(
                t.model_copy(update={"cells": cells, "folds": folds}),
                corrected[:slot],
                session.pipeline_schema,
            )
    return corrected


def origin_only(rounds: Sequence[RoundResult], standing: StandingRounds) -> bool:
    """No round has read the origin yet: none closed past it, none walked on it."""
    return [t.round for t in rounds] == [0] and 1 not in standing.proposals


async def apply_correction(
    cycle: Cycle, prior: list[RoundResult], dataset: list[Any]
) -> ForkCut | None:
    """*cycle* is seated on *prior*, and is seated on what the branch carries where one is cut:
    graded and re-banked before its take (`runner/mint.py::onto_fork`)."""
    session = cycle.session
    campaign_store, hop = session.store.campaigns, session.hop
    # One read of the line before the branch exists to displace it.
    standing = campaign_store.standing_rounds(hop, moment=None)
    if origin_only(prior, standing):
        # Walked in place, past the divergence check, in round 0's door (`loop.py::walk_round_zero`).
        return None
    cut = repair_cut(prior)
    # Only what the branch carries: it REGENERATES a round above the cut rather than correct it.
    carried_rounds = prior[: cut.resume_at]
    mended = await repair_incomplete_rounds(carried_rounds, cycle, dataset)
    repaired = [
        after.round
        for before, after in zip(carried_rounds, mended, strict=True)
        if after is not before
    ]
    if not repaired:
        return None
    # The whole trajectory: a round above the cut reads those below, so its package shows the reach.
    corrected = [*mended, *prior[cut.resume_at :]]
    # Graded by the one validity check; a round banked off the re-measured one was generated off
    # its hole, so it is reached by construction.
    equivalent = (
        cut.resume_at not in standing.proposals
        and line_divergence(session, cycle.config, cycle.optimizer, corrected, standing.decisions)
        is None
    )
    fork_cut = cut_fork(
        session.store,
        hop,
        cut.resume_at,
        trigger=ForkTrigger.SCORING_DIVERGENCE,
        reason=f"repair:round_{cut.rounds[0]}",
        issued_by=SYSTEM_PRINCIPAL,
        named=cut.edge,
    )
    branch = fork_cut.child
    repair_target = branch.cycle_id
    logger.warning(
        "Round(s) %s do not re-derive from their own rows; branched → %s from %s, and this cycle "
        "keeps them exactly as they ran. Everything after that candidate retires with it; its "
        "earlier siblings are untouched measurements and stay on the line. Whether the "
        "correction reaches anything is graded once it lands.",
        ", ".join(str(r) for r in cut.rounds),
        repair_target,
        "its line's pick" if cut.edge is None else f"{cut.edge.arm} of round {cut.edge.round}",
    )
    logger.warning(
        "Resume corrected round(s) %s in memory; measuring what the correction reached "
        "before deciding where it belongs.",
        ", ".join(str(r) for r in repaired),
    )
    corrected = _rebank_on_branch(cycle, standing.rounds, branch, corrected, cut.retirements)
    if cut.retirements:
        logger.warning(
            "Re-banked %d retired candidate(s) onto %s — the branch now names them itself, so "
            "what a reader sees at those positions is the corrected measurement rather than the "
            "parent's, which is the version the cut retired.",
            len(cut.retirements),
            repair_target,
        )
    campaign_store.grade_fork(
        branch, ForkDirection.EQUIVALENT if equivalent else ForkDirection.SUPERSEDE
    )
    if equivalent:
        logger.warning(
            "The correction reached no recorded decision — cut %s marked EQUIVALENT; the line "
            "continues on it.",
            repair_target,
        )
    cycle.replay_priors(corrected[: cut.resume_at])
    return fork_cut
