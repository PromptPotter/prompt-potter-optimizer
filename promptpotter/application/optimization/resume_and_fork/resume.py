"""Resume entry point — repair holed rounds, rescore, replay decisions, halt-or-fork.

:func:`resume_with_divergence_check` is the single entry point the runner reaches for.
It rescores prior rounds under the active scorer, repairs any that recorded a cell
without a measurement (:func:`repair_incomplete_rounds`), then walks
``replay_decisions`` for the first divergence and either halts with
:class:`ResumeDivergenceError` or mints a sibling cycle via :func:`_mint_fork`
(``ForkTrigger.SCORING_DIVERGENCE``), retargeting the active pointer.

**Incompleteness and divergence are different questions.** Divergence asks whether the
recorded outcome still holds under the active scorer, and the config diff decides
whether it is worth asking. Incompleteness asks whether the round ever had the evidence
to decide at all — a question the config cannot answer — so the repair runs regardless
of ``DiffScope`` and, when it lands, FORCES the walk it would otherwise short-circuit:
the rows the winner was elected from have just changed.

**A repair is priced by what it reached, not by the fact that it happened.** Every round's
optimizer packages are fingerprinted before the repair and again after
(``node_packages`` — one hash per registered node over what that node would be handed),
from ONE cycle, so the two renders differ by exactly what the repair changed and every
un-reconstructible input cancels. A repair no panel rendered leaves every fingerprint
identical and the resume simply continues.

**A repair that MOVED a package branches the run**, at the round after — the one built from
a package that no longer reproduces. It branches only if that round OWNS something, and a
round is more than its round file: the candidates cached for the round about to run are
content just as much as a closed round's rows. Where the next round owns neither, nothing
was produced from the moved package, so the branch rejoins the line it came from — which is
to say it never happened, and the correction is written in place. **This is why the repair
writes nothing itself**: where a correction belongs is not knowable until it is known what
it reached, and persisting first left a parent holding a repaired round beside descendants
generated from the version it replaced — a record that never existed.

Where it did reach something, the cut SUPERSEDES (``FORK_DIRECTION``): the new cycle carries
the corrected rounds and is the continuation, the parent stays byte-identical to what ran —
its pre-repair round, its stale candidate cache and all, which is why nothing here deletes
and why the old version stays inspectable.

That differential is blind across a restart — both renders happen under the code running
NOW, so it cannot see an optimizer that changed while the process was down. That half is
asked by :func:`_optimizer_divergences`, against the one thing a round records that DOES
re-derive in any process: which optimizer produced it (template ⊕ injection layout ⊕
resolved model/config, per node). It is the application-layer home of a check that used to
live on the CLI at campaign granularity, where a webapp resume bypassed it and its only
remedy was "mint a new campaign".

What neither half can see, stated rather than implied: a change to the RENDERER — the
code turning a bundle into panel text. Nothing on disk fingerprints it, and ``APP_VERSION``
does not move per commit, so an edit there is invisible to both. Naming it beats a hash
that pretends to cover it.

The escalation FSM is rebuilt via ``EscalationFSM.from_ledger`` because the ledger is
the SoT for layer-stall counters across resume.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.application.config import freeze_campaign_config
from promptpotter.application.knobs import DiffScope, classify_config_diff

# Leaf import (not the package surface): rebuilding the foundational FSM via
# escalation/__init__ would load the firing driver, which imports resume_and_fork
# back → import cycle. See escalation/__init__ "MAPPED" note.
from promptpotter.application.optimization.escalation.state import EscalationFSM
from promptpotter.application.optimization.resume_and_fork.fork_siblings import (
    ForkResult,
    _mint_fork,
)
from promptpotter.application.optimization.resume_and_fork.replayers import (
    Divergence,
    replay_decisions,
)
from promptpotter.application.scoring.formula import rescore_results
from promptpotter.domain.run_records import ForkSpec, ForkTrigger
from promptpotter.shared.errors import ResumeDivergenceError, graceful
from promptpotter.shared.instrument import MeasuredCandidate, MeasurementRole

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.domain.results import RoundResult
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

logger = logging.getLogger(__name__)

__all__ = ["resume_with_divergence_check"]


def _headline_disagrees(t: RoundResult) -> bool:
    """Does the round's headline still match its own winner's row? Pure — reads nothing.

    Split from the re-projection because the ANSWER decides where the repair may write, and
    that has to be known before anything is written.
    """
    if t.opt_sp is None:
        return False
    winner_id = t.opt_sp.lineage.id
    rows = t.all_candidate_results.get(winner_id)
    cs = next((c for c in t.candidate_scores if c.candidate_id == winner_id), None)
    if rows is None or cs is None:
        return False  # a HELD round — `results` carry the retained incumbent, not a candidate
    return list(t.results) != list(rows) or t.total != cs.total


def _resync_round_headline(t: RoundResult) -> bool:
    """Re-project the round's headline off the winner's OWN row. ``True`` if anything moved.

    ``results`` / ``total`` / ``accuracy`` / ``composite_fitness`` / ``evaluators`` are stored
    on the round as a copy of the elected winner's ``ScoredCandidate`` and its rows
    (``l1_score``) — the duplication ``RoundResult`` documents and defends. So a repair that
    reached ``all_candidate_results`` and not this copy leaves the document contradicting
    itself, and it is INVISIBLE to the hole check because by then the holes are gone. It is
    not a cosmetic disagreement: ``replay_priors`` builds the cumulative trajectory out of
    ``results``, so every panel reading the trajectory keeps quoting the holed panel while the
    candidate table beside it reads clean.

    A projection, never a second election. WHICH candidate won is not re-decided here —
    ``ROUND_WINNER``'s replayer owns that question and forks on it. ``improved`` / ``p_value``
    are the election's verdict rather than the winner's row, so they are deliberately left
    standing and the caller logs the move.

    A HELD round is skipped by construction: its ``results`` carry the retained incumbent,
    which is not a candidate, so no row matches and there is nothing to project from.
    """
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
    campaign_id: str,
    cycle_id: str,
    prior: list[Any],
    session: Session,
    cycle: Cycle,
    dataset: list[Any],
) -> list[int]:
    """Make an already-CLOSED round re-derive from its own rows again — IN MEMORY.

    **Nothing here writes.** Where the corrected rounds belong is not knowable until it is
    known whether the correction PROPAGATED, and that is measured by re-rendering the
    packages after this returns. A repair no panel read is a completion and belongs on the
    line it corrects; one that moved a package is a cut, and writing it in place would
    overwrite the only copy of what the rounds built on top of it actually read. Persisting
    first and asking second is what produced a parent holding a repaired round beside
    descendants generated from the version it replaced.


    Two ways it stops doing so, and the second only exists because of the first: a cell that
    came back without a measurement (re-measured here), and a headline that no longer matches
    the winner's row (re-projected by :func:`_resync_round_headline` — what a repair that
    landed on half the document leaves behind). Either one makes the round "repaired" for the
    caller, because either one changes what the round says and therefore what every panel
    downstream of it reads.

    A round that crowned a winner on a holed panel is not repaired by the panel gate —
    that gate stops the round it fires in, and rounds banked before it existed carry their
    holes forever. The evidence needed to spot one is the round's OWN rows, so the check is
    a re-derivation, not a recorded decision: an electable candidate holding an error row
    never had the panel its rivals were measured on.

    Each hole is plugged with a REAL measurement, never an archive row. That distinction is
    the whole point: a cached row for this ``(node_configs, sample_id)`` may have been
    produced as a ``BACKFILL`` — measured out of the round's shared order to fill someone
    else's paired comparison — and adopting it as this candidate's own panel cell is what
    made a repaired round unreproducible. ``force_fresh`` bypasses only the OUTER archive,
    so the spawn still resolves content-addressed and continues the furthest-along inner
    campaign banked for that cell rather than restarting it. Cost is therefore one inner
    run per hole, resumed from wherever it died.

    The already-measured cells are not re-run: a second gateway pass over the whole
    attempted set replays them from cache, and exists so the composite comes from the
    scoring gateway over the complete panel rather than a local computation stitched onto
    the partial return.

    Re-election is deliberately NOT done here. ``ROUND_WINNER`` is already ``REPLAYED``, so
    the divergence walk re-elects each repaired round from its repaired rows and answers
    "did completing the evidence change who won?" through the one mechanism that owns that
    question. This only makes the rows honest.
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
        cache = campaign_store.load_round_candidates(campaign_id, cycle_id, t.round)
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
            # ``force_fresh`` bypasses the outer measurement archive only: the inner spawn
            # still resolves content-addressed, so this lands in ``_open_inner_campaign``
            # and CONTINUES the furthest-along campaign banked for this (candidate, cell)
            # instead of restarting it. Reusing the archive here is what must not happen —
            # the row sitting there may have been measured as a BACKFILL, out of the
            # round's shared order for someone else's paired comparison, and adopting it
            # as this candidate's own panel cell is precisely what made round 1
            # unreproducible.
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
            # PHASE 2 — re-score the whole attempted set. Every cell is now in the archive,
            # so this is all cache hits; its purpose is that the composite comes from the
            # scoring gateway over the complete panel rather than from a second, local
            # computation stitched onto phase 1's partial return.
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
            # ``results`` is the WINNER's rows, deliberately stored beside
            # ``all_candidate_results[winner]`` (see ``RoundResult``). Repairing one half and
            # not the other would leave the round's headline payload — and every panel that
            # reads the trajectory through it — quoting the holed measurement.
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
        # AFTER the holes are plugged, never before. The headline is a projection of the
        # winner's row, and the row is precisely what the loop above moves — running it first
        # meant the one pass that repairs a round was GUARANTEED to leave that round
        # contradicting itself, with the fix arriving a whole resume late.
        if _resync_round_headline(t):
            changed = True
        if changed:
            # Diagnostics is a pure function of the round and the trajectory up to it, and it
            # is the `diagnostics` injection's whole source. Recomputing it here is what keeps
            # the repair from being half a repair: the rows say six cells while the panel every
            # optimizer node reads still describes four.
            t.diagnostics = compute_round_diagnostics(
                t,
                [p for p in prior if p.round < t.round] + [t],
                session.pipeline_schema,
            )
            repaired.append(t.round)
    return repaired


def _stale_generation(
    campaign_store: CampaignStore,
    campaign_id: str,
    cycle_id: str,
    prior: list[RoundResult],
    resumed_from_round: int,
) -> Divergence | None:
    """Is the round about to run holding candidates its own predecessor no longer matches?

    Asked on EVERY resume, gated on nothing. The package differential can only speak about a
    change this process made, so a re-distilled critique that landed on a previous resume is
    invisible to it — and that is the common case, because the resume that repairs and the
    resume that continues are usually different runs. This compares two things both sitting
    on disk: the digest a generation recorded of what it read, and what that round says now.
    No render, no cycle state, so it reproduces in any process.

    A cache with no recorded digest is UNVOUCHED, and unvouched is a divergence, not a
    shrug: it was written before generations recorded their source, so nothing can say what
    it read. Regenerating costs one optimizer call; replaying it costs a round built on
    evidence that may no longer exist.
    """
    from promptpotter.domain.results import round_document_digest

    cached = campaign_store.load_round_candidates(campaign_id, cycle_id, resumed_from_round)
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
    """Rounds produced by a DIFFERENT optimizer than the one loaded now, keyed by round.

    The cross-process half of the same question the package differential asks in-process,
    and the only half that survives a restart: template, layout and node config are read
    from ``promptpotter/assets/optimizer/pipeline.yaml`` and the override channel, so they
    re-derive identically in any process — unlike a rendered panel, which depends on live
    cycle state. So this is where an edited optimizer prompt, a moved layout, or a repointed
    model gets caught, and it is asked PER ROUND: the divergence lands on the first round
    that disagrees, which is what lets an edit fork a sibling from there instead of either
    passing unnoticed or condemning the whole campaign.

    A round with no stamp predates the field. That is reported, never guessed — assuming
    "same" would silently license the mixing this exists to prevent, and assuming
    "different" would fork every campaign banked before it.
    """
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

    One bundle per round serves both of that round's readers: the critique that distils it,
    and the generator / L2 / L3 of the round after it. So a drift here says two things at
    once — this round's critique was distilled from evidence that no longer holds, and the
    next round was generated from a package that no longer reproduces.

    **The cycle is replayed forward to round k before round k is rendered**, and that is not
    a refinement — it is what keeps the comparison honest. Half of what a bundle carries is
    CYCLE-level, above all the cumulative trajectory ``replay_priors`` folds out of every
    round's rows. Rendering every round against the fully-replayed cycle therefore let a
    repair in round k change round 0's bundle, and the resume forked at round 1 and threw
    the repair away. "Everything else cancels" holds only for state a repair cannot touch;
    the trajectory is exactly the state it can.
    """
    from promptpotter.application.optimization.dispatch.facade import build_bundle, node_packages

    out: dict[int, dict[str, str]] = {}
    for k, rr in enumerate(rounds):
        cycle.replay_priors(rounds[: k + 1])
        out[rr.round] = node_packages(build_bundle(cycle, latest_round=rr))
    cycle.replay_priors(rounds)  # leave the caller the full trajectory it walked in with
    return out


async def _rederive_critiques(
    campaign_store: CampaignStore,
    campaign_id: str,
    cycle_id: str,
    session: Session,
    cycle: Cycle,
    drifted: list[RoundResult],
) -> None:
    """Re-distil the critique of each round whose package drifted — in place, on disk.

    The critique is the one artifact a round file carries that an optimizer node PRODUCED,
    and the next round's generator reads it. Once the package it was distilled from no longer
    holds, carrying it forward is what makes the next round unreproducible — so it is
    re-derived rather than merely reported. One optimizer call per drifted round; the round's
    measurements, its winner and its candidates are untouched, which is why this is a repair
    and not a rewind.

    ``cycle.rounds`` is narrowed to the rounds BEFORE this one for the call, because that is
    what the live critique saw (``build_bundle`` takes ``latest_round`` separately for exactly
    this reason).

    Round 0 is not a target. Its critique is produced on the ORIGIN path, not by
    ``execute_round``, so this reproduces a call it never made — and narrowing to "before
    round 0" leaves no rounds at all, which is an ``IndexError`` inside ``origin_round`` that
    ``graceful`` then swallows. Skipping it by name beats re-deriving it wrongly or failing
    quietly.
    """
    from promptpotter.application.optimization.l1.critique import run_l1_critique
    from promptpotter.infrastructure.llm.models import reset_current_round, set_current_round

    saved = cycle.rounds
    try:
        for rr in drifted:
            if not rr.critique or rr.round == 0:
                continue
            cycle.rounds = [p for p in saved if p.round < rr.round]
            # Bind the round the call belongs to: `emit_token_usage` stamps its record from
            # this ContextVar, not from `LLMCallContext`, and outside the round loop it still
            # holds whatever the last round set — so the re-distil's cost landed on another
            # round's books.
            token = set_current_round(rr.round)
            try:
                with graceful(f"round {rr.round} critique re-derivation failed"):
                    rr.critique = await run_l1_critique(
                        cycle, rr, round_num=rr.round, ledger=session.state.ledger
                    )
                    campaign_store.save_round_file(campaign_id, cycle_id, rr)
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


async def resume_with_divergence_check(
    campaign_store: CampaignStore,
    campaign_id: str,
    cycle_id: str,
    resumed_from_round: int,
    session: Session,
    cycle: Cycle,
    dataset: list[Any],
    *,
    skip_divergence_check: bool,
    fork_on_divergence: bool = False,
) -> ForkResult | None:
    """Rescore prior rounds under the active scorer; halt or fork on divergence.

    Short-circuits when the diff between the active ``cycle.config`` and the
    campaign's frozen snapshot (``campaign.json::config``) classifies as
    :attr:`DiffScope.NONE` or :attr:`DiffScope.POLICY_ONLY`: the parent's
    data trace is fully valid, past decisions stay as the audit record, and
    the active policy governs unevaluated rounds. No fork, no divergence
    walk. See :func:`promptpotter.application.knobs.classify_config_diff`.
    """
    sc = session.scoring
    scorer = sc.scorer
    assert scorer is not None, "session.scoring.scorer required for divergence replay"
    prior = campaign_store.load_rounds_range(campaign_id, cycle_id, 0, resumed_from_round - 1)

    def _rescore(items: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = list(items or [])
        rescore_results(out, scorer, sc.scorer_id, sc.scorer_formula)
        return out

    for t in prior:
        _rescore(t.results)
        for items in t.all_candidate_results.values():
            _rescore(items)

    origin_results_rescored = _rescore(cycle.tracking.current_results)

    # Reconstruct the trajectory BEFORE anything is repaired, and fingerprint what every
    # optimizer node would have been handed for each round from it. `replay_priors` is
    # re-runnable, so the same rounds can be replayed again afterwards; the two fingerprint
    # sets are then built from ONE cycle and differ by exactly what the repair changed.
    before_rounds = [t.model_copy(deep=True) for t in prior]
    cycle.replay_priors(before_rounds)
    packages_before = _round_packages(cycle, before_rounds)

    # Repair BEFORE the divergence walk, and independently of the config diff: a holed
    # round is incomplete, not divergent, so the config-diff short-circuit below must not
    # be what decides whether it is noticed. A repair that lands makes the walk mandatory —
    # the rows the recorded winner was elected from have just changed, and `ROUND_WINNER`
    # is the mechanism that asks whether it still wins. It forks rather than overwrites,
    # so a flipped crown mints a sibling and the original trajectory survives.
    repaired = await repair_incomplete_rounds(
        campaign_store, campaign_id, cycle_id, prior, session, cycle, dataset
    )
    package_divergences: dict[int, Divergence] = {}
    repair_target = cycle_id
    if repaired:
        logger.warning(
            "Resume corrected round(s) %s in memory; measuring what the correction reached "
            "before deciding where it belongs.",
            ", ".join(str(r) for r in repaired),
        )
        cycle.replay_priors(prior)
        packages_after = _round_packages(cycle, prior)
        by_round = {t.round: t for t in prior}
        drifted: list[RoundResult] = []
        for rn, after in sorted(packages_after.items()):
            moved = sorted(n for n, h in after.items() if packages_before.get(rn, {}).get(n) != h)
            if not moved:
                # The repair reached no panel — the cells it plugged were never rendered to
                # any node. Nothing downstream read a different word, so the trajectory
                # stands and the resume continues on it. This is the common case and it is
                # why a repair does not simply fork.
                continue
            drifted.append(by_round[rn])
            logger.warning(
                "Round %d package drifted after repair — %s now read different evidence.",
                rn,
                ", ".join(moved),
            )
            # The divergence is raised at the round that CONSUMED the drifted package, not
            # at the one that produced it: round `rn`'s own measurements and winner are now
            # correct, while round `rn + 1` was built from a package that no longer
            # reproduces — so `rn + 1` is where the run branches.
            #
            # It is raised only if that round OWNS something. A round is more than its round
            # file: the candidates cached for the round about to run were generated from the
            # old package and are content just as much as a closed round's rows. Where it
            # owns neither, nothing was ever produced from the moved package and there is
            # nothing to branch away from — re-distilling in place is the whole remedy.
            nxt = rn + 1
            has_content = (
                nxt in by_round
                or campaign_store.load_round_candidates(campaign_id, cycle_id, nxt) is not None
            )
            if has_content and nxt not in package_divergences:
                package_divergences[nxt] = Divergence(
                    round_num=nxt,
                    kind=f"node_package:{','.join(moved)}",
                    recorded_outcome={n: packages_before.get(rn, {}).get(n) for n in moved},
                    current_outcome={n: packages_after[rn][n] for n in moved},
                    inputs_ref={"drifted_round": rn, "nodes": moved},
                )
        # WHERE the corrected rounds land, decided by whether the correction propagated —
        # the whole reason the repair above wrote nothing.
        #
        # Nothing propagated ⇒ the branch rejoins the line it came from, which is to say it
        # never happened: no reader saw a different word, so completing the record in place
        # destroys no version anything depends on. This is the common case.
        #
        # Something propagated ⇒ this is a cut, and it is taken BEFORE the corrected rounds
        # are written. `_mint_fork` copies the parent's rounds and candidate caches below the
        # cut first, so the fork starts as a faithful copy and the corrected rounds are then
        # written over it — leaving the parent byte-identical to what ran, descendants and
        # all. Written in place instead, the parent ends up holding a repaired round beside
        # candidates generated from the version it replaced: a record that never existed.
        if package_divergences:
            cut = min(package_divergences)
            repair_target = _mint_fork(
                campaign_store,
                campaign_id,
                session.session_id,
                cycle_id,
                cut,
                ForkSpec(
                    trigger=ForkTrigger.SCORING_DIVERGENCE,
                    reason=f"repair_propagated:round_{cut - 1}",
                    issued_by="system",
                ),
                surviving_rounds=list(prior[:cut]),
            )
            logger.warning(
                "Repair reached what round %d was built from, so it is a cut, not a "
                "correction: branched → %s, which carries the repaired rounds. This cycle "
                "keeps round(s) %s exactly as they ran.",
                cut,
                repair_target,
                ", ".join(str(r) for r in repaired),
            )
        for rn in repaired:
            campaign_store.save_round_file(campaign_id, repair_target, by_round[rn])
        if drifted:
            await _rederive_critiques(
                campaign_store, campaign_id, repair_target, session, cycle, drifted
            )
        if package_divergences:
            cut = min(package_divergences)
            survivors = list(prior[:cut])
            cycle.replay_priors(survivors)
            cycle.escalation = EscalationFSM.from_ledger(
                session.state.ledger, lives=cycle.config.optimization.lives
            )
            return ForkResult(new_cycle_id=repair_target, new_resumed_from_round=cut)

    if not skip_divergence_check:
        # Asked BEFORE the config diff, and not gated by it: `classify_config_diff` walks
        # `KNOBS` — the campaign's own config — and the optimizer's prompts, layouts and
        # node models are not knobs, so a `DiffScope.NONE` verdict says nothing at all about
        # whether the optimizer changed. Gating this behind that verdict is what would let
        # an edited prompt ride a config-clean resume straight into the trajectory.
        optimizer_divergences = _optimizer_divergences(prior)
        # Asked here, above the short-circuit, because it is the one check that answers for a
        # change made by an EARLIER resume: the repair that moved a round and the run that
        # continues from it are usually different processes, and by the second there is
        # nothing left to repair, no drift to compute, and a config that diffs clean. Gated
        # behind any of those, a stale generation replays forever.
        stale = _stale_generation(campaign_store, campaign_id, cycle_id, prior, resumed_from_round)
        if stale is not None and resumed_from_round not in package_divergences:
            package_divergences[resumed_from_round] = stale
        campaign = campaign_store.load_campaign(campaign_id)
        frozen = campaign.config if campaign is not None else {}
        scope, diffed = classify_config_diff(cycle.config, frozen)
        if (
            not repaired
            and not optimizer_divergences
            and not package_divergences
            and scope
            in (
                DiffScope.NONE,
                DiffScope.POLICY_ONLY,
            )
        ):
            if scope is DiffScope.POLICY_ONLY:
                logger.info(
                    "Resume: policy-only config diff (%s); continuing on cycle %s in-place",
                    ", ".join(diffed),
                    cycle_id,
                )
                # Refresh the campaign snapshot so future resumes diff
                # against current state.
                campaign_store.update_campaign(
                    campaign_id, {"config": freeze_campaign_config(cycle.config)}
                )
            cycle.escalation = EscalationFSM.from_ledger(
                session.state.ledger, lives=cycle.config.optimization.lives
            )
            return None
        if scope is DiffScope.DATA_AFFECTING and diffed:
            logger.info(
                "Resume: data-affecting config diff (%s); running divergence check",
                ", ".join(diffed),
            )

        # A resume repairing itself is not a divergence the operator has to adjudicate — it is
        # the resume doing its job. `resume` means "carry on from what is there": find what is
        # broken, fix it, keep what still holds, regenerate what does not. So the branches
        # this resume CAUSED (a repair, a stale generation) take themselves; only a change
        # from OUTSIDE — a different scorer, a different optimizer — is the operator's call
        # to make, and that is what `--fork-on-divergence` still decides.
        self_inflicted = set(package_divergences)

        async def _branch_or_halt(div: Divergence, survivors: list[RoundResult]) -> ForkResult:
            """The ONE exit every divergence takes: branch at ``div.round_num``, or halt.

            The cut is a ``SCORING_DIVERGENCE``, which **supersedes** — the new cycle is the
            continuation the active pointer moves to, and the parent is what was left behind
            (``FORK_DIRECTION``). That is why nothing here deletes: whatever the old package
            produced above the cut simply stays with the branch that produced it, because
            ``copy_parent_rounds_and_candidates(before_round=N)`` carries rounds AND their
            candidate caches below the cut and nothing above it.
            """
            if not fork_on_divergence and div.round_num not in self_inflicted:
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
                campaign_id,
                session.session_id,
                cycle_id,
                div.round_num,
                ForkSpec(
                    trigger=ForkTrigger.SCORING_DIVERGENCE,
                    reason=f"resume_divergence:{div.kind}",
                    issued_by="system",
                ),
                surviving_rounds=survivors,
            )
            cycle.replay_priors(survivors)
            cycle.escalation = EscalationFSM.from_ledger(
                session.state.ledger, lives=cycle.config.optimization.lives
            )
            logger.warning(
                "Resume diverged at round %d (%s); branched → %s. That cycle is the "
                "continuation; this one keeps what the superseded package produced.",
                div.round_num,
                div.kind,
                new_cycle_id,
            )
            return ForkResult(new_cycle_id=new_cycle_id, new_resumed_from_round=div.round_num)

        for i, t in enumerate(prior):
            # Three ways a recorded round can stop holding, asked outermost-first: a
            # different OPTIMIZER produced it (the only one visible across a restart), its
            # inputs no longer reproduce (a package that drifted when this resume repaired
            # an earlier round), or its outcome no longer re-derives under the active
            # scorer. All three land as one `Divergence` and take the same exit.
            div = (
                optimizer_divergences.get(t.round)
                or package_divergences.get(t.round)
                or replay_decisions(
                    t,
                    origin_results=origin_results_rescored,
                    delta_scale=cycle.delta_scale,
                )
            )
            if div is not None:
                return await _branch_or_halt(div, list(prior[:i]))

        # The round about to run has no round file, so the loop cannot visit it — and it is
        # the round most likely to be holding content built from a package this resume just
        # moved. Same statement, same exit; only the survivor set differs, because every
        # prior round is carried across the cut.
        if (pending := package_divergences.get(resumed_from_round)) is not None:
            return await _branch_or_halt(pending, list(prior))

    cycle.escalation = EscalationFSM.from_ledger(
        session.state.ledger, lives=cycle.config.optimization.lives
    )
    return None
