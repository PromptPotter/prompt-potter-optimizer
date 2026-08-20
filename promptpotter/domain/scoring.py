"""Datasets declare the formula in ``campaign.json::scoring`` — string shorthand is
``per_sample``, or the twin ``{"per_sample", "per_round"}``. Missing raises; no default."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any, NamedTuple, NotRequired, TypedDict, cast

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
    # The DEEPEST node that ran — never a failure signal. It doubles as the archive's reuse
    # depth ("this outcome depends on config only up to here"), which is what the L4 connector
    # stamps outright. Read it as "where did it get to", never as "where did it die".
    terminal_node: str
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
    # The task model's chain-of-thought, head-capped at the backend. The critique tier reads
    # it to diagnose WHERE a deduction broke, off the in-memory trajectory.
    reasoning_trace: str
    # L4: one outer sample IS a whole inner campaign, so its "answer" is a lift. The SE beside it
    # is this arm's OWN half of a paired cell difference — the shared origin level is excluded
    # because it cancels in that difference (`domain/l4/proxies.py`).
    mean_round_delta: float
    mean_adopted_level_se: float
    # Read defensively beside the top-level twins (`_absorb_sample_scored`, `RoundBuffer`):
    # no writer in this repo sets them here, but a backend that did must not be silently
    # dropped by ``ledger_sample_view``.
    error: str | None
    input_tokens: int
    output_tokens: int


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
    # ---- Stamped after measurement, by the scorer and the walk -------------------
    # Where the ground truth landed in the terminal ranking, and how many candidates it
    # ranked against — computed once at scoring time (``find_gt_rank``); the MISS tag reads
    # the pair and must never re-derive it.
    ground_truth_rank: NotRequired[int | None]
    n_candidates: NotRequired[int]
    input_tokens: NotRequired[int]
    output_tokens: NotRequired[int]
    # The candidate's composite over samples-so-far, attached for the ledger path only
    # (``query_loop._with_running``) so a file-tree or chat reader watches it converge.
    _running: NotRequired[dict[str, Any]]
    # The stale-data ladder's per-sample verdicts. Each renders one annotation under the
    # HIT/MISS line (``views/live/sample.py``) and nothing else reads them, so they are the
    # ladder's only report: a dropped flag makes a re-measurement look like a plain score.
    retry_of_deprecated_cache: NotRequired[bool]
    retry_of_degraded: NotRequired[bool]
    rerun_comparison: NotRequired[dict[str, Any]]
    samplescan_resolved: NotRequired[bool]
    switched_out: NotRequired[bool]
    config_fundamental_skip: NotRequired[bool]
    persistently_degraded: NotRequired[bool]
    degraded_observed: NotRequired[bool]
    degraded_obs_count: NotRequired[int]
    degraded_obs_threshold: NotRequired[int]


# The ledger's per-sample view: the UNION of what its two subscribers render — the terminal
# tape (`views/live/sample.py::fmt_query_result` + `classify_result`) and the dashboard
# (`live_dashboard/view.py::_absorb_sample_scored` → `RoundBuffer.append_sample` → the SSE
# chat's `sampleScoredCandidate`). A superset of both, so the ledger holds exactly what the
# operator was shown and there is one definition to keep in sync instead of two.
#
# What it drops is on disk twice already, from the same objects and never from this record:
# the MeasurementArchive row keyed (dataset_name, node_configs, sample_id), and
# `rounds/round_NNNN.json::results[]` + `::all_candidate_results{}`. The reasoning trace and
# the resolved params are the bulk of it, and the panels that cite them
# (`dispatch/injections/panels.py`) read the in-memory `InjectionBundle.trajectory_results`,
# never a ledger record.
_LEDGER_SAMPLE_KEYS: frozenset[str] = frozenset(
    {
        "sample_id",
        "query",
        "ground_truth",
        "predicted",
        "fitness",
        "cached",
        "error",
        "error_category",
        "ground_truth_rank",
        "n_candidates",
        "input_tokens",
        "output_tokens",
        "_running",
        "retry_of_deprecated_cache",
        "retry_of_degraded",
        "rerun_comparison",
        "samplescan_resolved",
        "switched_out",
        "config_fundamental_skip",
        "persistently_degraded",
        "degraded_observed",
        "degraded_obs_count",
        "degraded_obs_threshold",
    }
)
_LEDGER_PIPELINE_KEYS: frozenset[str] = frozenset(
    {
        "total_time",
        "terminal_node",
        "step_timings",
        "step_tokens",
        "diagnostics",
        "error",
        "input_tokens",
        "output_tokens",
        "mean_round_delta",
    }
)

# A key named here but not declared above is dropped from every ledger record and nothing
# says so — the reader keeps working on the default. Fail at import instead.
_DECLARED_SAMPLE_KEYS: frozenset[str] = frozenset(QueryMeasurement.__annotations__)
_DECLARED_PIPELINE_KEYS: frozenset[str] = frozenset(PipelineData.__annotations__)
assert _LEDGER_SAMPLE_KEYS.issubset(_DECLARED_SAMPLE_KEYS), (
    f"ledger keep-set names undeclared QueryMeasurement keys: "
    f"{sorted(_LEDGER_SAMPLE_KEYS - _DECLARED_SAMPLE_KEYS)}"
)
assert _LEDGER_PIPELINE_KEYS.issubset(_DECLARED_PIPELINE_KEYS), (
    f"ledger keep-set names undeclared PipelineData keys: "
    f"{sorted(_LEDGER_PIPELINE_KEYS - _DECLARED_PIPELINE_KEYS)}"
)


def ledger_sample_view(result: QueryMeasurement) -> QueryMeasurement:
    """NEW dicts, never a pop: the argument is the same object that becomes the archive row,
    ``RoundResult.results``, ``all_candidate_results``, ``overlap_results`` and
    ``InjectionBundle.trajectory_results``."""
    out: dict[str, Any] = {k: v for k, v in result.items() if k in _LEDGER_SAMPLE_KEYS}
    if "pipeline_data" in result:
        pd = result["pipeline_data"]
        out["pipeline_data"] = (
            None if pd is None else {k: v for k, v in pd.items() if k in _LEDGER_PIPELINE_KEYS}
        )
    return cast("QueryMeasurement", out)


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
    "ledger_sample_view",
    "modal_answer_share",
]
