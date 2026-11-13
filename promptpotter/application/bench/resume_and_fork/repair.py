from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application.bench.node_context import NodeContext, restated_round, rewalk_origin
from promptpotter.application.bench.resume_and_fork.fork_siblings import cut_fork
from promptpotter.application.bench.resume_and_fork.replayers import ReplayMismatch
from promptpotter.application.bench.round_analysis import mended_round
from promptpotter.application.optimizers.nodes import RoundContext
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.domain.arms import ArmState, Concluded, is_leader_eligible
from promptpotter.domain.cell_fold import repairable_holes
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.paired_reading import ArmKey, RoundArm
from promptpotter.domain.run_records import ForkDirection, ForkTrigger
from promptpotter.infrastructure.llm.telemetry import at_round
from promptpotter.shared.errors import ConflictError
from promptpotter.shared.identity import SYSTEM_PRINCIPAL
from promptpotter.shared.measurement_context import MeasurementRole

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.domain.optimizer_state import OptimizerState, StatePart
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.run_records import RoundProposedRecord
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.ledger import CycleEventLog
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


def _seat_on(
    cycle: Cycle, rounds: list[RoundResult], rr: RoundResult, ledger: CycleEventLog
) -> RoundContext:
    """*rr*'s round as the run stood in it, a view: the trajectory up to it, *rr* closed."""
    seated = cycle.seated([*(p for p in rounds if p.round < rr.round), rr])
    return RoundContext(cycle=seated, round_num=rr.round, ledger=ledger)


def round_packages(
    cycle: Cycle, rounds: list[RoundResult], ledger: CycleEventLog
) -> dict[int, dict[str, str]]:
    """``{round: {node: fingerprint}}``, each member asked in ITS OWN round's seat."""
    out: dict[int, dict[str, str]] = {}
    for rr in rounds:
        seat = _seat_on(cycle, rounds, rr, ledger)
        out[rr.round] = {
            node: fingerprint
            for member in cycle.optimizer.members
            for node, fingerprint in member.round_packages(NodeContext(seat, member)).items()
        }
    return out


async def _rederived(
    cycle: Cycle, rounds: list[RoundResult], drifted: list[int], ledger: CycleEventLog
) -> dict[int, OptimizerState]:
    """A repair, not a rewind: measurements and winner untouched; round 0's state is the origin path's. Before the branch exists, so a member that cannot re-derive refuses the repair rather than leave the branch graded on a hole."""
    restated = list(rounds)
    states: dict[int, OptimizerState] = {}
    for slot, rr in enumerate(rounds):
        if rr.round not in drifted or rr.round == 0:
            continue
        seat = _seat_on(cycle, restated, rr, ledger)
        # Outside the round loop this ContextVar still holds whatever the last round set.
        parts: list[StatePart] = []
        for member in cycle.optimizer.members:
            try:
                with at_round(rr.round):
                    parts.extend(await member.rederive(NodeContext(seat, member)))
            except Exception as exc:
                raise ConflictError(
                    f"The repair refused: round {rr.round}'s {member.name} read evidence the "
                    f"repair moved and could not re-derive what it recorded ({exc}). "
                    f"`resume --from {rr.round - 1}` re-runs that round instead.",
                    code="repair_refused",
                ) from exc
        if not parts:
            continue
        states[rr.round] = rr.closed_state.holding(*parts)
        restated[slot] = rr.model_copy(update={"optimizer_state": states[rr.round]})
        logger.warning(
            "Round %d %s re-derived: its input package drifted when the round was "
            "repaired, so what it recorded described evidence that no longer exists.",
            rr.round,
            ", ".join(part.kind for part in parts),
        )
    return states


def _package_drift(
    before: dict[int, dict[str, str]],
    after: dict[int, dict[str, str]],
    closed: set[int],
    proposed: Mapping[int, object],
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
        owns = nxt in closed or nxt in proposed
        if owns and nxt not in mismatches:
            mismatches[nxt] = ReplayMismatch(
                round_num=nxt,
                kind=f"node_package:{','.join(moved)}",
                recorded_outcome={n: before.get(rn, {}).get(n) for n in moved},
                current_outcome={n: now[n] for n in moved},
                inputs_ref={"drifted_round": rn, "nodes": moved},
            )
    return mismatches, drifted


def _origin_only(rounds: Sequence[RoundResult], standing: StandingRounds) -> bool:
    """No round has read the origin yet: none closed past it, none walked on it."""
    return [t.round for t in rounds] == [0] and 1 not in standing.proposals


async def rewalk_lost_origin(cycle: Cycle, dataset: list[Sample], ledger: CycleEventLog) -> None:
    """A replayed origin no round has read yet, that lost cells, re-closes in place, never on a
    branch that would keep only the dead measurement: one walk sends its holes and the cells its
    walk never sent, the rest replayed. Past the divergence check, so a resume it refuses pays nothing."""
    session = cycle.session
    origin, head = cycle.origin_round, cycle.origin_round.head
    standing = session.store.campaigns.standing_rounds(session.hop, moment=None)
    if origin.at_offset is None or not _origin_only(cycle.rounds, standing):
        return
    assert origin.election is not None and head is not None, "round 0 ends on its origin"
    rows = origin.rows(origin.election.ended_on)
    holes = repairable_holes(rows)
    if not holes and len(rows) >= head.expected_samples:
        return
    ctx = RoundContext(cycle=cycle, round_num=0, ledger=ledger)
    await rewalk_origin(ctx, cycle.origin_cells(dataset))
    logger.warning(
        "Resume re-measured the origin's %d lost cell(s) and the %d its walk never sent; round 0 "
        "closes again on them.",
        holes,
        max(0, head.expected_samples - len(rows)),
    )


async def apply_correction(
    cycle: Cycle,
    prior: list[RoundResult],
    packages_before: dict[int, dict[str, str]],
    dataset: list[Any],
    ledger: CycleEventLog,
) -> str | None:
    """*cycle* is seated on *prior*, and is seated on what the branch carries where one is cut."""
    session = cycle.session
    campaign_store, hop = session.store.campaigns, session.hop
    # One read of the line before the branch exists to displace it.
    standing = campaign_store.standing_rounds(hop, moment=None)
    if _origin_only(prior, standing):
        # Re-walked in place past the divergence check (`rewalk_lost_origin`).
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
    proposed: dict[int, RoundProposedRecord] = {
        n: held.record for n, held in standing.proposals.items()
    }
    mismatches, drifted = _package_drift(
        packages_before,
        round_packages(cycle, corrected, ledger),
        {t.round for t in corrected},
        proposed,
    )
    rederived = await _rederived(
        cycle, corrected, [rn for rn in drifted if rn < cut.resume_at], ledger
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
    repair_target = await asyncio.to_thread(fork_cut.take, session.store)
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
    branch = CycleHop(campaign_id=hop.campaign_id, cycle_id=repair_target)
    corrected = _rebank_on_branch(cycle, standing.rounds, branch, corrected, cut.retirements)
    if cut.retirements:
        logger.warning(
            "Re-banked %d retired candidate(s) onto %s — the branch now names them itself, so "
            "what a reader sees at those positions is the corrected measurement rather than the "
            "parent's, which is the version the cut retired.",
            len(cut.retirements),
            repair_target,
        )
    carried_closes = campaign_store.standing_rounds(branch, moment=None).rounds
    for slot, rr in enumerate(corrected):
        if (state := rederived.get(rr.round)) is not None:
            corrected[slot] = rr.model_copy(update={"optimizer_state": state})
            campaign_store.rebank_round(branch, carried_closes[rr.round].close.restated(state))

    equivalent = not mismatches
    campaign_store.grade_fork(
        branch, ForkDirection.EQUIVALENT if equivalent else ForkDirection.SUPERSEDE
    )
    if equivalent:
        if (carried := proposed.get(cut.resume_at)) is not None:
            campaign_store.propose_round(branch, carried)
        logger.warning(
            "The correction reached nothing any node reads — cut %s marked EQUIVALENT "
            "and carries round %d's candidates across unchanged; the line continues on it.",
            repair_target,
            cut.resume_at,
        )
    cycle.replay_priors(corrected[: cut.resume_at])
    return repair_target
