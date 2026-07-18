"""Round degradation-health verdict engine — pure functions, no I/O.

Lifted out of ``results.py`` (which keeps the frozen data models) so the file a
reader opens for "what shape is a round record" isn't interleaved with the
~385-line grading ladder. One-way dependency: this module imports the models
(``DegradationHealth``, ``WarningDict``, ``RoundResult``) from ``results``;
``results`` never imports back.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from promptpotter.config.settings import NO_RESULT
from promptpotter.domain.results import DegradationHealth, HealthGrade, RoundResult, WarningDict

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

BACKEND_UNREACHABLE_RATE: float = 0.50
"""Fraction of a round's samples that hit a backend-down signal (CONNECTION error
or the per-candidate consecutive-error circuit-breaker skip) at/above which the
round is graded ``critical`` with reason ``backend_unreachable``. Unlike a
structural node fault, an unreachable backend carries NO ``diagnostics.warnings``
(its ``pipeline_data`` is empty), so ``classify_sample_failure`` can't see it — it
was the blind spot that let a dead-backend round grade ``healthy`` and the loop
grind zero-accuracy rounds against a corpse. This is not an optimization signal:
the operator must restart the backend and ``resume``."""

UNSCOREABLE_RATE: float = 0.50
"""Fraction of a round's samples that SUCCEEDED yet produced no extractable
prediction (``predicted == NO_RESULT`` — empty terminal ranking) at/above which
the round is graded ``critical`` with reason ``unscoreable``. The distinct floor
nothing else catches: the backend reports the generation a ``success`` and stamps
no ``diagnostics.warning`` (its output simply carries no parseable label), so
``classify_sample_failure`` is blind to it — an all-``NO_RESULT`` origin grades
``healthy`` and sails into L1, where the optimizer wastes rounds mutating a prompt
whose every output is unscoreable (the answer-format / extraction mismatch that
left every JustLogic sample ``NO_RESULT``). PP owns this signal because PP, not the
backend, decides the prediction is empty (``sample_measurement`` → ``NO_RESULT``).
A majority-unscoreable round is structurally broken regardless of track record;
the origin gate then halts so the operator fixes the format before optimizing.
Distinct from a wrong-but-extractable miss (a hard task scoring 0% but emitting
real labels is NOT flagged — that's a measurement, not a broken floor)."""

EVIDENCE_STARVED_RATE: float = 0.40
"""Fraction of a round's samples for which a SINGLE pipeline node failed (hard
``failed`` status OR a failure warning — structural *or* transient) at/above which
the round is graded ``critical`` with reason ``evidence_starved`` and that node
becomes the ``dominant_node``. This is the ROUND-level aggregate PP owns: a
per-sample ``transient`` (a rate-limited search IS recoverable for one sample) is
correct in isolation, but a flood of the SAME enricher's transients across a round
is systemic — the enricher is starved (e.g. Brave quota exhausted), the round is
noise, and no prompt change recovers it. 0.40 catches the observed campaign where
an evidence node failed ~48% of one round while the dashboard read ``healthy`` and
the loop ground on. **Routes, never stops (R-48):** this grade is a *weak preemptor*
— ``evidence_starved_node`` feeds the ``l1_evidence_starved`` escalation rule, which
fires L2 (bypassing l1_patience so the loop doesn't grind more dead rounds) but never
itself stops. L2 reads the evidence, then self-heals (refine ``task_context``) or, when
the fault is unfixable by any prompt move, emits ``terminate_proposal`` — the HITL exit
that halts carrying the human-action request (the operator banner supplies the verbatim
reason). The stop authority stays with the LLM tier, not a deterministic tripwire."""


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


def evidence_starved_node(rates: dict[str, float]) -> str | None:
    """The single node at/above :data:`EVIDENCE_STARVED_RATE` (worst first), or ``None``.

    The ONE definition of "evidence-starved" — read by both the degradation grade
    (:func:`compute_degradation_health`) and the L2 router (the ``l1_evidence_starved``
    escalation rule, via ``runner/round.py::post_round``), so the verdict the operator
    sees and the routing the loop takes can never disagree."""
    return max(
        (n for n in rates if rates[n] >= EVIDENCE_STARVED_RATE),
        key=lambda n: rates[n],
        default=None,
    )


def compute_degradation_health(
    *,
    hits: int,
    total: int,
    attempted: int,
    structural_count: int,
    transient_count: int,
    prior_clean_rounds: int,
    consecutive_degraded_rounds: int,
    dominant_node: str | None = None,
    unreachable_count: int = 0,
    no_result_count: int = 0,
    node_failure_rates: dict[str, float] | None = None,
    node_warnings: dict[str, list[str]] | None = None,
    is_origin: bool = False,
) -> DegradationHealth | None:
    """Grade a round's degradation health from its winner's per-sample outcomes
    plus the cycle's track record. Two denominators, two meanings: every failure
    RATE is over ``attempted`` (all rows the round ran — health grades the round's
    work, and an errored row is exactly the evidence health exists to count), while
    the Wilson CI keeps ``hits``/``total`` (the EVIDENCE basis — the same scoreable
    population the accuracy beside it averages). Returns ``None`` when nothing was
    attempted — genuinely no verdict, not a fabricated clean one — EXCEPT
    the origin: a round-0 result only exists once scoring was attempted (the
    ``has_program`` gate), so an ``attempted == 0`` origin measured *nothing* where
    it was expected to, which is a broken floor (``critical`` / ``origin_unmeasured``),
    not an abstention — candidates would otherwise be elected against no origin measurement.

    Precedence (first match wins), thresholds = the module constants:
      * **critical** — the backend was unreachable for ≥
        :data:`BACKEND_UNREACHABLE_RATE` of samples (down, not a pipeline fault),
        OR structural failures dominate (``structural`` rate ≥
        :data:`STRUCTURAL_FLAG_RATE`), OR a single node was *evidence-starved*
        (failure rate ≥ :data:`EVIDENCE_STARVED_RATE` — a flood of one enricher's
        failures, systemic at the round level even when each sample's failure is a
        recoverable ``transient``), OR *any* structural failure at an untested
        config (no clean round precedes this one), OR ≥
        :data:`CONSECUTIVE_DEGRADED_CRITICAL` consecutive degraded rounds.
      * **degraded** — degraded fraction ≥ :data:`DEGRADED_RATE_FLAG` (transient /
        noise on a proven pipeline), OR an untested origin with a wide CI.
      * **healthy** — otherwise.

    ``evidence_starved`` grades the round and names the starved node; it *routes* to
    L2 (via the ``l1_evidence_starved`` escalation rule) but NEVER stops the loop
    (R-48). L2 reads this verdict, then self-heals or emits ``terminate_proposal`` —
    the stop authority stays with the intelligent tier, never this deterministic grade."""
    if attempted <= 0:
        if is_origin:
            return DegradationHealth(
                grade="critical",
                reasons=["origin_unmeasured"],
                samples=0,
                structural_count=0,
                transient_count=0,
                no_result_count=0,
                degraded_rate=0.0,
                consecutive_degraded_rounds=0,
                prior_clean_rounds=prior_clean_rounds,
                ci_lo=0.0,
                ci_hi=1.0,
                suggested_action=(
                    "the origin scored zero samples — no origin was measured, so "
                    "candidates would be elected against nothing. The pipeline/connector "
                    "produced no result rows for the origin (a crash or empty return, not "
                    "a wrong answer). Fix the pipeline so the origin scores, then rescore."
                ),
            )
        return None
    from promptpotter.shared.statistics import wilson_ci

    ci_lo, ci_hi = wilson_ci(hits, total)
    structural_rate = structural_count / attempted
    no_result_rate = no_result_count / attempted
    degraded_rate = (structural_count + transient_count) / attempted
    untested = prior_clean_rounds == 0

    # The most-failed enricher at/above the starvation threshold — the systemic
    # round-level signal, distinct from the structural ``dominant_node`` (the
    # starved node's per-sample failures are usually ``transient``, so structural
    # attribution misses it). When it fires it OWNS ``dominant_node``.
    rates = node_failure_rates or {}
    starved_node = evidence_starved_node(rates)

    grade: HealthGrade
    reasons: list[str] = []
    # Backend-down outranks every other verdict: an unreachable backend isn't a
    # pipeline problem the optimizer can move, it's a halt-and-restart condition.
    if unreachable_count / attempted >= BACKEND_UNREACHABLE_RATE:
        grade, reasons = "critical", ["backend_unreachable"]
    elif structural_rate >= STRUCTURAL_FLAG_RATE:
        grade, reasons = "critical", ["structural"]
    elif no_result_rate >= UNSCOREABLE_RATE:
        # Pipeline succeeded but emitted no extractable label on a majority of
        # samples — a broken floor the backend's success/warning channel can't
        # see. Ranked below ``structural`` (a hard node failure is the more
        # specific, node-attributed cause) but above the softer signals.
        grade, reasons = "critical", ["unscoreable"]
    elif starved_node is not None:
        grade, reasons = "critical", ["evidence_starved"]
        dominant_node = starved_node
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

    nw = node_warnings or {}

    def _reported_by(node: str | None) -> str:
        """The verbatim upstream reason(s) the node raised — the actual evidence
        behind the verdict, so a generic 'enricher starved' is grounded in the
        connector's real message ('Brave Search HTTP 429: …') and works the same
        for any node a third party wires in. Empty when the node was silent."""
        msgs = nw.get(node or "", [])
        if not msgs:
            return ""
        return f" Reported by {node}: «{'; '.join(msgs[:2])}»."

    suggested_action: str | None = None
    if grade == "critical":
        where = f"{dominant_node} " if dominant_node else ""
        if "backend_unreachable" in reasons:
            pct = round(unreachable_count / attempted * 100)
            suggested_action = (
                f"backend unreachable on {pct}% of samples — it is down or overloaded, "
                "not a pipeline fault; restart the backend and `resume`."
            )
        elif "evidence_starved" in reasons:
            pct = round(rates.get(dominant_node or "", 0.0) * 100)
            suggested_action = (
                f"{where}produced no evidence on {pct}% of samples — the enricher is "
                f"starved (e.g. quota / rate-limit exhausted), not a prompt fault.{_reported_by(dominant_node)} "
                "Fix the backend (restore quota) and `resume` — don't burn rounds chasing it."
            )
        elif "unscoreable" in reasons:
            pct = round(no_result_rate * 100)
            suggested_action = (
                f"the pipeline produced no extractable answer on {pct}% of samples — "
                "the model's output isn't matching what the grader reads (it ran "
                "successfully, but no parseable label came back). Fix the prompt's "
                "answer_format so the model commits a single parseable label (or the "
                "extraction contract), then rescore — don't optimize against it."
            )
        elif "persistent" in reasons:
            suggested_action = (
                f"{consecutive_degraded_rounds} consecutive degraded rounds — "
                f"likely a persistent pipeline problem.{_reported_by(dominant_node)} "
                "Consider aborting and fixing config."
            )
        else:
            pct = round(structural_rate * 100)
            suggested_action = (
                f"{where}failing structurally on {pct}% of samples — likely a config/schema "
                f"fault, not noise.{_reported_by(dominant_node)} "
                "Consider aborting, fixing config, and re-minting."
            )

    return DegradationHealth(
        grade=grade,
        reasons=reasons,
        samples=attempted,
        structural_count=structural_count,
        transient_count=transient_count,
        no_result_count=no_result_count,
        degraded_rate=degraded_rate,
        consecutive_degraded_rounds=consecutive_degraded_rounds,
        prior_clean_rounds=prior_clean_rounds,
        dominant_node=dominant_node,
        node_failure_rates=dict(rates),
        node_warnings={n: list(v) for n, v in nw.items()},
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


def compute_node_failure_rates(results: list[dict[str, Any]]) -> dict[str, float]:
    """Per-NODE round-level failure rate: the fraction of a round's ATTEMPTED samples
    in which each node failed (hard ``failed`` status OR a failure warning of either
    kind); the denominator is derived here (``len(results)``) so no caller can pass a
    diverging one.

    Distinct from :func:`classify_sample_failure`, which attributes ONE node per sample:
    a node failing *transiently* across many samples (e.g. an enricher whose search
    quota is exhausted) surfaces here as a high rate even though each sample's failure
    is individually recoverable. That round-level rate is the ``evidence_starved`` signal
    (R-48) — both :func:`compute_degradation_health` (the grade) and the L1-critique panel
    read THIS helper, so the verdict and the surface never diverge. Backend-down samples
    carry no diagnostics, so they contribute nothing to the numerators.
    Counts each failed node at most once per sample, so the value is a fraction-of-samples."""
    total = len(results or [])
    if total <= 0:
        return {}
    counts: dict[str, int] = {}
    for r in results or []:
        diag = (r.get("pipeline_data") or {}).get("diagnostics") or {}
        statuses = diag.get("step_statuses") or {}
        warnings = diag.get("warnings") or []
        failed_nodes: set[str] = {n for n, st in statuses.items() if st == "failed"}
        for w in warnings:
            if w.get("kind") in ("structural", "transient"):
                wn = str(w.get("step") or "") or None
                if wn is not None:
                    failed_nodes.add(wn)
        for n in failed_nodes:
            counts[n] = counts.get(n, 0) + 1
    return {n: c / total for n, c in counts.items()}


def collect_node_warnings(results: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Distinct upstream warning reasons per node, harvested from each sample's
    ``pipeline_data.diagnostics.warnings`` — the verbatim ``[code] message`` the
    connector's :class:`StepWarning` carried (e.g. a backend web-search node
    forwarding ``Brave Search HTTP 429: …``). Deduped by ``(step, code)`` so a
    flood of identical failures collapses to one line. This is the evidence behind
    the degradation verdict and is connector-agnostic — whatever message a node
    raises shows up, so someone wiring a brand-new node/API sees its real error,
    not a hardcoded guess. ``message`` clipped to keep the banner readable."""
    seen: dict[str, dict[str, str]] = {}
    for r in results or []:
        warnings = ((r.get("pipeline_data") or {}).get("diagnostics") or {}).get("warnings") or []
        for w in warnings:
            if w.get("kind") not in ("structural", "transient"):
                continue
            step = str(w.get("step") or "")
            if not step:
                continue
            by_code = seen.setdefault(step, {})
            code = str(w.get("code") or "unknown")
            if code not in by_code:
                msg = str(w.get("message") or "").strip()
                by_code[code] = (f"[{code}] {msg}" if msg else f"[{code}]")[:200]
    return {step: list(by_code.values()) for step, by_code in seen.items()}


def compute_round_health(
    *,
    hits: int,
    total: int,
    results: list[dict[str, Any]],
    prior_healths: Sequence[DegradationHealth | None],
    is_origin: bool = False,
) -> DegradationHealth | None:
    """Stamp a closed round's degradation verdict — the SINGLE computation site
    (the app-layer round close). Every surface reads ``RoundResult.health``; none
    recomputes (R-36). Counts structural/transient over the winner's per-sample
    rows (``results``) via the backend's ``step_statuses``, derives the track
    record (clean rounds + consecutive degraded run) from prior rounds' verdicts,
    then grades via :func:`compute_degradation_health` — failure rates over the
    ATTEMPTED rows (``len(results)``), the Wilson CI over the evidence ``total``."""
    from promptpotter.shared.errors import ErrorCategory, error_category

    structural = transient = unreachable = no_result = 0
    structural_nodes: dict[str, int] = {}
    for r in results or []:
        # Backend-down samples carry NO diagnostics (empty pipeline_data), so they're
        # invisible to classify_sample_failure — count them off the typed error channel
        # instead. CONNECTION = the transport failed; ``skipped_after_consecutive_errors``
        # = the per-candidate circuit-breaker that only fires after a run of those.
        if error_category(r) == ErrorCategory.CONNECTION or (
            str(r.get("error") or "") == "skipped_after_consecutive_errors"
        ):
            unreachable += 1
            continue
        # The pipeline ran (no transport/error) but the terminal ranker emitted no
        # candidate → ``predicted == NO_RESULT``. The backend calls this a success and
        # stamps no warning, so it's invisible to ``classify_sample_failure`` below —
        # counted here as the PP-owned unscoreable signal.
        if r.get("predicted") == NO_RESULT:
            no_result += 1
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
    # Per-node round-level failure rate (the systemic evidence-starvation signal) is a
    # separate aggregate over the raw statuses/warnings — same pure helper the critique
    # panel reads, so the grade and the surface never diverge.
    node_failure_rates = compute_node_failure_rates(results)
    # The verbatim reasons behind those rates — forwarded into the verdict so the
    # operator-facing banner names the connector's real error, not a generic guess.
    node_warnings = collect_node_warnings(results)

    # An ungraded prior (``None`` — a probe round, or a round that measured zero
    # samples) is TRANSPARENT to the track record: it is not a clean round (so it
    # can't fake ``untested=False`` and suppress the untested escalations) and it
    # does not break the consecutive-degraded chain (a probe interleaving two
    # degraded rounds must still reach ``persistent``). Only a real ``healthy``
    # verdict counts clean; a ``None`` in the consecutive walk is skipped, not a stop.
    prior_clean = sum(1 for h in prior_healths if h is not None and h.grade == "healthy")
    consecutive = 0
    if structural + transient + unreachable > 0:
        consecutive = 1
        for h in reversed(list(prior_healths)):
            if h is None:
                continue
            if h.grade in ("degraded", "critical"):
                consecutive += 1
            else:
                break

    return compute_degradation_health(
        hits=hits,
        total=total,
        attempted=len(results or []),
        structural_count=structural,
        transient_count=transient,
        prior_clean_rounds=prior_clean,
        consecutive_degraded_rounds=consecutive,
        dominant_node=dominant,
        unreachable_count=unreachable,
        no_result_count=no_result,
        node_failure_rates=node_failure_rates,
        node_warnings=node_warnings,
        is_origin=is_origin,
    )
