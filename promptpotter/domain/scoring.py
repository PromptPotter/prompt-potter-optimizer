"""Datasets declare the formula in ``campaign.json::scoring`` — string shorthand is
``per_sample``, or the twin ``{"per_sample", "per_cell"}``. Missing raises; no default."""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, NamedTuple, NotRequired, TypedDict, cast

from promptpotter.config.settings import ANSWER_SPACE_CAP
from promptpotter.shared.errors import ErrorCategory


class LedgerPipelineData(TypedDict, total=False):
    """The pipeline half of a ledger record. Membership IS the projection: a field declared here
    reaches ``ledger_sample_view``'s output, one declared on :class:`PipelineData` does not."""

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
    diagnostics: dict[str, Any]
    # L4: one outer sample IS a whole inner campaign, so its "answer" is a lift.
    mean_round_delta: float
    # Read defensively beside the top-level twins (``_absorb_sample_scored``, ``RoundBuffer``):
    # no writer in this repo sets them here, but a backend that did must not be silently
    # dropped by ``ledger_sample_view``.
    error: str | None
    input_tokens: int
    output_tokens: int


class PipelineData(LedgerPipelineData, total=False):
    """What the backend returned BESIDE the ledger half. It is on disk twice already, from the
    same objects and never from a ledger record: the ``MeasurementArchive`` row keyed
    ``(dataset_name, node_configs, sample_id)``, and ``rounds/round_NNNN.json::results[]`` +
    ``::all_candidate_results{}``. The reasoning trace and the resolved params are the bulk of it,
    and the panels that cite them (``dispatch/injections/panels.py``) read the in-memory
    ``InjectionBundle.trajectory_results``, never a ledger record."""

    # The pipeline's result ranking — the terminal ranker's output, derived at
    # measurement time (``terminal_ranking``). The scorer + ``find_gt_rank`` read this.
    result_ranking: list[dict[str, Any]]
    # Raw per-node ranker outputs, copied from the wire response for per-node
    # diagnostics (retriever recall vs ranker precision). One of these is the source
    # ``result_ranking`` was derived from; both may be absent for non-ranking pipelines.
    final_ranking: list[dict[str, Any]]
    pipeline_params: dict[str, Any]
    # The task model's chain-of-thought, head-capped at the backend. The critique tier reads
    # it to diagnose WHERE a deduction broke, off the in-memory trajectory.
    reasoning_trace: str
    # The SE beside ``mean_round_delta`` is this arm's OWN half of a paired cell difference — the
    # shared origin level is excluded because it cancels in that difference (`domain/l4/proxies.py`).
    mean_parent_level_se: float
    # The rest of that inner campaign's own trajectory and cost, carried beside the lift so a
    # reader can ask what the run DID rather than only what it scored. `inner_spend_usd` /
    # `inner_tokens` are reporting figures and bill nothing — see `InnerCellFacts`.
    inner_origin_level: float
    inner_final_lift: float
    inner_peak_lift: float
    inner_rounds_ran: int
    inner_round_budget: int
    inner_stop_reason: str
    inner_unworked_s: float
    inner_spend_usd: float | None
    inner_tokens: int | None
    inner_campaign_id: str


class QueryMeasurement(TypedDict):
    """``fitness`` and ``objective`` are the active-scorer projections written only by
    ``rescore_results`` — a fresh trace has neither. ``sample_id`` is a foreign key to
    ``Sample.id``, stable across campaigns."""

    sample_id: int
    query: str
    ground_truth: str
    predicted: str
    fitness: NotRequired[float]
    # What this cell was WORTH under the campaign's composite formula — the value θ is fit on.
    # Equals ``fitness`` wherever no ``per_cell`` formula was declared. See :class:`CellScorer`.
    objective: NotRequired[float]
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
_LEDGER_PIPELINE_KEYS: frozenset[str] = frozenset(
    LedgerPipelineData.__required_keys__ | LedgerPipelineData.__optional_keys__
)

_ROW_KEYS: frozenset[str] = frozenset(
    QueryMeasurement.__required_keys__ | QueryMeasurement.__optional_keys__
)
_PIPELINE_KEYS: frozenset[str] = frozenset(
    PipelineData.__required_keys__ | PipelineData.__optional_keys__
)


# -- what an archive row may lose ---------------------------------------------
#
# `application/archive_maintenance.py` moves these into the cold store; they live HERE because the
# question they answer — which of a row's keys does anything read — is about the two types above,
# and a set of key names sitting anywhere else is a second contract nobody declared.
#
# Two sets rather than one, because the asserts below run in OPPOSITE directions and a flat set
# could not carry either.

UNREAD_ROW_KEYS: frozenset[str] = frozenset({"objective"})
"""Declared, written, and read by nothing.

Archive rows are RE-GRADED by the reading campaign's scorer (`intelligence/hard_sample_archive.py`
builds its observations through `CellScorer.objective`), so a STORED verdict is never consulted."""

ABANDONED_ROW_KEYS: frozenset[str] = frozenset({"hit", "scored"})
"""On disk in quantity, declared by nothing, and written by no code path in this tree.

They are not deprecated fields — they were never fields. Nothing derives them either: every reader
meaning "did this cell land" calls :func:`is_hit` on ``fitness`` at the point of use. Naming them
is what lets a compaction move them; the assert is what stops one silently becoming a real key
again."""

UNREAD_PIPELINE_KEYS: frozenset[str] = frozenset({"reasoning_trace", "total_time"})
"""``pipeline_data`` keys no estimator, cache, ruler or index reads.

``reasoning_trace`` reaches only the three L1 transcript panels, and only for rows live in the
current cycle; ``total_time`` is zeroed on replay anyway.

**A ranking may not be moved.** The `candidate_recall` / `source_recall` evaluators walk
`final_ranking` / `candidate_ranking` for GT membership, and a row cannot tell a MOVED key from a
ranker that legitimately returned nothing — so a compacted row scores a real miss, the denominator
(`terminal_node` / `step_timings`) survives compaction intact, and the fabricated rate is banked as
`index.jsonl::scores` and re-read by any `score:` lens."""

assert UNREAD_ROW_KEYS <= _ROW_KEYS, "an unread key must be one QueryMeasurement declares"
assert not (ABANDONED_ROW_KEYS & _ROW_KEYS), "an abandoned key that got declared is no longer one"
assert UNREAD_PIPELINE_KEYS <= _PIPELINE_KEYS, "an unread key must be one PipelineData declares"


def ledger_sample_view(result: QueryMeasurement) -> QueryMeasurement:
    """NEW dicts, never a pop: the argument is the same object that becomes the archive row,
    ``RoundResult.results``, ``all_candidate_results``, ``overlap_results`` and
    ``InjectionBundle.trajectory_results``. Every top-level key rides along — only
    ``pipeline_data`` is narrowed, to :class:`LedgerPipelineData`."""
    out: dict[str, Any] = {k: v for k, v in result.items() if k != "pipeline_data"}
    if "pipeline_data" in result:
        pd = result["pipeline_data"]
        out["pipeline_data"] = (
            None if pd is None else {k: v for k, v in pd.items() if k in _LEDGER_PIPELINE_KEYS}
        )
    return cast("QueryMeasurement", out)


class CellScorer(NamedTuple):
    """The two numbers one cell carries, minted together because they are read apart.

    ``fitness`` is CORRECTNESS — what :func:`is_hit` thresholds for the tape, the difficulty
    stratification and the failing-samples panel L1 repairs from. ``objective`` is WORTH, what the
    campaign's composite makes of that cell, and the only one θ is fit on
    (``intelligence/exploration.py::graded_response``).

    Two numbers rather than one: a cost penalty folded into the first renders a correct-but-slow
    answer MISS and sends L1 to repair reasoning that was right, while a second left at bare
    accuracy keeps every cost and reliability term out of the election a round is won on."""

    fitness: Callable[[dict[str, Any]], float]
    objective: Callable[[dict[str, Any]], float]


# The MASK's evaluator, over a round's stored per-round evaluator map. A read-side counterfactual,
# so it stays per-round: the record it reads may no longer have the rows.
RoundScorer = Callable[[dict[str, float]], float]

DEFAULT_SCORER_ID = "default_hit"

HIT_THRESHOLD = 1.0


def extract_item_label(c: Any) -> str:
    """Canonical label of a ranked item (dict ``{candidate: ...}``, list/tuple, or string) — what a
    rank walk compares against ground truth and what a display line prints."""
    if isinstance(c, dict):
        return str(c.get("candidate", c))
    return c[0] if isinstance(c, (list, tuple)) else str(c)


def is_hit(fitness: float | None) -> bool:
    """Per-sample display and stratification ONLY — never a rate, an interval or a comparison:
    graded formulas never reach the ceiling, and on a binary one the mean is ``accuracy``."""
    return fitness is not None and fitness >= HIT_THRESHOLD


def recorded_elapsed_s(result: QueryMeasurement) -> float | None:
    """Wall-clock this row RECORDED, or ``None`` where it recorded none — the display read.

    A cached replay's ``0.0`` is a true reading (``_materialize_cached`` stamps it; nothing was
    spent), while a row that never reached the pipeline has no time at all. Two display sites
    each wrote ``pd.get("total_time", 0.0) or 0.0``, which made those two identical and painted
    the second as an instant success. What a cell COST is the question ``recorded_cost_s``
    answers, off ``step_timings``, which survives the cache stamp."""
    pd = result.get("pipeline_data") or {}
    total = pd.get("total_time")
    return float(total) if isinstance(total, int | float) else None


def recorded_cost_s(result: QueryMeasurement) -> float | None:
    """What producing this row COST in seconds, or ``None`` where it recorded no timing.

    The cache-surviving half of the pair above: ``step_timings`` holds what the work took the
    first time it ran, so a replay still prices the cell it replays. Summing an EMPTY map to
    ``0.0`` would report an unpriced cell as a free one, so absent stays absent."""
    timings = (result.get("pipeline_data") or {}).get("step_timings")
    if not isinstance(timings, dict) or not timings:
        return None
    total = 0.0
    for entry in timings.values():
        if not isinstance(entry, int | float) or isinstance(entry, bool):
            return None
        total += float(entry)
    return total


class ScoringSpec(NamedTuple):
    """Parsed ``campaign.json::scoring`` block — ``(per_sample, per_cell, scorer_id)``.

    ``per_cell`` is the composite, evaluated on ONE cell rather than the round: latency, cost and
    unreliability are provoked prompt by prompt, and a round-level mean hides a 2000-second cell
    behind a fast one."""

    per_sample: str | None
    per_cell: str | None
    scorer_id: str


def _summands(node: ast.expr) -> list[ast.expr]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return [*_summands(node.left), node.right]
    return [node]


def _weighted_name(node: ast.expr) -> tuple[str, float] | None:
    """One ``w * name`` / ``name * w`` / ``w * (1 - name)`` term, or a bare name at weight 1.0 —
    which the default formula (`accuracy`) is, and rejecting it would leave the common case
    unseeded."""
    if isinstance(node, ast.Name):
        return (node.id, 1.0)
    if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Mult):
        return None
    coef, term = (node.left, node.right)
    if not isinstance(coef, ast.Constant):
        coef, term = (node.right, node.left)
    if not isinstance(coef, ast.Constant) or isinstance(coef.value, bool):
        return None
    if not isinstance(coef.value, int | float):
        return None
    # `(1 - name)` is how a lower-is-better evaluator enters a sum; the weight is the operator's
    # either way, so the two shapes report the same number.
    if (
        isinstance(term, ast.BinOp)
        and isinstance(term.op, ast.Sub)
        and isinstance(term.left, ast.Constant)
        and term.left.value == 1
        and isinstance(term.right, ast.Name)
    ):
        return (term.right.id, float(coef.value))
    return (term.id, float(coef.value)) if isinstance(term, ast.Name) else None


def weighted_sum_weights(formula: str | None) -> dict[str, float] | None:
    """*formula*'s per-evaluator coefficient where it IS a weighted sum — the shape every default
    and operator formula takes.

    ``None`` where it is not one, and that is the load-bearing answer rather than a failure: a
    control offering one weight per evaluator can only describe a weighted sum, so inventing
    entries for a formula that is not one hands the operator sliders that do not add up to what is
    being scored. The browser did exactly that with a regex, silently substituting a fixed default
    for every name it could not parse. A name appearing twice is refused for the same reason —
    one slider cannot stand for two terms.

    Whether a term is INVERTED is deliberately not returned: the evaluator registry already says
    which are lower-is-better, and a second answer here is one that can disagree with it.
    """
    if not formula:
        return None
    try:
        tree = ast.parse(formula, "<scoring formula>", "eval")
    except SyntaxError:
        return None
    weights: dict[str, float] = {}
    for summand in _summands(tree.body):
        term = _weighted_name(summand)
        if term is None or term[0] in weights:
            return None
        weights[term[0]] = term[1]
    return weights or None


def is_verifier_graded(ground_truth: str | None) -> bool:
    """Whether this cell was graded with NO label — the backend answered with a number and the
    task's own verifier (or L4's outer proxies) decided it, so there is no truth string for
    ``predicted`` to match.

    **The one place that fact is asked.** It takes the LABEL rather than its carrier because the
    two carriers are different types — a not-yet-measured ``Sample`` (``ground_truth: str | None``,
    where ``None`` is the declaration) and a measured row (``QueryMeasurement.ground_truth: str``,
    where the same fact arrives as ``""``) — and both ask this one question.

    Ask this, never ``predicted == NO_RESULT``. That sentinel is set by ``terminal_ranking``
    returning nothing, which a DATASET decides: Harbor's ``agent`` node declares no ``node_role``
    so the sentinel fires, while ``promptpotter-self``'s ``l1_critique`` declares ``ranker`` so it
    never does. Two labelless backends, opposite answers, from a proxy for something neither of
    them is about. The label's absence is the same on both.
    """
    return not (ground_truth or "")


def all_verifier_graded(labels: Iterable[str | None]) -> bool:
    """The SET arity: whether a whole round, bank or dataset carries no labels.

    Empty is False — a set with no members declares nothing, and the readers that ask this
    (rank statistics, the recall evaluators, the formula gate) would otherwise treat "measured
    nothing yet" as "this backend has no labels" and go silent on a real one.

    What it decides is whether a LABEL-comparing reading means anything. With no label,
    ``predicted`` can never equal ``ground_truth``, so every row is a miss, every rank is
    ``not_found`` and every recall is ``0.0`` — a split that partitions nothing, reported as if it
    had. Emit absence there, not zero.
    """
    seen = False
    for label in labels:
        if not is_verifier_graded(label):
            return False
        seen = True
    return seen


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
    "ABANDONED_ROW_KEYS",
    "DEFAULT_SCORER_ID",
    "HIT_THRESHOLD",
    "UNREAD_PIPELINE_KEYS",
    "UNREAD_ROW_KEYS",
    "CellScorer",
    "PipelineData",
    "QueryMeasurement",
    "RoundScorer",
    "ScoringSpec",
    "all_verifier_graded",
    "enumerable_truth_labels",
    "is_answer_collapsed",
    "is_hit",
    "is_verifier_graded",
    "ledger_sample_view",
    "modal_answer_share",
]
