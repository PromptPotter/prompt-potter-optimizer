"""Datasets declare the formula in ``campaign.json::scoring`` — string shorthand is
``per_sample``, or the twin ``{"per_sample", "per_round"}``. Missing raises; no default."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any, NamedTuple, NotRequired, TypedDict

from promptpotter.config.settings import ANSWER_SPACE_CAP
from promptpotter.shared.errors import ErrorCategory


class PipelineData(TypedDict, total=False):
    # The pipeline's result ranking — the terminal ranker's output, derived at
    # measurement time (``terminal_ranking``). The scorer + ``find_gt_rank`` read this.
    result_ranking: list[dict[str, Any]]
    # Raw per-node ranker outputs, copied from the wire response for per-node
    # diagnostics (retriever recall vs ranker precision). One of these is the source
    # ``result_ranking`` was derived from; both may be absent for non-ranking pipelines.
    final_ranking: list[dict[str, Any]]
    total_time: float
    terminated_at: str
    step_timings: dict[str, Any]
    # Per-LLM-node tokens — mirror of ``StepTokenUsage`` (application/scoring/
    # sample_measurement.py): ``{node: {input, output, estimated,
    # [cost_usd, model, finish_reason, reasoning]}}``; ``estimated=True`` ⇒ counts
    # came from chars/4 fallback, not provider usage; the bracketed keys are present
    # only when the provider surfaced them.
    step_tokens: dict[str, dict[str, Any]]
    llm_provider: str
    pipeline_params: dict[str, Any]
    diagnostics: dict[str, Any]


class QueryMeasurement(TypedDict):
    """``fitness`` is the active-scorer projection written only by ``rescore_results`` — a fresh
    trace has none. ``sample_id`` is a foreign key to ``Sample.id``, stable across campaigns."""

    sample_id: int
    query: str
    ground_truth: str
    predicted: str
    fitness: NotRequired[float]
    # True when this measurement was reused from a prior identical searchpoint
    # instead of a fresh backend call. Stamped ``False`` at measurement time,
    # ``True`` by ``_materialize_cached``. Always present so readers (the live
    # tape + the per-candidate audit table) can show fresh-vs-cached uniformly.
    cached: NotRequired[bool]
    error: str | None
    # Typed error channel: the category owns "this sample errored"; ``error`` is a
    # plain human message (no ``[TAG]`` prefix). ``None``/absent ⇒ clean measurement.
    error_category: NotRequired[ErrorCategory | None]
    pipeline_data: PipelineData | None


Scorer = Callable[[dict[str, Any]], float]
RoundScorer = Callable[[dict[str, float]], float]

DEFAULT_SCORER_ID = "default_hit"

HIT_THRESHOLD = 1.0


def is_hit(fitness: float | None) -> bool:
    """Per-sample display and stratification ONLY — never a rate, an interval or a comparison:
    graded formulas never reach the ceiling, and on a binary one the mean is ``accuracy``."""
    return fitness is not None and fitness >= HIT_THRESHOLD


class ScoringSpec(NamedTuple):
    """Parsed ``campaign.json::scoring`` block — ``(per_sample, per_round, scorer_id)``."""

    per_sample: str | None
    per_round: str | None
    scorer_id: str


def enumerable_truth_labels(rows: Sequence[Mapping[str, Any]]) -> Counter[str] | None:
    """The ground-truth label tally, or ``None`` where collapse is not a meaningful question —
    above ``ANSWER_SPACE_CAP`` truths, or one truth per row, every prediction is its own bucket."""
    truth = Counter(str(v) for r in rows if (v := r.get("ground_truth")) not in (None, ""))
    if not truth or len(truth) > ANSWER_SPACE_CAP or len(truth) == len(rows):
        return None
    return truth


def modal_answer_share(rows: Sequence[Mapping[str, Any]]) -> float | None:
    """Over PREDICTIONS — the ``answer_distribution`` panel's ``constant`` is over GROUND TRUTHS.
    Reports and never gates: below 1.0 this measures hedging, the gradient the loop climbs."""
    if enumerable_truth_labels(rows) is None:
        return None
    said = Counter(str(v) for r in rows if (v := r.get("predicted")) not in (None, ""))
    total = sum(said.values())
    if total == 0:
        return None
    return said.most_common(1)[0][1] / total


def is_answer_collapsed(rows: Sequence[Mapping[str, Any]]) -> bool:
    """The ABSENCE of a measurement, not a low score — θ fitted to a constant answer is an
    artifact, so the candidate is withheld from θ and eliminated by PoBB."""
    truth = enumerable_truth_labels(rows)
    if truth is None or len(truth) < 2:
        return False
    said = Counter(str(v) for r in rows if (v := r.get("predicted")) not in (None, ""))
    return len(said) == 1


__all__ = [
    "DEFAULT_SCORER_ID",
    "HIT_THRESHOLD",
    "PipelineData",
    "QueryMeasurement",
    "RoundScorer",
    "Scorer",
    "ScoringSpec",
    "enumerable_truth_labels",
    "is_answer_collapsed",
    "is_hit",
    "modal_answer_share",
]
