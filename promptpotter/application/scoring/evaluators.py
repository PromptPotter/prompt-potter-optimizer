"""A REPORTING surface: nothing here decides a round, the election reads the per-cell ``objective``."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, Literal, NamedTuple

from promptpotter.application.scoring.classification import scoreable_rows
from promptpotter.application.scoring.formula.compiler import (
    EVAL_BUILTIN_NAMES,
    PIPELINE_BOUND_NAMES,
    REPORTABLE_CORE_TERMS,
    ROW_HEALTH_TERMS,
)
from promptpotter.application.scoring.formula.matchers import MATCHERS
from promptpotter.domain.cell_terms import CELL_BOUND_NAMES, CELL_TERMS, CellTerm
from promptpotter.domain.pipeline_schema import NodeRole
from promptpotter.domain.results_health import is_degraded
from promptpotter.domain.scoring import (
    ROW_GRADES,
    TURN_SCALAR_KEYS,
    TURN_STEP_SUFFIX,
    JudgeReading,
    all_verifier_graded,
    extract_item_label,
)
from promptpotter.shared.composite import to_short_formula
from promptpotter.shared.hashing import shapes_optimizer_prompt
from promptpotter.shared.worded import Worded

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineNode, PipelineSchema
    from promptpotter.domain.scoring import GradedCell, MeasuredCell


Scope = Literal["per_sample", "per_round"]


@shapes_optimizer_prompt
def compute_fitness(*, results: Sequence[GradedCell], **_: Any) -> float | None:
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


@dataclass(frozen=True)
class NameOwner:
    """One owner of names the cell namespace binds: its names, or every name ending ``suffix``."""

    what: str
    names: frozenset[str]
    suffix: str | None = None

    def holds(self, name: str) -> bool:
        return name in self.names or (self.suffix is not None and name.endswith(self.suffix))


_PACKAGE_EVALUATOR = "a package evaluator's term"
_PIPELINE_KEY = "a PipelineData key"
_CORE_TERM = "a core cell term"


def cell_name_owners() -> tuple[NameOwner, ...]:
    """EVERY name the cell namespace and its channels bind, each under the ONE owner serving its value."""
    return (
        # A report answers these where the core reader cannot (an L4 cell's inner spend).
        NameOwner(_CORE_TERM, REPORTABLE_CORE_TERMS),
        NameOwner("a row-health term", ROW_HEALTH_TERMS),
        # `ground_truth_rank` and `error` among them: the cell's grade and message serve them.
        NameOwner("a name the namespace binds off the cell", CELL_BOUND_NAMES),
        NameOwner("a matcher", frozenset(MATCHERS)),
        # `target_prompt_chars` among them: its channel reads the field.
        NameOwner(_PIPELINE_KEY, PIPELINE_BOUND_NAMES),
        # `{step}_turns` names a step the backend's turns carry, unknown before a cell lands.
        NameOwner("a turn scalar", TURN_SCALAR_KEYS, suffix=TURN_STEP_SUFFIX),
        NameOwner("a builtin every formula may call", EVAL_BUILTIN_NAMES),
        NameOwner(
            _PACKAGE_EVALUATOR,
            frozenset(ev.name for ev in _REGISTRY if ev.scope == "per_sample"),
        ),
    )


def bound_by(
    name: str, *, feeds: str | None = None, beside: Sequence[NameOwner] = ()
) -> str | None:
    """Which owner other than ``feeds`` binds ``name``, or ``None``; ``beside`` adds a campaign's own owners."""
    holders = (o.what for o in (*cell_name_owners(), *beside) if o.holds(name))
    return next((what for what in holders if what != feeds), None)


def _validate_registry() -> None:
    """At import: each package evaluator, then ONE owner per name, the package's included."""
    seen: set[str] = set()
    for ev in _REGISTRY:
        if ev.name in seen:
            raise ValueError(f"evaluator {ev.name!r}: declared twice in the registry.")
        seen.add(ev.name)
        _validate_evaluator(ev, "built-in")
    owners = cell_name_owners()
    for i, owner in enumerate(owners):
        for other in owners[i + 1 :]:
            shared = {n for n in owner.names if other.holds(n)}
            shared |= {n for n in other.names if owner.holds(n)}
            if shared:
                raise ValueError(
                    f"{sorted(shared)}: each is {owner.what} and {other.what}; a name has one "
                    "owner, the one serving its value."
                )


_validate_registry()


def connector_name_refusals(terms: Iterable[CellTerm], reported: Iterable[str]) -> list[str]:
    """Each name a connector declares that another owner binds, as one sentence; a reported key may name a core cell term."""
    out = [f"cell term {t.name!r} is {owner}" for t in terms if (owner := bound_by(t.name))]
    out += [
        f"reported key {key!r} is {owner}"
        for key in reported
        if (owner := bound_by(key, feeds=_CORE_TERM))
    ]
    return out


def connector_report_owner(connector_name: str, report_keys: Iterable[str]) -> NameOwner:
    return NameOwner(f"a key connector {connector_name!r} reports", frozenset(report_keys))


def observation_owner(observation_keys: Iterable[str]) -> NameOwner:
    return NameOwner("an observation key of this pipeline", frozenset(observation_keys))


def observation_key_refusals(keys: Iterable[str], report: NameOwner) -> list[str]:
    """An observation lands on ``PipelineData``, so only another owner's name is refused."""
    return [
        f"observation key {key!r} is {owner}"
        for key in sorted(keys)
        if (owner := bound_by(key, feeds=_PIPELINE_KEY, beside=(report,)))
    ]


def validate_campaign_evaluator(ev: Evaluator, origin: str, beside: Sequence[NameOwner]) -> None:
    """A campaign judge: every owner's name is refused, the package evaluators' and the campaign's included."""
    _validate_evaluator(ev, origin)
    if owner := bound_by(ev.name, beside=beside):
        raise ValueError(
            f"evaluator {ev.name!r} ({origin}): the name is {owner}, and a cell's formula reads "
            f"one value per name. Pick another term."
        )


# What every campaign's per_cell formula can name: the core cell's terms, then each per_sample
# evaluator's; a connector's own ride its campaign's pipeline answer (`cell_terms`).
CELL_TERM_META: Worded[str, CellTerm] = Worded(
    {
        **CELL_TERMS,
        **{
            ev.name: CellTerm(name=ev.name, direction=ev.direction, description=ev.description)
            for ev in _REGISTRY
            if ev.scope == "per_sample"
        },
    }
)


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
