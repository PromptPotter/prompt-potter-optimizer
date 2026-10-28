from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application.bench.resume_and_fork.fork_siblings import mint_fork
from promptpotter.application.bench.resume_and_fork.replayers import ReplayMismatch
from promptpotter.application.bench.round_analysis import compute_round_diagnostics
from promptpotter.application.run_callbacks import stated_line
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.paired_reading import ArmKey, RoundArm
from promptpotter.domain.results import ArmState, Concluded, is_leader_eligible
from promptpotter.domain.run_records import (
    ForkDirection,
    ForkTrigger,
    RoundRestatedRecord,
)
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.shared.errors import ErrorCategory
from promptpotter.shared.identity import SYSTEM_PRINCIPAL
from promptpotter.shared.measurement_context import MeasuredPass, MeasurementRole

if TYPE_CHECKING:
    from collections.abc import Mapping

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.bench.difficulty import DifficultyView
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.campaign_config import LivesConfig
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.scoring import GradedCell
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

logger = logging.getLogger(__name__)


def _repairable_hole(cell: GradedCell) -> bool:
    """``HALTED`` is no hole: the declared bound cuts the next attempt at the same place."""
    return cell.facts.errored and cell.facts.error_category is not ErrorCategory.HALTED


def _holed(t: RoundResult, key: ArmKey, arm: ArmState) -> bool:
    return is_leader_eligible(arm) and any(map(_repairable_hole, t.rows(key)))


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
    campaign_store: CampaignStore,
    parent: CycleHop,
    branch_cycle_id: str,
    rounds: list[RoundResult],
    retirements: list[tuple[int, ArmKey]],
    *,
    slot_of: Mapping[str, int],
    difficulty: DifficultyView,
    lives: LivesConfig | None,
) -> list[RoundResult]:
    branch = CycleHop(campaign_id=parent.campaign_id, cycle_id=branch_cycle_id)
    closes = campaign_store.standing_rounds(parent, moment=None).rounds
    keys: dict[int, list[ArmKey]] = {}
    for round_num, key in retirements:
        keys.setdefault(round_num, []).append(key)
    restated: dict[int, RoundResult] = {}
    for t in rounds:
        if t.round not in keys:
            continue
        line, series, standing = stated_line(t, rounds, difficulty, lives=lives)
        # Under its round's own anchors, never the resuming launch's.
        anchors = closes[t.round].close.anchors
        offset = campaign_store.restate_round(
            branch,
            RoundRestatedRecord.of(line, slot_of, series, standing, anchors),
        )
        # The restatement MOVES the document's address; the retired one re-folds to the holed round.
        restated[t.round] = t.model_copy(update={"at_offset": offset})
    return [restated.get(t.round, t) for t in rounds]


async def repair_incomplete_rounds(
    prior: list[RoundResult],
    session: Session,
    cycle: Cycle,
    dataset: list[Any],
) -> list[RoundResult]:
    """Writes nothing, to a ledger or to *prior*; an untouched round is the SAME object handed in."""

    by_key = {s.key: s for s in dataset}
    corrected = list(prior)
    for slot, t in enumerate(prior):
        cells = dict(t.cells)
        folds = dict(t.folds)
        for key, arm in t.panel.items():
            rows = t.rows(key)
            if not isinstance(arm, Concluded) or not _holed(t, key, arm):
                continue
            attempted = [by_key[r.key] for r in rows if r.key in by_key]
            if not attempted:
                logger.warning(
                    "Round %d arm %s has unmeasured cells but none in this dataset to "
                    "re-measure — leaving it holed rather than guessing.",
                    t.round,
                    arm.label,
                )
                continue
            sp = cycle.points.measured(arm)
            stamp = MeasuredPass(individual_id=arm.individual.id, role=MeasurementRole.REPAIR)
            missing = [by_key[r.key] for r in rows if _repairable_hole(r) and r.key in by_key]
            for hole in missing:
                await score_search_point(
                    sp,
                    [hole],
                    session,
                    label=stamp.role,
                    sample_index=cycle.sample_index,
                    measured=stamp,
                    force_fresh=True,
                )
            # Re-scored whole (all cache hits now), so the composite is the GATEWAY's over the full panel.
            scored = await score_search_point(
                sp,
                attempted,
                session,
                label=stamp.role,
                sample_index=cycle.sample_index,
                measured=stamp,
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
                len(missing),
                len(scored.sheet),
                None if (was := t.folds.get(key)) is None else was.composite_fitness,
                scored.scores.composite_fitness,
            )
        # The headline is the head arm's own: re-keying its rows moves it, never a second write.
        mended = t.model_copy(update={"cells": cells, "folds": folds}) if cells != t.cells else t
        if mended is not t:
            diagnostics = compute_round_diagnostics(mended, session.pipeline_schema)
            corrected[slot] = mended.model_copy(update={"diagnostics": diagnostics})
    return corrected


def _package_drift(
    before: dict[int, dict[str, str]],
    after: dict[int, dict[str, str]],
    closed: set[int],
    campaign_store: CampaignStore,
    hop: CycleHop,
) -> tuple[dict[int, ReplayMismatch], list[int]]:
    """A mismatch is raised at the CONSUMER (``rn + 1``), never the producer, and only where it owns something."""
    mismatches: dict[int, ReplayMismatch] = {}
    drifted: list[int] = []
    for rn, now in sorted(after.items()):
        moved = sorted(n for n, h in now.items() if before.get(rn, {}).get(n) != h)
        if not moved:
            continue
        drifted.append(rn)
        logger.warning(
            "Round %d package drifted after repair — %s now read different evidence.",
            rn,
            ", ".join(moved),
        )
        nxt = rn + 1
        owns = nxt in closed or campaign_store.round_proposals(hop, nxt) is not None
        if owns and nxt not in mismatches:
            mismatches[nxt] = ReplayMismatch(
                round_num=nxt,
                kind=f"node_package:{','.join(moved)}",
                recorded_outcome={n: before.get(rn, {}).get(n) for n in moved},
                current_outcome={n: now[n] for n in moved},
                inputs_ref={"drifted_round": rn, "nodes": moved},
            )
    return mismatches, drifted


async def apply_correction(
    campaign_store: CampaignStore,
    hop: CycleHop,
    prior: list[RoundResult],
    packages_before: dict[int, dict[str, str]],
    session: Session,
    cycle: Cycle,
    dataset: list[Any],
) -> str | None:
    cut = repair_cut(prior)
    # Only what the branch carries: it REGENERATES a round above the cut rather than correct it.
    carried_rounds = prior[: cut.resume_at]
    mended = await repair_incomplete_rounds(carried_rounds, session, cycle, dataset)
    repaired = [
        after.round
        for before, after in zip(carried_rounds, mended, strict=True)
        if after is not before
    ]
    if not repaired:
        return None
    # Read before the branch exists to displace it.
    carried = campaign_store.round_proposals(hop, cut.resume_at)
    repair_target = mint_fork(
        campaign_store,
        hop,
        cut.resume_at,
        trigger=ForkTrigger.SCORING_DIVERGENCE,
        reason=f"repair:round_{cut.rounds[0]}",
        issued_by=SYSTEM_PRINCIPAL,
        named=cut.edge,
    )
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
    # The whole trajectory: a round above the cut reads those below, so its package shows the reach.
    corrected = [*mended, *prior[cut.resume_at :]]
    mismatches, drifted = _package_drift(
        packages_before,
        cycle.optimizer.runtime.round_packages(cycle, corrected),
        {t.round for t in corrected},
        campaign_store,
        hop,
    )
    branch = CycleHop(campaign_id=hop.campaign_id, cycle_id=repair_target)
    corrected = _rebank_on_branch(
        campaign_store,
        hop,
        repair_target,
        corrected,
        cut.retirements,
        slot_of=session.bank.slot_of,
        difficulty=cycle.difficulty,
        lives=cycle.config.optimization.lives,
    )
    logger.warning(
        "Re-banked %d retired candidate(s) onto %s — the branch now names them itself, so "
        "what a reader sees at those positions is the corrected measurement rather than the "
        "parent's, which is the version the cut retired.",
        len(cut.retirements),
        repair_target,
    )
    if drifted:
        corrected = await cycle.optimizer.runtime.rederive(
            campaign_store, branch, cycle, corrected, drifted
        )

    equivalent = not mismatches
    campaign_store.grade_fork(
        branch, ForkDirection.EQUIVALENT if equivalent else ForkDirection.SUPERSEDE
    )
    if equivalent:
        if carried is not None:
            CycleEventLog.open(CycleDir(campaign_store.cycle_dir(branch))).append(carried)
        logger.warning(
            "The correction reached nothing any node reads — cut %s marked EQUIVALENT "
            "and carries round %d's candidates across unchanged; the line continues on it.",
            repair_target,
            cut.resume_at,
        )
    cycle.replay_priors(corrected[: cut.resume_at])
    return repair_target
