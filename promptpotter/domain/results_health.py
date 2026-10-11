from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from promptpotter.domain.results import (
    DegradationHealth,
    HealthCause,
    HealthGrade,
    RoundResult,
)
from promptpotter.domain.scoring import (
    NO_RESULT,
    GradedCell,
    MeasuredCell,
    NodeWarning,
    modal_answer_share,
)
from promptpotter.shared.errors import ErrorCategory
from promptpotter.shared.hashing import shapes_optimizer_prompt

shapes_optimizer_prompt(__name__)

STRUCTURAL_FLAG_RATE: float = 0.30
DEGRADED_RATE_FLAG: float = 0.20
CONSECUTIVE_DEGRADED_CRITICAL: int = 3
UNSCOREABLE_RATE: float = 0.50
EVIDENCE_STARVED_RATE: float = 0.40
# The share of its panel a round must have SENT before any rate below may grade the pipeline.
MEASURED_COVERAGE_FLOOR: float = 0.50


@dataclass(frozen=True)
class ResultClassification:
    """``infra`` deprecates the sample, never the candidate; ``fatal`` eliminates on one hit."""

    advisory_codes: frozenset[str]
    infra_codes: frozenset[str]
    fatal_codes: frozenset[str]

    @property
    def deprecates(self) -> bool:
        return bool(self.fatal_codes or self.infra_codes)

    @property
    def all_codes(self) -> list[str]:
        return sorted(self.advisory_codes | self.infra_codes | self.fatal_codes)

    @property
    def dominant_fatal(self) -> str | None:
        """``fatal_codes`` ONLY: an infra deprecation never takes the one-sighting fast path."""
        return next(iter(sorted(self.fatal_codes)), None)


# Head-anchored: a refusal opens with the apology, and one mid-reasoning is not a refusal.
_REFUSAL_PATTERN = re.compile(
    r"^\s*(?:i'?m\s+sorry|i\s+apologi[sz]e|i\s+cannot|i\s+can'?t|i'?m\s+(?:not\s+able|unable))\b",
    re.IGNORECASE,
)


def _is_refusal(facts: MeasuredCell) -> bool:
    """A refusal completes with ``finish_reason=stop`` and no warning: every other channel sees a plain MISS."""
    predicted = facts.predicted
    if not predicted:
        return False
    head = predicted[:120]
    return bool(_REFUSAL_PATTERN.match(head))


UNKNOWN_STEP = "unknown"


def _collect_advisories(facts: MeasuredCell) -> set[str]:
    advisories: set[str] = set()
    for w in facts.pipeline.diagnostics.warnings:
        advisories.add(_advisory_key(w))
    reached = terminal_node(facts) or UNKNOWN_STEP
    if not advisories and facts.errored:
        advisories.add(f"{reached}:error")
    if _is_refusal(facts):
        advisories.add(f"{reached}:model_refusal")
    return advisories


def _structural_advisory_keys(facts: MeasuredCell) -> set[str]:
    """A warning with no ``kind`` is NOT structural: under-count, never over-eliminate."""
    return {_advisory_key(w) for w in facts.pipeline.diagnostics.warnings if w.kind == "structural"}


def _advisory_key(warning: NodeWarning) -> str:
    return f"{warning.step or UNKNOWN_STEP}:{warning.code or 'unknown'}"


def terminal_node(facts: MeasuredCell) -> str | None:
    return facts.pipeline.terminal_node


def _terminal_llm_shape(facts: MeasuredCell, node: str) -> tuple[str | None, int]:
    usage = facts.pipeline.step_tokens.get(node)
    if usage is None:
        return (None, 0)
    return (usage.finish_reason, usage.reasoning or 0)


def classify_result(facts: MeasuredCell) -> ResultClassification:
    """Truncation is INFRA (a provider ceiling, recurring per sample); a backend 4xx is FATAL on one sighting."""
    advisories = _collect_advisories(facts)
    structural_advs = _structural_advisory_keys(facts)
    infra: set[str] = set()
    fatals: set[str] = set()

    node = terminal_node(facts)
    # ``content_empty`` describes ONE ATTEMPT: the backend retries, and that retry can answer.
    predicted = facts.predicted.strip()
    answered = bool(predicted) and predicted != NO_RESULT
    if node is not None and f"{node}:content_empty" in advisories and not answered:
        finish_reason, reasoning_tokens = _terminal_llm_shape(facts, node)
        # Reasoning tokens prove the model WORKED, so an empty answer is the ROUTE's, never the candidate's.
        if reasoning_tokens > 0:
            infra.add(
                f"{node}:reasoning_budget_exhausted"
                if finish_reason == "length"
                else f"{node}:reasoning_only_response"
            )
        elif finish_reason == "length":
            infra.add(f"{node}:output_truncated")
        else:
            fatals.add(f"{node}:empty_response")

    for adv in advisories:
        if adv.endswith(":content_filtered"):
            fatals.add(adv)
        elif adv.endswith(":model_refusal"):
            # Infra, not fatal: a rephrased instruction can recover, so no elimination at n=1.
            infra.add(adv)
        elif adv in structural_advs:
            # Source-stamped structural is deterministic for the config, so fatal.
            fatals.add(adv)

    if facts.error_category == ErrorCategory.CLIENT:
        fatals.add("backend:client_error")

    return ResultClassification(
        advisory_codes=frozenset(advisories),
        infra_codes=frozenset(infra),
        fatal_codes=frozenset(fatals),
    )


def is_deprecated(facts: MeasuredCell) -> bool:
    return classify_result(facts).deprecates


def _failure_kind(step_statuses: Mapping[str, str], warning: NodeWarning) -> str | None:
    """A node that finished ``success`` produced its evidence: a recovered retry is a cost, never a failure."""
    if warning.kind not in ("structural", "transient"):
        return None
    if step_statuses.get(warning.step) == "success":
        return None
    return warning.kind


def classify_sample_failure(
    step_statuses: Mapping[str, str],
    warnings: Sequence[NodeWarning],
) -> tuple[str | None, str | None]:
    """A warning-bearing structural node outranks one merely stamped ``failed``, which is silent collateral."""
    structural_node: str | None = None
    transient_node: str | None = None
    warned_nodes: set[str] = set()
    for w in warnings:
        node = w.step or None
        if node is not None:
            warned_nodes.add(node)
        kind = _failure_kind(step_statuses, w)
        if kind == "structural" and structural_node is None:
            structural_node = node
        elif kind == "transient" and transient_node is None:
            transient_node = node
        # An unknown kind still lands in warned_nodes, so the silent-failed arm cannot grade it structural.
    if structural_node is not None:
        return "structural", structural_node
    # A node stamped ``failed`` with NO warning is an unexplained hard break → structural.
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


def row_failure(facts: MeasuredCell) -> tuple[str | None, str | None]:
    diagnostics = facts.pipeline.diagnostics
    return classify_sample_failure(diagnostics.step_statuses, diagnostics.warnings)


def is_degraded(facts: MeasuredCell) -> bool:
    """A cell that warned and still answered is not degraded; an errored one is ``MeasuredCell.errored``'s."""
    return not facts.errored and row_failure(facts)[0] is not None


def evidence_starved_node(rates: dict[str, float]) -> str | None:
    return max(
        (n for n in rates if rates[n] >= EVIDENCE_STARVED_RATE),
        key=lambda n: rates[n],
        default=None,
    )


def compute_degradation_health(
    *,
    attempted: int,
    structural_count: int,
    transient_count: int,
    prior_clean_rounds: int,
    consecutive_degraded_rounds: int,
    dominant_node: str | None = None,
    no_result_count: int = 0,
    hole_count: int = 0,
    not_attempted: int = 0,
    unscored: int = 0,
    last_error: str | None = None,
    answer_modal_share: float | None = None,
    node_failure_rates: dict[str, float] | None = None,
    node_warnings: dict[str, list[str]] | None = None,
    is_origin: bool = False,
) -> DegradationHealth | None:
    """Never add a PRECISION clause: a Wilson width is not a failure RATE, and grades every small-n round degraded."""
    counts = _RoundCounts(
        attempted=attempted,
        structural=structural_count,
        transient=transient_count,
        no_result=no_result_count,
        holes=hole_count,
        not_attempted=not_attempted,
        unscored=unscored,
        prior_clean_rounds=prior_clean_rounds,
        consecutive_degraded_rounds=consecutive_degraded_rounds,
        node_failure_rates=node_failure_rates or {},
        node_warnings=node_warnings or {},
        is_origin=is_origin,
    )
    # A PRECONDITION for the ORIGIN alone: a later short round is re-measured, the origin never is.
    if attempted <= 0 or (is_origin and attempted < counts.panel * MEASURED_COVERAGE_FLOOR):
        if not is_origin:
            return None
        return DegradationHealth(
            grade="critical",
            cause="origin_unmeasured",
            samples=attempted,
            structural_count=structural_count,
            transient_count=transient_count,
            no_result_count=no_result_count,
            hole_count=hole_count,
            not_attempted=not_attempted,
            unscored=unscored,
            last_error=last_error,
            degraded_rate=0.0,
            consecutive_degraded_rounds=0,
            prior_clean_rounds=prior_clean_rounds,
            suggested_action=_suggested_action(counts, "origin_unmeasured", None),
        )

    grade, cause = _grade(counts)
    if cause == "evidence_starved":
        dominant_node = counts.starved_node
    return DegradationHealth(
        grade=grade,
        cause=cause,
        samples=attempted,
        not_attempted=not_attempted,
        unscored=unscored,
        last_error=last_error,
        structural_count=structural_count,
        transient_count=transient_count,
        no_result_count=no_result_count,
        hole_count=hole_count,
        answer_modal_share=answer_modal_share,
        degraded_rate=counts.degraded_rate,
        consecutive_degraded_rounds=consecutive_degraded_rounds,
        prior_clean_rounds=prior_clean_rounds,
        dominant_node=dominant_node,
        node_failure_rates=dict(counts.node_failure_rates),
        node_warnings={n: list(v) for n, v in counts.node_warnings.items()},
        suggested_action=_suggested_action(counts, cause, dominant_node),
    )


@dataclass(frozen=True)
class _RoundCounts:
    """Every rate divides by ``attempted``: read none before the coverage precondition passed."""

    attempted: int
    structural: int
    transient: int
    no_result: int
    holes: int
    not_attempted: int
    unscored: int
    prior_clean_rounds: int
    consecutive_degraded_rounds: int
    node_failure_rates: dict[str, float]
    node_warnings: dict[str, list[str]]
    is_origin: bool

    @property
    def panel(self) -> int:
        return self.attempted + self.not_attempted

    @property
    def structural_rate(self) -> float:
        return self.structural / self.attempted

    @property
    def no_result_rate(self) -> float:
        return self.no_result / self.attempted

    @property
    def hole_rate(self) -> float:
        return self.holes / self.attempted

    @property
    def degraded_rate(self) -> float:
        # A hole can never be in the numerator: in the denominator it grades a round healthier the more it failed.
        classifiable = self.attempted - self.holes
        return (self.structural + self.transient) / classifiable if classifiable else 0.0

    @property
    def starved_node(self) -> str | None:
        # A starved node's per-sample failures are usually ``transient``, so structural attribution misses it.
        return evidence_starved_node(self.node_failure_rates)

    def reported_by(self, node: str | None) -> str:
        msgs = self.node_warnings.get(node or "", [])
        if not msgs:
            return ""
        return f" Reported by {node}: «{'; '.join(msgs[:2])}»."


def _grade(counts: _RoundCounts) -> tuple[HealthGrade, HealthCause | None]:
    """Ordered by severity, so a cause never masks one graded above it."""
    if counts.structural_rate >= STRUCTURAL_FLAG_RATE:
        return "critical", "structural"
    if counts.no_result_rate >= UNSCOREABLE_RATE:
        return "critical", "unscoreable"
    if counts.hole_rate >= UNSCOREABLE_RATE:
        return "critical", "holed"
    if counts.starved_node is not None:
        return "critical", "evidence_starved"
    if counts.prior_clean_rounds == 0 and counts.structural > 0:
        return "critical", "structural_untested"
    if counts.consecutive_degraded_rounds >= CONSECUTIVE_DEGRADED_CRITICAL:
        return "critical", "persistent"
    if counts.is_origin and (counts.holes or counts.no_result or counts.not_attempted):
        # ANY missing cell, not a rate: this baseline is permanent.
        return "degraded", "origin_incomplete"
    if counts.degraded_rate >= DEGRADED_RATE_FLAG:
        return "degraded", "degraded"
    return "healthy", None


def _origin_unmeasured_action(counts: _RoundCounts) -> str:
    # ``panel`` is 0 when the connector returned an empty list and the walk finished.
    measured = (
        f"only {counts.attempted} of {counts.panel} origin cells were measured"
        if counts.panel
        else "the origin measured nothing"
    )
    unsent = (
        f"{counts.not_attempted} were never sent — the walk stopped early, so those "
        "cells did not fail, they never ran. "
        if counts.not_attempted
        else "The pipeline/connector returned no result rows at all (a crash or "
        "an empty return, not a wrong answer). "
    )
    ungraded = (
        f"{counts.unscored} of the cells that DID run carry no verdict — the scoring "
        "formula names a term their rows do not carry, so they were measured and "
        "not graded. Fix the formula and re-read them; do not re-buy them. "
        if counts.unscored
        else ""
    )
    return (
        f"{measured}, so there is no origin to elect candidates against and nothing here says "
        f"anything about the prompt. {unsent}{ungraded}"
        "Re-measure with `resume`. If it stops in the same place, read the error on "
        "the last cell that WAS measured — the cause is upstream of the prompt."
    )


def _suggested_action(
    counts: _RoundCounts, cause: HealthCause | None, dominant_node: str | None
) -> str | None:
    where = f"{dominant_node} " if dominant_node else ""
    reported = counts.reported_by(dominant_node)
    match cause:
        case None:
            return None
        case "origin_unmeasured":
            return _origin_unmeasured_action(counts)
        case "evidence_starved":
            pct = round(counts.node_failure_rates.get(dominant_node or "", 0.0) * 100)
            return (
                f"{where}produced no evidence on {pct}% of samples — the enricher is "
                f"starved (e.g. quota / rate-limit exhausted), not a prompt fault.{reported} "
                "Fix the backend (restore quota) and `resume` — don't burn rounds chasing it."
            )
        case "unscoreable":
            pct = round(counts.no_result_rate * 100)
            return (
                f"the pipeline produced no extractable answer on {pct}% of samples — "
                "the model's output isn't matching what the grader reads (it ran "
                "successfully, but no parseable label came back). Fix the prompt's "
                "answer_format so the model commits a single parseable label (or the "
                "extraction contract), then rescore — don't optimize against it."
            )
        case "holed":
            # Counts, not a percentage: the rate is over cells SENT, and a round cut short sent few.
            never_sent = (
                f", and {counts.not_attempted} more were never sent" if counts.not_attempted else ""
            )
            return (
                f"{counts.holes} of the {counts.attempted} cells this round measured returned no "
                f"measurement at all — they were attempted and errored{never_sent}. There is "
                "nothing here to optimize against and nothing about the prompt to conclude. "
                "Re-measure: a plain `resume` re-runs the cells (their errored rows are never "
                "served from cache). If they keep failing, the cause is upstream of the prompt — "
                "read the row's error text before changing anything."
            )
        case "persistent":
            return (
                f"{counts.consecutive_degraded_rounds} consecutive degraded rounds — "
                f"likely a persistent pipeline problem.{reported} "
                "Consider aborting and fixing config."
            )
        case "structural" | "structural_untested":
            pct = round(counts.structural_rate * 100)
            return (
                f"{where}failing structurally on {pct}% of samples — likely a config/schema "
                f"fault, not noise.{reported} "
                "Consider aborting, fixing config, and re-minting."
            )
        case "origin_incomplete":
            reported_cells = counts.attempted - counts.holes - counts.no_result
            never_sent = (
                f", {counts.not_attempted} of them never sent" if counts.not_attempted else ""
            )
            return (
                f"the origin measured {reported_cells} of {counts.panel} cells — "
                f"{counts.panel - reported_cells} never "
                f"reported{never_sent}. This baseline is what every later round's lift is read "
                "against, so the "
                "shortfall is permanent and silent: overlap lines will quote the surviving cells as "
                "though nothing were missing. Re-measure the origin before spending a round on top of "
                "it — a plain `resume` re-runs errored cells, which are never served from cache."
            )
        case "degraded":
            pct = round(counts.degraded_rate * 100)
            return (
                f"{where}degraded on {pct}% of samples, {counts.structural} of them failing "
                f"STRUCTURALLY — under the abort bar, but not noise.{reported} "
                "Read the node's error before trusting this round's numbers."
                if counts.structural
                else f"{where}degraded on {pct}% of samples, all transient."
                f"{reported} The numbers are soft but usable; no action "
                "needed if the next round comes back clean."
            )


def critical_health_title(round_num: int, health: DegradationHealth | None) -> str | None:
    if health is None or health.grade != "critical":
        return None
    who = "Degraded origin" if round_num == 0 else f"Round {round_num} degraded"
    return f"{who} — pipeline may be structurally broken"


def assemble_prior_healths(
    rounds: Sequence[RoundResult],
    round_num: int,
) -> list[DegradationHealth | None]:
    """Oldest→newest: ``_trailing_degraded`` walks it reversed."""
    return [r.health for r in rounds if r.round != round_num]


def compute_node_failure_rates(results: Sequence[GradedCell]) -> dict[str, float]:
    total = len(results)
    if total <= 0:
        return {}
    counts: dict[str, int] = {}
    for r in results:
        diagnostics = r.facts.pipeline.diagnostics
        statuses = diagnostics.step_statuses
        failed_nodes: set[str] = {n for n, st in statuses.items() if st == "failed"}
        for w in diagnostics.warnings:
            if w.step and _failure_kind(statuses, w) is not None:
                failed_nodes.add(w.step)
        for n in failed_nodes:
            counts[n] = counts.get(n, 0) + 1
    return {n: c / total for n, c in counts.items()}


def _collect_node_warnings(results: Sequence[GradedCell]) -> dict[str, list[str]]:
    seen: dict[str, dict[str, str]] = {}
    for r in results:
        for w in r.facts.pipeline.diagnostics.warnings:
            if w.kind not in ("structural", "transient") or not w.step:
                continue
            by_code = seen.setdefault(w.step, {})
            code = w.code or "unknown"
            if code not in by_code:
                msg = w.message.strip()
                by_code[code] = (f"[{code}] {msg}" if msg else f"[{code}]")[:200]
    return {step: list(by_code.values()) for step, by_code in seen.items()}


def _trailing_degraded(prior_healths: Sequence[DegradationHealth | None]) -> int:
    count = 0
    for h in reversed(prior_healths):
        if h is None:
            continue
        if h.grade not in ("degraded", "critical"):
            break
        count += 1
    return count


def compute_round_health(
    *,
    results: Sequence[GradedCell],
    prior_healths: Sequence[DegradationHealth | None],
    is_origin: bool = False,
    # Cells the walk never sent: they have no row in ``results``.
    not_attempted: int = 0,
    # Passed in, never recounted off ``results``: ``runner/round.py`` owns the number the round file carries.
    unscored: int = 0,
) -> DegradationHealth | None:
    structural = transient = no_result = holes = 0
    structural_nodes: dict[str, int] = {}
    # From the END: `Walk.take` appends the row of the cell that aborts the round before returning.
    last_error = next(
        (msg for r in reversed(results) if r.facts.errored and (msg := r.facts.error or "")),
        None,
    )
    for r in results:
        # Not on a verifier-graded cell, where NO_RESULT is the backend's SHAPE and every row would count.
        if r.facts.predicted == NO_RESULT and not r.facts.verifier_graded:
            no_result += 1
        # A typed non-transport error carries no diagnostics: uncounted, it joins the denominator alone.
        if r.facts.errored:
            holes += 1
            continue
        kind, node = row_failure(r.facts)
        if kind == "structural":
            structural += 1
            if node is not None:
                structural_nodes[node] = structural_nodes.get(node, 0) + 1
        elif kind == "transient":
            transient += 1
    dominant = (
        max(structural_nodes, key=lambda k: structural_nodes[k]) if structural_nodes else None
    )
    node_failure_rates = compute_node_failure_rates(results)
    node_warnings = _collect_node_warnings(results)

    # A ``None`` prior (a probe round, zero samples) is TRANSPARENT: neither clean nor a chain break.
    prior_clean = sum(1 for h in prior_healths if h is not None and h.grade == "healthy")
    consecutive = 1 + _trailing_degraded(prior_healths) if structural + transient + holes > 0 else 0

    return compute_degradation_health(
        attempted=len(results),
        structural_count=structural,
        transient_count=transient,
        prior_clean_rounds=prior_clean,
        consecutive_degraded_rounds=consecutive,
        dominant_node=dominant,
        no_result_count=no_result,
        hole_count=holes,
        not_attempted=not_attempted,
        unscored=unscored,
        last_error=last_error,
        # Over every attempted row: hedging IS how a round produces unscoreable rows.
        answer_modal_share=modal_answer_share(results),
        node_failure_rates=node_failure_rates,
        node_warnings=node_warnings,
        is_origin=is_origin,
    )
