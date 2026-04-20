"""Decision records and replayers — the kind-agnostic divergence mechanism.

Every loop decision (round winner, sequential-elimination cut, L2/L3
escalation trigger, …anything future) is a pure function of scored
results plus local state. We persist each decision's ``kind`` + minimal
``inputs_ref`` pointers + ``outcome`` inside the trial JSON. On resume,
we rescore the trial's per-query results under the currently active
scorer and re-run each decision via a kind-keyed replayer registry. The
first mismatch is a :class:`Divergence`, which the CLI converts into a
:class:`ResumeDivergenceError` and the user either reverts the scorer or
runs ``fork`` to branch.

The shape is "stable forever": adding a new decision kind is one call to
:func:`record_decision` at the site plus a replayer registered via
:func:`replayer`. Neither the walker nor the fork machinery ever inspects
``kind``.

Option A per design: ``inputs_ref`` stores only pointers (candidate_ids,
round_num, thresholds) — replayers re-derive their own inputs from the
trial's rescored view. Trial JSONs stay compact; replay genuinely
re-executes the decision rather than comparing cached numbers.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "REPLAYERS",
    "Decision",
    "Divergence",
    "ReplayContext",
    "record_decision",
    "replay_decisions",
    "replayer",
]


@dataclass(frozen=True)
class Decision:
    """One recorded decision in a trial.

    Two-tier shape:

    - ``inputs_ref`` + ``outcome`` are the flow-determining half.
      ``outcome`` is compared against the replayer's re-derivation for
      divergence detection.
    - ``data`` is an archival sidecar (LLM outputs, diagnostics,
      meta-optimization context). ``replay_decisions`` never reads it —
      rescoring wiggles that leave ``outcome`` intact do not flip the
      archival payload either.
    """

    kind: str
    inputs_ref: dict[str, Any]
    outcome: Any
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "inputs_ref": dict(self.inputs_ref),
            "outcome": self.outcome,
            "data": dict(self.data),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Decision:
        return cls(
            kind=d["kind"],
            inputs_ref=dict(d.get("inputs_ref") or {}),
            outcome=d.get("outcome"),
            data=dict(d.get("data") or {}),
        )


@dataclass(frozen=True)
class Divergence:
    """A recorded decision re-derived to a different outcome under the current scorer."""

    round_num: int
    kind: str
    recorded_outcome: Any
    current_outcome: Any
    inputs_ref: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReplayContext:
    """Context passed to replayers.

    ``trial`` is the currently-being-replayed trial dict with its
    ``results`` / ``all_candidate_results`` already rescored under the
    active scorer — replayers read from this directly.

    ``prior_trials`` carries earlier rounds (same rescoring applied) for
    decisions that span multiple rounds (L2/L3 improvement-gate triggers).

    ``baseline_results`` is the per-query baseline evaluation list
    (rescored under the active scorer) — the round-0 predecessor for
    threshold derivation when ``prior_trials`` is empty.
    """

    trial: dict[str, Any]
    prior_trials: list[dict[str, Any]] = field(default_factory=list)
    baseline_results: list[dict[str, Any]] = field(default_factory=list)


Replayer = Callable[[ReplayContext, dict[str, Any]], Any]

REPLAYERS: dict[str, Replayer] = {}


def replayer(kind: str) -> Callable[[Replayer], Replayer]:
    """Register a replayer function for a decision kind."""

    def deco(fn: Replayer) -> Replayer:
        REPLAYERS[kind] = fn
        return fn

    return deco


def record_decision(
    decisions: list[dict[str, Any]],
    kind: str,
    inputs_ref: dict[str, Any],
    outcome: Any,
    *,
    data: dict[str, Any] | None = None,
) -> Any:
    """Append a ``Decision`` to *decisions* and return *outcome* for passthrough.

    ``data`` is an archival sidecar (LLM outputs, diagnostics). Not read
    by :func:`replay_decisions`. Call sites stay one-line::

        winner_id = record_decision(
            state.current_decisions,
            "round_winner",
            {"candidate_ids": [...], "round_num": R},
            _select_round_winner(...),
        )
    """
    decisions.append(Decision(kind, dict(inputs_ref), outcome, dict(data or {})).to_dict())
    return outcome


def replay_decisions(
    trial: dict[str, Any],
    prior_trials: list[dict[str, Any]] | None = None,
    baseline_results: list[dict[str, Any]] | None = None,
) -> Divergence | None:
    """Walk ``trial['decisions']`` in order; return the first mismatch.

    Unknown kinds (added in a newer version, resumed under an older one)
    are silently skipped — forward-compat. A decision whose replayer
    raises is treated as a non-divergence (``logged, continue``); hard
    failures belong in the scorer or replayer itself, not here.

    ``baseline_results`` threads the rescored baseline to replayers that
    need a round-0 threshold (currently ``round_winner``).
    """
    ctx = ReplayContext(
        trial=trial,
        prior_trials=list(prior_trials or []),
        baseline_results=list(baseline_results or []),
    )
    for rec in trial.get("decisions") or []:
        kind = rec.get("kind", "")
        fn = REPLAYERS.get(kind)
        if fn is None:
            continue
        try:
            current = fn(ctx, rec.get("inputs_ref") or {})
        except Exception:
            # Replayer failure shouldn't poison resume — treat as non-divergence.
            continue
        recorded = rec.get("outcome")
        if current != recorded:
            return Divergence(
                round_num=int(trial.get("round", -1)),
                kind=kind,
                recorded_outcome=recorded,
                current_outcome=current,
                inputs_ref=dict(rec.get("inputs_ref") or {}),
            )
    return None


# ---------------------------------------------------------------------------
# Built-in replayers
# ---------------------------------------------------------------------------


def _mean_score(results: list[dict]) -> float:
    """Mean of the active-scorer projection (``score``) over results.

    Expects results to have been rescored by the caller before replay.
    """
    if not results:
        return 0.0
    return sum(float(r.get("score", 0.0)) for r in results) / len(results)


@replayer("round_winner")
def _replay_round_winner(ctx: ReplayContext, inputs_ref: dict[str, Any]) -> str:
    """Re-derive the round's winner id from rescored per-candidate results.

    Mirrors ``_select_round_winner`` in ``application/optimization/nodes/score.py``:
    both sides compare candidates on ``mean(score)`` with strict ``>``.
    ``improvement_threshold`` only affects the node's ``improved`` display
    flag, not winner selection.

    The beat-threshold is **derived**, never read from ``inputs_ref`` —
    a stale persisted threshold (computed under the old scorer) would
    manufacture false divergences. Derivation order:

    - If ``prior_trials`` is non-empty: mean of the most recent prior
      trial's rescored winner results (``trial['results']``).
    - Else: mean of the rescored baseline results
      (``ctx.baseline_results``).

    ``inputs_ref`` carries only ``candidate_ids`` (pointers). Reads
    ``trial['all_candidate_results']`` — per-candidate result lists,
    already rescored under the active scorer at trial-load time.
    """
    all_results: dict[str, list[dict]] = ctx.trial.get("all_candidate_results") or {}
    if ctx.prior_trials:
        best_acc = _mean_score(list(ctx.prior_trials[-1].get("results") or []))
    else:
        best_acc = _mean_score(list(ctx.baseline_results))
    winner_id = ""
    for cid in inputs_ref.get("candidate_ids") or []:
        acc = _mean_score(all_results.get(cid) or [])
        if acc > best_acc:
            best_acc = acc
            winner_id = cid
    return winner_id


@replayer("elimination_cut")
def _replay_elimination_cut(ctx: ReplayContext, inputs_ref: dict[str, Any]) -> bool:
    """Re-run the Wilcoxon signed-rank gate under rescored scores.

    ``inputs_ref`` carries:

    - ``candidate_id``: the candidate whose evaluation was stopped.
    - ``prior_candidate_ids``: ids of fully-completed candidates whose
      score vectors were the priors at test time, in the order they
      were registered (= the order they finished).
    - ``queries_evaluated``: how many query scores the candidate had
      produced when the test fired (use this as the prefix length).
    - ``alpha``: Holm-Bonferroni family-wise alpha.

    The test reads per-candidate rescored ``score`` lists from
    ``trial['all_candidate_results']`` and returns the gate bool.
    """
    from promptpotter.shared.statistics import should_stop_early

    all_results: dict[str, list[dict]] = ctx.trial.get("all_candidate_results") or {}
    candidate_id = str(inputs_ref.get("candidate_id", ""))
    prior_ids = list(inputs_ref.get("prior_candidate_ids") or [])
    n = int(inputs_ref.get("queries_evaluated", 0))
    alpha = float(inputs_ref.get("alpha", 0.2))

    current = [float(r.get("score", 0.0)) for r in (all_results.get(candidate_id) or [])[:n]]
    priors = [
        [float(r.get("score", 0.0)) for r in (all_results.get(pid) or [])] for pid in prior_ids
    ]
    priors = [p for p in priors if p]
    if not priors or len(current) < 2:
        return False
    stop, _ = should_stop_early(current, priors, alpha)
    return bool(stop)


def _rescored_composite(trial: dict[str, Any]) -> float:
    """Approximate rescored composite as the mean of the winner's rescored scores.

    Rescoring rewrites per-query ``score`` fields in place; the cached
    ``composite`` on the trial dict is stale. Mean-of-winner-scores is
    the simplest rescore-sensitive proxy and matches the active
    scorer's accuracy projection. Falls back to the cached value if
    ``results`` is empty (probe rounds, etc.).
    """
    winner_results = trial.get("results") or []
    if winner_results:
        return _mean_score(winner_results)
    return float(trial.get("composite", trial.get("accuracy", 0.0)))


def _derive_stall_count(
    prior_trials: list[dict[str, Any]],
    entry_round: int,
    this_round: int,
) -> int:
    """Reconstruct ``LayerCounter.stall_count`` at the end of ``this_round``.

    ``state.best_composite`` is a running max across rounds. The layer's
    ``record_outcome`` compares that running max against
    ``best_composite_at_entry``; stall resets whenever it exceeds the
    baseline. Because running max is monotonic non-decreasing, the stall
    at round N after entry at E simplifies to:

    - 0 if any round in (E, N] had composite strictly greater than the
      running-max-at-E (the baseline),
    - N - E otherwise (strictly post-entry rounds, none of which
      improved).

    Returns 0 when ``entry_round`` is negative (L2 never fired → gate
    has never counted stalls).
    """
    if entry_round < 0:
        return 0
    sorted_trials = sorted(prior_trials, key=lambda t: int(t.get("round", -1)))
    running_max = 0.0
    baseline: float | None = None
    rounds_after = 0
    for t in sorted_trials:
        r = int(t.get("round", -1))
        if r < 0 or r > this_round:
            continue
        comp = _rescored_composite(t)
        running_max = max(running_max, comp)
        if r <= entry_round:
            if r == entry_round:
                baseline = running_max
            continue
        rounds_after += 1
        if baseline is not None and running_max > baseline:
            return 0
    return rounds_after if baseline is not None else 0


@replayer("l2_escalation_trigger")
def _replay_l2_trigger(ctx: ReplayContext, inputs_ref: dict[str, Any]) -> bool:
    """Re-derive whether L2 fires under rescored trials.

    ``inputs_ref``:
    - ``round_num``: the round at which this trigger was evaluated.
    - ``l2_patience``: threshold (int) or ``None``.
    - ``from_degradation``: if True, replayer echoes recorded outcome
      (degradation-triggered L2 isn't rescore-replayable in this pass).
    - ``entry_round``: round that reset the L2 stall counter (the round
      where L2 last fired, or -1 for "never fired yet").

    ``outcome``: bool — L2 fired (True) or patience-deferred (False).
    """
    if bool(inputs_ref.get("from_degradation", False)):
        # Degradation-triggered L2 is not rescore-replayable in this pass
        # (would require re-running the degradation signal detector). Raise
        # so ``replay_decisions`` treats this as non-divergence — the
        # record + ``data`` archive still drive meta-optimization analysis.
        raise NotImplementedError("degradation-triggered L2 is archive-only for replay")
    patience = inputs_ref.get("l2_patience")
    if patience is None:
        return True
    entry_round = int(inputs_ref.get("entry_round", -1))
    this_round = int(inputs_ref.get("round_num", -1))
    stalls = _derive_stall_count(ctx.prior_trials, entry_round, this_round)
    return stalls < int(patience)


@replayer("l3_escalation_trigger")
def _replay_l3_trigger(ctx: ReplayContext, inputs_ref: dict[str, Any]) -> bool:
    """Re-derive whether L3 fires. Same shape as ``l2_escalation_trigger``."""
    patience = inputs_ref.get("l3_patience")
    if patience is None:
        return True
    entry_round = int(inputs_ref.get("entry_round", -1))
    this_round = int(inputs_ref.get("round_num", -1))
    stalls = _derive_stall_count(ctx.prior_trials, entry_round, this_round)
    return stalls < int(patience)


# probe_round_commitment — recorded but intentionally NOT replayed.
# Probe is a projection of L2's LLM output (``action == "probe"``).
# Under a pure scorer swap it's invariant; under a meta-optimizer config
# swap replay would require re-calling the changed L2, which is out of
# scope for the rescore-on-load divergence mechanism. The record's
# ``data`` archive (directive preview, warned-query summary) still
# matters for meta-optimization attribution of downstream divergences.
# Unknown-kind decisions are silently skipped by ``replay_decisions``
# (see the loop above) — that's the mechanism we rely on here.
