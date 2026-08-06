"""Evaluator registry + materializers; per-round/per-sample compute fns return a float in [0, 1],
or ``None`` when the round/sample carried nothing to measure — the materializers then omit the
key rather than fabricate a value. A zero is a verdict; an absence is not."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.application.scoring.formula import extract_item_label
from promptpotter.shared.composite import to_short_formula
from promptpotter.shared.errors import has_pipeline_warnings, is_error_result

if TYPE_CHECKING:
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.pipeline_schema import PipelineNode, PipelineSchema
    from promptpotter.domain.scoring import QueryMeasurement


Scope = Literal["per_sample", "per_round"]


# Cost/latency yardsticks for the cost-shaped evaluators. Intentionally fixed
# module constants, NOT per-campaign knobs: these normalize cross-dataset cost
# terms onto one comparable [0,1] scale, so a dataset with long prompts SHOULD
# read a lower compactness — that's the signal, not a miscalibration. Make them
# tunable only if a real per-dataset need appears (then they move to
# campaign.json::scoring); until then, one yardstick keeps comparisons honest.
LATENCY_BUDGET_MS = 10_000.0  # ≥ budget → 0.0, 0 → 1.0
PROMPT_BUDGET_CHARS = 4_000  # ≈ 1000 tokens; soft linear ceiling
OUTPUT_TOKEN_BUDGET = 12_000  # generation-cost soft ceiling; ≥ budget → 0.0


__all__ = [
    "LATENCY_BUDGET_MS",
    "OUTPUT_TOKEN_BUDGET",
    "PROMPT_BUDGET_CHARS",
    "SELF_HEALERS",
    "Evaluator",
    "SelfHealerSpec",
    "all_evaluators",
    "default_per_round_formula",
    "default_per_round_formula_short",
    "evaluators_meta",
    "materialize_round_values",
    "materialize_row_derivable",
    "materialize_sample_values",
    "resolve_round_formula",
]


def compute_accuracy(*, results: list[QueryMeasurement], **_: Any) -> float | None:
    """Mean fitness over SCOREABLE samples. Three row classes, three treatments:

    - **scoreable** (not deprecated, not errored) — carries a verdict; averaged.
    - **deprecated** (fatal-classified) — a verdict-of-0-class failure, already penalized
      via ``runtime_failure_rate``; excluded from the mean.
    - **error** (``is_error_result`` — the measurement never happened) — the ABSENCE of a
      verdict, not a zero; excluded from the mean and surfaced via ``compute_error_rate``.

    ``None`` when the round scored no samples at all. No-scoreable-rows returns 0.0 — for
    all-deprecated that is the honest verdict (every sample measured and fatally failed);
    for all-errored the honest value is ``None``, but that Optional must propagate through
    ``materialize_round_values`` → ``compute_composite_fitness`` without
    ``ScoringTermMissingError`` first (see ``docs/specs/code-debt-cleanup.md``)."""
    # Lazy: scoring → optimization circular.
    from promptpotter.application.optimization.pobb.classification import is_deprecated

    if not results:
        return None
    scoreable = [r for r in results if not is_deprecated(r) and not is_error_result(r)]
    if not scoreable:
        return 0.0
    return sum(r.get("fitness", 0.0) for r in scoreable) / len(scoreable)


def compute_error_rate(*, results: list[QueryMeasurement], **_: Any) -> float | None:
    if not results:
        return None
    return sum(1 for r in results if is_error_result(r)) / len(results)


def compute_degraded_rate(*, results: list[QueryMeasurement], **_: Any) -> float | None:
    if not results:
        return None
    return sum(1 for r in results if has_pipeline_warnings(r)) / len(results)


@dataclass(frozen=True)
class SelfHealerSpec:
    name: str
    attr: str
    description: str


SELF_HEALERS: tuple[SelfHealerSpec, ...] = (
    SelfHealerSpec(
        "validation_failure_rate",
        "validation_failures",
        "Fraction of samples where L1 output was malformed; L1 re-proposes (owner=L1).",
    ),
    SelfHealerSpec(
        "runtime_failure_rate",
        "runtime_failures",
        "Fraction of samples that triggered DegradationCheck; L1 retunes, or operator-flagged if locked.",
    ),
    SelfHealerSpec(
        "l2_guard_breach_rate",
        "l2_guard_breaches",
        "Fraction of samples where L2 refinement breached guards; L3 healed.",
    ),
    SelfHealerSpec(
        "l3_guard_breach_rate",
        "l3_guard_breaches",
        "Fraction of samples where L3 plan breached its own guards.",
    ),
)


def _make_self_healer_evaluator(spec: SelfHealerSpec) -> Evaluator:
    """Build the per-channel self-heal Evaluator: ``min(len(events)/n_samples, 1.0)``,
    ``direction='low'``. ``events`` is the matching list on ``opt_sp.memory.wounds``."""

    def compute(
        *, results: list[QueryMeasurement], opt_sp: OptSearchPoint | None = None, **_: Any
    ) -> float | None:
        # No samples, or no OptSearchPoint to read the wound channels off: the heal rate was
        # not measured. 0.0 would read as "nothing needed healing".
        if not results or opt_sp is None:
            return None
        # The four wound channels live on ``opt_sp.memory.wounds`` (not the OSP
        # top level, which is ``extra="forbid"``); reading the top level always
        # missed, so every self-heal rate silently computed 0.0.
        events = getattr(opt_sp.memory.wounds, spec.attr)
        return min(len(events) / len(results), 1.0)

    return Evaluator(
        name=spec.name,
        description=spec.description,
        scope="per_round",
        compute=compute,
        direction="low",
    )


def compute_latency_norm(*, results: list[QueryMeasurement], **_: Any) -> float | None:
    """``None`` when no sample carried a timing — an untimed round is not an instant one."""
    latencies: list[float] = []
    for r in results:
        pd = r.get("pipeline_data") or {}
        t = pd.get("total_time")
        if t is None:
            continue
        try:
            latencies.append(float(t))
        except (TypeError, ValueError):
            continue
    if not latencies:
        return None
    mean_ms = sum(latencies) / len(latencies)
    return max(0.0, 1.0 - mean_ms / LATENCY_BUDGET_MS)


def _compute_recall(
    *,
    results: list[QueryMeasurement],
    node: PipelineNode,
    candidate_key: str,
    **_: Any,
) -> float | None:
    """Fraction of non-error queries (where *node* ran) with GT in *candidate_key*'s list.
    ``None`` when the node ran on no query — recall over nothing is not zero recall."""

    def _step_ran(r: QueryMeasurement) -> bool:
        pd = r.get("pipeline_data") or {}
        if pd.get("terminated_at") == node.name:
            return True
        return (pd.get("step_timings") or {}).get(node.name) is not None

    scoped = [r for r in results if _step_ran(r) and not is_error_result(r)]
    if not scoped:
        return None
    found = 0
    for r in scoped:
        pd = r.get("pipeline_data") or {}
        raw = pd.get(candidate_key)
        candidates: list[Any] = list(raw) if isinstance(raw, list) else []
        gt = r.get("ground_truth", "")
        if any(extract_item_label(c) == gt for c in candidates):
            found += 1
    return found / len(scoped)


def compute_cache_hit_rate(
    *, results: list[QueryMeasurement], node: PipelineNode, **_: Any
) -> float | None:
    """``None`` when every query errored — a rate over no completed query is unmeasured."""
    cache_hits = non_error = 0
    for r in results:
        if is_error_result(r):
            continue
        non_error += 1
        pd = r.get("pipeline_data") or {}
        if (pd.get("step_timings") or {}).get(node.name) is not None:
            cache_hits += 1
    return cache_hits / non_error if non_error else None


_LIMIT_KEY_SUFFIXES = ("max_sites", "num_results", "max_token_candidates", "max_tokens")


def _limit_nodes(schema: PipelineSchema) -> list[tuple[PipelineNode, str, int]]:
    out: list[tuple[PipelineNode, str, int]] = []
    for node in schema.nodes:
        cfg = node.current_config
        for key in cfg:
            if not any(key == s or key.endswith(s) for s in _LIMIT_KEY_SUFFIXES):
                continue
            target = cfg.get(key)
            if not isinstance(target, int) or target <= 0:
                continue
            out.append((node, key, target))
            break
    return out


def has_limit_node(schema: PipelineSchema) -> bool:
    return bool(_limit_nodes(schema))


def _retrieval_shortfall_for_result(
    result: QueryMeasurement, schema: PipelineSchema
) -> float | None:
    """Mean of min(observed/target, 1.0) over limit-bearing nodes; None when none apply."""
    pd = result.get("pipeline_data") or {}
    ratios: list[float] = []
    for node, _key, target in _limit_nodes(schema):
        for mapping in node.observation_mappings:
            val = pd.get(mapping.pipeline_key)
            if isinstance(val, list):
                ratios.append(min(len(val) / target, 1.0))
                break
    if not ratios:
        return None
    return sum(ratios) / len(ratios)


def compute_retrieval_shortfall_per_sample(
    *, result: QueryMeasurement, schema: PipelineSchema | None = None, **_: Any
) -> float | None:
    """``None`` when no limit-bearing node produced a list for this sample — a retrieval that
    never ran did not meet its target."""
    if schema is None:
        return None
    return _retrieval_shortfall_for_result(result, schema)


def compute_mean_retrieval_shortfall(
    *, results: list[QueryMeasurement], schema: PipelineSchema, **_: Any
) -> float | None:
    values: list[float] = []
    for r in results:
        v = _retrieval_shortfall_for_result(r, schema)
        if v is not None:
            values.append(v)
    if not values:
        return None
    return sum(values) / len(values)


def compute_pipeline_compactness(*, schema: PipelineSchema, **_: Any) -> float:
    if schema.is_single_node:
        return 1.0
    n = len(schema.active_steps)
    worst = 12  # node-count yardstick, same intentionally-fixed rationale as the budgets above
    return max(0.0, 1.0 - (n - 1) / (worst - 1))


def compute_prompt_compactness(*, opt_sp: OptSearchPoint | None = None, **_: Any) -> float | None:
    """``1 - len(render)/budget``; ``None`` when there is no prompt to measure — an absent
    prompt is not a maximally compact one."""
    if opt_sp is None:
        return None
    rendered = opt_sp.render() or ""
    if not rendered:
        return None
    return max(0.0, 1.0 - len(rendered) / PROMPT_BUDGET_CHARS)


def compute_output_compactness(*, results: list[QueryMeasurement], **_: Any) -> float | None:
    """``1 - mean(output_tokens)/budget`` — the generation-cost twin of
    ``prompt_compactness`` (output side). Sums each step's completion tokens from
    ``pipeline_data.step_tokens[*].output``; ``None`` when no sample reported token
    counts — a round whose cost we failed to record is not a maximally cheap one, and
    scoring it 1.0 paid a fitness bonus for missing telemetry. This is the
    accuracy-vs-cost axis the optimizer can trade against — a terse candidate scores
    higher here than a verbose one."""
    totals: list[float] = []
    for r in results:
        st = (r.get("pipeline_data") or {}).get("step_tokens") or {}
        out = 0.0
        for v in st.values():
            o = v.get("output") if isinstance(v, dict) else None
            if isinstance(o, (int, float)):
                out += float(o)
        totals.append(out)
    if not totals or not any(totals):
        return None
    mean_out = sum(totals) / len(totals)
    return max(0.0, 1.0 - mean_out / OUTPUT_TOKEN_BUDGET)


@dataclass(frozen=True)
class Evaluator:
    name: str
    description: str
    scope: Scope
    # ``None`` = this round/sample carried nothing to measure. The materializers below OMIT
    # the key rather than substituting a default, so a formula naming an unmeasured term halts
    # loud (``round_scorer``) instead of scoring on a number nobody computed. An
    # empty-collection default reads as PERFECT here — inverted for every health term.
    compute: Callable[..., float | None]
    # `high` = larger is better; `low` = larger is worse (webapp What-If panel direction-corrects).
    direction: Literal["high", "low"] = "high"
    node_type: str | None = None
    applies: Callable[[PipelineSchema], bool] = field(default=lambda _schema: True)
    # True ⇒ a pure function of the persisted per-sample rows alone (``compute`` needs
    # only ``results`` — no ``schema`` / ``node`` / ``opt_sp``). The read-side mask
    # recomputes exactly this subset from ``all_candidate_results`` at read time
    # (``materialize_row_derivable``), so it is present on every record regardless of
    # when the record was written — no backfill, no namespace-gap. The complement
    # (recall / cache / *_shortfall / pipeline_compactness / self-heal /
    # prompt_compactness) needs the unpersisted schema/opt_sp and is read from the
    # stored snapshot only.
    from_rows: bool = False


_REGISTRY: list[Evaluator] = [
    Evaluator(
        name="accuracy",
        description="Mean per-sample score across non-deprecated samples.",
        scope="per_round",
        compute=compute_accuracy,
        from_rows=True,
    ),
    Evaluator(
        name="error_rate",
        description="Fraction of queries that errored (ERROR predicted or exception).",
        scope="per_round",
        compute=compute_error_rate,
        direction="low",
        from_rows=True,
    ),
    Evaluator(
        name="degraded_rate",
        description="Fraction of queries that completed with pipeline degradation warnings.",
        scope="per_round",
        compute=compute_degraded_rate,
        direction="low",
        from_rows=True,
    ),
    # Self-healers — one Evaluator per SELF_HEALERS spec; combined weight ~0.30 in default formula.
    *(_make_self_healer_evaluator(spec) for spec in SELF_HEALERS),
    Evaluator(
        name="latency_norm",
        description=(
            "Mean latency normalized against LATENCY_BUDGET_MS (1.0 = instant, 0.0 = ≥ budget)."
        ),
        scope="per_round",
        compute=compute_latency_norm,
        from_rows=True,
    ),
    Evaluator(
        name="source_recall",
        description="Fraction of queries where GT appears in a candidate_source node's output.",
        scope="per_round",
        compute=partial(_compute_recall, candidate_key="candidate_ranking"),
        node_type="candidate_source",
        applies=lambda s: any(n.node_type == "candidate_source" for n in s.nodes),
    ),
    Evaluator(
        name="candidate_recall",
        description="Fraction of queries where GT appears in a ranker node's final_ranking.",
        scope="per_round",
        compute=partial(_compute_recall, candidate_key="final_ranking"),
        node_type="ranker",
        applies=lambda s: any(n.node_type == "ranker" for n in s.nodes),
    ),
    Evaluator(
        name="cache_hit_rate",
        description="Fraction of queries resolved by a cache node (non-null timing).",
        scope="per_round",
        compute=compute_cache_hit_rate,
        node_type="cache",
        applies=lambda s: any(n.node_type == "cache" for n in s.nodes),
    ),
    Evaluator(
        name="retrieval_shortfall",
        description=(
            "Per-sample min(observed/target, 1.0) across nodes with max_*/num_* limits "
            "on list-valued outputs. 1.0 = target met or exceeded."
        ),
        scope="per_sample",
        compute=compute_retrieval_shortfall_per_sample,
        applies=has_limit_node,
    ),
    Evaluator(
        name="mean_retrieval_shortfall",
        description="Mean of retrieval_shortfall across the round's results.",
        scope="per_round",
        compute=compute_mean_retrieval_shortfall,
        applies=has_limit_node,
    ),
    Evaluator(
        name="pipeline_compactness",
        description=(
            "1 - (active_steps - 1) / 11 — shorter pipelines score higher (single-node = 1.0)."
        ),
        scope="per_round",
        compute=compute_pipeline_compactness,
        direction="low",
    ),
    Evaluator(
        name="output_compactness",
        description=(
            "1 - mean(output_tokens) / OUTPUT_TOKEN_BUDGET — terser (cheaper) generations "
            "score higher. The accuracy-vs-cost axis; available to formulas, not in the "
            "default composite."
        ),
        scope="per_round",
        compute=compute_output_compactness,
        from_rows=True,
    ),
    Evaluator(
        name="prompt_compactness",
        description=(
            "1 - len(rendered_prompt) / PROMPT_BUDGET_CHARS — shorter prompts score "
            "higher (≤ budget → 1.0, ≥ budget → 0.0). Penalizes overly verbose "
            "prompt templates in the composite_fitness score."
        ),
        scope="per_round",
        compute=compute_prompt_compactness,
    ),
]


def all_evaluators() -> list[Evaluator]:
    """Return the full registry (copy)."""
    return list(_REGISTRY)


def evaluators_meta() -> list[dict[str, Any]]:
    """JSON-serializable registry projection for the webapp What-If panel — drops `compute`/`applies`."""
    return [
        {
            "name": ev.name,
            "description": ev.description,
            "scope": ev.scope,
            "direction": ev.direction,
            "node_type": ev.node_type,
        }
        for ev in _REGISTRY
    ]


def _concrete_round_entries(
    schema: PipelineSchema,
) -> list[tuple[str, Evaluator, PipelineNode | None]]:
    """Applicable per-round evaluators; node-type-bound names get namespaced when >1 matching node."""
    out: list[tuple[str, Evaluator, PipelineNode | None]] = []
    for ev in _REGISTRY:
        if ev.scope != "per_round":
            continue
        if not ev.applies(schema):
            continue
        if ev.node_type is None:
            out.append((ev.name, ev, None))
            continue
        matching = [n for n in schema.nodes if n.node_type == ev.node_type]
        namespace = len(matching) > 1
        for node in matching:
            display_name = f"{node.name}_{ev.name}" if namespace else ev.name
            out.append((display_name, ev, node))
    return out


def materialize_round_values(
    schema: PipelineSchema,
    results: list[QueryMeasurement],
    *,
    opt_sp: OptSearchPoint | None = None,
) -> dict[str, float]:
    """Run every per-round evaluator that applies; return ``{name: value}``, omitting the
    evaluators that had nothing to measure (``None``)."""
    values: dict[str, float] = {}
    for display_name, ev, node in _concrete_round_entries(schema):
        kwargs: dict[str, Any] = {"results": results, "schema": schema, "opt_sp": opt_sp}
        if node is not None:
            kwargs["node"] = node
        value = ev.compute(**kwargs)
        if value is not None:
            values[display_name] = float(value)
    return values


def materialize_row_derivable(results: list[QueryMeasurement]) -> dict[str, float]:
    """Materialize the per-round evaluators that are pure functions of the persisted
    per-sample rows (``Evaluator.from_rows``) — ``accuracy``, ``output_compactness``,
    ``latency_norm``, ``error_rate``, ``degraded_rate``. No schema, no ``opt_sp``: the
    read-side mask recomputes exactly these from ``all_candidate_results`` so they are
    present on every record (and re-scored over a sample subset when masked), while the
    schema/opt_sp-bound evaluators come from the stored snapshot. An evaluator with nothing
    to measure on this row subset is omitted, not defaulted."""
    out: dict[str, float] = {}
    for ev in _REGISTRY:
        if ev.scope != "per_round" or not ev.from_rows:
            continue
        value = ev.compute(results=results)
        if value is not None:
            out[ev.name] = float(value)
    return out


def materialize_sample_values(
    schema: PipelineSchema,
    result: QueryMeasurement,
) -> dict[str, float]:
    """Run every per-sample evaluator that applies on a single result; omit the unmeasured."""
    values: dict[str, float] = {}
    for ev in _REGISTRY:
        if ev.scope != "per_sample":
            continue
        if not ev.applies(schema):
            continue
        value = ev.compute(result=result, schema=schema)
        if value is not None:
            values[ev.name] = float(value)
    return values


def default_per_round_formula(schema: PipelineSchema) -> str:
    """``accuracy`` — composite_fitness defaults to plain accuracy so the decision metric
    and the headline number agree (no latency/recall/self-heal blend inflating it above
    the real score). Degradation is gated separately by the round ``health`` block, not
    folded into fitness. Re-weight in other terms via campaign.json::scoring."""
    return "accuracy"


def default_per_round_formula_short(schema: PipelineSchema) -> str:
    """Short form of the default formula — derived from the full formula via the
    shared short-code table (no hand-synced literal); fits the 70-char frame."""
    return to_short_formula(default_per_round_formula(schema))


def resolve_round_formula(
    explicit: str | None,
    schema: PipelineSchema | None,
) -> tuple[str | None, str | None]:
    """``(full, short)`` composite-fitness formula for a cycle — campaign override first,
    else the schema default, else nothing to report.

    The ONE resolution. Three surfaces answer "what formula is this cycle scored under" —
    the live dashboard at INIT.enter, the same at INIT.exit, and the finished cycle's
    `index.json::final`, which `log.md` reads. The third had no writer at all, so every
    `log.md` ever rendered said `(formula unavailable)` about a cycle whose formula the
    other two were already displaying. A short form exists only for the default: an
    operator-authored formula is shown verbatim or not at all.
    """
    if explicit:
        return explicit, None
    if schema is None:
        return None, None
    return default_per_round_formula(schema), default_per_round_formula_short(schema)
