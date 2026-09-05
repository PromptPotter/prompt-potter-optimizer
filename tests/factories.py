"""Builders that return REAL domain models, never duck-typed stand-ins.

Not a test file — no ``test_`` prefix, collects nothing. It exists because a
``SimpleNamespace`` stand-in for a strict model is the one construct in this suite that
can carry silent harm past every gate: rename a field on ``RoundResult`` and ruff, mypy
and pytest all stay green while the real read path breaks. That is exactly the class
``test_numerics.py`` exists to catch, so the fakes were defeating the guard from inside.

Worse than drift, a fake can assert a shape the model cannot produce. The pair these
replace stamped ``l1_n_no_op`` / ``l1_n_duplicate`` directly onto the round — but those
are ``@computed_field`` properties DERIVED from ``candidate_scores`` (a collapsed variant
rides that list with ``invalid=True`` and an ``INVARIANT_REASONS`` failure), so a stamped
value cannot win no matter what a fake asserts — ``@computed_field`` plus ``extra="ignore"``
already refuse it. Building the real model is what carries that refusal into every test.

Only what a test actually bends is a parameter; everything else is a plausible default.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from promptpotter.domain.escalation_signals import ValidationFailure
from promptpotter.domain.phases import StopReason
from promptpotter.domain.results import (
    CycleResult,
    DegradationHealth,
    RoundResult,
    ScoredCandidate,
)
from promptpotter.domain.spend import SpendBucket, SpendRollup

# Repeated as the ground truth of every row a factory-built round measures. Deliberately
# only two labels: with as many distinct truths as rows the answer space reads as
# identity-keyed and no constant answerer is detectable.
_TRUTH = ["TRUE", "FALSE", "TRUE", "FALSE"]


def measurement(
    sample_id: int,
    fitness: float | None = 1.0,
    *,
    objective: float | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """One measured cell, stamped the way ``rescore_results`` leaves one.

    ``objective`` defaults to ``fitness`` — that IS the identity wherever a campaign declares no
    ``per_cell`` formula — and stamping both is what keeps these rows readable by
    ``graded_response``, which RAISES on a row carrying neither rather than reading the absence
    as a 0.0. Pass it separately only to make the two differ.

    ``fitness=None`` builds the other row shape: a real error row (``_error_result``) carries no
    grade at all, and the coverage floor and the θ fit are both about that ABSENCE rather than
    about a low score. Eight local copies of these two shapes drifted apart here once already —
    adding ``objective`` to the loop had to find every one of them.
    """
    if fitness is None:
        return {"sample_id": sample_id, **extra}
    return {
        "sample_id": sample_id,
        "hit": fitness > 0.5,
        "fitness": fitness,
        "objective": fitness if objective is None else objective,
        **extra,
    }


def measurements(
    grades: Sequence[float], sample_ids: Sequence[int] | None = None
) -> list[dict[str, Any]]:
    """One arm's panel. Ids run ``0..n-1`` unless the test needs a specific set — a subset
    disjoint from the prior's is how the paired-PoBB and subset-drift cases are built."""
    ids = range(len(grades)) if sample_ids is None else sample_ids
    return [measurement(sid, g) for sid, g in zip(ids, grades, strict=True)]


def scored_candidate(
    candidate_id: str = "c0",
    *,
    accuracy: float = 0.5,
    total: int = 4,
    invalid_reason: str | None = None,
    **overrides: Any,
) -> ScoredCandidate:
    """One candidate row. ``invalid_reason`` collapses it the way the validator does —
    ``invalid=True`` plus a ``ValidationFailure``, which is where ``RoundResult`` reads
    its collapse counts back from."""
    failures = (
        [ValidationFailure(axis="prompt_fields", value="", allowed=[], reason=invalid_reason)]
        if invalid_reason
        else []
    )
    return ScoredCandidate(
        candidate_id=candidate_id,
        label=candidate_id,
        accuracy=accuracy,
        composite_fitness=accuracy,
        total=total,
        invalid=invalid_reason is not None,
        validation_failures=failures,
        **overrides,
    )


def degradation_health(
    *, samples: int = 24, degraded_rate: float = 0.0, no_result: int = 0
) -> DegradationHealth:
    """The round's degradation verdict — only the three fields the L4 proxies read."""
    return DegradationHealth(
        grade="healthy" if degraded_rate == 0.0 and not no_result else "degraded",
        samples=samples,
        structural_count=0,
        transient_count=0,
        no_result_count=no_result,
        degraded_rate=degraded_rate,
        consecutive_degraded_rounds=0,
        prior_clean_rounds=0,
    )


def round_result(
    rnd: int,
    *,
    improved: bool = True,
    degraded_rate: float = 0.0,
    no_result: int = 0,
    samples: int = 24,
    candidates_scored: int = 2,
    parse_failure: str | None = None,
    no_op: int = 0,
    dup: int = 0,
    collapsed: int = 0,
    cut: int = 0,
    **overrides: Any,
) -> RoundResult:
    """A closed round, shaped the way the loop actually writes one.

    ``parse_failure`` yields ZERO candidates by construction (``l1_generate`` returned
    ``[]``), so it is modelled on the round — no ``ScoredCandidate`` can carry one.

    ``no_op`` / ``dup`` add COLLAPSED candidates: they ride ``candidate_scores`` beside the
    measured ones but are absent from ``candidates_scored`` and from
    ``all_candidate_results``, so ``l1_n_no_op`` / ``l1_n_duplicate`` derive to them and
    the mode-collapse denominator (collapsed + scored) comes out right.

    ``collapsed`` makes that many measured candidates answer ONE label to every sample —
    the constant answerer built below. ``cut`` makes them *also* stop
    after 2 rows, an arm PoBB eliminated before it earned a verdict; below
    ``elimination_n_min`` a collapse is indistinguishable from small-n noise, which is why
    a cut arm must not be charged as dirt.
    """
    if parse_failure:
        candidates_scored = 0
    measured = [
        scored_candidate(f"c{i}", total=2 if i < cut else len(_TRUTH))
        for i in range(candidates_scored)
    ]
    rejected = [
        scored_candidate(f"x{i}", invalid_reason=reason)
        for reason, count in (("no_op_variant", no_op), ("duplicate_variant", dup))
        for i in range(count)
    ]
    # Merged rather than splatted after the literals, so ``overrides`` reaches EVERY field. Spelled
    # the other way it silently ``TypeError``s on the eight named here — the builder's contract is
    # "bend the field you care about", and half the fields did not honour it.
    base: dict[str, Any] = {
        "round": rnd,
        "label": f"round_{rnd}",
        "accuracy": 0.5,
        "total": 4,
        "improved": improved,
        "prompt_fields": {},
        "candidates_scored": candidates_scored,
        "candidate_scores": measured + rejected,
        "all_candidate_results": {
            f"c{i}": [
                {"predicted": "Uncertain" if i < collapsed or i < cut else t, "ground_truth": t}
                for t in (_TRUTH[:2] if i < cut else _TRUTH)
            ]
            for i in range(candidates_scored)
        },
        "health": degradation_health(
            samples=samples, degraded_rate=degraded_rate, no_result=no_result
        ),
        "l1_parse_failure": parse_failure,
    }
    return RoundResult(**(base | overrides))


def cycle_result(
    levels: list[float],
    origin: float | None,
    rounds: list[RoundResult],
    *,
    cost: float = 0.03,
    stop_reason: StopReason = StopReason.MAX_ROUNDS,
    unpriced_tokens: int = 0,
    billed: float | None = None,
    **overrides: Any,
) -> CycleResult:
    """A finished cycle. ``rounds`` carries L1 rounds ONLY — round 0 is peeled off upstream
    (``Cycle.absorb_round`` is the sole sink for a finished L1 round) — and ``levels``
    carries one adopted level per L1 round.

    ``cost`` is the INCURRED cost: what the search would cost cold, and the only divisor the
    proxies read. ``billed`` (defaults to the same) is deliberately independent — the two
    diverge exactly when a cycle replays the tenant-global cache.
    """
    return CycleResult(
        rounds=rounds,
        n_l1_rounds=len(rounds),
        best_accuracy=0.5,
        best_round=len(rounds),
        origin_accuracy=origin or 0.0,
        origin_level=origin,
        round_parent_levels=levels,
        winner_prompt_fields={},
        stop_reason=stop_reason,
        started_at="2026-01-01T00:00:00Z",
        finished_at="2026-01-01T01:00:00Z",
        spend=SpendRollup(
            total_used_usd=cost if billed is None else billed,
            total_incurred_usd=cost,
            loop=SpendBucket(incurred_unpriced_tokens=unpriced_tokens),
        ),
        **overrides,
    )


def lost_round(
    round_num: int,
    field: str,
    value: str,
    *,
    total: int = 20,
    acc: float = 0.3,
    elimination_context: dict[str, Any] | None = None,
) -> RoundResult:
    """A prior round holding one candidate that was MEASURED and LOST — the history the
    repeat detector reads. ``matched_parent_accuracy`` is the bar ``acc`` is judged against.

    Pass ``elimination_context`` to make the loss a CUT instead: the gate inside it decides
    whether the arm was measured at all, and an empty one is a degradation cut, which names none."""
    return RoundResult(
        round=round_num,
        label=f"round_{round_num}",
        accuracy=acc,
        total=total,
        improved=False,
        prompt_fields={},
        candidates_scored=1,
        candidate_scores=[
            scored_candidate(
                "c0",
                accuracy=acc,
                total=total,
                matched_parent_accuracy=0.5,
                prompt_fields={field: value},
                elimination_stopped=elimination_context is not None,
                elimination_context=elimination_context or {},
            )
        ],
    )
