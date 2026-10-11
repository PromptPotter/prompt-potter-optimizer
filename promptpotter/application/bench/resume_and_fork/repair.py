from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application.bench.resume_and_fork.fork_siblings import (
    ForkResult,
    mint_fork,
)
from promptpotter.application.bench.resume_and_fork.replayers import ReplayMismatch
from promptpotter.application.bench.round_analysis import compute_round_diagnostics
from promptpotter.application.run_callbacks import RunCallbacks
from promptpotter.application.scoring.candidate_report import build_score_report
from promptpotter.application.scoring.row_diagnostics import count_degraded_samples
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.opt_search_point import IndividualLineage, OptSearchPoint
from promptpotter.domain.results import is_leader_eligible, recall_at
from promptpotter.domain.run_records import (
    CandidateMintedRecord,
    CandidateScoredRecord,
    ForkDirection,
    ForkSpec,
    ForkTrigger,
    LedgerCandidate,
    SampleScoredRecord,
)
from promptpotter.domain.scoring import NO_CELLS
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.shared.errors import ErrorCategory
from promptpotter.shared.measurement_context import MeasuredCandidate, MeasurementRole

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.scoring import GradedCell
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

logger = logging.getLogger(__name__)

__all__ = [
    "RepairCut",
    "apply_correction",
    "repair_cut",
    "repair_incomplete_rounds",
]


def _headline_disagrees(t: RoundResult) -> bool:
    if t.opt_sp is None:
        return False
    winner_id = t.opt_sp.id
    rows = t.all_candidate_results.get(winner_id)
    cs = next((c for c in t.candidate_scores if c.candidate_id == winner_id), None)
    if rows is None or cs is None:
        return False  # a HELD round — `results` carry the retained parent, not a candidate
    return t.results.cells != rows.cells or t.total != cs.total


def _repairable_hole(cell: GradedCell) -> bool:
    """``HALTED`` is no hole: the declared bound cuts the next attempt at the same place."""
    return cell.facts.errored and cell.facts.error_category is not ErrorCategory.HALTED


def _first_divergent_candidate(t: RoundResult) -> str | None:
    for cs in t.candidate_scores:
        if is_leader_eligible(cs) and any(
            map(_repairable_hole, t.all_candidate_results.get(cs.candidate_id, NO_CELLS))
        ):
            return cs.candidate_id
    if _headline_disagrees(t) and t.opt_sp is not None:
        return t.opt_sp.id
    return None


class RepairCut(NamedTuple):
    """The cut is a CANDIDATE, not a round: earlier siblings are untouched and stay on the line."""

    rounds: list[int]
    resume_at: int
    edge: str | None  # None ⇒ it leaves from the course root
    # Only what the BRANCH CARRIES (edge → end of the cut round); it regenerates the later ones.
    retirements: list[tuple[int, int]]


def repair_cut(prior: list[RoundResult]) -> RepairCut:
    rounds: list[int] = []
    resume_at, edge = 0, None
    retirements: list[tuple[int, int]] = []
    cutting = carrying = False
    for t in prior:
        diverged = _first_divergent_candidate(t)
        if diverged is not None:
            rounds.append(t.round)
            if not cutting:
                resume_at, carrying = t.round + 1, True
        for i, cs in enumerate(t.candidate_scores):
            cutting = cutting or cs.candidate_id == diverged
            if not cutting:
                edge = cs.candidate_id
            elif carrying:
                retirements.append((t.round, i))
        carrying = False
    return RepairCut(rounds, resume_at, edge, retirements)


def _rebank_on_branch(
    campaign_store: CampaignStore,
    parent: CycleHop,
    branch_cycle_id: str,
    rounds: list[RoundResult],
    retirements: list[tuple[int, int]],
) -> list[RoundResult]:
    branch = CycleHop(campaign_id=parent.campaign_id, cycle_id=branch_cycle_id)
    minted_at = {(c.round, c.idx): c for c in campaign_store.standing_rounds(parent).candidates()}
    ledger = CycleEventLog.open(CycleDir(campaign_store.cycle_dir(branch)))
    cb = RunCallbacks(ledger=ledger)
    # `lineage` reaches a reader ONLY through the mint record.
    identity = set(CandidateMintedRecord.model_fields) & set(LedgerCandidate.model_fields)
    slots: dict[int, list[int]] = {}
    for round_num, idx in retirements:
        slots.setdefault(round_num, []).append(idx)
    reclosed: dict[int, RoundResult] = {}
    for t in rounds:
        if t.round not in slots:
            continue
        for idx in slots[t.round]:
            if (minted := minted_at.get((t.round, idx))) is not None:
                ledger.append(CandidateMintedRecord(**minted.model_dump(include=identity)))
            scores = t.candidate_scores[idx]
            # The walk too: the mint above empties the slot.
            for walked in t.all_candidate_results.get(scores.candidate_id, NO_CELLS):
                ledger.append(
                    SampleScoredRecord(
                        round=t.round,
                        candidate_idx=idx,
                        candidate_total=len(t.candidate_scores),
                        individual_id=scores.candidate_id,
                        role=MeasurementRole.PANEL,
                        sample_total=scores.expected_samples,
                        result=walked.ledger_wire(),
                    )
                )
            ledger.append(
                CandidateScoredRecord(
                    round=t.round,
                    candidate_idx=idx,
                    candidate_total=len(t.candidate_scores),
                    scores=scores,
                )
            )
        cb.on_election(t)
        # The re-close MOVES the document's address; the retired one re-folds to the holed round.
        reclosed[t.round] = t.model_copy(update={"at_offset": cb.on_round_close(t)})
    return [reclosed.get(t.round, t) for t in rounds]


def _resync_round_headline(t: RoundResult) -> RoundResult:
    """A projection off the winner's own row, never a second election; *t* itself where they agree."""

    if not _headline_disagrees(t):
        return t
    assert t.opt_sp is not None
    winner_id = t.opt_sp.id
    rows = t.all_candidate_results[winner_id]
    cs = next(c for c in t.candidate_scores if c.candidate_id == winner_id)
    logger.warning(
        "Round %d headline disagreed with its own winner's rows (total %d→%d, "
        "composite %.4f→%.4f); re-projected. `improved` is the election's verdict and is "
        "left as recorded — ROUND_WINNER's replay is what re-asks it.",
        t.round,
        t.total,
        cs.total,
        t.composite_fitness,
        cs.composite_fitness,
    )
    return t.model_copy(
        update={
            "results": rows,
            "total": cs.total,
            "accuracy": cs.accuracy,
            "composite_fitness": cs.composite_fitness,
            "evaluators": dict(cs.evaluators),
            "degraded_samples": count_degraded_samples(rows),
            "recall_at": recall_at(rows),
        }
    )


async def repair_incomplete_rounds(
    prior: list[RoundResult],
    session: Session,
    cycle: Cycle,
    dataset: list[Any],
) -> list[RoundResult]:
    """Writes nothing, to a ledger or to *prior*; an untouched round is the SAME object handed in."""

    by_id = {str(s.id): s for s in dataset}
    corrected = list(prior)
    for slot, t in enumerate(prior):
        arms = dict(t.all_candidate_results)
        scores = list(t.candidate_scores)
        head = t.results
        plugged = False
        for i, cs in enumerate(t.candidate_scores):
            rows = t.all_candidate_results.get(cs.candidate_id, NO_CELLS)
            if not is_leader_eligible(cs) or not any(map(_repairable_hole, rows)):
                continue
            attempted = [by_id[sid] for r in rows if (sid := str(r.sample_id)) in by_id]
            if not attempted:
                logger.warning(
                    "Round %d candidate %s has unmeasured cells but none in this dataset to "
                    "re-measure — leaving it holed rather than guessing.",
                    t.round,
                    cs.label,
                )
                continue
            sp = cycle.searchpoint(cs.candidate_id, rounds=prior)
            cand_osp = OptSearchPoint.from_prompt_fields(
                cs.prompt_fields,
                pipeline_params=cs.resolved_pipeline_params,
                lineage=IndividualLineage(changes_description=cs.changes_description),
            )
            stamp = MeasuredCandidate(
                idx=i,
                candidate_id=cs.candidate_id,
                label=cs.label,
                role=MeasurementRole.REPAIR,
            )
            missing = [
                by_id[sid]
                for r in rows
                if _repairable_hole(r) and (sid := str(r.sample_id)) in by_id
            ]
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
                    "Round %d candidate %s: re-scoring its cells stopped after %d/%d (%s) — "
                    "leaving it holed rather than reporting part of its panel as the whole.",
                    t.round,
                    cs.label,
                    len(scored.sheet),
                    len(attempted),
                    scored.stopped,
                )
                continue
            results = scored.sheet
            arms[cs.candidate_id] = results
            scores[i] = build_score_report(
                cand_osp,
                cs.validation_failures,
                cs.pipeline_overlay,
                scored.scores,
                results.cells,
                attempted,
                label=cs.label,
                sp_hash=sp.sp_hash(session.pipeline_schema),
                outcome=cs.outcome,
                resolved_pipeline_params=cs.resolved_pipeline_params,
                elimination_context=cs.elimination_context,
                elimination_reason=cs.elimination_reason,
            )
            # `RoundResult` holds the winner's rows twice; repairing one half leaves the headline holed.
            if t.opt_sp is not None and cs.candidate_id == t.opt_sp.id:
                head = results
            plugged = True
            logger.warning(
                "Repaired round %d candidate %s: %d cell(s) had no measurement; "
                "re-measured %d, composite %.4f → %.4f",
                t.round,
                cs.label,
                len(missing),
                len(results),
                cs.composite_fitness,
                scores[i].composite_fitness,
            )
        mended = (
            t.model_copy(
                update={"all_candidate_results": arms, "candidate_scores": scores, "results": head}
            )
            if plugged
            else t
        )
        # AFTER the holes, never before: the headline projects the row the loop above moves.
        mended = _resync_round_headline(mended)
        if mended is not t:
            diagnostics = compute_round_diagnostics(
                mended,
                [*(p for p in corrected if p.round < t.round), mended],
                session.pipeline_schema,
            )
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
) -> ForkResult | None:
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
    repair_spec = ForkSpec(
        trigger=ForkTrigger.SCORING_DIVERGENCE,
        reason=f"repair:round_{cut.rounds[0]}",
        issued_by="system",
        from_round=cut.rounds[0],
        from_candidate_id=cut.edge,
    )
    # Read before the branch exists to displace it.
    carried = campaign_store.round_proposals(hop, cut.resume_at)
    repair_target = mint_fork(campaign_store, hop, cut.resume_at, repair_spec)
    logger.warning(
        "Round(s) %s do not re-derive from their own rows; branched → %s from %s, and this cycle "
        "keeps them exactly as they ran. Everything after that candidate retires with it; its "
        "earlier siblings are untouched measurements and stay on the line. Whether the "
        "correction reaches anything is graded once it lands.",
        ", ".join(str(r) for r in cut.rounds),
        repair_target,
        cut.edge or "the course root",
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
    corrected = _rebank_on_branch(campaign_store, hop, repair_target, corrected, cut.retirements)
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
            "and carries round %d's candidates across unchanged. Both lines continue.",
            repair_target,
            cut.resume_at,
        )
    cycle.replay_priors(corrected[: cut.resume_at])
    return ForkResult(new_cycle_id=repair_target, new_resumed_from_round=cut.resume_at)
