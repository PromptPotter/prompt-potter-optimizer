"""Evaluator registry + materializers — the round-level REPORTING surface, and what the read-side
mask re-scores over. A compute fn returns a float in [0, 1], or ``None`` when the round/sample
carried nothing to measure: a zero is a verdict, an absence is not.

**Nothing here decides a round.** The election reads the per-cell ``objective``
(``domain/scoring.py::CellScorer``), so an evaluator says what a round LOOKED like, never what it
was worth. Two shapes are therefore inadmissible: a per-cell quantity the channel map already names
(``latency`` / ``cost`` / ``tokens``, meaned in by ``metrics.py``), and a CANDIDATE CONSTANT, which
cannot be a term at cell scope at all — under a logistic link a constant on y moves θ by an amount
that depends on δ."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.application.scoring.formula.compiler import CELL_INTRINSIC_NAMES
from promptpotter.domain.pipeline_schema import NodeType
from promptpotter.domain.scoring import (
    all_verifier_graded,
    extract_item_label,
    is_verifier_graded,
)
from promptpotter.shared.composite import to_short_formula
from promptpotter.shared.errors import has_pipeline_warnings, is_error_result

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineNode, PipelineSchema
    from promptpotter.domain.scoring import QueryMeasurement


Scope = Literal["per_sample", "per_round"]


__all__ = [
    "DEFAULT_CELL_FORMULA",
    "Evaluator",
    "all_evaluators",
    "evaluators_meta",
    "materialize_round_values",
    "materialize_row_derivable",
    "materialize_sample_values",
    "resolve_cell_formula",
    "validate_campaign_evaluator",
]


def compute_accuracy(*, results: list[QueryMeasurement], **_: Any) -> float | None:
    """Mean fitness over SCOREABLE rows. A DEPRECATED row carries no verdict and an ERRORED one
    never happened; the latter surfaces via ``compute_error_rate``."""
    # Lazy: scoring → optimization circular.
    from promptpotter.application.optimization.pobb.classification import scoreable_rows

    if not results:
        return None
    scoreable = scoreable_rows(results)
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


def _compute_recall(
    *,
    results: list[QueryMeasurement],
    node: PipelineNode,
    candidate_key: str,
    **_: Any,
) -> float | None:
    def _step_ran(r: QueryMeasurement) -> bool:
        pd = r.get("pipeline_data") or {}
        if pd.get("terminal_node") == node.name:
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


@dataclass(frozen=True)
class Evaluator:
    name: str
    description: str
    scope: Scope
    # ``None`` = this round/sample carried nothing to measure. The materializers below OMIT
    # the key rather than substituting a default, so a formula naming an unmeasured term halts
    # loud (``round_scorer``) instead of scoring on a number nobody computed. An
    # empty-collection default reads as PERFECT here — inverted for every health term.
    compute: Callable[..., float | None | Awaitable[float | None]]
    # The awaitable arm is `per_sample` ONLY, refused elsewhere by `_validate_evaluator` —
    # `judges/CLAUDE.md` § The seam says why a round materializer may never await.
    # `high` = larger is better; `low` = larger is worse (the webapp's mask editor direction-corrects).
    direction: Literal["high", "low"] = "high"
    node_type: NodeType | None = None
    # An extra structural requirement no node type can express (``has_limit_node``). The declared
    # ``node_type`` is NOT restated here — ``applies`` asks it.
    requires: Callable[[PipelineSchema], bool] = field(default=lambda _schema: True)

    def applies(self, schema: PipelineSchema) -> bool:
        """Has this evaluator anything to measure on ``schema``. The declared ``node_type`` IS half
        the test, asked here rather than re-spelled per entry as a lambda — a typo in such a copy
        is an evaluator that silently never renders."""
        if self.node_type is not None and not any(
            n.node_type == self.node_type for n in schema.nodes
        ):
            return False
        return self.requires(schema)

    # True ⇒ this number is a comparison AGAINST A LABEL, so it is undefined on a verifier-graded
    # backend rather than 0.0. Declared rather than derived because ``applies`` sees the schema
    # alone and the fact lives in the ROWS (`connectors/CLAUDE.md` § The answer shape).
    needs_labels: bool = False
    # True ⇒ a pure function of the persisted per-sample rows alone (``compute`` needs
    # only ``results`` — no ``schema`` / ``node``). The read-side mask recomputes exactly
    # this subset from ``all_candidate_results`` at read time (``materialize_row_derivable``),
    # so it is present on every record regardless of when the record was written — no
    # backfill, no namespace-gap. The complement (recall / cache / *_shortfall) needs the
    # unpersisted schema and is read from the stored snapshot only. The per-cell channel
    # means ``metrics.py`` folds in beside these are row-derivable by construction.
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
    Evaluator(
        name="source_recall",
        description="Fraction of queries where GT appears in a candidate_source node's output.",
        scope="per_round",
        compute=partial(_compute_recall, candidate_key="candidate_ranking"),
        node_type=NodeType.CANDIDATE_SOURCE,
        needs_labels=True,
    ),
    Evaluator(
        name="candidate_recall",
        description="Fraction of queries where GT appears in a ranker node's final_ranking.",
        scope="per_round",
        compute=partial(_compute_recall, candidate_key="final_ranking"),
        node_type=NodeType.RANKER,
        needs_labels=True,
    ),
    Evaluator(
        name="cache_hit_rate",
        description="Fraction of queries resolved by a cache node (non-null timing).",
        scope="per_round",
        compute=compute_cache_hit_rate,
        node_type=NodeType.CACHE,
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


def _validate_evaluator(ev: Evaluator, origin: str) -> None:
    """Every invariant an ``Evaluator`` must satisfy, built-in and campaign-declared alike.

    The load-bearing clause is the ``per_round`` × awaitable refusal — the sync read paths
    (``metrics.py``, ``mask/load.py``, ``l1/population.py``) re-derive over archived rows, so an
    awaiting compute there re-bills the whole measurement history on every index warm."""
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
    if ev.scope == "per_sample":
        if ev.name in CELL_INTRINSIC_NAMES:
            raise ValueError(
                f"{where}: the name collides with a term `cell_namespace` binds itself, so the "
                f"value would be silently dropped by the pipeline_data splat and no formula could "
                f"reach it. Pick another name."
            )
        if ev.from_rows:
            raise ValueError(
                f"{where}: `from_rows` is a per_round declaration — `materialize_row_derivable` "
                f"skips every per_sample entry — so setting it here is dead config that reads as "
                f"protection."
            )


def validate_campaign_evaluator(ev: Evaluator, origin: str) -> None:
    """Every invariant an evaluator a CAMPAIGN declares must satisfy — a judge's, today.

    The extra clause over :func:`_validate_evaluator` is the roster collision, and it belongs here
    because it is a property of the PAIR: ``materialize_sample_values`` iterates
    ``(*_REGISTRY, *extra)`` writing ``values[ev.name]``, so a campaign term repeating a package
    evaluator's name overwrites it — silently, with a number measuring something else."""
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


def evaluators_meta() -> list[dict[str, Any]]:
    """JSON-serializable registry projection for the webapp's scoring-mask editor — drops ``compute``/``requires``."""
    return [
        {
            "name": ev.name,
            "description": ev.description,
            "scope": ev.scope,
            "direction": ev.direction,
            "node_type": ev.node_type,
            # Carried because the browser has to know which evaluators survive a SAMPLE-SET mask
            # intact: only these recompute from the filtered rows, so a mask mixing them with
            # snapshot-only names reports a subset number added to a whole-set one.
            "from_rows": ev.from_rows,
        }
        for ev in _REGISTRY
    ]


def _round_value(ev: Evaluator, value: float | None | Awaitable[float | None]) -> float | None:
    """Narrow a ``per_round`` compute's result to the sync arm. Unreachable in a loaded registry,
    and a raise rather than a cast so an evaluator that somehow got there stops instead of
    re-billing the archive."""
    if isinstance(value, Awaitable):
        raise TypeError(
            f"evaluator {ev.name!r}: per_round compute returned an awaitable. Only per_sample "
            f"evaluators may reach a model; a round materializer re-derives over archived rows."
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
) -> dict[str, float]:
    """No ``opt_sp``: every evaluator that read one was a candidate constant, and those are gone.
    What a round REPORTS is now a pure function of its rows and the schema they ran on."""
    values: dict[str, float] = {}
    labelless = all_verifier_graded(r.get("ground_truth") for r in results)
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


def materialize_row_derivable(results: list[QueryMeasurement]) -> dict[str, float]:
    """The per-round evaluators that are pure functions of the persisted rows (``Evaluator.from_rows``).
    The read-side mask recomputes exactly these, so they survive a re-score over a sample subset."""
    out: dict[str, float] = {}
    for ev in _REGISTRY:
        if ev.scope != "per_round" or not ev.from_rows:
            continue
        value = _round_value(ev, ev.compute(results=results))
        if value is not None:
            out[ev.name] = float(value)
    return out


async def materialize_sample_values(
    schema: PipelineSchema,
    result: QueryMeasurement,
    extra: Sequence[Evaluator] = (),
) -> dict[str, float]:
    """The per-sample evaluators' values, keyed by name, for the ONE caller that measures a cell
    (``sample_measurement.py::measure_sample``).

    **Async, and only at this scope.** A ``per_sample`` evaluator may reach an LLM — that is what
    an LLM-as-judge IS — so its ``compute`` may return an awaitable, which is awaited here. The
    ``per_round`` materializers below stay strictly synchronous because their callers are sync
    READ paths (``metrics.py``, ``mask/load.py``, ``l1/population.py``) that re-derive over
    already-archived rows; an awaitable reaching one of those would re-bill the whole measurement
    history on every index refresh. :func:`_validate_evaluator` refuses the combination outright,
    so the asymmetry is a declared invariant rather than a convention.

    ``extra`` carries the evaluators a CAMPAIGN declares rather than the package — today, its
    judges, one per term. They are not appended to ``_REGISTRY``: that dict is process-global and a
    campaign's graders are not, so registering them would leak into every other run in the process,
    inner L4 cells included. It is also why the roster collision is checked once at init
    (:func:`validate_campaign_evaluator`) rather than here — ``extra`` is written last and would
    otherwise overwrite a package name silently, per cell.

    The caller writes these TOP-LEVEL into ``pipeline_data``, which is what makes them addressable
    from a scoring formula — see :func:`materialize_row_derivable` for the complement."""
    values: dict[str, float] = {}
    for ev in (*_REGISTRY, *extra):
        if ev.scope != "per_sample":
            continue
        if ev.needs_labels and is_verifier_graded(result.get("ground_truth")):
            continue
        if not ev.applies(schema):
            continue
        value = ev.compute(result=result, schema=schema)
        if inspect.isawaitable(value):
            value = await value
        if value is not None:
            values[ev.name] = float(value)
    return values


# The composite a campaign declaring none is scored on: the cell's own score, so the decision
# metric and the headline agree and adopting the machinery costs nothing. Degradation is gated by
# the round ``health`` block, never folded into fitness.
DEFAULT_CELL_FORMULA = "fitness"


def resolve_cell_formula(
    explicit: str | None,
    schema: PipelineSchema | None,
) -> tuple[str | None, str | None]:
    """``(full, short)`` for a cycle — campaign override, else the default, else nothing. THE one
    resolution for all three surfaces; a short form exists only for the default, never for an override."""
    if explicit:
        return explicit, None
    if schema is None:
        return None, None
    # Short form derived through the shared code table, never a synced literal.
    return DEFAULT_CELL_FORMULA, to_short_formula(DEFAULT_CELL_FORMULA)
