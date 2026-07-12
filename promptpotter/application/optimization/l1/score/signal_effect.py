"""Escalation-signal decoding for one candidate's eval state.

:func:`decode_signal_effect` folds the four overlapping reads of
``signal.check_result`` (RuntimeFailure construction, elimination context,
ELIMINATION_CUT decision payload, LEADER_LOCK_IN decision payload) into a
single pass returning one :class:`SignalEffect`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from promptpotter.application.optimization.l1.population import pobb_decision_data
from promptpotter.application.optimization.pobb.elimination import PoBBCheck
from promptpotter.config.settings import POBB_DEFAULT_EPSILON
from promptpotter.domain.escalation_signals import (
    EscalationSignal,
    NurseOwner,
    RuntimeFailure,
)
from promptpotter.domain.results import DegradationContext, EliminationContext
from promptpotter.shared.errors import ErrorCategory

# A scoring-error abort is a broken-for-all-queries fault the operator must fix
# ONLY when it is config-deterministic — dominated by CLIENT (4xx / bad schema)
# or PIPELINE (node ERROR). A transport-dominated abort (CONNECTION timeouts,
# SERVER 5xx) is a transient provider hiccup, not a broken program.
_CONFIG_DETERMINISTIC_ABORT = frozenset({ErrorCategory.CLIENT.value, ErrorCategory.PIPELINE.value})


def is_transient_scoring_abort(signal: EscalationSignal | None) -> bool:
    """True when a scoring signal is a scoring-error abort dominated by transient transport
    (CONNECTION/SERVER — a provider blip) rather than a config-deterministic break. The
    origin path reads this to refuse banking a floor a transient hiccup corrupted."""
    if signal is None or signal.check_name != "scoring_error_abort":
        return False
    return not _abort_is_config_break(signal.check_result)


def _abort_is_config_break(cr: dict[str, Any]) -> bool:
    """True when a scoring-error abort is an operator-fixable config break.

    Reads the abort's warning histogram: dominant CLIENT/PIPELINE ⇒ the
    candidate's config is broken for all queries (operator-terminal). A
    transport-dominated abort (CONNECTION/SERVER — the endpoint blipped) is
    transient infrastructure, not a program fault — treating it as terminal
    would blame a blip on the config and can escalate a hiccup to the HITL
    terminate path. Empty histogram ⇒ transient (never halt on ambiguity).
    """
    wt = cr.get("warning_types") or {}
    if not wt:
        return False
    dominant_cat = max(wt.items(), key=lambda kv: kv[1])[0]
    return str(dominant_cat) in _CONFIG_DETERMINISTIC_ABORT


class CandidateOutcome(StrEnum):
    """How ``score_one_candidate`` exited. Caller fires the report unconditionally
    and uses the tag to decide whether to break the loop or continue.

    SCORED is the default exit. SKIPPED_VALIDATION is an early return from
    path 1; it still produces a report. LEADER_LOCKED tags a candidate whose
    posterior cleared ``lock_in`` (per-candidate stop — the outer loop
    continues). ESCALATED signals the caller to break."""

    SKIPPED_VALIDATION = "skipped_validation"
    SCORED = "scored"
    LEADER_LOCKED = "leader_locked"
    ESCALATED = "escalated"


@dataclass(frozen=True)
class SignalEffect:
    """One pure decode of an ``EscalationSignal`` over a candidate's eval state.

    Folds four overlapping reads of ``signal.check_result`` (RuntimeFailure
    construction, elimination context, ELIMINATION_CUT decision payload,
    LEADER_LOCK_IN decision payload) into a single pass. The caller still
    owns leader-label decoration (needs prior-rank lookup over already-scored
    candidates) and decision emission gating. Decision payloads are kept as
    ``(inputs_ref, data)`` tuples — the ``ResumeCheckpointKind`` literal stays at
    the ``record_decision`` callsite (static check in
    ``test_no_bare_string_decision_kinds``).
    """

    aborted: bool
    elimination_stopped: bool
    leader_locked: bool
    leader_locked_loose: bool
    leader_id: str
    runtime_failure: RuntimeFailure | None
    elim_context: EliminationContext | None
    degradation_context: DegradationContext | None
    elimination_decision: tuple[dict[str, Any], dict[str, Any]] | None
    leader_lock_decision: tuple[dict[str, Any], dict[str, Any]] | None


def decode_signal_effect(
    signal: EscalationSignal | None,
    *,
    results: list[Any],
    dataset: list[Any],
    effective_pipeline_params: dict[str, Any] | None,
    round_num: int,
    elim_check: PoBBCheck,
    candidate_id: str,
    candidate_label: str,
    priors_at_test: list[str],
) -> SignalEffect:
    """Decode all per-candidate signal effects in one pass over ``check_result``."""
    if signal is None:
        return SignalEffect(False, False, False, False, "", None, None, None, None, None)

    elimination_stopped = signal.is_elimination
    leader_locked_loose = signal.is_leader_lock
    scoring_error_abort = signal.check_name == "scoring_error_abort"
    leader_locked = signal.is_leader_lock and signal.check_name == elim_check.name
    aborted = not leader_locked_loose and (scoring_error_abort or len(results) < len(dataset))

    cr = signal.check_result

    new_rf: RuntimeFailure | None = None
    if elimination_stopped and signal.check_name == "degradation":
        rf_kind: str | None = "degradation_check"
        dominant = cr.get("dominant_warning", "unknown:unknown")
        node_cfg = (effective_pipeline_params or {}).get(dominant.split(":", 1)[0], {})
        rate = float(cr.get("degraded_rate", 0.0))
    elif scoring_error_abort:
        rf_kind = "scoring_error_abort"
        dominant = str(cr.get("dominant_warning") or "scoring_error")
        node_cfg = effective_pipeline_params or {}
        dc_tmp = int(cr.get("degraded_count", 0))
        te_tmp = int(cr.get("total_scored", len(results)))
        rate = (dc_tmp / te_tmp) if te_tmp else 0.0
    else:
        rf_kind = None
    if rf_kind is not None:
        # Stamp who owns the fix. A DETERMINISTIC-for-config break (DegradationCheck
        # fatal fast-path, ``cr["fatal"]``) or a CONFIG-deterministic scoring-error abort
        # (CLIENT/PIPELINE dominant) is one the in-loop param retune cannot be relied on
        # to fix — the backend eliminated the candidate as broken-for-all-queries — so it
        # escalates to the OPERATOR (trim the schema/prompt, change the model). The
        # canonical case is the token blowout (output exceeds the provider ceiling on a
        # locked schema). A RATE-based degradation, OR a TRANSPORT-dominated abort
        # (CONNECTION/SERVER — a transient provider hiccup, not a broken program), is
        # partial noise L1 can retune around → owner L1: a blip must not be blamed on the
        # config nor routed to the HITL terminate path. The render + the L1 directive read
        # this so L1 never burns a variant "fixing" an operator-bound break.
        operator_terminal = bool(cr.get("fatal")) or (
            rf_kind == "scoring_error_abort" and _abort_is_config_break(cr)
        )
        new_rf = RuntimeFailure(
            source=rf_kind,
            dominant_warning=dominant,
            warning_types=dict(cr.get("warning_types") or {}),
            degraded_rate=rate,
            degraded_count=int(cr.get("degraded_count", 0)),
            total_scored=int(cr.get("total_scored", len(results))),
            observed_config=dict(node_cfg),
            first_seen_round=round_num,
            candidate_label=candidate_label,
            owner=NurseOwner.OPERATOR if operator_terminal else NurseOwner.L1,
        )

    elim_ctx: EliminationContext | None = None
    leader_id = ""
    if (elimination_stopped or leader_locked_loose) and signal.check_name == "elimination":
        leader_id = str(cr.get("leader_id", ""))
        elim_ctx = {
            "p_best": float(cr.get("p_best", 0.0)),
            "epsilon": float(cr.get("epsilon", POBB_DEFAULT_EPSILON)),
            "leader_id": leader_id,
            "queries_scored": int(cr.get("queries_scored", len(results))),
            "total_queries": int(cr.get("total_samples", len(dataset))),
            "n_priors": int(cr.get("n_priors", 0)),
            "leader_locked": leader_locked_loose,
        }
        margin_block = cr.get("margin")
        if cr.get("gate") == "margin" and margin_block:
            elim_ctx["gate"] = "margin"
            elim_ctx["p_clear"] = float(margin_block.get("p_clear", 0.0))
            elim_ctx["wins"] = int(margin_block.get("wins", 0))
            elim_ctx["losses"] = int(margin_block.get("losses", 0))
            elim_ctx["net"] = int(margin_block.get("net", 0))
            elim_ctx["margin"] = int(margin_block.get("margin", 0))
            elim_ctx["need"] = int(margin_block.get("need", 0))
            elim_ctx["opportunities_left"] = int(margin_block.get("opportunities", 0))

    # Degradation context — populated when DegradationCheck (or scoring-
    # error abort) fires. Disjoint from elim_ctx: the renderer reads one
    # or the other based on which check name attached to the signal.
    degrad_ctx: DegradationContext | None = None
    if elimination_stopped and signal.check_name in ("degradation", "scoring_error_abort"):
        degrad_ctx = {
            "degraded_rate": float(cr.get("degraded_rate", 0.0)),
            "degraded_count": int(cr.get("degraded_count", 0)),
            "total_scored": int(cr.get("total_scored", len(results))),
            "dominant_warning": str(cr.get("dominant_warning", "unknown")),
            "fatal": bool(cr.get("fatal", False)),
            "warning_types": dict(cr.get("warning_types") or {}),
            "source": signal.check_name,
        }

    queries_scored = int(cr.get("queries_scored", len(results)))
    recorded_p_best = float(cr.get("p_best", 0.0))
    # Paired-PoBB decision snapshot — the sample IDs in play at decision
    # time + each prior's fitness on exactly those samples. Replay reads
    # these directly (no cross-round crawl, no backfill replay).
    candidate_sample_ids = [
        str(r.get("sample_id", ""))
        for r in results[:queries_scored]
        if r.get("sample_id") is not None
    ]
    prior_histories_snapshot = elim_check.snapshot_priors(candidate_sample_ids)
    elimination_decision: tuple[dict[str, Any], dict[str, Any]] | None = None
    if elimination_stopped and signal.check_name == elim_check.name:
        # ``gate`` discriminates which elimination rule fired: the paired-margin
        # (futility) cut records under MARGIN_CUT and replays via the stratified
        # binomial re-derivation, since a TIED candidate it cuts has p_best≈0.5 and
        # would NOT re-derive under the ε-gate's ``p_best < ε`` replayer. Its decision
        # inputs (seed hit/miss strata, universe, margin, budget) ride ``margin``.
        elimination_decision = (
            {
                "candidate_id": candidate_id,
                "prior_candidate_ids": priors_at_test,
                "queries_scored": queries_scored,
                "epsilon": float(elim_check.epsilon),
                "n_min": int(elim_check.n_min),
                "round_num": round_num,
                "recorded_p_best": recorded_p_best,
                "gate": str(cr.get("gate", "epsilon")),
                "margin": dict(cr.get("margin") or {}),
            },
            pobb_decision_data(
                cr,
                candidate_sample_ids=candidate_sample_ids,
                prior_histories=prior_histories_snapshot,
            ),
        )
    leader_lock_decision: tuple[dict[str, Any], dict[str, Any]] | None = None
    if leader_locked:
        leader_lock_decision = (
            {
                "candidate_id": candidate_id,
                "prior_candidate_ids": priors_at_test,
                "queries_scored": queries_scored,
                "lock_in": float(elim_check.lock_in),
                "lock_in_n_min": int(elim_check.lock_in_n_min),
                "round_num": round_num,
                "recorded_p_best": recorded_p_best,
            },
            pobb_decision_data(
                cr,
                candidate_sample_ids=candidate_sample_ids,
                prior_histories=prior_histories_snapshot,
            ),
        )

    return SignalEffect(
        aborted=aborted,
        elimination_stopped=elimination_stopped,
        leader_locked=leader_locked,
        leader_locked_loose=leader_locked_loose,
        leader_id=leader_id,
        runtime_failure=new_rf,
        elim_context=elim_ctx,
        degradation_context=degrad_ctx,
        elimination_decision=elimination_decision,
        leader_lock_decision=leader_lock_decision,
    )


__all__ = ["CandidateOutcome", "SignalEffect", "decode_signal_effect"]
