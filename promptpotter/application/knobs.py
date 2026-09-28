"""The knob layer. ``KNOBS`` is WALKED off each field's own ``Knob`` metadata, never re-listed, so it
cannot go stale; an optimizer node's knobs are walked the same way off its member's ``knobs`` model.
Couplings + the one-way ``knobs`` → ``config`` import: ``application/CLAUDE.md``."""

from __future__ import annotations

import functools
import logging
import types
import typing
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from promptpotter.application import optimizers
from promptpotter.application.campaign_config import CampaignConfig, Estimand, Knob, Scope
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import MemberCoupling

shapes_optimizer_prompt(__name__)

logger = logging.getLogger(__name__)

__all__ = [
    "COUPLINGS",
    "KNOBS",
    "Coupling",
    "DiffScope",
    "check_couplings",
    "classify_config_diff",
    "declared_couplings",
    "member_knob_count",
    "resolve_knob_states",
]


@dataclass(frozen=True)
class KnobDecl:
    path: tuple[str, ...]
    knob: Knob
    default: Any
    required: bool

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


def _default_of(field: FieldInfo) -> Any:
    if field.is_required():
        return None
    if field.default_factory is not None:
        return field.default_factory()  # type: ignore[call-arg]
    return field.default


def _walk(model_cls: type[BaseModel], prefix: tuple[str, ...] = ()) -> list[KnobDecl]:
    """Every knob under *model_cls*, in declaration order. A field with no ``Knob`` that is not a nested
    model is an UNDECLARED knob and fails here — else it ships invisible and DATA_AFFECTING forever."""
    out: list[KnobDecl] = []
    for name, field in model_cls.model_fields.items():
        path = (*prefix, name)
        knob = next((m for m in field.metadata if isinstance(m, Knob)), None)
        if knob is not None:
            out.append(
                KnobDecl(
                    path=path,
                    knob=knob,
                    default=_default_of(field),
                    required=field.is_required(),
                )
            )
            continue
        inner = _unwrap_optional(field.annotation)
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            out.extend(_walk(inner, path))
            continue
        raise RuntimeError(
            f"config leaf {'.'.join(path)!r} carries no Knob(...) — an undeclared "
            "knob is invisible to the config map and classifies DATA_AFFECTING on every "
            "resume. Declare its scope + estimand(s) on the field itself: "
            "`Annotated[<type>, Knob(Scope.POLICY, Estimand.STOPPING)]`."
        )
    return out


# The registry. Derived, so it cannot list a knob the model doesn't have, nor miss one
# it does — the two failure modes of the name-keyed tables this replaces.
KNOBS: dict[tuple[str, ...], KnobDecl] = {d.path: d for d in _walk(CampaignConfig)}

# The overlay's own entry stands for its leaves, which each node's member declares.
_NODES_PATH = ("optimization", "nodes")


def _node_prefix(node: str) -> tuple[str, ...]:
    return (*_NODES_PATH, node, "config")


def _node_decls(selected: SelectedOptimizer) -> dict[tuple[str, ...], KnobDecl]:
    out: dict[tuple[str, ...], KnobDecl] = {}
    for node in selected.member_nodes:
        declared = selected.declared_knobs(node).model_dump(mode="json")
        for decl in _walk(optimizers.member(node).knobs, _node_prefix(node)):
            value: Any = declared
            for part in decl.path[len(_node_prefix(node)) :]:
                value = value.get(part) if isinstance(value, dict) else None
            out[decl.path] = KnobDecl(path=decl.path, knob=decl.knob, default=value, required=False)
    return out


def member_knob_count() -> int:
    """Every registered member's knob leaves — what the ledger's config-leaf row adds to ``KNOBS``."""
    return sum(len(_walk(m.knobs, _node_prefix(n))) for n, m in optimizers.registered().items())


def _table(config: CampaignConfig) -> dict[tuple[str, ...], KnobDecl]:
    bench = {p: d for p, d in KNOBS.items() if p != _NODES_PATH}
    return {**bench, **_node_decls(select_optimizer(config.optimization))}


class DiffScope(StrEnum):
    """Resume-time diff classification, the union of the diffed leaves' scopes. ``POLICY_ONLY`` keeps
    past measurements valid; ``DATA_AFFECTING`` (or any unclassified path) sends resume to divergence."""

    NONE = "none"
    POLICY_ONLY = "policy_only"
    DATA_AFFECTING = "data_affecting"


def _diff_paths(
    table: dict[tuple[str, ...], KnobDecl],
    active: Any,
    frozen: Any,
    prefix: tuple[str, ...] = (),
) -> list[tuple[str, ...]]:
    if prefix in table:
        return [prefix] if active != frozen else []
    if isinstance(active, dict) or isinstance(frozen, dict):
        a = active if isinstance(active, dict) else {}
        f = frozen if isinstance(frozen, dict) else {}
        out: list[tuple[str, ...]] = []
        for key in set(a.keys()) | set(f.keys()):
            out.extend(_diff_paths(table, a.get(key), f.get(key), (*prefix, key)))
        return out
    return [prefix] if active != frozen else []


def classify_config_diff(
    config: CampaignConfig, frozen: dict[str, Any]
) -> tuple[DiffScope, list[str]]:
    """Classify *config* vs the frozen snapshot. Both sides are deltas from the code DEFAULTS, so the
    snapshot is never validated — the resume that must report drift is the one that must not die on it."""
    if not frozen:
        # A check-in skeleton (`mint_checkin_skeleton`) carries `config: {}` — the campaign has
        # no snapshot yet. That is "nothing to diff against", not "every leaf changed".
        return DiffScope.NONE, []
    active = config.model_dump(mode="json", exclude_defaults=True)
    table = _table(config)
    diffs = _diff_paths(table, active, frozen)
    if not diffs:
        return DiffScope.NONE, []
    has_data = False
    diff_strs: list[str] = []
    for path in diffs:
        decl = table.get(path)
        if decl is None:
            logger.warning(
                "classify_config_diff: unclassified config path %r — treating as "
                "DATA_AFFECTING. This campaign's snapshot names a knob the engine no "
                "longer has; re-stamp it (`promptpotter restamp --apply`).",
                ".".join(path),
            )
            has_data = True
        elif decl.knob.scope is Scope.DATA:
            has_data = True
        diff_strs.append(".".join(path))
    if has_data:
        return DiffScope.DATA_AFFECTING, diff_strs
    return DiffScope.POLICY_ONLY, diff_strs


@dataclass(frozen=True)
class Coupling:
    """A declared relationship between knobs sharing an estimand; ``predicate`` is True in the violating
    combination. ``collision`` = ill-defined statistic, ``inert`` = silent waste, ``info`` = co-moves."""

    name: str
    knobs: tuple[str, ...]
    estimand: Estimand
    relation: str
    consequence: str
    severity: str
    predicate: Callable[[CampaignConfig], bool]


# The bench's own couplings. An optimizer's live on its members (`MemberCoupling`).
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
        name="graduation_self_gated_on_holdout",
        knobs=(
            "optimization.enable_2pl_graduation",
            "optimization.elimination_n_min",
        ),
        estimand=Estimand.DISCRIMINATION,
        relation=(
            "enable_2pl_graduation lets the difficulty ruler add per-sample "
            "discrimination aₛ (2PL), but only where a data-rich dataset wins held-out "
            "cross-validation; the same elimination_n_min floor that warms δ gates when "
            "the bank is rich enough to fit aₛ at all."
        ),
        consequence=(
            "Self-gated, not a collision: a cold or non-discriminating dataset stays "
            "1PL automatically, and the held-out gate means 2PL can never regress a "
            "dataset. Shown so the operator sees the ruler may carry discrimination "
            "once the bank is rich. Turn OFF to pin 1PL everywhere."
        ),
        severity="info",
        predicate=lambda c: False,
    ),
)


def _member_couplings(config: CampaignConfig) -> Iterator[Coupling]:
    selected = select_optimizer(config.optimization)
    for node in selected.member_nodes:
        knobs, declared = selected.knobs(node), selected.declared_knobs(node)
        for mc in optimizers.member(node).couplings:
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


def declared_couplings(config: CampaignConfig) -> list[Coupling]:
    """The bench's couplings and the selected optimizer's, active or not."""
    return [*COUPLINGS, *_member_couplings(config)]


def check_couplings(config: CampaignConfig) -> list[Coupling]:
    return [c for c in declared_couplings(config) if c.predicate(config)]


@dataclass(frozen=True)
class KnobState:
    path: str
    value: Any
    source: str  # default | campaign | required | manifest
    estimands: tuple[Estimand, ...]


_MISSING = object()


def _at(data: Any, path: tuple[str, ...]) -> Any:
    cur: Any = data
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def resolve_knob_states(config: CampaignConfig) -> list[KnobState]:
    """Every knob's effective value + source layer + estimands. ``source`` is required / campaign /
    default for a bench knob and campaign / manifest for a node's; ``value`` is ``None`` where an
    opt-in submodel is off, not merely unset."""
    selected = select_optimizer(config.optimization)
    dumped = config.model_dump(mode="json")
    authored = config.model_dump(mode="json", exclude_defaults=True)
    nodes = {n: selected.knobs(n).model_dump(mode="json") for n in selected.member_nodes}
    dumped["optimization"]["nodes"] = {n: {"config": v} for n, v in nodes.items()}
    states: list[KnobState] = []
    for path, decl in _table(config).items():
        value = _at(dumped, path)
        set_by_operator = _at(authored, path) is not _MISSING
        if path[: len(_NODES_PATH)] == _NODES_PATH:
            source = "campaign" if set_by_operator else "manifest"
        else:
            source = "required" if decl.required else ("campaign" if set_by_operator else "default")
        states.append(
            KnobState(
                path=decl.dotted,
                value=None if value is _MISSING else value,
                source=source,
                estimands=tuple(sorted(decl.knob.estimands)),
            )
        )
    return states


# A coupling naming a knob that no longer exists points the operator at a knob they
# cannot set. Cheap to check, beside the table it guards.
_declared = {d.dotted for d in KNOBS.values()}
_ghosts = sorted({k for c in COUPLINGS for k in c.knobs} - _declared)
if _ghosts:
    raise RuntimeError(f"COUPLINGS name knobs that are not CampaignConfig leaves: {_ghosts}.")
del _declared, _ghosts
