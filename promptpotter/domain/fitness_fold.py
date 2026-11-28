"""A round's cells folded to its `ScoreSummary`, for every reader: nothing here decides a round."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import field_serializer

from promptpotter.domain.arms import ArmState
from promptpotter.domain.cell_fold import CellFold, ScoreSummary, field_accuracy, recall_at
from promptpotter.domain.cell_terms import CELL_TERMS
from promptpotter.domain.paired_reading import ArmKey, Estimate, MeasurandUnit
from promptpotter.domain.pipeline_schema import NodeRole, PipelineNode, PipelineSchema
from promptpotter.domain.results_health import is_degraded, is_deprecated
from promptpotter.domain.scoring import (
    ROW_GRADES,
    CellGrade,
    CellSheet,
    Grade,
    GradedCell,
    MeasuredCell,
    RowGrade,
    Scorer,
    all_verifier_graded,
    extract_item_label,
)
from promptpotter.domain.spend import StepUsage, TokenAccount, bill_or_rate_usd
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.statistics import bounded_mean_interval


def _number(value: float | None) -> float | None:
    return None if value is None else float(value)


def _step_cost(steps: Mapping[str, StepUsage]) -> float | None:
    """``None`` where any node carries neither figure: a partial sum reads as the whole cost."""
    costs = [bill_or_rate_usd(usage.cost_usd, usage.rate_priced_usd) for usage in steps.values()]
    if not costs or any(cost is None for cost in costs):
        return None
    return sum(cost for cost in costs if cost is not None)


def _step_tokens(steps: Mapping[str, StepUsage]) -> float | None:
    account = TokenAccount.from_step_tokens(steps)
    return None if account is None else float(account.total)


ChannelReader = Callable[[MeasuredCell, Grade | None], float | None]

# The core's own channels: where its reader cannot answer, a connector's report does.
_CHANNEL_READERS: dict[str, ChannelReader] = {
    "fitness": lambda _facts, grade: None if grade is None else _number(grade.fitness),
    # `cost_s`, never `total_time`: a replayed row's `total_time` is zeroed.
    "latency": lambda facts, _grade: facts.cost_s,
    "unworked": lambda facts, _grade: facts.pipeline.unworked_s,
    "cost": lambda facts, _grade: _step_cost(facts.pipeline.step_tokens),
    "tokens": lambda facts, _grade: _step_tokens(facts.pipeline.step_tokens),
}

# Channels whose value another owner serves, so no report answers them: the rank the cell's
# grade binds, the prompt length its `PipelineData` carries.
_BORROWED_CHANNELS: dict[str, ChannelReader] = {
    "ground_truth_rank": lambda _facts, grade: (
        None if grade is None else _number(grade.ground_truth_rank)
    ),
    # Characters, not tokens: no tokenizer ships.
    "target_prompt_chars": lambda facts, _grade: _number(facts.pipeline.target_prompt_chars),
}

# Deliberately NOT channels: a predicate answers for ANY cell, measured or not.
ROW_HEALTH: dict[str, Callable[[MeasuredCell], float]] = {
    "errored": lambda facts: float(facts.errored),
    "degraded": lambda facts: float(is_degraded(facts)),
    "cached": lambda facts: float(facts.cached),
}

CELL_CHANNELS: tuple[str, ...] = tuple(
    name for name in CELL_TERMS if name in _CHANNEL_READERS or name in _BORROWED_CHANNELS
)
assert set(CELL_TERMS) == {*CELL_CHANNELS, *ROW_HEALTH}, "a per-cell term went untaught"
# The core terms a report may answer; a health term is the row's own, never a report's.
REPORTABLE_CORE_TERMS: frozenset[str] = frozenset(_CHANNEL_READERS)
ROW_HEALTH_TERMS: frozenset[str] = frozenset(ROW_HEALTH)


def cell_channels_of(facts: MeasuredCell, grade: Grade | None) -> dict[str, float]:
    """A key absent from the result is a channel the cell cannot answer."""
    out: dict[str, float] = {}
    for name, read in (*_CHANNEL_READERS.items(), *_BORROWED_CHANNELS.items()):
        value = read(facts, grade)
        if value is not None:
            out[name] = value
    for name, value in facts.pipeline.cell_report.items():
        if isinstance(value, int | float) and not isinstance(value, bool):
            out.setdefault(name, float(value))
    return out


def compute_error_rate(*, results: Sequence[GradedCell], **_: Any) -> float | None:
    if not results:
        return None
    return sum(1 for r in results if r.facts.errored) / len(results)


def compute_degraded_rate(*, results: Sequence[GradedCell], **_: Any) -> float | None:
    if not results:
        return None
    return sum(1 for r in results if is_degraded(r.facts)) / len(results)


def _compute_recall(
    *,
    results: Sequence[GradedCell],
    node: PipelineNode,
    **_: Any,
) -> float | None:
    def _step_ran(facts: MeasuredCell) -> bool:
        pd = facts.pipeline
        return pd.terminal_node == node.name or node.name in pd.step_timings

    scoped = [r.facts for r in results if _step_ran(r.facts) and not r.facts.errored]
    if not scoped:
        return None
    found = 0
    for facts in scoped:
        candidates = node.ranking_in(facts.pipeline.observations) or []
        if any(extract_item_label(c) == facts.ground_truth for c in candidates):
            found += 1
    return found / len(scoped)


def compute_cache_hit_rate(
    *, results: Sequence[GradedCell], node: PipelineNode, **_: Any
) -> float | None:
    cache_hits = non_error = 0
    for r in results:
        if r.facts.errored:
            continue
        non_error += 1
        if node.name in r.facts.pipeline.step_timings:
            cache_hits += 1
    return cache_hits / non_error if non_error else None


_LIMIT_KEY_SUFFIXES = ("max_sites", "num_results", "max_token_candidates", "max_tokens")


def limit_nodes(schema: PipelineSchema) -> list[tuple[PipelineNode, str, int]]:
    out: list[tuple[PipelineNode, str, int]] = []
    for node in schema.running_nodes:
        cfg = node.current_config.laid_keys()
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
    return bool(limit_nodes(schema))


def compute_mean_retrieval_shortfall(*, results: Sequence[GradedCell], **_: Any) -> float | None:
    """What each cell BANKED under its own limits, never re-derived against this round's schema."""
    values = [
        float(banked)
        for r in results
        if isinstance(banked := r.facts.pipeline.observations.get("retrieval_shortfall"), float)
    ]
    return sum(values) / len(values) if values else None


# Unenforced: no per-candidate constant (on the logistic link its θ shift varies with δ), no channel-map quantity.
@dataclass(frozen=True, kw_only=True)
class Evaluator:
    name: str
    description: str
    direction: Literal["high", "low"] = "high"
    # True ⇒ a comparison AGAINST A LABEL: undefined, not 0.0, on a verifier-graded backend.
    needs_labels: bool = False
    requires: Callable[[PipelineSchema], bool] = field(default=lambda _schema: True)


@dataclass(frozen=True, kw_only=True)
class RoundEvaluator(Evaluator):
    # Sync: a round materializer re-derives over archived rows, so an awaiting one re-bills every refresh.
    compute: Callable[..., float | None]
    node_role: NodeRole | None = None


ROUND_EVALUATORS: tuple[RoundEvaluator, ...] = (
    RoundEvaluator(
        name="error_rate",
        description="Fraction of queries that errored (ERROR predicted or exception).",
        compute=compute_error_rate,
        direction="low",
    ),
    RoundEvaluator(
        name="degraded_rate",
        description="Fraction of queries where a pipeline node did not finish cleanly.",
        compute=compute_degraded_rate,
        direction="low",
    ),
    RoundEvaluator(
        name="source_recall",
        description="Fraction of queries where GT appears in a candidate_source node's output.",
        compute=_compute_recall,
        node_role=NodeRole.CANDIDATE_SOURCE,
        needs_labels=True,
    ),
    RoundEvaluator(
        name="candidate_recall",
        description="Fraction of queries where GT appears in a ranker node's output.",
        compute=_compute_recall,
        node_role=NodeRole.RANKER,
        needs_labels=True,
    ),
    RoundEvaluator(
        name="cache_hit_rate",
        description="Fraction of queries resolved by a cache node (non-null timing).",
        compute=compute_cache_hit_rate,
        node_role=NodeRole.CACHE,
    ),
    RoundEvaluator(
        name="mean_retrieval_shortfall",
        description="Mean of retrieval_shortfall across the round's results.",
        compute=compute_mean_retrieval_shortfall,
        requires=has_limit_node,
    ),
)


def _concrete_round_entries(
    schema: PipelineSchema,
) -> list[tuple[str, RoundEvaluator, PipelineNode | None]]:
    out: list[tuple[str, RoundEvaluator, PipelineNode | None]] = []
    for ev in ROUND_EVALUATORS:
        if not ev.requires(schema):
            continue
        if ev.node_role is None:
            out.append((ev.name, ev, None))
            continue
        matching = [n for n in schema.running_nodes if n.role == ev.node_role]
        namespace = len(matching) > 1
        for node in matching:
            display_name = f"{node.name}_{ev.name}" if namespace else ev.name
            out.append((display_name, ev, node))
    return out


def materialize_round_values(
    schema: PipelineSchema,
    results: Sequence[GradedCell],
) -> dict[str, float]:
    values: dict[str, float] = {}
    labelless = all_verifier_graded(r.facts.ground_truth for r in results)
    for display_name, ev, node in _concrete_round_entries(schema):
        if ev.needs_labels and labelless:
            continue
        kwargs: dict[str, Any] = {"results": results, "schema": schema}
        if node is not None:
            kwargs["node"] = node
        value = ev.compute(**kwargs)
        if value is not None:
            values[display_name] = float(value)
    return values


# Meaned whatever the formula reads; `errored`/`degraded` are reported as rates.
_MEANED_CHANNELS: tuple[str, ...] = ("latency", "cost", "tokens")


def pooled_band(
    sheets: Iterable[CellSheet], grade: CellGrade, unit: MeasurandUnit
) -> Estimate | None:
    """THE level: each sheet's standing scored cells, pooled; one sheet is one individual's."""
    held = tuple(sheets)
    if len({(s.scorer_id, s.two_valued) for s in held}) > 1:
        raise ValueError("a level pools only sheets graded under one scorer")
    values = [v for s in held for v in s.column(ROW_GRADES[grade]).values()]
    if not values:
        return None
    level = sum(values) / len(values)
    lo, hi = bounded_mean_interval(values, binary=grade in held[0].two_valued) or (None, None)
    return Estimate(
        value=level, ci_lo=lo, ci_hi=hi, n=len(values), se=None, unit=unit, form="level"
    )


def level_band(sheet: CellSheet, grade: CellGrade, unit: MeasurandUnit) -> Estimate | None:
    return pooled_band((sheet,), grade, unit)


def _channel_means(scoreable: Sequence[GradedCell], names: Iterable[str]) -> dict[str, float]:
    """Over the rows that ANSWERED each channel: the full count reports the round as cheaper than it was."""
    sums: dict[str, list[float]] = {}
    for cell in scoreable:
        answered = cell_channels_of(cell.facts, cell.grade)
        for name in names:
            if (value := answered.get(name)) is not None:
                sums.setdefault(name, []).append(value)
    return {name: sum(vs) / len(vs) for name, vs in sums.items()}


def fold_cells(sheet: CellSheet, fitness_unit: MeasurandUnit) -> CellFold:
    return _folded(sheet, level_band(sheet, "fitness", fitness_unit), fitness_unit)


def _folded(sheet: CellSheet, fitness: Estimate | None, fitness_unit: MeasurandUnit) -> CellFold:
    composite = level_band(sheet, "objective", "score")
    return CellFold(
        total=len(sheet.scoreable),
        fitness=None if fitness is None else fitness.value,
        fitness_unit=fitness_unit,
        composite_fitness=None if composite is None else composite.value,
        deprecated=sum(1 for cell in sheet if is_deprecated(cell.facts)),
        degraded=sum(1 for cell in sheet if is_degraded(cell.facts)),
        recall_at=recall_at(sheet),
        field_accuracy=field_accuracy(sheet.scoreable),
    )


def compute_composite_fitness(
    sheet: CellSheet, scorer: Scorer | FoldBasis, pipeline_schema: PipelineSchema
) -> ScoreSummary:
    """The MEAN of each cell's worth, folded and never re-scored: the same per-cell value θ is fit on."""
    band = level_band(sheet, "fitness", scorer.fitness_unit)
    fold = _folded(sheet, band, scorer.fitness_unit)
    evaluator_values = materialize_round_values(pipeline_schema, sheet.cells)
    # A reading of the round, never a formula input: f(mean) is not mean(f). `fitness` is the fold's.
    terms = scorer.terms - {"fitness"}
    evaluator_values.update(_channel_means(sheet.scoreable, {*_MEANED_CHANNELS, *terms}))
    if fold.fitness is not None:
        evaluator_values["fitness"] = fold.fitness
    return ScoreSummary(
        **fold.model_dump(),
        walked=len(sheet.cells),
        cached=sum(1 for cell in sheet if cell.facts.cached),
        evaluators=evaluator_values,
        fitness_terms=sorted(terms & evaluator_values.keys()),
        fitness_band=band,
        tokens=TokenAccount.from_measured_rows(cell.facts for cell in sheet),
    )


def arm_folds(
    arms: Mapping[ArmKey, ArmState],
    cells: Mapping[ArmKey, CellSheet],
    scorer: Scorer,
    schema: PipelineSchema,
) -> dict[ArmKey, ScoreSummary]:
    """THE fold of a round's arms, each over the cells the round files for it."""
    return {
        key: compute_composite_fitness(cells[key], scorer, schema)
        for key, arm in arms.items()
        if not arm.invalid
    }


class FoldBasis(FrozenModel):
    """What a walk's recorded rows fold under: its scorer's declaration and the chain it ran."""

    scorer_id: str
    two_valued: frozenset[RowGrade]
    fitness_unit: MeasurandUnit
    terms: frozenset[str]
    chain: PipelineSchema

    @field_serializer("chain")
    def _declared(self, chain: PipelineSchema) -> object:
        # Without what it serves (`NodeOutputSchema.contract`), which its read refuses.
        return chain.model_dump(mode="json", exclude_computed_fields=True)

    @classmethod
    def of(cls, scorer: Scorer, chain: PipelineSchema) -> FoldBasis:
        return cls(
            scorer_id=scorer.id,
            two_valued=scorer.two_valued,
            fitness_unit=scorer.fitness_unit,
            terms=scorer.terms,
            chain=chain,
        )

    def sheet(self, cells: Iterable[GradedCell]) -> CellSheet:
        return CellSheet(self.scorer_id, self.two_valued, tuple(cells))

    def fold(self, cells: Iterable[GradedCell]) -> ScoreSummary:
        return compute_composite_fitness(self.sheet(cells), self, self.chain)
