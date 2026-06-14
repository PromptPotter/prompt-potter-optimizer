"""Round and run outcome models — pure pydantic types, no I/O."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field, computed_field

from promptpotter.domain.escalation_signals import (
    EscalationSignal,
    RuntimeFailure,
    ValidationFailure,
)
from promptpotter.domain.opt_search_point import EvidenceGrounding, OptSearchPoint
from promptpotter.domain.round_diagnostics import RoundDiagnostics
from promptpotter.domain.run_records import DecisionRecord

__all__ = [
    "CandidateProposal",
    "CritiqueReadout",
    "CycleResult",
    "DegradationContext",
    "DegradationHealth",
    "DiagnosticRunRecord",
    "EliminationContext",
    "PayloadOutcome",
    "RoundMetadata",
    "RoundOrigin",
    "RoundPayload",
    "RoundResult",
    "RoundSummary",
    "RoundSummaryCandidate",
    "SampleOrderStep",
    "ScoredCandidate",
    "SweepBatchResult",
    "WarningDict",
    "assemble_prior_healths",
    "candidate_label",
    "classify_sample_failure",
    "compute_degradation_health",
    "compute_round_health",
    "is_round_winner",
]

# Degradation-health thresholds (explicit — no hidden defaults). The verdict
# distinguishes a structurally-broken pipeline (abort-worthy) from transient
# backend noise (keep going), weighted by track record. See
# ``compute_degradation_health``.
STRUCTURAL_FLAG_RATE: float = 0.30
"""Fraction of samples whose pipeline node *hard-failed* (``step_statuses``
``failed`` — e.g. a schema/json break) at/above which the round is structurally
broken → ``critical``, regardless of track record."""

DEGRADED_RATE_FLAG: float = 0.20
"""Fraction of samples that degraded at all (failed OR soft) at/above which a
round with a clean track record is at least ``degraded``."""

CONSECUTIVE_DEGRADED_CRITICAL: int = 3
"""Consecutive degraded rounds at/above which persistence alone escalates to
``critical`` — a sustained problem, not a one-round blip."""


class EliminationContext(TypedDict, total=False):
    """PoBB exit context on a ``ScoredCandidate`` — written by ``decode_signal_effect``
    when the elimination check fires. Empty ``{}`` when the candidate wasn't cut.
    ``leader_label`` is decorated post-construction (needs prior-rank lookup), so the
    whole shape is ``total=False``. Disjoint from :class:`DegradationContext`."""

    p_best: float
    epsilon: float
    leader_id: str
    queries_scored: int
    total_queries: int
    n_priors: int
    leader_locked: bool
    leader_label: str


class DegradationContext(TypedDict, total=False):
    """DegradationCheck / scoring-error-abort exit context on a ``ScoredCandidate``.
    Empty ``{}`` when the candidate wasn't degradation-cut. Disjoint from
    :class:`EliminationContext` — the renderer branches on which is non-empty."""

    degraded_rate: float
    degraded_count: int
    total_scored: int
    dominant_warning: str
    fatal: bool
    warning_types: dict[str, int]
    source: str


class CritiqueReadout(TypedDict, total=False):
    """Serialized ``L1CritiqueOutput`` as it rides ``RoundResult.critique`` and the
    dispatch ``RoundDigest``. Domain-local mirror of the three load-bearing fields so
    a round file round-trips without the optimization layer's schema in scope; the
    optimizer node's full Pydantic shape stays in ``dispatch/schemas.py``."""

    priority_fix: str
    suggested_axes: list[str]
    failure_highlights: list[str]


def candidate_label(round_num: int, idx: int) -> str:
    """Canonical candidate label — `C0` for origin, `C{N}.{idx+1}` otherwise. Sole writer."""
    if round_num == 0:
        return "C0"
    return f"C{round_num}.{idx + 1}"


def is_round_winner(changes_description: str | None, winner_label: str) -> bool:
    """The round winner is the candidate whose diff description equals the round's
    elected label. Sole definition of the rule — the round-file scoreboard
    (`_build_scoreboard`) and the dashboard round summary (`build_round_summary`)
    both call this so the operator-visible winner flag can't diverge between the
    two surfaces."""
    return bool(changes_description) and changes_description == winner_label


class ScoredCandidate(BaseModel):
    """One candidate's L1 score report — the single shape for round-file scores.

    ``model_dump()`` *is* the wire format; ``model_validate()`` reads it back.
    ``ci_lo``/``ci_hi`` are computed from ``hits``/``total`` (sole Wilson site),
    so they round-trip through serialization without being stored or recomputed
    by readers.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    candidate_id: str
    label: str
    changes_description: str = ""
    accuracy: float
    composite_fitness: float
    hits: int
    total: int
    evaluators: dict[str, float] = Field(default_factory=dict)
    pipeline_params_override: dict[str, Any] | None = None
    # The candidate's evolved prompt (``OptSearchPoint.prompt_field_dict()`` shape).
    # Paired with ``pipeline_params_override`` this is the full searchpoint an
    # operator selects to seed an operator-steered fork (read side, Decision F).
    prompt_fields: dict[str, Any] = Field(default_factory=dict)
    escalation_aborted: bool = False
    elimination_stopped: bool = False
    scored_samples: int = 0
    expected_samples: int = 0
    invalid: bool = False
    resumed_from_cache: bool = False
    validation_failures: list[ValidationFailure] = Field(default_factory=list)
    runtime_failures: list[RuntimeFailure] = Field(default_factory=list)
    elimination_context: EliminationContext = Field(default_factory=EliminationContext)
    degradation_context: DegradationContext = Field(default_factory=DegradationContext)
    # Origin restricted to this candidate's measured samples — apples-to-apples
    # when PoBB locks mid-budget; equals full-set origin when fully scored.
    matched_origin_accuracy: float = 0.0
    matched_origin_hits: int = 0
    matched_origin_composite: float = 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ci_lo(self) -> float:
        from promptpotter.shared.statistics import wilson_ci

        return wilson_ci(self.hits, self.total)[0]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ci_hi(self) -> float:
        from promptpotter.shared.statistics import wilson_ci

        return wilson_ci(self.hits, self.total)[1]


class CandidateProposal(BaseModel):
    """LLM-proposed candidate — OSP + nested pipeline-params override, persisted across generate→score for resume replay."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    osp: OptSearchPoint
    pipeline_params_override: dict[str, dict[str, Any]] = Field(default_factory=dict)
    evidence_grounding: EvidenceGrounding | None = Field(
        default=None,
        description="Mirrors osp.lineage.evidence_grounding — duplicated so audit / display sites can read it before OSP construction.",
    )


class RoundOrigin(BaseModel):
    """The round's challenger anchor. On probe rounds, scalars reflect the probe subset, not the full set."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    accuracy: float
    composite_fitness: float
    osp: OptSearchPoint
    # Per-sample ``QueryMeasurement`` rows plus open-ended stale-data protocol
    # markers (``retry_of_degraded`` etc.) — kept ``dict`` so the markers survive
    # serialization (a closed model would strip them); readers cast at hot sites.
    results: list[dict[str, Any]] = Field(default_factory=list)
    label: str
    evaluators: dict[str, float] = Field(default_factory=dict)


class RoundMetadata(BaseModel):
    """Checkpoint-critical scalars (no raw payloads); `RoundResult` inherits flat for wire-format compat."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    round: int
    label: str
    accuracy: float
    composite_fitness: float = 0.0
    hits: int
    total: int
    improved: bool
    # One-sided two-proportion p-value vs origin; drives the IMPROVED gate. None ⇒ no test ran.
    p_value: float | None = None
    # When `improved=False` despite Δ>0: which of {delta_threshold, significance, min_samples} blocked promotion.
    improved_reason: str | None = None
    degraded_samples: int = 0
    # Fatal-warning samples discarded from hits/total/accuracy on the winner's run.
    deprecated: int = 0
    escalation_signal: EscalationSignal | None = None
    # Origin restricted to the winner's measured samples — apples-to-apples when PoBB locks
    # at q8/20 while origin has 20. Drives `improved`, p_value, verdict Δ.
    matched_origin_accuracy: float = 0.0
    matched_origin_hits: int = 0
    matched_origin_composite: float = 0.0
    # Per-sample best-so-far across rounds; dashboard renders "current best on N measured" without walking priors.
    cumulative_total: int = 0
    cumulative_accuracy: float = 0.0


class SampleOrderStep(BaseModel):
    """One measurement step in a round's adaptive sample-selection timeline.

    Frozen snapshot of the adaptive queue mechanism's state the moment it picked
    the ``step``-th sample: ``computed`` are the samples already measured (in
    measurement order), ``planned`` the picker's intended order for the remaining
    samples under the posterior at that step, ``current_sample_id`` the one about
    to be measured (``planned[0]``, or ``None`` at the terminal step). The
    ``planned`` tail re-ranks every step, so each row is the *frozen* plan as it
    was then — the trajectory hover reads one row per step.
    """

    model_config = ConfigDict(frozen=True)

    step: int
    current_sample_id: int | None = None
    computed: list[int] = Field(default_factory=list)
    planned: list[int] = Field(default_factory=list)


class RoundPayload(BaseModel):
    """Raw round payload — per-candidate detail for replay, audit, full rendering."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    prompt_fields: dict[str, Any]
    pipeline_params: dict[str, Any] | None = None
    # Prior round's accuracy (or campaign origin for round 0).
    origin_accuracy: float = 0.0
    # Per-sample rows — ``QueryMeasurement`` + stale-data markers (see ``RoundOrigin.results``).
    results: list[dict[str, Any]] = Field(default_factory=list)
    # Per-candidate scored results — lets resume rescore under a changed scorer + replay decisions.
    all_candidate_results: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    candidates_scored: int
    candidate_scores: list[ScoredCandidate] = Field(default_factory=list)
    # ResumeCheckpoint records consumed by the divergence-replay walker.
    decisions: list[DecisionRecord] = Field(default_factory=list)
    evaluators: dict[str, float] = Field(default_factory=dict)
    # L1 yield + failure counts; defaults assume "all valid" for replay paths bypassing the detector.
    l1_yield: float = 1.0
    l1_n_no_op: int = 0
    l1_n_duplicate: int = 0
    # Adaptive-queue-mechanism sample-selection timeline for the round's
    # representative (longest-surviving) candidate — one row per measurement
    # step, each a frozen (computed, planned) split. Mirrors ``selection``'s
    # longest-candidate rule (``round_summary._measurement_order``) so the
    # executed prefix lines up. Empty on replay / cache paths that bypass the
    # online picker.
    sample_order_timeline: list[SampleOrderStep] = Field(default_factory=list)


class RoundResult(RoundMetadata, RoundPayload):
    """Flat conjunction of `RoundMetadata` + `RoundPayload`, wire-compatible with `index.json`.

    `diagnostics` + `critique` computed post-scoring; read by dispatch's `diagnostics`/`critique` signals.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    diagnostics: RoundDiagnostics | None = None
    critique: CritiqueReadout | None = None
    # Context-aware degradation verdict, stamped at round close (sole compute
    # site); every surface (dashboard summary, CLI, round file) renders this.
    health: DegradationHealth | None = None


class CycleError(BaseModel):
    """Structured error carried on :class:`CycleResult` when the runner exited
    on ``CRASHED`` / ``RENDER_ERROR`` / ``DIVERGED``.

    Mirrors the trailing ``ErrorRecord`` on the canonical ledger; carried as a
    typed field so :class:`~promptpotter.application.jobs.registry.JobRegistry`
    and the post-run teardown read crash detail from one place without
    coupling to projection state. The runner's ``except`` sites build this
    instance and call ``emit_error_record`` from the same kwargs.
    """

    model_config = ConfigDict(frozen=True)

    kind: str
    message: str
    traceback: str | None = None


class CycleResult(BaseModel):
    """Final result of the feedback cycling process."""

    rounds: list[RoundResult]
    n_rounds: int
    best_accuracy: float
    best_round: int
    origin_accuracy: float
    winner_prompt_fields: dict[str, Any]
    winner_pipeline_params: dict[str, Any] | None = None
    stop_reason: str
    started_at: str
    finished_at: str
    langfuse_trace_id: str | None = None
    cycle_id: str | None = None
    session_id: str | None = None
    resumed_from_round: int = 1
    # Set when ``stop_reason`` ∈ ``{CRASHED, RENDER_ERROR, DIVERGED}``;
    # ``None`` on clean completions. The runner's ``except`` sites populate
    # this in lockstep with the ``emit_error_record`` ledger append.
    error: CycleError | None = None


class DiagnosticRunRecord(BaseModel):
    """One on-demand candidate verification → webapp Verify tab.

    Per-sample data lands in `archive/measurements/`; this record carries the
    workspace-scope verdict (did the source-campaign composite hold).
    """

    model_config = ConfigDict(frozen=True)

    ts: str
    dataset: str
    source_campaign: str
    source_cycle: str
    source_label: str
    source_candidate_id: str
    config_hash: str
    samples_requested: int
    samples_added: int
    workspace_n: int
    workspace_accuracy: float
    workspace_composite: float
    source_campaign_accuracy: float
    source_campaign_composite: float
    source_campaign_n: int


class RoundSummaryCandidate(BaseModel):
    """Display-summary row for `dashboard.json::rounds[].candidates` — chart/lineage/sparkline subset of `ScoredCandidate`."""

    model_config = ConfigDict(frozen=True)

    candidate_id: str
    label: str
    accuracy: float
    composite_fitness: float
    scored_samples: int
    expected_samples: int
    is_winner: bool
    evaluators: dict[str, float] = Field(default_factory=dict)
    changes_description: str = ""


class WarningDict(TypedDict):
    """One backend diagnostics warning as it rides ``round_file::results[].pipeline_data.diagnostics.warnings``.

    ``kind`` is **source-stamped by the backend** (TermNorm's ``WarningKind`` enum —
    ``structural`` = config/schema fault the operator must fix, ``transient`` =
    recoverable noise). PromptPotter reads it directly and keeps NO shadow code→kind
    taxonomy: an absent or unrecognized ``kind`` is SKIPPED, never guessed. That
    inverts the old failure mode safely — a backend site that forgets to stamp
    under-counts (and surfaces in the live smoke), instead of silently grading a
    transient blip as an abort-worthy ``structural`` break."""

    step: str
    code: str
    message: str
    kind: str
    details: NotRequired[list[Any]]
    stats: NotRequired[dict[str, Any]]


def classify_sample_failure(
    step_statuses: Mapping[str, str],
    warnings: Sequence[WarningDict],
) -> tuple[str | None, str | None]:
    """Classify ONE sample's pipeline outcome AND attribute the causing node.

    Returns ``(kind, causing_node)`` — ``kind ∈ {"structural","transient",None}``.
    Reads the **source-stamped** ``WarningDict.kind`` (no PromptPotter-side code
    taxonomy); an absent/unrecognized kind is SKIPPED, never defaulted to structural
    (that was the shadow-taxonomy bug — it false-alarmed ``critical`` on ordinary
    transient noise the backend's frozenset hadn't enumerated).

    Attribution follows the **warning-bearing** node (the explained failure), not raw
    ``step_statuses``: a node merely stamped ``failed`` with no warning is silent
    collateral (e.g. an upstream node marked failed because the real break was
    downstream), and must NOT outvote the node that actually broke. A node that DID
    warn — even with an unclassifiable kind — counts as *explained*, so the bare-
    ``failed`` fallback won't re-grade it. Only a genuinely silent ``failed`` (no
    warning at all) drives the verdict structural. ``causing_node`` is ``None`` when clean."""
    structural_node: str | None = None
    transient_node: str | None = None
    warned_nodes: set[str] = set()
    for w in warnings or []:
        node = str(w.get("step") or "") or None
        if node is not None:
            warned_nodes.add(node)  # explained (has a warning), even if kind is unclassifiable
        kind = w.get("kind")
        if kind == "structural" and structural_node is None:
            structural_node = node
        elif kind == "transient" and transient_node is None:
            transient_node = node
        # else: missing/unknown kind → skip, no default. The node is already in
        # warned_nodes, so the silent-failed fallback won't re-grade it structural.
    if structural_node is not None:
        return "structural", structural_node
    # A node stamped ``failed`` with NO warning is an unexplained hard break →
    # structural. A failed node that DID warn is already classified by that warning
    # (e.g. a 429 ``rate_limited`` → transient), so it must not fall through.
    silent_failed = [
        n for n, st in step_statuses.items() if st == "failed" and n not in warned_nodes
    ]
    if silent_failed:
        return "structural", silent_failed[0]
    if transient_node is not None:
        return "transient", transient_node
    degraded = [n for n, st in step_statuses.items() if st == "degraded"]
    if degraded:
        return "transient", degraded[0]
    return None, None


class DegradationHealth(BaseModel):
    """Backend-computed, context-aware degradation verdict for a round (origin
    included) — the single graded signal every surface renders (R-36).

    ``grade`` distinguishes a structurally-broken pipeline (``critical``,
    abort-worthy) from transient backend noise (``degraded``, keep going) from a
    sound round (``healthy``). The SAME degradation grades differently by track
    record: structural failures at an untested config (``prior_clean_rounds == 0``)
    are abort-suspect, while the same isolated failure deep in a proven campaign is
    noise. The verdict NEVER stops the run — ``suggested_action`` (set only at
    ``critical``) is an operator-facing recommendation, surfaced read-only."""

    model_config = ConfigDict(frozen=True)

    grade: str  # "healthy" | "degraded" | "critical"
    reasons: list[str] = Field(default_factory=list)
    samples: int
    structural_count: int
    transient_count: int
    degraded_rate: float
    consecutive_degraded_rounds: int
    prior_clean_rounds: int
    dominant_node: str | None = None
    ci_lo: float
    ci_hi: float
    suggested_action: str | None = None


def compute_degradation_health(
    *,
    hits: int,
    total: int,
    structural_count: int,
    transient_count: int,
    prior_clean_rounds: int,
    consecutive_degraded_rounds: int,
    dominant_node: str | None = None,
) -> DegradationHealth | None:
    """Grade a round's degradation health from its winner's per-sample outcomes
    plus the cycle's track record. Returns ``None`` when nothing was measured
    (``total <= 0``) — genuinely no verdict, not a fabricated clean one.

    Precedence (first match wins), thresholds = the module constants:
      * **critical** — structural failures dominate (``structural`` rate ≥
        :data:`STRUCTURAL_FLAG_RATE`), OR *any* structural failure at an untested
        config (no clean round precedes this one), OR ≥
        :data:`CONSECUTIVE_DEGRADED_CRITICAL` consecutive degraded rounds.
      * **degraded** — degraded fraction ≥ :data:`DEGRADED_RATE_FLAG` (transient /
        noise on a proven pipeline), OR an untested origin with a wide CI.
      * **healthy** — otherwise."""
    if total <= 0:
        return None
    from promptpotter.shared.statistics import wilson_ci

    ci_lo, ci_hi = wilson_ci(hits, total)
    structural_rate = structural_count / total
    degraded_rate = (structural_count + transient_count) / total
    untested = prior_clean_rounds == 0

    reasons: list[str] = []
    if structural_rate >= STRUCTURAL_FLAG_RATE:
        grade, reasons = "critical", ["structural"]
    elif untested and structural_count > 0:
        grade, reasons = "critical", ["structural_untested"]
    elif consecutive_degraded_rounds >= CONSECUTIVE_DEGRADED_CRITICAL:
        grade, reasons = "critical", ["persistent"]
    elif degraded_rate >= DEGRADED_RATE_FLAG:
        grade, reasons = "degraded", ["degraded"]
    elif untested and (ci_hi - ci_lo) >= DEGRADED_RATE_FLAG:
        grade, reasons = "degraded", ["untested"]
    else:
        grade = "healthy"

    suggested_action: str | None = None
    if grade == "critical":
        where = f"{dominant_node} " if dominant_node else ""
        if "persistent" in reasons:
            suggested_action = (
                f"{consecutive_degraded_rounds} consecutive degraded rounds — "
                "likely a persistent pipeline problem; consider aborting and fixing config."
            )
        else:
            pct = round(structural_rate * 100)
            suggested_action = (
                f"{where}failing structurally on {pct}% of samples — likely a config/schema "
                "fault, not noise; consider aborting, fixing config, and re-minting."
            )

    return DegradationHealth(
        grade=grade,
        reasons=reasons,
        samples=total,
        structural_count=structural_count,
        transient_count=transient_count,
        degraded_rate=round(degraded_rate, 6),
        consecutive_degraded_rounds=consecutive_degraded_rounds,
        prior_clean_rounds=prior_clean_rounds,
        dominant_node=dominant_node,
        ci_lo=ci_lo,
        ci_hi=ci_hi,
        suggested_action=suggested_action,
    )


def assemble_prior_healths(
    origin_health: DegradationHealth | None,
    rounds: Sequence[RoundResult],
    round_num: int,
) -> list[DegradationHealth | None]:
    """The track record for the round being closed: the origin's verdict first
    (round 0 — the floor every L1 round improves on), then the prior L1 rounds in
    order. The origin lives on ``Cycle.origin_health`` because ``Cycle.rounds`` is
    the 1-indexed L1 trajectory that omits it; the round-0 entry that resume's
    ``replay_priors`` leaves in ``rounds`` is dropped here so it isn't double-counted,
    and the round being closed (``round_num``, already appended by ``absorb_round``)
    is dropped too. Ordering is oldest→newest so ``compute_round_health``'s reversed
    consecutive-degraded walk reads most-recent-first."""
    prior: list[DegradationHealth | None] = []
    if origin_health is not None:
        prior.append(origin_health)
    prior.extend(r.health for r in rounds if r.round not in (0, round_num))
    return prior


def compute_round_health(
    *,
    hits: int,
    total: int,
    results: list[dict[str, Any]],
    prior_healths: Sequence[DegradationHealth | None],
) -> DegradationHealth | None:
    """Stamp a closed round's degradation verdict — the SINGLE computation site
    (the app-layer round close). Every surface reads ``RoundResult.health``; none
    recomputes (R-36). Counts structural/transient over the winner's per-sample
    rows (``results``) via the backend's ``step_statuses``, derives the track
    record (clean rounds + consecutive degraded run) from prior rounds' verdicts,
    then grades via :func:`compute_degradation_health`."""
    structural = transient = 0
    structural_nodes: dict[str, int] = {}
    for r in results or []:
        diag = (r.get("pipeline_data") or {}).get("diagnostics") or {}
        statuses = diag.get("step_statuses") or {}
        warnings = diag.get("warnings") or []
        kind, node = classify_sample_failure(statuses, warnings)
        if kind == "structural":
            structural += 1
            if node is not None:
                structural_nodes[node] = structural_nodes.get(node, 0) + 1
        elif kind == "transient":
            transient += 1
    # ``dominant_node`` names the structural CAUSE (warning-attributed) — never a
    # silently-cascaded ``failed`` node — so the critical message points at the node
    # that actually broke.
    dominant = (
        max(structural_nodes, key=lambda k: structural_nodes[k]) if structural_nodes else None
    )

    prior_clean = sum(1 for h in prior_healths if h is None or h.grade == "healthy")
    consecutive = 0
    if structural + transient > 0:
        consecutive = 1
        for h in reversed(list(prior_healths)):
            if h is not None and h.grade in ("degraded", "critical"):
                consecutive += 1
            else:
                break

    return compute_degradation_health(
        hits=hits,
        total=total,
        structural_count=structural,
        transient_count=transient,
        prior_clean_rounds=prior_clean,
        consecutive_degraded_rounds=consecutive,
        dominant_node=dominant,
    )


class RoundSummary(BaseModel):
    """Display row for `dashboard.json::rounds[]` — webapp's completed-round source.

    Top-level `accuracy`/`composite_fitness` mirror the winner so trend/sparkline
    don't scan `candidates`. In-flight round rides `current_round`.
    """

    model_config = ConfigDict(frozen=True)

    round: int
    accuracy: float
    composite_fitness: float
    candidates: list[RoundSummaryCandidate] = Field(default_factory=list)
    # Per-round selection from the adaptive queue mechanism — sample ids
    # in measurement order (longest candidate sequence carries the full
    # series since PoBB truncates losers, not the queue mechanism itself).
    selection: list[int] = Field(default_factory=list)
    # Backend-computed degradation verdict for this round (origin included).
    # ``None`` only when the round measured zero samples. Webapp/CLI render it;
    # never recompute (R-36).
    health: DegradationHealth | None = None


class PayloadOutcome(BaseModel):
    """Per-payload row inside a ``SweepBatchResult``."""

    source_file: str
    status: str  # completed | interrupted | skipped | skipped_already_forked
    cycle_id: str


class SweepBatchResult(BaseModel):
    """Final outcome of a sweep batch — all forks attempted, persistence finalized."""

    batch_id: str
    parent_cycle_id: str
    family_root: str
    started_at: str
    completed_at: str
    fork_cycle_ids: list[str]
    payload_outcomes: list[PayloadOutcome]
    interrupted: bool
