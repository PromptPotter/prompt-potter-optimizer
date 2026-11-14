from __future__ import annotations

import functools
import types
import typing
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from pydantic import BaseModel, Field
from pydantic.fields import FieldInfo
from pydantic_core import to_jsonable_python

from promptpotter.application import optimizers
from promptpotter.application.optimizers.nodes import CountKnob
from promptpotter.domain.campaign import RunDeclaration
from promptpotter.domain.campaign_config import CampaignConfig, Estimand, Knob, knob_label
from promptpotter.domain.pipeline_schema import KnobLayer, KnobLayers
from promptpotter.domain.pool import DemoPool
from promptpotter.domain.said import Tone
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.dataset_access import campaign_defaults
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.worded import Worded

if TYPE_CHECKING:
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import MemberCoupling


@dataclass(frozen=True)
class KnobDecl:
    path: tuple[str, ...]
    knob: Knob

    @property
    def dotted(self) -> str:
        return ".".join(self.path)


def _unwrap_optional(annotation: object) -> object:
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        non_none = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def _walk(model_cls: type[BaseModel], prefix: tuple[str, ...] = ()) -> list[KnobDecl]:
    out: list[KnobDecl] = []
    for name, field in model_cls.model_fields.items():
        path = (*prefix, name)
        knob = next((m for m in field.metadata if isinstance(m, Knob)), None)
        if knob is not None:
            out.append(KnobDecl(path=path, knob=knob))
            continue
        inner = _unwrap_optional(field.annotation)
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            out.extend(_walk(inner, path))
            continue
        raise RuntimeError(
            f"config leaf {'.'.join(path)!r} carries no Knob(...) — an undeclared "
            "knob is invisible to the config map and keys every campaign's measured "
            "address. Declare its estimand(s) on the field itself: "
            "`Annotated[<type>, Knob(Estimand.STOPPING)]`."
        )
    return out


KNOBS: dict[tuple[str, ...], KnobDecl] = {d.path: d for d in _walk(CampaignConfig)}

_NODES_PATH = ("optimization", "nodes")


def _node_prefix(node: str) -> tuple[str, ...]:
    return (*_NODES_PATH, node, "config")


def _node_decls(selected: SelectedOptimizer) -> dict[tuple[str, ...], KnobDecl]:
    out: dict[tuple[str, ...], KnobDecl] = {}
    for node in selected.member_nodes:
        for decl in _walk(optimizers.NODE_MEMBERS.get(node).knobs, _node_prefix(node)):
            out[decl.path] = decl
    return out


def member_knob_count() -> int:
    """Every registered member's knob leaves — what the ledger's config-leaf row adds to ``KNOBS``."""
    return sum(
        len(_walk(m.knobs, _node_prefix(n))) for n, m in optimizers.NODE_MEMBERS.table.items()
    )


def _models_in(annotation: object) -> Iterator[type[BaseModel]]:
    """Every model a field can hold, through ``Optional``, unions, ``dict`` values and ``list`` items."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
        return
    for arg in typing.get_args(annotation):
        yield from _models_in(arg)


def _default_of(field: FieldInfo) -> Any:
    if field.is_required():
        return None
    if field.default_factory is not None:
        return field.default_factory()  # type: ignore[call-arg]
    return field.default


def _leaf_defaults(
    model_cls: type[BaseModel], prefix: str, within: frozenset[type[BaseModel]] = frozenset()
) -> Iterator[tuple[str, Any]]:
    for name, field in model_cls.model_fields.items():
        path = f"{prefix}{name}"
        yield path, _default_of(field)
        for inner in _models_in(field.annotation):
            if inner not in within:
                yield from _leaf_defaults(inner, f"{path}<{inner.__name__}>.", within | {model_cls})


def bench_defaults_pin() -> str:
    """Walked LIVE, so any moved default moves it, `defaults.yaml`'s or a sub-model's; the
    optimizer's are its ``Treatment.manifest``."""
    bench = {
        p: d
        for p, d in _leaf_defaults(CampaignConfig, "")
        if not p.startswith("optimization.nodes")
    }
    return stable_hash(to_jsonable_python({**bench, "defaults.yaml": campaign_defaults()}))


_UNMEASURED = frozenset({Estimand.SPEND, Estimand.DISPLAY})


def _measured(loop: Mapping[str, Any], prefix: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in loop.items():
        path = (*prefix, key)
        decl = KNOBS.get(path)
        if decl is not None:
            if not set(decl.knob.estimands) <= _UNMEASURED:
                out[key] = value
        elif isinstance(value, Mapping):
            out[key] = _measured(value, path)
        else:
            out[key] = value
    return out


def measured_address(declaration: RunDeclaration) -> dict[str, Any]:
    """What a root id addresses: the declaration less its budget, defaults pin and spend- or display-only keys."""
    dumped = declaration.model_dump(mode="json", exclude={"budget", "bench_defaults_pin", "loop"})
    return {**dumped, "loop": _measured(declaration.loop, ())}


# Gravest first: an ill-defined statistic, a silent waste, knobs that co-move.
CouplingSeverity = Literal["collision", "inert", "info"]


class CouplingSeverityInfo(NamedTuple):
    # The badge an ACTIVE coupling wears; an inactive one wears none.
    tone: Tone


COUPLING_SEVERITY_INFO: Worded[CouplingSeverity, CouplingSeverityInfo] = Worded(
    {
        "collision": CouplingSeverityInfo("danger"),
        "inert": CouplingSeverityInfo("attention"),
        "info": CouplingSeverityInfo("quiet"),
    }
)


@dataclass(frozen=True)
class Coupling:
    """``predicate`` is True in the VIOLATING combination."""

    name: str
    knobs: tuple[str, ...]
    estimand: Estimand
    relation: str
    consequence: str
    severity: CouplingSeverity
    predicate: Callable[[CampaignConfig], bool]


COUPLINGS: tuple[Coupling, ...] = (
    Coupling(
        name="fatal_fastpath_needs_degradation",
        knobs=(
            "optimization.degradation_fatal_fastpath",
            "optimization.degradation_threshold",
        ),
        estimand=Estimand.STOPPING,
        relation="The fatal fast-path only runs while the degradation check is armed (degradation_threshold > 0).",
        consequence=(
            "degradation_fatal_fastpath is ON but degradation_threshold is 0, so the "
            "degradation check is disarmed and the fast-path never fires."
        ),
        severity="inert",
        predicate=lambda c: (
            c.optimization.degradation_fatal_fastpath and c.optimization.degradation_threshold == 0
        ),
    ),
    Coupling(
        name="lives_no_headroom",
        knobs=("optimization.lives.start", "optimization.budget"),
        estimand=Estimand.STOPPING,
        relation=(
            "lives stops a stalling run early, but only when the bank can empty before the "
            "calendar cap does."
        ),
        consequence=(
            "lives.start ≥ budget.rounds, so hearts can never run out first: the run stops on "
            "the calendar. Lower lives.start to brake a stalling run early, or raise "
            "budget.rounds to give it room."
        ),
        severity="inert",
        predicate=lambda c: (
            c.optimization.lives is not None
            and c.optimization.budget.rounds is not None
            and c.optimization.lives.start >= c.optimization.budget.rounds
        ),
    ),
)


def _member_couplings(selected: SelectedOptimizer) -> Iterator[Coupling]:
    for member in selected.members:
        node = member.name
        knobs, declared = selected.knobs(node), selected.declared_knobs(node)
        for mc in member.couplings:
            if ghosts := sorted(set(mc.knobs) - set(type(knobs).model_fields)):
                raise RuntimeError(f"{node}: coupling {mc.name!r} names unknown knobs {ghosts}.")
            yield Coupling(
                name=mc.name,
                knobs=(
                    *(".".join((*_node_prefix(node), k)) for k in mc.knobs),
                    *mc.bench_knobs,
                ),
                estimand=mc.estimand,
                relation=mc.relation,
                consequence=mc.consequence,
                severity=mc.severity,
                predicate=functools.partial(_member_predicate, mc, knobs, declared),
            )


def _member_predicate(
    coupling: MemberCoupling, knobs: object, declared: object, config: CampaignConfig
) -> bool:
    return coupling.predicate(config, knobs, declared)


def _draw_couplings(selected: SelectedOptimizer) -> Iterator[Coupling]:
    """Every member drawing shots by a count of its own: inert where no demo pool is held out."""
    for member in selected.members:
        draw = member.draws.get(DemoPool)
        if not isinstance(draw, CountKnob):
            continue
        yield Coupling(
            name=f"{member.name}.shots_without_demo_pool",
            knobs=(".".join((*_node_prefix(member.name), draw.knob)), "dataset_split"),
            estimand=Estimand.SEARCH,
            relation="An individual's shots are drawn from the demo pool `dataset_split.demo` "
            "holds out.",
            consequence=f"The campaign holds out no demo pool, so {member.name} writes no shot "
            f"and its {draw.knob} is inert.",
            severity="inert",
            predicate=functools.partial(_starves_shots, draw.count(selected, member)),
        )


def _starves_shots(shots: int, config: CampaignConfig) -> bool:
    return shots > 0 and (config.dataset_split is None or config.dataset_split.demo == 0)


def declared_couplings(selected: SelectedOptimizer) -> list[Coupling]:
    return [*COUPLINGS, *_member_couplings(selected), *_draw_couplings(selected)]


def check_couplings(config: CampaignConfig, selected: SelectedOptimizer) -> list[Coupling]:
    return [c for c in declared_couplings(selected) if c.predicate(config)]


# A set optimizer-node knob names the layer that set it last; a set bench knob is `campaign`.
KnobSource = Literal["default", KnobLayer, "manifest"]


class ConfigKnob(StrictModel):
    path: str = Field(
        description="Dotted CampaignConfig path (or const.<NAME> for a hardcoded floor)"
    )
    label: str = Field(description="Short display name (prefix-stripped)")
    value: Any = Field(description="Effective value in this campaign's frozen config")
    source: KnobSource = Field(
        description="Where the value came from: default (`defaults.yaml`) | manifest (an optimizer "
        "node's knob, as its manifest declares it) | the layer that set it: campaign (the "
        "operator's edit; any set bench knob), and on an optimizer node's knob dataset, "
        "free_tier or seed"
    )


class ConfigEstimandGroup(StrictModel):
    key: str = Field(description="Estimand key (selection, difficulty, ability, …)")
    label: str = Field(description="Human-readable estimand name")
    knobs: list[ConfigKnob] = Field(description="Knobs that move this estimand, in declared order")


class ConfigCoupling(StrictModel):
    name: str = Field(description="Coupling id")
    labels: list[str] = Field(description="Short display names for those knobs")
    relation: str = Field(description="The relationship rule, plain language")
    consequence: str = Field(description="What goes wrong when the combination is violated")
    severity: CouplingSeverity = Field(
        description="collision (soundness) | inert (wasted knob) | info (relationship)"
    )
    active: bool = Field(
        description="True when this campaign's config is in the violating combination"
    )


class ConfigMapResponse(StrictModel):
    """Built off the couplings the pre-run preflight warning reads, so no surface disagrees with
    the engine on which knobs collide."""

    groups: list[ConfigEstimandGroup] = Field(description="Estimand groups, in declared order")
    couplings: list[ConfigCoupling] = Field(
        description="Declared couplings in reading order: the ones this config violates first, "
        "then gravest severity first, declaration order within a severity"
    )


class ConfigNodeKnobs(StrictModel):
    node: str = Field(description="The manifest node that owns these knobs")
    kind: str = Field(description="The node's type: sampler | eliminator | selector | …")
    keys: list[str] = Field(
        description="Its knobs' keys, in declared order, each a row of `node_config_schema[node]`"
    )


def node_knobs(selected: SelectedOptimizer) -> list[ConfigNodeKnobs]:
    initializers = {initializer.name for initializer in selected.plan.initializers}
    return [
        ConfigNodeKnobs(node=node, kind=str(selected.node(node).kind), keys=list(keys))
        for node in selected.member_nodes
        if node not in initializers
        and (keys := optimizers.NODE_MEMBERS.get(node).knobs.model_fields)
    ]


_MISSING = object()


def _at(data: Any, path: tuple[str, ...]) -> Any:
    cur: Any = data
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def _knob_rows(
    config: CampaignConfig, selected: SelectedOptimizer, laid_by: KnobLayers
) -> list[tuple[ConfigKnob, frozenset[Estimand]]]:
    """``value`` is ``None`` where an opt-in submodel is off, not merely unset; *laid_by* names
    the layer behind each ``optimization.nodes`` key *config* sets (`CycleConfig.knob_layers`); a
    bench knob holding `defaults.yaml`'s value, or an unset opt-in's, is the default."""
    dumped = config.model_dump(mode="json")
    defaults = campaign_defaults()
    authored = config.model_dump(mode="json", exclude_defaults=True)
    nodes = {n: selected.knobs(n).model_dump(mode="json") for n in selected.member_nodes}
    dumped["optimization"]["nodes"] = {n: {"config": v} for n, v in nodes.items()}
    rows: list[tuple[ConfigKnob, frozenset[Estimand]]] = []
    bench = {p: d for p, d in KNOBS.items() if p != _NODES_PATH}
    for path, decl in {**bench, **_node_decls(selected)}.items():
        value = _at(dumped, path)
        set_by_operator = _at(authored, path) is not _MISSING
        source: KnobSource
        if path[: len(_NODES_PATH)] == _NODES_PATH:
            node, _, key = path[len(_NODES_PATH) : len(_NODES_PATH) + 3]
            source = laid_by[node][key] if set_by_operator else "manifest"
        else:
            stated = set_by_operator and value != _at(defaults, path)
            source = "campaign" if stated else "default"
        knob = ConfigKnob(
            path=decl.dotted,
            label=knob_label(decl.dotted),
            value=None if value is _MISSING else value,
            source=source,
        )
        rows.append((knob, frozenset(decl.knob.estimands)))
    return rows


def config_map(
    config: CampaignConfig, selected: SelectedOptimizer, laid_by: KnobLayers
) -> ConfigMapResponse:
    rows = _knob_rows(config, selected, laid_by)
    groups = [
        ConfigEstimandGroup(
            key=estimand.value,
            label=estimand.value.replace("_", " ").title(),
            knobs=[knob for knob, estimands in rows if estimand in estimands],
        )
        for estimand in Estimand
    ]
    active = {c.name for c in check_couplings(config, selected)}
    couplings = [
        ConfigCoupling(
            name=c.name,
            labels=[knob_label(k) for k in c.knobs],
            relation=c.relation,
            consequence=c.consequence,
            severity=c.severity,
            active=c.name in active,
        )
        for c in declared_couplings(selected)
    ]
    severities = typing.get_args(CouplingSeverity)
    return ConfigMapResponse(
        groups=[g for g in groups if g.knobs],
        couplings=sorted(couplings, key=lambda c: (not c.active, severities.index(c.severity))),
    )


# A coupling naming a knob that does not exist points the operator at one they cannot set.
_declared = {d.dotted for d in KNOBS.values()}
_ghosts = sorted({k for c in COUPLINGS for k in c.knobs} - _declared)
if _ghosts:
    raise RuntimeError(f"COUPLINGS name knobs that are not CampaignConfig leaves: {_ghosts}.")
del _declared, _ghosts
