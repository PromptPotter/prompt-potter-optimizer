"""A cell's per-sample evaluators and the names its namespace binds; the round's fold is `domain/fitness_fold.py`."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application.scoring.classification import scoreable_rows
from promptpotter.application.scoring.formula.compiler import (
    EVAL_BUILTIN_NAMES,
    PIPELINE_BOUND_NAMES,
)
from promptpotter.application.scoring.formula.matchers import MATCHERS
from promptpotter.domain.cell_terms import CELL_BOUND_NAMES, CELL_TERMS, CellTerm
from promptpotter.domain.fitness_fold import (
    REPORTABLE_CORE_TERMS,
    ROUND_EVALUATORS,
    ROW_HEALTH_TERMS,
    Evaluator,
    has_limit_node,
    limit_nodes,
)
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.scoring import (
    ROW_GRADES,
    TURN_SCALAR_KEYS,
    TURN_STEP_SUFFIX,
    GradedCell,
    JudgeReading,
    MeasuredCell,
)
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.worded import Worded

if TYPE_CHECKING:
    from promptpotter.connectors.protocol import Connector


def compute_fitness(*, results: Sequence[GradedCell], **_: Any) -> float | None:
    """``None``, never 0.0, with no scoreable row: a zero invents the worst measurement out of none."""
    scoreable = scoreable_rows(results)
    if not scoreable:
        return None
    fitness = ROW_GRADES["fitness"]
    return sum(fitness.read(cell) for cell in scoreable) / len(scoreable)


async def compute_retrieval_shortfall_per_sample(
    *, result: MeasuredCell, schema: PipelineSchema, **_: Any
) -> float | None:
    observed = result.pipeline.observations
    ratios: list[float] = []
    for node, _key, target in limit_nodes(schema):
        for mapping in node.observation_mappings:
            val = observed.get(mapping.pipeline_key)
            if isinstance(val, list):
                ratios.append(min(len(val) / target, 1.0))
                break
    if not ratios:
        return None
    return sum(ratios) / len(ratios)


class JudgedTerm(NamedTuple):
    score: float | None
    reading: JudgeReading


class SampleTerms(NamedTuple):
    values: dict[str, float]
    judged: dict[str, JudgeReading]


Computed = float | JudgedTerm | None


@dataclass(frozen=True, kw_only=True)
class SampleEvaluator(Evaluator):
    # ``None`` = nothing to measure, and the key is OMITTED: a zero is a verdict, an absence is not.
    compute: Callable[..., Awaitable[Computed]]


_SAMPLE_EVALUATORS: tuple[SampleEvaluator, ...] = (
    SampleEvaluator(
        name="retrieval_shortfall",
        description=(
            "Per-sample min(observed/target, 1.0) across nodes with max_*/num_* limits "
            "on list-valued outputs. 1.0 = target met or exceeded."
        ),
        compute=compute_retrieval_shortfall_per_sample,
        requires=has_limit_node,
    ),
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
        # `{step}_step_turns` names a step the backend's turns carry, unknown before a cell lands.
        NameOwner("a turn scalar", TURN_SCALAR_KEYS, suffix=TURN_STEP_SUFFIX),
        NameOwner("a builtin every formula may call", EVAL_BUILTIN_NAMES),
        NameOwner(
            _PACKAGE_EVALUATOR,
            frozenset(ev.name for ev in _SAMPLE_EVALUATORS),
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
    for ev in (*ROUND_EVALUATORS, *_SAMPLE_EVALUATORS):
        if ev.name in seen:
            raise ValueError(f"evaluator {ev.name!r}: declared twice in the registry.")
        seen.add(ev.name)
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


def connector_report_owner(connector: Connector) -> NameOwner:
    return NameOwner(f"a key connector {connector.name!r} reports", connector.cell_report_keys)


def campaign_owners(connector: Connector, observation_keys: Iterable[str]) -> tuple[NameOwner, ...]:
    """The names a campaign's cells bind beyond every campaign's (`cell_name_owners`): its
    connector's report and its pipeline's observations."""
    return (
        connector_report_owner(connector),
        NameOwner("an observation key of this pipeline", frozenset(observation_keys)),
    )


def refuse_unowned_terms(terms: Iterable[str], beside: Sequence[NameOwner]) -> None:
    """A criterion's name no owner binds would compile, then refuse at every cell it grades."""
    owners = (*cell_name_owners(), *beside)
    if unowned := sorted(t for t in terms if not any(o.holds(t) for o in owners)):
        raise PayloadInvalidError(
            f"the criterion names {unowned}, which no cell of this campaign carries: a term is "
            "a core cell term, its connector's, an observation of its pipeline or one of its judges.",
            code="pipeline_config_invalid",
            details={"unowned_terms": unowned},
        )


def observation_key_refusals(keys: Iterable[str], report: NameOwner) -> list[str]:
    """An observation lands on ``PipelineData``, so only another owner's name is refused."""
    return [
        f"observation key {key!r} is {owner}"
        for key in sorted(keys)
        if (owner := bound_by(key, feeds=_PIPELINE_KEY, beside=(report,)))
    ]


def validate_campaign_evaluator(
    ev: SampleEvaluator, origin: str, beside: Sequence[NameOwner]
) -> None:
    """A campaign judge: every owner's name is refused, the package evaluators' and the campaign's included."""
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
            for ev in _SAMPLE_EVALUATORS
        },
    }
)


async def materialize_sample_values(
    schema: PipelineSchema,
    result: MeasuredCell,
    extra: Sequence[SampleEvaluator] = (),
) -> SampleTerms:
    """``extra`` is never appended to ``_SAMPLE_EVALUATORS``: those are process-global, a campaign's judges are not."""
    terms = SampleTerms({}, {})
    for ev in (*_SAMPLE_EVALUATORS, *extra):
        if ev.needs_labels and result.verifier_graded:
            continue
        if not ev.requires(schema):
            continue
        value = await ev.compute(result=result, schema=schema)
        if isinstance(value, JudgedTerm):
            if value.reading != JudgeReading():
                terms.judged[ev.name] = value.reading
            value = value.score
        if value is not None:
            terms.values[ev.name] = float(value)
    return terms
