"""The knob layer. ``KNOBS`` is WALKED off each field's own ``Knob`` metadata, never re-listed, so it
cannot go stale. Couplings + the one-way ``knobs`` → ``config`` import: ``application/CLAUDE.md``."""

from __future__ import annotations

import logging
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from promptpotter.application.campaign_config import CampaignConfig, Estimand, Knob, Scope
from promptpotter.config.settings import POBB_DEFAULT_EPSILON

logger = logging.getLogger(__name__)

__all__ = [
    "COUPLINGS",
    "KNOBS",
    "Coupling",
    "DiffScope",
    "check_couplings",
    "classify_config_diff",
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
            f"CampaignConfig leaf {'.'.join(path)!r} carries no Knob(...) — an undeclared "
            "knob is invisible to the config map and classifies DATA_AFFECTING on every "
            "resume. Declare its scope + estimand(s) on the field itself: "
            "`Annotated[<type>, Knob(Scope.POLICY, Estimand.STOPPING)]` "
            "(promptpotter/application/campaign_config.py)."
        )
    return out


# The registry. Derived, so it cannot list a knob the model doesn't have, nor miss one
# it does — the two failure modes of the name-keyed tables this replaces.
KNOBS: dict[tuple[str, ...], KnobDecl] = {d.path: d for d in _walk(CampaignConfig)}


class DiffScope(StrEnum):
    """Resume-time diff classification, the union of the diffed leaves' scopes. ``POLICY_ONLY`` keeps
    past measurements valid; ``DATA_AFFECTING`` (or any unclassified path) sends resume to divergence."""

    NONE = "none"
    POLICY_ONLY = "policy_only"
    DATA_AFFECTING = "data_affecting"


def _diff_paths(
    active: Any,
    frozen: Any,
    prefix: tuple[str, ...] = (),
) -> list[tuple[str, ...]]:
    if prefix in KNOBS:
        return [prefix] if active != frozen else []
    if isinstance(active, dict) or isinstance(frozen, dict):
        a = active if isinstance(active, dict) else {}
        f = frozen if isinstance(frozen, dict) else {}
        out: list[tuple[str, ...]] = []
        for key in set(a.keys()) | set(f.keys()):
            out.extend(_diff_paths(a.get(key), f.get(key), (*prefix, key)))
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
    diffs = _diff_paths(active, frozen)
    if not diffs:
        return DiffScope.NONE, []
    has_data = False
    diff_strs: list[str] = []
    for path in diffs:
        decl = KNOBS.get(path)
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


def _sel(c: CampaignConfig) -> Any:
    return c.optimization.mechanisms.selection


def _elim(c: CampaignConfig) -> Any:
    return c.optimization.mechanisms.elimination


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


# Defaults read off the registry so an inert-knob predicate compares against the real
# default and can never drift from it.
_LOCK_IN_DEFAULT = KNOBS[("optimization", "pobb_lock_in")].default
_LOCK_IN_NMIN_DEFAULT = KNOBS[("optimization", "pobb_lock_in_n_min")].default


COUPLINGS: tuple[Coupling, ...] = (
    Coupling(
        name="resubset_subset_relative_on_thin_bank",
        knobs=(
            "optimization.mechanisms.selection.per_round_resubset",
            "optimization.elimination_n_min",
        ),
        estimand=Estimand.SELECTION,
        relation=(
            "per_round_resubset=ON re-picks the scored subset per candidate. Every "
            "cross-round comparator reads ONE anchored δ ruler in θ — the stall replayer, "
            "c0_ok, the round-winner election and PoBB elimination all via "
            "fit_theta_given_delta on cycle.ruler — and that ruler is EXTENDED to cover each "
            "round's new cells, so the accuracy-space collision is resolved."
        ),
        consequence=(
            "Resubset is comparability-coherent in θ, with TWO residuals. WARMTH: below "
            "elimination_n_min banked samples the ruler stays flat and θ is logit-accuracy "
            "on each arm's OWN subset, so those rounds are subset-relative until it warms. "
            "BAND: the acquisition buys the cells whose δ sits nearest the leader's θ, which "
            "against a wide bank collapses onto a narrow δ range — inside it every cell is "
            "equally hard, so θ reduces to logit-accuracy plus a constant while the ruler is "
            "warm and every id matches, which makes this the silent one. Turning resubset "
            "OFF freezes the panel to the campaign-start prefix, which removes the band "
            "residual by forcing the same cells into every panel. A cell missing from a WARM "
            "ruler is neither residual — it raises."
        ),
        severity="info",
        predicate=lambda c: bool(_sel(c).per_round_resubset),
    ),
    Coupling(
        name="lock_in_floor_below_elimination",
        knobs=("optimization.pobb_lock_in_n_min", "optimization.elimination_n_min"),
        estimand=Estimand.STOPPING,
        relation=(
            "A leader can lock in (and stop measuring) at pobb_lock_in_n_min samples; "
            "losers only start being eliminated at elimination_n_min."
        ),
        consequence=(
            "If lock-in fires on fewer samples than elimination needs, a leader is "
            "crowned before the field can be dropped — the round can end before it is "
            "tested. Keep pobb_lock_in_n_min ≥ elimination_n_min."
        ),
        severity="info",
        predicate=lambda c: c.optimization.pobb_lock_in_n_min < c.optimization.elimination_n_min,
    ),
    Coupling(
        name="epsilon_threshold_inert",
        knobs=(
            "optimization.pobb_epsilon",
            "optimization.mechanisms.elimination.epsilon_elimination",
        ),
        estimand=Estimand.STOPPING,
        relation="pobb_epsilon is read only by the Bayesian best-test (epsilon_elimination).",
        consequence=(
            "pobb_epsilon was tuned away from its default but epsilon_elimination is OFF, "
            "so nothing reads it — the knob is inert and every round runs full budget."
        ),
        severity="inert",
        predicate=lambda c: (
            (not _elim(c).epsilon_elimination)
            and c.optimization.pobb_epsilon != POBB_DEFAULT_EPSILON
        ),
    ),
    Coupling(
        name="epsilon_floor_inverted",
        knobs=(
            "optimization.pobb_epsilon_floor",
            "optimization.pobb_epsilon",
        ),
        estimand=Estimand.STOPPING,
        relation=(
            "pobb_epsilon_floor is the bar at elimination_n_min, ramping UP to pobb_epsilon "
            "by twice that depth — being a floor, it belongs at or below pobb_epsilon."
        ),
        consequence=(
            "pobb_epsilon_floor sits ABOVE pobb_epsilon, which would grade the bar DOWNWARD "
            "as evidence accumulates. The ramp goes flat at pobb_epsilon instead, so the floor "
            "is inert and shallow candidates are cut on the deep bar."
        ),
        severity="inert",
        predicate=lambda c: c.optimization.pobb_epsilon_floor > c.optimization.pobb_epsilon,
    ),
    Coupling(
        name="lock_in_threshold_inert",
        knobs=(
            "optimization.pobb_lock_in",
            "optimization.pobb_lock_in_n_min",
            "optimization.mechanisms.elimination.leader_lock_in",
        ),
        estimand=Estimand.STOPPING,
        relation=(
            "pobb_lock_in / pobb_lock_in_n_min only govern anything while the "
            "leader_lock_in toggle is ON."
        ),
        consequence=(
            "A lock-in threshold was tuned but leader_lock_in is OFF, so no leader "
            "ever locks in early — the knobs are inert."
        ),
        severity="inert",
        predicate=lambda c: (
            (not _elim(c).leader_lock_in)
            and (
                c.optimization.pobb_lock_in != _LOCK_IN_DEFAULT
                or c.optimization.pobb_lock_in_n_min != _LOCK_IN_NMIN_DEFAULT
            )
        ),
    ),
    Coupling(
        name="fatal_fastpath_needs_degradation",
        knobs=(
            "optimization.mechanisms.elimination.degradation_fatal_fastpath",
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
            _elim(c).degradation_fatal_fastpath and c.optimization.degradation_threshold == 0
        ),
    ),
    Coupling(
        name="headline_subset_relative_under_resubset",
        knobs=(
            "headline_metric",
            "optimization.mechanisms.selection.per_round_resubset",
        ),
        estimand=Estimand.DISPLAY,
        relation=(
            "With per_round_resubset ON, accuracy/composite are subset-relative while "
            "the gate is θ; the headline number the operator reads should be ability "
            "(θ) or carry the subset badge."
        ),
        consequence=(
            "The headline reads accuracy/composite while θ decides the winner. Every "
            "surface now prints θ beside it, so the pairing is legible rather than "
            "unexplained — but the headline is still the subset-relative one. Set "
            "headline_metric='ability' under resubset."
        ),
        # `inert`, not `info`: nothing about this combination co-moves — one knob's display
        # choice simply wastes the other's invariance, which is exactly what inert names.
        severity="inert",
        predicate=lambda c: bool(_sel(c).per_round_resubset) and c.headline_metric != "ability",
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


@dataclass(frozen=True)
class KnobState:
    path: str
    value: Any
    source: str  # default | campaign | required | constant
    estimands: tuple[Estimand, ...]


# Hardcoded statistical constants that participate in couplings — not config leaves, but
# knobs in the statistical sense: they bound the estimators. Surfaced in the map so a
# statistician sees the floors that gate when θ becomes meaningful.
_CONSTANT_KNOBS: tuple[KnobState, ...] = (
    KnobState("const.POBB_DEFAULT_EPSILON", POBB_DEFAULT_EPSILON, "constant", (Estimand.STOPPING,)),
)

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
    default / constant; ``value`` is ``None`` where an opt-in submodel is off, not merely unset."""
    dumped = config.model_dump(mode="json")
    authored = config.model_dump(mode="json", exclude_defaults=True)
    states: list[KnobState] = []
    for path, decl in KNOBS.items():
        value = _at(dumped, path)
        set_by_operator = _at(authored, path) is not _MISSING
        source = "required" if decl.required else ("campaign" if set_by_operator else "default")
        states.append(
            KnobState(
                path=decl.dotted,
                value=None if value is _MISSING else value,
                source=source,
                estimands=tuple(sorted(decl.knob.estimands)),
            )
        )
    return [*states, *_CONSTANT_KNOBS]


def check_couplings(config: CampaignConfig) -> list[Coupling]:
    return [c for c in COUPLINGS if c.predicate(config)]


# A coupling naming a knob that no longer exists points the operator at a knob they
# cannot set. Cheap to check, beside the table it guards.
_declared = {d.dotted for d in KNOBS.values()} | {k.path for k in _CONSTANT_KNOBS}
_ghosts = sorted({k for c in COUPLINGS for k in c.knobs} - _declared)
if _ghosts:
    raise RuntimeError(f"COUPLINGS name knobs that are not CampaignConfig leaves: {_ghosts}.")
del _declared, _ghosts
