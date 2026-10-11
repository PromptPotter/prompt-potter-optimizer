"""A REPORTING surface: nothing here decides a round, the election reads the per-cell ``objective``."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, Literal, NamedTuple

from promptpotter.application.scoring.classification import scoreable_rows
from promptpotter.application.scoring.formula.compiler import CELL_INTRINSIC_NAMES, CELL_TERMS
from promptpotter.domain.pipeline_schema import NodeRole
from promptpotter.domain.results_health import is_degraded
from promptpotter.domain.scoring import (
    ROW_GRADES,
    JudgeReading,
    all_verifier_graded,
    extract_item_label,
)
from promptpotter.shared.composite import to_short_formula
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineNode, PipelineSchema
    from promptpotter.domain.scoring import GradedCell, MeasuredCell


Scope = Literal["per_sample", "per_round"]


__all__ = [
    "DEFAULT_CELL_FORMULA",
    "Evaluator",
    "JudgedTerm",
    "SampleTerms",
    "all_evaluators",
    "cell_terms_meta",
    "materialize_round_values",
    "materialize_sample_values",
    "resolve_cell_formula",
    "validate_campaign_evaluator",
]


@shapes_optimizer_prompt
def compute_accuracy(*, results: Sequence[GradedCell], **_: Any) -> float | None:
    """``None``, never 0.0, with no scoreable row: a zero invents the worst measurement out of none."""
    scoreable = scoreable_rows(results)
    if not scoreable:
        return None
    fitness = ROW_GRADES["fitness"]
    return sum(fitness.read(cell) for cell in scoreable) / len(scoreable)


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


def compute_retrieval_shortfall_per_sample(
    *, result: MeasuredCell, schema: PipelineSchema, **_: Any
) -> float | None:
    observed = result.pipeline.observations
    ratios: list[float] = []
    for node, _key, target in _limit_nodes(schema):
        for mapping in node.observation_mappings:
            val = observed.get(mapping.pipeline_key)
            if isinstance(val, list):
                ratios.append(min(len(val) / target, 1.0))
                break
    if not ratios:
        return None
    return sum(ratios) / len(ratios)


def compute_mean_retrieval_shortfall(*, results: Sequence[GradedCell], **_: Any) -> float | None:
    """What each cell BANKED under its own limits, never re-derived against this round's schema."""
    values = [
        float(banked)
        for r in results
        if isinstance(banked := r.facts.pipeline.observations.get("retrieval_shortfall"), float)
    ]
    return sum(values) / len(values) if values else None


class JudgedTerm(NamedTuple):
    score: float | None
    reading: JudgeReading


class SampleTerms(NamedTuple):
    values: dict[str, float]
    judged: dict[str, JudgeReading]


Computed = float | JudgedTerm | None


@dataclass(frozen=True)
class Evaluator:
    name: str
    description: str
    scope: Scope
    # ``None`` = nothing to measure, and the key is OMITTED: a zero is a verdict, an absence is not.
    # The awaitable arm is `per_sample` ONLY (`_validate_evaluator`).
    compute: Callable[..., Computed | Awaitable[Computed]]
    direction: Literal["high", "low"] = "high"
    node_role: NodeRole | None = None
    requires: Callable[[PipelineSchema], bool] = field(default=lambda _schema: True)

    def applies(self, schema: PipelineSchema) -> bool:
        if self.node_role is not None and not any(n.role == self.node_role for n in schema.nodes):
            return False
        return self.requires(schema)

    # True ⇒ a comparison AGAINST A LABEL: undefined, not 0.0, on a verifier-graded backend.
    needs_labels: bool = False


_REGISTRY: list[Evaluator] = [
    Evaluator(
        name="accuracy",
        description="Mean per-sample score across the samples that carry a verdict.",
        scope="per_round",
        compute=compute_accuracy,
    ),
    Evaluator(
        name="error_rate",
        description="Fraction of queries that errored (ERROR predicted or exception).",
        scope="per_round",
        compute=compute_error_rate,
        direction="low",
    ),
    Evaluator(
        name="degraded_rate",
        description="Fraction of queries where a pipeline node did not finish cleanly.",
        scope="per_round",
        compute=compute_degraded_rate,
        direction="low",
    ),
    Evaluator(
        name="source_recall",
        description="Fraction of queries where GT appears in a candidate_source node's output.",
        scope="per_round",
        compute=_compute_recall,
        node_role=NodeRole.CANDIDATE_SOURCE,
        needs_labels=True,
    ),
    Evaluator(
        name="candidate_recall",
        description="Fraction of queries where GT appears in a ranker node's output.",
        scope="per_round",
        compute=_compute_recall,
        node_role=NodeRole.RANKER,
        needs_labels=True,
    ),
    Evaluator(
        name="cache_hit_rate",
        description="Fraction of queries resolved by a cache node (non-null timing).",
        scope="per_round",
        compute=compute_cache_hit_rate,
        node_role=NodeRole.CACHE,
    ),
    Evaluator(
        name="retrieval_shortfall",
        description=(
            "Per-sample min(observed/target, 1.0) across nodes with max_*/num_* limits "
            "on list-valued outputs. 1.0 = target met or exceeded."
        ),
        scope="per_sample",
        compute=compute_retrieval_shortfall_per_sample,
        requires=has_limit_node,
    ),
    Evaluator(
        name="mean_retrieval_shortfall",
        description="Mean of retrieval_shortfall across the round's results.",
        scope="per_round",
        compute=compute_mean_retrieval_shortfall,
        requires=has_limit_node,
    ),
]


# Unenforced: no per-candidate constant (on the logistic link its θ shift varies with δ), no channel-map quantity.
def _validate_evaluator(ev: Evaluator, origin: str) -> None:
    where = f"evaluator {ev.name!r} ({origin})"
    if not ev.name:
        raise ValueError(f"{where}: name must be non-empty.")
    if ev.scope not in ("per_sample", "per_round"):
        raise ValueError(f"{where}: scope {ev.scope!r} is not 'per_sample' or 'per_round'.")
    if not callable(ev.compute):
        raise ValueError(f"{where}: compute is not callable.")
    if ev.scope == "per_round" and inspect.iscoroutinefunction(ev.compute):
        raise ValueError(
            f"{where}: a per_round evaluator may not be async. Its materializers are sync READ "
            f"paths that re-derive over archived rows, so an awaiting compute re-bills the whole "
            f"measurement history on every refresh. Measure once at per_sample scope instead."
        )
    if ev.scope == "per_sample" and ev.name in CELL_INTRINSIC_NAMES:
        raise ValueError(
            f"{where}: the name collides with a term `cell_namespace` binds itself, so the "
            f"value would be silently dropped by the pipeline_data splat and no formula could "
            f"reach it. Pick another name."
        )


def validate_campaign_evaluator(ev: Evaluator, origin: str) -> None:
    _validate_evaluator(ev, origin)
    if ev.name in {e.name for e in _REGISTRY}:
        raise ValueError(
            f"evaluator {ev.name!r} ({origin}): the name is a package evaluator's. A campaign term "
            f"is materialized after the registry and would overwrite it, so the formula would read "
            f"this value under a name that promises the other one. Pick another term."
        )


def _validate_registry() -> None:
    seen: set[str] = set()
    for ev in _REGISTRY:
        if ev.name in seen:
            raise ValueError(f"evaluator {ev.name!r}: declared twice in the registry.")
        seen.add(ev.name)
        _validate_evaluator(ev, "built-in")


_validate_registry()


def all_evaluators() -> list[Evaluator]:
    return list(_REGISTRY)


def cell_terms_meta() -> list[dict[str, Any]]:
    terms: list[dict[str, Any]] = [
        {
            "name": name,
            "direction": term.direction,
            "description": term.description,
            "dial": term.dial,
            "primary": term.primary,
        }
        for name, term in CELL_TERMS.items()
    ]
    banked: list[dict[str, Any]] = [
        {
            "name": ev.name,
            "direction": ev.direction,
            "description": ev.description,
            "dial": None,
            "primary": False,
        }
        for ev in _REGISTRY
        if ev.scope == "per_sample"
    ]
    return terms + banked


def _round_value(ev: Evaluator, value: Computed | Awaitable[Computed]) -> float | None:
    if isinstance(value, Awaitable | JudgedTerm):
        raise TypeError(
            f"evaluator {ev.name!r}: per_round compute returned {type(value).__name__}. Only "
            f"per_sample evaluators may reach a model; a round materializer re-derives over "
            f"archived rows."
        )
    return value


def _concrete_round_entries(
    schema: PipelineSchema,
) -> list[tuple[str, Evaluator, PipelineNode | None]]:
    out: list[tuple[str, Evaluator, PipelineNode | None]] = []
    for ev in _REGISTRY:
        if ev.scope != "per_round":
            continue
        if not ev.applies(schema):
            continue
        if ev.node_role is None:
            out.append((ev.name, ev, None))
            continue
        matching = [n for n in schema.nodes if n.role == ev.node_role]
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
        value = _round_value(ev, ev.compute(**kwargs))
        if value is not None:
            values[display_name] = float(value)
    return values


async def materialize_sample_values(
    schema: PipelineSchema,
    result: MeasuredCell,
    extra: Sequence[Evaluator] = (),
) -> SampleTerms:
    """``extra`` is never appended to ``_REGISTRY``: that list is process-global, a campaign's judges are not."""
    terms = SampleTerms({}, {})
    for ev in (*_REGISTRY, *extra):
        if ev.scope != "per_sample":
            continue
        if ev.needs_labels and result.verifier_graded:
            continue
        if not ev.applies(schema):
            continue
        value = ev.compute(result=result, schema=schema)
        if inspect.isawaitable(value):
            value = await value
        if isinstance(value, JudgedTerm):
            if value.reading != JudgeReading():
                terms.judged[ev.name] = value.reading
            value = value.score
        if value is not None:
            terms.values[ev.name] = float(value)
    return terms


DEFAULT_CELL_FORMULA: Annotated[str, shapes_optimizer_prompt] = "fitness"


@shapes_optimizer_prompt
def resolve_cell_formula(
    explicit: str | None,
    schema: PipelineSchema | None,
) -> tuple[str | None, str | None]:
    """``(full, short)``; a short form exists only for the default, never for an override."""
    if explicit:
        return explicit, None
    if schema is None:
        return None, None
    return DEFAULT_CELL_FORMULA, to_short_formula(DEFAULT_CELL_FORMULA)
