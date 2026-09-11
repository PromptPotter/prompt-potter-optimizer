from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from promptpotter.config.settings import NO_RESULT
from promptpotter.domain.results import (
    DegradationHealth,
    HealthCause,
    HealthGrade,
    RoundResult,
    WarningDict,
)
from promptpotter.domain.scoring import is_verifier_graded, modal_answer_share
from promptpotter.shared.errors import ErrorCategory, error_category, is_error_result
from promptpotter.shared.hashing import shapes_optimizer_prompt

shapes_optimizer_prompt(__name__)

STRUCTURAL_FLAG_RATE: float = 0.30
DEGRADED_RATE_FLAG: float = 0.20
CONSECUTIVE_DEGRADED_CRITICAL: int = 3
BACKEND_UNREACHABLE_RATE: float = 0.50
UNSCOREABLE_RATE: float = 0.50
EVIDENCE_STARVED_RATE: float = 0.40
# The share of its panel a round must actually have SENT before any rate below is allowed to grade
# the pipeline. Below it there is no verdict to give — only "re-measure".
MEASURED_COVERAGE_FLOOR: float = 0.50


@dataclass(frozen=True)
class ResultClassification:
    """Three buckets: ``advisory`` observes; ``infra`` deprecates the sample without blaming the
    candidate; ``fatal`` is candidate-quality and eliminates on one sighting."""

    advisory_codes: frozenset[str]
    infra_codes: frozenset[str]
    fatal_codes: frozenset[str]

    @property
    def is_fatal(self) -> bool:
        """True iff the sample should be treated as deprecated (fatal OR infra)."""
        return bool(self.fatal_codes or self.infra_codes)

    @property
    def all_codes(self) -> list[str]:
        return sorted(self.advisory_codes | self.infra_codes | self.fatal_codes)

    @property
    def dominant_fatal(self) -> str | None:
        """Pick a fatal code for one-sighting fast-elimination. Reads ``fatal_codes`` ONLY — infra-driven deprecation must
        never trigger the fast path."""
        return next(iter(sorted(self.fatal_codes)), None)


_REFUSAL_PATTERN = re.compile(
    r"^\s*(?:i'?m\s+sorry|i\s+apologi[sz]e|i\s+cannot|i\s+can'?t|i'?m\s+(?:not\s+able|unable))\b",
    re.IGNORECASE,
)
"""Head-anchored regex for LLM refusal prefixes. Anchored to ``^`` so
mid-text apologies inside genuine reasoning don't false-positive — a
real refusal opens with the apology, not buries it."""


def _is_refusal(result: Mapping[str, Any]) -> bool:
    """A refusal completes with ``finish_reason=stop`` and no warning, so every advisory channel
    sees a plain MISS — L2 needs it as its own failure mode to propose a mitigation."""
    predicted = str(result.get("predicted") or "")
    if not predicted:
        return False
    # Cap the matched prefix at the first sentence/120 chars — refusals
    # are short; a 500-char prediction that opens with apology framing
    # is likely a real reasoning chain that started with hedging.
    head = predicted[:120]
    return bool(_REFUSAL_PATTERN.match(head))


def _collect_advisories(result: Mapping[str, Any]) -> set[str]:
    pd = result.get("pipeline_data") or {}
    advisories: set[str] = set()
    for w in (pd.get("diagnostics") or {}).get("warnings") or []:
        advisories.add(f"{w.get('step', 'unknown')}:{w.get('code', 'unknown')}")
    if not advisories and is_error_result(result):
        advisories.add(f"{terminal_node(result)}:error")
    if _is_refusal(result):
        advisories.add(f"{terminal_node(result)}:model_refusal")
    return advisories


def _structural_advisory_keys(result: Mapping[str, Any]) -> set[str]:
    """Keys whose SOURCE-STAMPED ``kind`` is structural. The backend owns that verdict and PoBB reads it directly, so
    elimination stays in lockstep. A warning with no ``kind`` is NOT structural: under-count, never over-eliminate."""
    pd = result.get("pipeline_data") or {}
    keys: set[str] = set()
    for w in (pd.get("diagnostics") or {}).get("warnings") or []:
        if w.get("kind") == "structural":
            keys.add(f"{w.get('step', 'unknown')}:{w.get('code', 'unknown')}")
    return keys


def terminal_node(result: Mapping[str, Any]) -> str:
    """The deepest node this result reached, read off its OWN ``pipeline_data`` rather than a literal name, so truncation
    classification keys on this result's terminal node and fires for a multi-node terminal LLM too."""
    pd = result.get("pipeline_data") or {}
    return pd.get("terminal_node") or "llm_only"


def _terminal_llm_shape(result: Mapping[str, Any]) -> tuple[str | None, int]:
    """(finish_reason, reasoning_tokens) from the terminal LLM node's step_tokens;
    (None, 0) if missing."""
    pd = result.get("pipeline_data") or {}
    st = (pd.get("step_tokens") or {}).get(terminal_node(result)) or {}
    fr = st.get("finish_reason")
    reasoning = int(st.get("reasoning") or 0)
    return (fr, reasoning)


def classify_result(result: Mapping[str, Any]) -> ResultClassification:
    """Advisories + response shape → advisory / infra / fatal codes. Truncation is INFRA (provider-ceiling, recurs per
    sample); a backend 4xx is FATAL, so one sighting kills the candidate instead of poisoning every remaining one."""
    advisories = _collect_advisories(result)
    structural_advs = _structural_advisory_keys(result)
    infra: set[str] = set()
    fatals: set[str] = set()

    node = terminal_node(result)
    # ``content_empty`` describes ONE ATTEMPT, not the result: the backend raises it and
    # retries (``llm_retry`` beside it, both stamped transient), and that retry can answer.
    # A result carrying a real prediction is not an empty response whatever the advisory
    # says — three archived rows recovered this way and two scored 1.0, yet all three were
    # stamped ``empty_response``, whose FATAL routing fast-eliminates the candidate off one
    # sighting. Read the result, not the attempt. ``NO_RESULT`` is the scorer's sentinel for
    # "terminal node emitted nothing parseable" (``compute_round_health`` below owns the
    # round-level version of this same question).
    predicted = str(result.get("predicted") or "").strip()
    answered = bool(predicted) and predicted != NO_RESULT
    if f"{node}:content_empty" in advisories and not answered:
        finish_reason, reasoning_tokens = _terminal_llm_shape(result)
        # ``reasoning_tokens > 0`` is proof the model WORKED — it neither refused (a refusal
        # carries content, or ``finish_reason=content_filter``) nor idled. Emitting nothing
        # visible after thinking is a property of the ROUTE, deterministic for every prompt
        # we could send it, so it routes to infra whatever ended the call: hitting the cap
        # (``length``) and stopping on its own (``stop``) are the same fault seen at two
        # budgets. Observed on ``z-ai/glm-4.7-flash`` — empty content, ``stop``, 5352
        # reasoning chars, then a schema-repair re-prompt — and charging that to the
        # candidate fast-eliminates a prompt that was never read.
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
            # Refusal routes to infra (not fatal): the same query at a
            # different temperature / rephrased instruction can recover,
            # so don't fast-path eliminate the candidate at n=1. But
            # surfacing it in infra_codes routes it to RUNTIME FAILURES
            # so L2 sees the pattern and can propose mitigations
            # (different model, less safety-triggering instruction).
            infra.add(adv)
        elif adv in structural_advs:
            # Source-stamped structural (``WarningKind.STRUCTURAL`` from the backend):
            # a deterministic-for-config candidate failure — route to fatal so
            # DegradationCheck fast-eliminates the candidate instead of retrying the
            # same broken config on every remaining sample. Lockstep with the
            # degradation verdict, which grades the same warning structural-critical
            # off the same stamped field (one truth, not two disagreeing classifiers).
            fatals.add(adv)

    if is_error_result(result) and error_category(result) == ErrorCategory.CLIENT:
        fatals.add("backend:client_error")

    return ResultClassification(
        advisory_codes=frozenset(advisories),
        infra_codes=frozenset(infra),
        fatal_codes=frozenset(fatals),
    )


def classify_sample_failure(
    step_statuses: Mapping[str, str],
    warnings: Sequence[WarningDict],
) -> tuple[str | None, str | None]:
    """Attribution follows the WARNING-BEARING node, never raw ``step_statuses``: a node merely
    stamped failed with no warning is silent collateral and must not outvote what actually broke."""
    structural_node: str | None = None
    transient_node: str | None = None
    warned_nodes: set[str] = set()
    for w in warnings:
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
    """The ONE definition of evidence-starved, read by both the degradation grade and the L2
    router — so the verdict the operator sees and the routing the loop takes cannot disagree."""
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
    unreachable_count: int = 0,
    no_result_count: int = 0,
    hole_count: int = 0,
    not_attempted: int = 0,
    last_error: str | None = None,
    answer_modal_share: float | None = None,
    node_failure_rates: dict[str, float] | None = None,
    node_warnings: dict[str, list[str]] | None = None,
    is_origin: bool = False,
) -> DegradationHealth | None:
    """Never add a PRECISION clause. A Wilson width is not a failure RATE, and reusing the rate flag for one grades every
    round under n≈100 as degraded — which, since only healthy rounds count as priors, arms the untested arm forever."""
    # COVERAGE IS A PRECONDITION, NOT A RATE — for the ORIGIN, whose shortfall is permanent. Every
    # rate below divides by cells that were actually SENT, and a walk cut short by an abort, a skip
    # or a pause leaves cells nothing ever dispatched: they carry no row and say nothing about the
    # pipeline. A LATER round that comes up short is simply re-measured, so it keeps its rates and
    # each message states the denominator it read; an origin's shortfall is the baseline every
    # later round is read against, so below the floor it may not grade a pipeline at all. Its
    # predecessor could only ask ``attempted <= 0``, because the abort padded the tail with
    # fabricated error rows and so kept the denominator full — which is how a single upstream 429
    # came to be reported as "pipeline may be structurally broken".
    panel = attempted + not_attempted
    if attempted <= 0 or (is_origin and attempted < panel * MEASURED_COVERAGE_FLOOR):
        if is_origin:
            return DegradationHealth(
                grade="critical",
                cause="origin_unmeasured",
                samples=attempted,
                structural_count=structural_count,
                transient_count=transient_count,
                no_result_count=no_result_count,
                hole_count=hole_count,
                not_attempted=not_attempted,
                last_error=last_error,
                degraded_rate=0.0,
                consecutive_degraded_rounds=0,
                prior_clean_rounds=prior_clean_rounds,
                suggested_action=(
                    # ``panel`` is 0 when nothing was sent AND nothing was left unsent — the
                    # connector returned an empty list and the walk finished. "0 of 0" is not a
                    # coverage statement, so that case says what happened instead.
                    (
                        f"only {attempted} of {panel} origin cells were measured"
                        if panel
                        else "the origin measured nothing"
                    )
                    + ", so there is no origin to elect candidates against and nothing here says "
                    "anything about the prompt. "
                    + (
                        f"{not_attempted} were never sent — the walk stopped early, so those "
                        "cells did not fail, they never ran. "
                        if not_attempted
                        else "The pipeline/connector returned no result rows at all (a crash or "
                        "an empty return, not a wrong answer). "
                    )
                    + "Re-measure with `resume`. If it stops in the same place, read the error on "
                    "the last cell that WAS measured — the cause is upstream of the prompt."
                ),
            )
        return None
    structural_rate = structural_count / attempted
    no_result_rate = no_result_count / attempted
    hole_rate = hole_count / attempted
    # Holes are in the denominator and can never be in the numerator — a hole `continue`s before
    # ``classify_sample_failure`` ever sees it — so a round graded CLOSER TO 0% the more completely
    # it failed, which is exactly what "Degraded rate 0%" read beside "98% of cells returned no
    # measurement". The rate is over the cells that came back with something to classify, and
    # UNREACHABLE is the other kind that never gets there: it `continue`s one arm earlier off the
    # typed transport error. Subtracting one and not the other leaves the same bug for the failure
    # mode that produces it most — a backend going down mid-round.
    classifiable = attempted - hole_count - unreachable_count
    degraded_rate = (structural_count + transient_count) / classifiable if classifiable else 0.0
    untested = prior_clean_rounds == 0

    # The most-failed enricher at/above the starvation threshold — the systemic
    # round-level signal, distinct from the structural ``dominant_node`` (the
    # starved node's per-sample failures are usually ``transient``, so structural
    # attribution misses it). When it fires it OWNS ``dominant_node``.
    rates = node_failure_rates or {}
    starved_node = evidence_starved_node(rates)

    grade: HealthGrade
    cause: HealthCause | None = None
    # Backend-down outranks every other verdict: an unreachable backend isn't a
    # pipeline problem the optimizer can move, it's a halt-and-restart condition.
    if unreachable_count / attempted >= BACKEND_UNREACHABLE_RATE:
        grade, cause = "critical", "backend_unreachable"
    elif structural_rate >= STRUCTURAL_FLAG_RATE:
        grade, cause = "critical", "structural"
    elif no_result_rate >= UNSCOREABLE_RATE:
        # Pipeline succeeded but emitted no extractable label on a majority of
        # samples — a broken floor the backend's success/warning channel can't
        # see. Ranked below ``structural`` (a hard node failure is the more
        # specific, node-attributed cause) but above the softer signals.
        grade, cause = "critical", "unscoreable"
    elif hole_rate >= UNSCOREABLE_RATE:
        # Most of the round's cells never reported. There is nothing to grade and nothing
        # to optimize against — the remedy is to re-measure, not to change the prompt.
        grade, cause = "critical", "holed"
    elif starved_node is not None:
        grade, cause = "critical", "evidence_starved"
        dominant_node = starved_node
    elif untested and structural_count > 0:
        grade, cause = "critical", "structural_untested"
    elif consecutive_degraded_rounds >= CONSECUTIVE_DEGRADED_CRITICAL:
        grade, cause = "critical", "persistent"
    elif is_origin and (hole_count or no_result_count or not_attempted):
        # ANY missing cell, not a rate: this baseline is permanent, and every later round quotes
        # the survivors as though nothing were absent. Below every critical cause — it grades
        # `degraded`, and a chain ordered by severity may not let it mask one.
        grade, cause = "degraded", "origin_incomplete"
    elif degraded_rate >= DEGRADED_RATE_FLAG:
        grade, cause = "degraded", "degraded"
    else:
        grade = "healthy"

    nw = node_warnings or {}

    def _reported_by(node: str | None) -> str:
        """The verbatim upstream reason(s) the node raised, so a generic verdict stays grounded in
        the connector's real message. Empty when the node was silent."""
        msgs = nw.get(node or "", [])
        if not msgs:
            return ""
        return f" Reported by {node}: «{'; '.join(msgs[:2])}»."

    suggested_action: str | None = None
    where = f"{dominant_node} " if dominant_node else ""
    if grade == "critical":
        if cause == "backend_unreachable":
            pct = round(unreachable_count / attempted * 100)
            suggested_action = (
                f"backend unreachable on {pct}% of samples — it is down or overloaded, "
                "not a pipeline fault; restart the backend and `resume`."
            )
        elif cause == "evidence_starved":
            pct = round(rates.get(dominant_node or "", 0.0) * 100)
            suggested_action = (
                f"{where}produced no evidence on {pct}% of samples — the enricher is "
                f"starved (e.g. quota / rate-limit exhausted), not a prompt fault.{_reported_by(dominant_node)} "
                "Fix the backend (restore quota) and `resume` — don't burn rounds chasing it."
            )
        elif cause == "unscoreable":
            pct = round(no_result_rate * 100)
            suggested_action = (
                f"the pipeline produced no extractable answer on {pct}% of samples — "
                "the model's output isn't matching what the grader reads (it ran "
                "successfully, but no parseable label came back). Fix the prompt's "
                "answer_format so the model commits a single parseable label (or the "
                "extraction contract), then rescore — don't optimize against it."
            )
        elif cause == "holed":
            # Counts, not a percentage: the rate is over the cells this round SENT, and a round cut
            # short sent few — so "98% of this round's cells" read as a verdict on the whole panel
            # when the walk had reached two of forty.
            never_sent = f", and {not_attempted} more were never sent" if not_attempted else ""
            suggested_action = (
                f"{hole_count} of the {attempted} cells this round measured returned no "
                f"measurement at all — they were attempted and errored{never_sent}. There is "
                "nothing here to optimize against and nothing about the prompt to conclude. "
                "Re-measure: a plain `resume` re-runs the cells (their errored rows are never "
                "served from cache). If they keep failing, the cause is upstream of the prompt — "
                "read the row's error text before changing anything."
            )
        elif cause == "persistent":
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
    elif cause == "origin_incomplete":
        reported = attempted - hole_count - no_result_count
        never_sent = f", {not_attempted} of them never sent" if not_attempted else ""
        suggested_action = (
            f"the origin measured {reported} of {panel} cells — {panel - reported} never "
            f"reported{never_sent}. This baseline is what every later round's lift is read "
            "against, so the "
            "shortfall is permanent and silent: overlap lines will quote the surviving cells as "
            "though nothing were missing. Re-measure the origin before spending a round on top of "
            "it — a plain `resume` re-runs errored cells, which are never served from cache."
        )
    elif grade == "degraded":
        # This grade fires on structural AND transient together, so a reader told "transient"
        # is told something the split here can contradict.
        pct = round(degraded_rate * 100)
        if structural_count:
            suggested_action = (
                f"{where}degraded on {pct}% of samples, {structural_count} of them failing "
                f"STRUCTURALLY — under the abort bar, but not noise.{_reported_by(dominant_node)} "
                "Read the node's error before trusting this round's numbers."
            )
        else:
            suggested_action = (
                f"{where}degraded on {pct}% of samples, all transient."
                f"{_reported_by(dominant_node)} The numbers are soft but usable; no action "
                "needed if the next round comes back clean."
            )

    return DegradationHealth(
        grade=grade,
        cause=cause,
        samples=attempted,
        not_attempted=not_attempted,
        last_error=last_error,
        structural_count=structural_count,
        transient_count=transient_count,
        no_result_count=no_result_count,
        hole_count=hole_count,
        answer_modal_share=answer_modal_share,
        degraded_rate=degraded_rate,
        consecutive_degraded_rounds=consecutive_degraded_rounds,
        prior_clean_rounds=prior_clean_rounds,
        dominant_node=dominant_node,
        node_failure_rates=dict(rates),
        node_warnings={n: list(v) for n, v in nw.items()},
        suggested_action=suggested_action,
    )


def assemble_prior_healths(
    rounds: Sequence[RoundResult],
    round_num: int,
) -> list[DegradationHealth | None]:
    """Oldest→newest, because ``compute_round_health``'s reversed consecutive-degraded walk
    reads most-recent-first."""
    return [r.health for r in rounds if r.round != round_num]


def compute_node_failure_rates(results: list[dict[str, Any]]) -> dict[str, float]:
    """Round-LEVEL rate: a node failing transiently across many samples surfaces here even though
    each sample is individually recoverable. The grade and the L1-critique panel share this helper."""
    total = len(results)
    if total <= 0:
        return {}
    counts: dict[str, int] = {}
    for r in results:
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


def _collect_node_warnings(results: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Connector-agnostic — whatever message a node raises shows up, so a brand-new node's real
    error surfaces rather than a hardcoded guess. Deduped by ``(step, code)``."""
    seen: dict[str, dict[str, str]] = {}
    for r in results:
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
    results: list[dict[str, Any]],
    prior_healths: Sequence[DegradationHealth | None],
    is_origin: bool = False,
    # Cells of the panel the walk never sent. They have no row in ``results`` by construction, so
    # this is the only way the verdict learns the round was cut short rather than simply small.
    not_attempted: int = 0,
) -> DegradationHealth | None:
    """The SINGLE computation site: every surface reads ``RoundResult.health`` and none
    recomputes it."""

    structural = transient = unreachable = no_result = holes = 0
    structural_nodes: dict[str, int] = {}
    # Read from the END of the walk, which is what makes it the TRIGGER: `_absorb` appends a row,
    # then classifies it, then returns on an abort — so the last errored row IS the cell that
    # stopped the round, and it is the one message that explains the whole thing. Taken from the
    # front it was whichever cell errored first, so a transient blip dozens of cells earlier stood
    # in for the fault that actually halted the walk, on the very row the advice below tells the
    # operator to read.
    last_error = next(
        (
            msg
            for r in reversed(results)
            if is_error_result(r) and (msg := str(r.get("error") or ""))
        ),
        None,
    )
    for r in results:
        # Backend-down samples carry NO diagnostics (empty pipeline_data), so they're
        # invisible to classify_sample_failure — count them off the typed error channel
        # instead. CONNECTION = the transport failed. This used to also match a row whose
        # ``error`` read ``skipped_after_consecutive_errors``; that string only ever appeared on
        # the abort's fabricated tail, so with the padding gone the arm matched nothing. A short
        # walk is now reported as coverage (``not_attempted``), which is what it is.
        if error_category(r) == ErrorCategory.CONNECTION:
            unreachable += 1
            continue
        # The pipeline ran (no transport/error) but the terminal ranker emitted no
        # candidate → ``predicted == NO_RESULT``. The backend calls this a success and
        # stamps no warning, so it's invisible to ``classify_sample_failure`` below —
        # counted here as the PP-owned unscoreable signal.
        #
        # NOT on a verifier-graded cell, where NO_RESULT is the SHAPE of the backend rather than
        # a fault in it: the reward already graded the episode and no node emits a ranking, so
        # every row would count and the round would grade `critical/unscoreable` at 100% —
        # sending the operator to fix an `answer_format` that decides nothing, and (on an origin)
        # reporting a measured baseline as unmeasured.
        if r.get("predicted") == NO_RESULT and not is_verifier_graded(r.get("ground_truth")):
            no_result += 1
        # A HOLE: the row carries a typed error that is not a transport failure, so it has
        # no diagnostics and `classify_sample_failure({}, [])` scores it neither structural
        # nor transient. Counted here or it joins the denominator alone — which is how two
        # abandoned L4 inner cells made their round grade healthier than a complete one.
        if is_error_result(r):
            holes += 1
            continue
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
    node_warnings = _collect_node_warnings(results)

    # An ungraded prior (``None`` — a probe round, or a round that measured zero
    # samples) is TRANSPARENT to the track record: it is not a clean round (so it
    # can't fake ``untested=False`` and suppress the untested escalations) and it
    # does not break the consecutive-degraded chain (a probe interleaving two
    # degraded rounds must still reach ``persistent``). Only a real ``healthy``
    # verdict counts clean; a ``None`` in the consecutive walk is skipped, not a stop.
    prior_clean = sum(1 for h in prior_healths if h is not None and h.grade == "healthy")
    consecutive = 0
    if structural + transient + unreachable + holes > 0:
        consecutive = 1
        for h in reversed(list(prior_healths)):
            if h is None:
                continue
            if h.grade in ("degraded", "critical"):
                consecutive += 1
            else:
                break

    return compute_degradation_health(
        attempted=len(results),
        structural_count=structural,
        transient_count=transient,
        prior_clean_rounds=prior_clean,
        consecutive_degraded_rounds=consecutive,
        dominant_node=dominant,
        unreachable_count=unreachable,
        no_result_count=no_result,
        hole_count=holes,
        not_attempted=not_attempted,
        last_error=last_error,
        # Over every attempted row, not just the scoreable ones: hedging IS how a round
        # produces unscoreable rows, so excluding them would hide the behaviour in the
        # denominator. `modal_answer_share` ignores rows with no prediction itself.
        answer_modal_share=modal_answer_share(results),
        node_failure_rates=node_failure_rates,
        node_warnings=node_warnings,
        is_origin=is_origin,
    )
