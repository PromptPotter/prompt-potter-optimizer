"""Scoring domain types — pure data; compiler + ``rescore_results`` in ``application.scoring.formula``.

Datasets declare a formula in ``campaign.json::scoring``:
- string shorthand ⇒ ``per_sample`` (``per_round`` uses registry default);
- twin form ``{"per_sample", "per_round"}``.

Missing ``scoring`` raises in ``compile_scorer`` — no implicit default.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any, NamedTuple, NotRequired, TypedDict

from promptpotter.config.settings import ANSWER_SPACE_CAP
from promptpotter.shared.errors import ErrorCategory


class PipelineData(TypedDict, total=False):
    """Nested pipeline execution details within a QueryMeasurement."""

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
    """Per-sample measurement.

    Raw trace fields (query/ground_truth/predicted/error/pipeline_data) populated
    at measurement time. ``hit``/``fitness`` are the active-scorer projection,
    written exclusively by ``rescore_results`` (which also populates the
    ``scored`` audit map: ``{scorer_id: {fitness, hit, formula}}``). ``NotRequired``
    because a freshly measured trace hasn't been scored yet.

    ``sample_id`` = foreign key to ``Sample.id``, stable across campaigns.
    """

    sample_id: int
    query: str
    ground_truth: str
    predicted: str
    hit: NotRequired[bool]
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
    """Did this measurement max out the active scorer? The ONE definition of that threshold.

    It says nothing about ability and is never evidence. On a graded formula (a bounded
    product, ``rr``, ``sigmoid``) the ceiling is unreachable, so this reads ``False``
    forever; on a binary one the mean of these bools is *exactly* ``accuracy`` (mean
    fitness), which is what every aggregate already reports. Either way it adds no
    information to the ``fitness`` sitting beside it.

    So the only honest uses are **per-sample display** and **stratification** — never a
    rate, a confidence interval, or a candidate comparison. θ, PoBB and the round election
    all read graded ``fitness`` directly (``intelligence/exploration.py::graded_response``).

    ``None`` — never measured — is not a hit.
    """
    return fitness is not None and fitness >= HIT_THRESHOLD


class ScoringSpec(NamedTuple):
    """Parsed ``campaign.json::scoring`` block — ``(per_sample, per_round, scorer_id)``."""

    per_sample: str | None
    per_round: str | None
    scorer_id: str


def enumerable_truth_labels(rows: Sequence[Mapping[str, Any]]) -> Counter[str] | None:
    """The ground-truth label tally, or ``None`` where the answer space has no repeated label.

    ``None`` means "collapse is not a meaningful question here": above ``ANSWER_SPACE_CAP``
    distinct truths the output is free-text, and when every row carries a DISTINCT truth the
    answers are identity-keyed (an L4 outer round's per-seed inner-result tokens) — in both
    cases every prediction is its own bucket and a constant answer cannot be detected.

    One definition, three consumers: the ``answer_distribution`` panel that reports the constant
    floor, and :func:`is_answer_collapsed`'s two gates. Deriving the rule twice is how the panel
    could call a round collapsed while the scorer called it clean."""
    truth = Counter(str(v) for r in rows if (v := r.get("ground_truth")) not in (None, ""))
    if not truth or len(truth) > ANSWER_SPACE_CAP or len(truth) == len(rows):
        return None
    return truth


def is_answer_collapsed(rows: Sequence[Mapping[str, Any]]) -> bool:
    """True when a candidate answered the SAME label to every sample while the truth varied.

    This is **not** a low score — it is the ABSENCE of a measurement. A constant answerer's
    responses carry no information about ability, so fitting θ to them reports an artifact: its
    accuracy is decided by how much of the label it happens to emit appears in the drawn subset,
    not by the candidate. Measured live: a candidate answering ``Uncertain`` to all 8 samples of a
    4-TRUE/4-FALSE subset scored 0.0 and drew θ=-1.87, where the same behaviour scores ~0.27 on
    the full bank. One such arm dragged its round 0.8 logits below origin.

    Deliberately says nothing about WHY it collapsed, and carries no minimum sample count: the
    ``len(truth) > 1`` clause self-normalizes, since a single-sample or single-truth round cannot
    distinguish a constant answer from a correct one."""
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
]
