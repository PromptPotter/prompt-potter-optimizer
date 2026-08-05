"""Resume entry point — repair holed rounds, rescore, replay decisions, halt-or-fork.

:func:`resume_with_divergence_check` is the runner's one entry point: rescore prior rounds,
repair any that recorded a cell without a measurement, walk ``replay_decisions`` for the first
divergence, then halt or mint a sibling via :func:`_mint_fork`.

Incompleteness and divergence are different questions, so the repair runs regardless of
``DiffScope`` and, when it lands, forces the walk. **A correction cuts FIRST and is graded
SECOND**; it is priced by what it REACHED, not by having happened. That behaviour, and the
three blind spots each check answers for: `docs/operations/persistence-and-state.md`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application.config import freeze_campaign_config
from promptpotter.application.knobs import DiffScope, classify_config_diff
from promptpotter.application.optimization.resume_and_fork.fork_siblings import (
    ForkResult,
    _mint_fork,
)
from promptpotter.application.optimization.resume_and_fork.replayers import (
    Divergence,
    replay_decisions,
)
from promptpotter.application.scoring.formula import rescore_results
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.run_records import (
    CandidateMintedRecord,
    ForkDirection,
    ForkSpec,
    ForkTrigger,
    LedgerCandidate,
    SnapshotRecord,
)
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_candidates
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.shared.errors import ResumeDivergenceError, graceful
from promptpotter.shared.instrument import MeasuredCandidate, MeasurementRole

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.domain.results import RoundResult
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

logger = logging.getLogger(__name__)

__all__ = ["resume_with_divergence_check"]


def _headline_disagrees(t: RoundResult) -> bool:
    """Does the round's headline still match its own winner's row? Pure — the answer decides
    where the repair may write, so it is asked before anything is."""
    if t.opt_sp is None:
        return False
    winner_id = t.opt_sp.lineage.id
    rows = t.all_candidate_results.get(winner_id)
    cs = next((c for c in t.candidate_scores if c.candidate_id == winner_id), None)
    if rows is None or cs is None:
        return False  # a HELD round — `results` carry the retained incumbent, not a candidate
    return list(t.results) != list(rows) or t.total != cs.total


def _first_divergent_candidate(t: RoundResult) -> str | None:
    """The earliest candidate in *t* the repair will move. Two ways a round stops re-deriving:
    a HOLE (about one candidate's rows) or a HEADLINE that no longer matches its winner."""
    from promptpotter.domain.results import is_leader_eligible, unscoreable_cells

    for cs in t.candidate_scores:
        if is_leader_eligible(cs) and unscoreable_cells(
            t.all_candidate_results.get(cs.candidate_id) or []
        ):
            return cs.candidate_id
    if _headline_disagrees(t) and t.opt_sp is not None:
        return t.opt_sp.lineage.id
    return None


class RepairCut(NamedTuple):
    """Where a correction branches. **The cut is a CANDIDATE, not a round** — earlier siblings
    are untouched measurements and stay on the line."""

    rounds: list[int]  # rounds that no longer re-derive from their own rows; empty ⇒ no cut
    resume_at: int  # a fork still LIFTS whole rounds — a round is the unit of election
    edge: str | None  # the candidate it hangs off; None ⇒ it leaves from the course root
    # Only what the BRANCH CARRIES (edge → end of the cut round). Later retirements exist, but
    # the branch REGENERATES those, so re-banking one names a candidate it never ran.
    retirements: list[tuple[RoundResult, int]]


def repair_cut(prior: list[RoundResult]) -> RepairCut:
    """Read the cut off the round documents alone, BEFORE the repair writes: once it lands,
    the holes this is read from are gone."""
    rounds: list[int] = []
    resume_at, edge = 0, None
    retirements: list[tuple[RoundResult, int]] = []
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
                retirements.append((t, i))
        carrying = False  # only the round the cut was taken in travels with the branch
    return RepairCut(rounds, resume_at, edge, retirements)


def _rebank_on_branch(
    campaign_store: CampaignStore,
    parent: CycleHop,
    branch_cycle_id: str,
    retirements: list[tuple[RoundResult, int]],
) -> int:
    """Put the corrected candidates on the BRANCH's own ledger — a measurement enters through
    the ingress, and a round file with none behind it leaves the branch unable to name a
    candidate of its own. Identity is copied; only the MEASUREMENT is re-emitted."""
    branch = CycleHop(campaign_id=parent.campaign_id, cycle_id=branch_cycle_id)
    parent_ledger = CycleLayout(campaign_store.cycle_dir(parent)).ledger
    minted_at = {(c.round, c.idx): c for c in scan_ledger_candidates(parent_ledger)}
    ledger = CycleEventLog.open(CycleDir(campaign_store.cycle_dir(branch)))
    # `parent_id` and `source` reach a reader ONLY through the mint record; every other field
    # rides the snapshot (`ledger_scan._SCORED_INCLUDE`). Copied by shared field NAME, so
    # adding one to both models carries it without a third edit here.
    identity = set(CandidateMintedRecord.model_fields) & set(LedgerCandidate.model_fields)
    for t, idx in retirements:
        if (minted := minted_at.get((t.round, idx))) is not None:
            ledger.append(CandidateMintedRecord(**minted.model_dump(include=identity)))
        ledger.append(
            SnapshotRecord(
                event="candidate_scored",
                round=t.round,
                candidate_idx=idx,
                candidate_total=len(t.candidate_scores),
                payload={"scores": t.candidate_scores[idx].model_dump(mode="json")},
            )
        )
    return len(retirements)


def _resync_round_headline(t: RoundResult) -> bool:
    """Re-project the round's headline off the winner's OWN row — a stored copy (the
    duplication ``RoundResult`` defends), so a repair that misses it leaves the trajectory
    quoting the holed panel. A projection, never a second election."""
    from promptpotter.application.scoring.metrics import count_degraded_samples

    if not _headline_disagrees(t):
        return False
    assert t.opt_sp is not None  # `_headline_disagrees` established both
    winner_id = t.opt_sp.lineage.id
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
    t.results = list(rows)
    t.total = cs.total
    t.accuracy = cs.accuracy
    t.composite_fitness = cs.composite_fitness
    t.evaluators = dict(cs.evaluators)
    t.matched_origin_accuracy = cs.matched_origin_accuracy
    t.matched_origin_composite = cs.matched_origin_composite
    t.degraded_samples = count_degraded_samples(t.results)
    return True


async def repair_incomplete_rounds(
    campaign_store: CampaignStore,
    hop: CycleHop,
    prior: list[Any],
    session: Session,
    cycle: Cycle,
    dataset: list[Any],
) -> list[int]:
    """Make an already-CLOSED round re-derive from its own rows again — IN MEMORY.

    **Nothing here writes**: where the corrected rounds belong is unknowable until the caller
    has measured whether the correction propagated. Each hole is plugged with a REAL
    measurement, never an archive row, which may be a PoBB ``BACKFILL`` measured out of the
    round's shared order. The panel gate does not cover this — it stops the round it fires
    in, and rounds banked before it existed keep their holes. Re-election is NOT done here;
    ``ROUND_WINNER`` is ``REPLAYED``, so the divergence walk owns it.
    """
    from promptpotter.application.optimization.l1.population import build_score_report
    from promptpotter.application.optimization.round_analysis import compute_round_diagnostics
    from promptpotter.application.scoring.search_point_scorer import score_search_point
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.results import is_leader_eligible, unscoreable_cells
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.shared.errors import is_error_result

    by_id = {str(s.id): s for s in dataset}
    repaired: list[int] = []
    for t in prior:
        cache = campaign_store.load_round_candidates(hop, t.round)
        cached = cache[0] if cache else []
        opt_sps: dict[str, OptSearchPoint] = {}
        for entry in cached:
            try:
                osp = OptSearchPoint.model_validate(entry.get("opt_sp") or {})
            except Exception:  # a stale cache entry must not abort the resume
                continue
            opt_sps[osp.lineage.id] = osp
        changed = False
        for i, cs in enumerate(t.candidate_scores):
            rows = list(t.all_candidate_results.get(cs.candidate_id) or [])
            if not is_leader_eligible(cs) or not unscoreable_cells(rows):
                continue
            attempted = [by_id[sid] for r in rows if (sid := str(r.get("sample_id"))) in by_id]
            cand_osp = opt_sps.get(cs.candidate_id)
            if cand_osp is None or not attempted:
                logger.warning(
                    "Round %d candidate %s has unmeasured cells but no cached searchpoint "
                    "to re-measure them with — leaving it holed rather than guessing.",
                    t.round,
                    cs.label,
                )
                continue
            sp = JobSearchPoint(
                pipeline_params=cs.resolved_pipeline_params, prompt_fields=cs.prompt_fields
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
                if is_error_result(r) and (sid := str(r.get("sample_id"))) in by_id
            ]
            # PHASE 1 — plug each hole with a REAL measurement, one cell at a time.
            for hole in missing:
                await score_search_point(
                    sp,
                    [hole],
                    session,
                    label="round_repair",
                    opt_sp=None,
                    degradation_checks=None,
                    n_total_candidates=0,
                    axes=cycle.axes,
                    on_sample_scored=None,
                    on_sample_starting=None,
                    measured=stamp,
                    force_fresh=True,
                )
            # PHASE 2 — re-score the whole attempted set (all cache hits by now), so the
            # composite comes from the scoring GATEWAY over the complete panel rather than a
            # local computation stitched onto phase 1's partial return.
            results, scores, _signal = await score_search_point(
                sp,
                attempted,
                session,
                label="round_repair",
                opt_sp=None,
                degradation_checks=None,
                n_total_candidates=0,
                axes=cycle.axes,
                on_sample_scored=None,
                on_sample_starting=None,
                measured=stamp,
            )
            t.all_candidate_results[cs.candidate_id] = results
            t.candidate_scores[i] = build_score_report(
                cand_osp,
                cs.pipeline_params_override,
                scores,
                results,
                attempted,
                label=cs.label,
                resolved_pipeline_params=cs.resolved_pipeline_params,
                elimination_stopped=cs.elimination_stopped,
            )
            # Duplicated on purpose (see `RoundResult`); repairing one half leaves the
            # trajectory quoting the holed measurement.
            if t.opt_sp is not None and cs.candidate_id == t.opt_sp.lineage.id:
                t.results = results
            changed = True
            logger.warning(
                "Repaired round %d candidate %s: %d cell(s) had no measurement; "
                "re-measured %d, composite %.4f → %.4f",
                t.round,
                cs.label,
                unscoreable_cells(rows),
                len(results),
                cs.composite_fitness,
                t.candidate_scores[i].composite_fitness,
            )
        # AFTER the holes, never before: the headline projects the row the loop above moves.
        if _resync_round_headline(t):
            changed = True
        if changed:
            # The `diagnostics` injection's whole source — without it the rows say six cells
            # while every optimizer node's panel still describes four.
            t.diagnostics = compute_round_diagnostics(
                t,
                [p for p in prior if p.round < t.round] + [t],
                session.pipeline_schema,
            )
            repaired.append(t.round)
    return repaired


def _stale_generation(
    campaign_store: CampaignStore,
    hop: CycleHop,
    prior: list[RoundResult],
    resumed_from_round: int,
) -> Divergence | None:
    """Is the round about to run holding candidates its own predecessor no longer matches?
    Both sides are persisted, so unlike the package differential this survives a restart. No
    recorded digest ⇒ UNVOUCHED ⇒ a divergence, not a shrug."""
    from promptpotter.domain.results import round_document_digest

    cached = campaign_store.load_round_candidates(hop, resumed_from_round)
    if cached is None or not prior:
        return None
    _candidates, consumed = cached
    current = round_document_digest(prior[-1])
    if consumed == current:
        return None
    logger.warning(
        "Round %d's cached candidates were generated from round %d as it read %s, and it "
        "now reads %s — regenerating on a branch rather than replaying them.",
        resumed_from_round,
        prior[-1].round,
        consumed or "(unrecorded)",
        current,
    )
    return Divergence(
        round_num=resumed_from_round,
        kind="stale_generation",
        recorded_outcome=consumed,
        current_outcome=current,
        inputs_ref={"generated_from_round": prior[-1].round},
    )


def _optimizer_divergences(prior: list[RoundResult]) -> dict[int, Divergence]:
    """Rounds produced by a DIFFERENT optimizer than the one loaded now. Template, layout and
    node config re-derive in any process, unlike a rendered panel. Asked PER ROUND so an edit
    forks from where it bites; an unstamped round is REPORTED, never guessed."""
    from promptpotter.application.optimization.dispatch.llm_call.prompts import (
        compute_optimizer_prompt_hashes,
    )

    current = compute_optimizer_prompt_hashes()
    out: dict[int, Divergence] = {}
    unstamped = [t.round for t in prior if not t.optimizer_prompt_hashes]
    if unstamped:
        logger.warning(
            "Round(s) %s carry no optimizer stamp, so whether they ran under the optimizer "
            "loaded now cannot be asked — they are neither confirmed nor diverged.",
            ", ".join(str(r) for r in unstamped),
        )
    for t in prior:
        recorded = t.optimizer_prompt_hashes
        moved = sorted(n for n, h in recorded.items() if current.get(n) != h)
        if not moved:
            continue
        out[t.round] = Divergence(
            round_num=t.round,
            kind=f"optimizer_identity:{','.join(moved)}",
            recorded_outcome={n: recorded[n] for n in moved},
            current_outcome={n: current.get(n) for n in moved},
            inputs_ref={"nodes": moved},
        )
        logger.warning(
            "Round %d was produced by a different optimizer than the one loaded now — %s "
            "changed (template, injection layout or resolved model/config).",
            t.round,
            ", ".join(moved),
        )
    return out


def _round_packages(cycle: Cycle, rounds: list[RoundResult]) -> dict[int, dict[str, str]]:
    """``{round: {node: package fingerprint}}``, each rebuilt at ITS OWN point in the run.

    **Replayed to the rounds BEFORE k, with k arriving only as ``latest_round``** — how
    ``run_l1_critique`` receives it. Per round, not once fully: a bundle carries the
    cumulative trajectory, so otherwise a repair in round k moved round 0's bundle too. The
    generator of k+1 reads a different bundle and is :func:`_stale_generation`'s job.
    """
    from promptpotter.application.optimization.dispatch.facade import build_bundle, node_packages

    out: dict[int, dict[str, str]] = {}
    for k, rr in enumerate(rounds):
        # ``max(k, 1)``, because ``replay_priors([])`` is a pure NO-OP — it returns before
        # touching anything — so k=0 would inherit whatever trajectory the caller walked in
        # with, i.e. the full one, and round 0's fingerprint would then move whenever a LATER
        # round was repaired. Round 0 IS the origin, so the state before it is the origin's:
        # seed from that. Every round's state is now set here, which is why no caller
        # pre-replays.
        cycle.replay_priors(rounds[: max(k, 1)])
        out[rr.round] = node_packages(build_bundle(cycle, latest_round=rr))
    cycle.replay_priors(rounds)  # leave the caller the full trajectory it walked in with
    return out


async def _rederive_critiques(
    campaign_store: CampaignStore,
    hop: CycleHop,
    session: Session,
    cycle: Cycle,
    drifted: list[RoundResult],
) -> None:
    """Re-distil the critique of each round whose package drifted — in place, on disk. It is
    the one artifact a round file carries that an optimizer node PRODUCED, and the next
    round's generator reads it. Measurements and winner untouched, so this is a repair, not a
    rewind. Round 0 is not a target: its critique comes from the ORIGIN path."""
    from promptpotter.application.optimization.l1.critique import run_l1_critique
    from promptpotter.infrastructure.llm.telemetry import reset_current_round, set_current_round

    saved = cycle.rounds
    try:
        for rr in drifted:
            if not rr.critique or rr.round == 0:
                continue
            cycle.rounds = [p for p in saved if p.round < rr.round]
            # `emit_token_usage` stamps from this ContextVar, which outside the round loop
            # still holds whatever the last round set — so the cost landed on other books.
            token = set_current_round(rr.round)
            try:
                with graceful(f"round {rr.round} critique re-derivation failed"):
                    rr.critique = await run_l1_critique(
                        cycle, rr, round_num=rr.round, ledger=session.state.ledger
                    )
                    campaign_store.save_round_file(hop, rr)
                    logger.warning(
                        "Round %d critique re-distilled: its input package drifted when the "
                        "round was repaired, so the recorded one described evidence that no "
                        "longer exists.",
                        rr.round,
                    )
            finally:
                reset_current_round(token)
    finally:
        cycle.rounds = saved


def _package_drift(
    before: dict[int, dict[str, str]],
    after: dict[int, dict[str, str]],
    by_round: dict[int, RoundResult],
    campaign_store: CampaignStore,
    hop: CycleHop,
) -> tuple[dict[int, Divergence], list[RoundResult]]:
    """What the correction REACHED: ``({round that consumed a moved package: divergence},
    [rounds whose own package moved])``. Raised at the CONSUMER (``rn + 1``), never the
    producer, and only where it owns something — a cached candidate set counts."""
    divergences: dict[int, Divergence] = {}
    drifted: list[RoundResult] = []
    for rn, now in sorted(after.items()):
        moved = sorted(n for n, h in now.items() if before.get(rn, {}).get(n) != h)
        if not moved:
            # Nothing downstream read a different word — the common case, and why a repair
            # does not simply fork.
            continue
        drifted.append(by_round[rn])
        logger.warning(
            "Round %d package drifted after repair — %s now read different evidence.",
            rn,
            ", ".join(moved),
        )
        nxt = rn + 1
        owns = nxt in by_round or campaign_store.load_round_candidates(hop, nxt) is not None
        if owns and nxt not in divergences:
            divergences[nxt] = Divergence(
                round_num=nxt,
                kind=f"node_package:{','.join(moved)}",
                recorded_outcome={n: before.get(rn, {}).get(n) for n in moved},
                current_outcome={n: now[n] for n in moved},
                inputs_ref={"drifted_round": rn, "nodes": moved},
            )
    return divergences, drifted


async def _apply_correction(
    campaign_store: CampaignStore,
    hop: CycleHop,
    prior: list[RoundResult],
    packages_before: dict[int, dict[str, str]],
    session: Session,
    cycle: Cycle,
    dataset: list[Any],
) -> ForkResult | None:
    """Cut, correct, grade. ``None`` ⇒ nothing needed correcting; the caller carries on.

    **The cut comes first** — read off the round documents and NOT again, since the repair
    plugs the very holes it is read from — then GRADED by what the correction reached.
    A cut with nothing repairable behind it leaves an empty branch for
    ``cleanup_stub_fork_if_empty`` and returns ``None``.
    """
    cut = repair_cut(prior)
    repair_target = hop.cycle_id
    repair_spec: ForkSpec | None = None
    if cut.rounds:
        # The CANDIDATE, not the label — every course mints its own `C2.1`.
        repair_spec = ForkSpec(
            trigger=ForkTrigger.SCORING_DIVERGENCE,
            reason=f"repair:round_{cut.rounds[0]}",
            issued_by="system",
            from_round=cut.rounds[0],
            from_candidate_id=cut.edge,
        )
        repair_target = _mint_fork(
            campaign_store,
            hop,
            session.session_id,
            cut.resume_at,
            repair_spec,
            surviving_rounds=list(prior[: cut.resume_at]),
        )
        logger.warning(
            "Round(s) %s do not re-derive from their own rows; branched → %s from %s BEFORE "
            "correcting, so this cycle keeps them exactly as they ran. Everything after that "
            "candidate retires with it; its earlier siblings are untouched measurements and "
            "stay on the line. Whether the correction reaches anything is graded once it lands.",
            ", ".join(str(r) for r in cut.rounds),
            repair_target,
            cut.edge or "the course root",
        )

    # ONLY what the branch carries: a round above the cut was generated from the version being
    # replaced, so the branch REGENERATES it rather than paying to correct a document it will
    # discard. No cut ⇒ empty slice, the same answer the walk gives.
    repaired = await repair_incomplete_rounds(
        campaign_store, hop, prior[: cut.resume_at], session, cycle, dataset
    )
    if not repaired:
        return None
    assert repair_spec is not None  # `repaired` ⊆ `cut.rounds`, and that is what minted it
    logger.warning(
        "Resume corrected round(s) %s in memory; measuring what the correction reached "
        "before deciding where it belongs.",
        ", ".join(str(r) for r in repaired),
    )
    by_round = {t.round: t for t in prior}
    divergences, drifted = _package_drift(
        packages_before,
        _round_packages(cycle, prior),
        by_round,
        campaign_store,
        hop,
    )
    branch = CycleHop(campaign_id=hop.campaign_id, cycle_id=repair_target)
    # As a round file AND on the branch's ledger — see `_rebank_on_branch`.
    for rn in repaired:
        campaign_store.save_round_file(branch, by_round[rn])
    logger.warning(
        "Re-banked %d retired candidate(s) onto %s — the branch now names them itself, so "
        "what a reader sees at those positions is the corrected measurement rather than the "
        "parent's, which is the version the cut retired.",
        _rebank_on_branch(campaign_store, hop, repair_target, cut.retirements),
        repair_target,
    )
    if drifted:
        await _rederive_critiques(campaign_store, branch, session, cycle, drifted)

    # GRADE the cut now its consequence is known, by RE-SERIALIZING the spec it was minted
    # from — `update` replaces `index.json::fork` wholesale, so a hand-built dict dropped
    # `from_candidate_id` and `_retired_by` then had no cut to place.
    equivalent = not divergences
    direction = ForkDirection.EQUIVALENT if equivalent else ForkDirection.SUPERSEDE
    campaign_store.update(
        branch,
        {
            "fork": repair_spec.model_copy(update={"direction": direction}).model_dump(
                mode="json", exclude={"seed"}
            )
        },
    )
    if equivalent:
        campaign_store.copy_parent_rounds_and_candidates(
            hop.campaign_id, hop.cycle_id, repair_target, before_round=cut.resume_at + 1
        )
        logger.warning(
            "The correction reached nothing any node reads — cut %s marked EQUIVALENT "
            "and carries round %d's candidates across unchanged. Both lines continue.",
            repair_target,
            cut.resume_at,
        )
    cycle.replay_priors(list(prior[: cut.resume_at]))
    return ForkResult(new_cycle_id=repair_target, new_resumed_from_round=cut.resume_at)


async def resume_with_divergence_check(
    campaign_store: CampaignStore,
    hop: CycleHop,
    resumed_from_round: int,
    session: Session,
    cycle: Cycle,
    dataset: list[Any],
    *,
    skip_divergence_check: bool,
    fork_on_divergence: bool = False,
) -> ForkResult | None:
    """Rescore prior rounds under the active scorer; halt or fork on divergence.

    Short-circuits on a :attr:`DiffScope.NONE` / :attr:`DiffScope.POLICY_ONLY` config diff —
    the parent's data trace is fully valid and the active policy governs unevaluated rounds.
    """
    sc = session.scoring
    scorer = sc.scorer
    assert scorer is not None, "session.scoring.scorer required for divergence replay"
    prior = campaign_store.load_rounds_range(hop, 0, resumed_from_round - 1)

    def _rescore(items: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = list(items or [])
        rescore_results(out, scorer, sc.scorer_id, sc.scorer_formula)
        return out

    for t in prior:
        _rescore(t.results)
        for items in t.all_candidate_results.values():
            _rescore(items)

    origin_results_rescored = _rescore(cycle.tracking.current_results)

    # Fingerprinted BEFORE any repair, from ONE cycle, so both sets differ by exactly what the
    # repair changed. Deep copies because the repair mutates `prior` in place. No pre-replay:
    # `_round_packages` seeds every round's state itself, k=0 included.
    packages_before = _round_packages(cycle, [t.model_copy(deep=True) for t in prior])

    correction = await _apply_correction(
        campaign_store, hop, prior, packages_before, session, cycle, dataset
    )
    if correction is not None:
        return correction

    if not skip_divergence_check:
        # Both ABOVE the config short-circuit, deliberately: optimizer prompts and layouts are
        # not KNOBS, so `DiffScope.NONE` says nothing about them; and a stale generation is the
        # one check answering for an EARLIER resume, which by now diffs clean.
        optimizer_divergences = _optimizer_divergences(prior)
        stale = _stale_generation(campaign_store, hop, prior, resumed_from_round)
        campaign = campaign_store.load_campaign(hop.campaign_id)
        frozen = campaign.config if campaign is not None else {}
        scope, diffed = classify_config_diff(cycle.config, frozen)
        if (
            not optimizer_divergences
            and stale is None
            and scope in (DiffScope.NONE, DiffScope.POLICY_ONLY)
        ):
            if scope is DiffScope.POLICY_ONLY:
                logger.info(
                    "Resume: policy-only config diff (%s); continuing on cycle %s in-place",
                    ", ".join(diffed),
                    hop.cycle_id,
                )
                # Refresh the campaign snapshot so future resumes diff
                # against current state.
                campaign_store.update_campaign(
                    hop.campaign_id, {"config": freeze_campaign_config(cycle.config)}
                )
            return None
        if scope is DiffScope.DATA_AFFECTING and diffed:
            logger.info(
                "Resume: data-affecting config diff (%s); running divergence check",
                ", ".join(diffed),
            )

        def _branch_or_halt(
            div: Divergence, survivors: list[RoundResult], *, self_inflicted: bool
        ) -> ForkResult:
            """The ONE exit every divergence takes: branch at ``div.round_num``, or halt. A
            ``SCORING_DIVERGENCE`` supersedes, and nothing here deletes — what the old package
            produced above the cut stays with it.

            Branches this resume CAUSED take themselves; a change from OUTSIDE is the
            operator's call, which ``--fork-on-divergence`` decides."""
            if not fork_on_divergence and not self_inflicted:
                raise ResumeDivergenceError(
                    round_num=div.round_num,
                    kind=div.kind,
                    recorded_outcome=div.recorded_outcome,
                    current_outcome=div.current_outcome,
                    diagnostics={
                        "scorer_id": sc.scorer_id,
                        "fork_hint": (
                            "rerun `resume --fork-on-divergence` to branch a new cycle "
                            "here; this one keeps what the superseded package produced"
                        ),
                    },
                )
            new_cycle_id = _mint_fork(
                campaign_store,
                hop,
                session.session_id,
                div.round_num,
                ForkSpec(
                    trigger=ForkTrigger.SCORING_DIVERGENCE,
                    reason=f"resume_divergence:{div.kind}",
                    issued_by="system",
                ),
                surviving_rounds=survivors,
            )
            cycle.replay_priors(survivors)
            logger.warning(
                "Resume diverged at round %d (%s); branched → %s. That cycle is the "
                "continuation; this one keeps what the superseded package produced.",
                div.round_num,
                div.kind,
                new_cycle_id,
            )
            return ForkResult(new_cycle_id=new_cycle_id, new_resumed_from_round=div.round_num)

        for i, t in enumerate(prior):
            # A different OPTIMIZER produced it, or its OUTCOME no longer re-derives. One
            # `Divergence`, one exit. Both come from outside this resume.
            div = optimizer_divergences.get(t.round) or replay_decisions(
                t, origin_results=origin_results_rescored, delta_scale=cycle.delta_scale
            )
            if div is not None:
                return _branch_or_halt(div, list(prior[:i]), self_inflicted=False)

        # The round about to run has no round file, so the loop above cannot visit it — same
        # exit, every prior round crossing the cut. This one IS ours: a generation left stale
        # by an earlier resume is the resume doing its job, so it branches without asking.
        if stale is not None:
            return _branch_or_halt(stale, list(prior), self_inflicted=True)

    return None
