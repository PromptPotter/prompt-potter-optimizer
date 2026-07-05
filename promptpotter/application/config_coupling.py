"""Config-knob coupling + provenance registry — the single declared map of
*which optimization knob moves which statistical estimand* and *which knobs
collide*.

Why this exists: the optimization knobs (``per_round_resubset``,
``pobb_epsilon``, ``elimination_n_min``, …) do not act independently — several
co-determine the same statistical quantity (the scored subset, the difficulty
ruler δ, the ability θ, the gate), so flipping one in isolation can silently
make another ill-defined. Until now those interactions lived only as English
"deferred-with-the-flip" prose in ``docs/specs/fitness-comparability.md``; nothing
machine-checked them, so a collision (e.g. turning ``per_round_resubset`` ON while
the cross-round comparators are still accuracy-space) read as a clean toggle.

This module lifts the interactions into one declared structure with two facets,
mirroring the proven ``config_diff._FIELD_SCOPES`` pattern (declare-once +
import-time completeness guard):

- **Provenance** (``resolve_knob_states``) — every knob's effective value, the
  layer it came from (operator-set vs Pydantic default vs hardcoded constant),
  and the estimand(s) it touches. Answers *"what overwrites what."*
- **Couplings** (``COUPLINGS`` / ``check_couplings``) — the declared relationships
  between knobs, each with a predicate over the resolved config that says whether
  the combination is currently *violated*. Answers *"what clashes with what."*

Three consumers read this one registry: ``run_preflight_checks`` (a pre-run CLI
warning), the ``diagnostics.config_map`` CLI table, and the ``GET
/campaigns/{id}/config-map`` webapp endpoint — so the collision is visible on
every operator surface, never just in the engine.

One-way import — ``config_coupling`` depends on ``config`` (for the models) and
on the statistical constants it couples against; never the reverse. Import it
lazily from ``config.run_preflight_checks`` to keep the constant imports off
``config``'s module-load path.
"""

from __future__ import annotations

import types
import typing
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from promptpotter.application.config import CampaignConfig, OptimizationConfig
from promptpotter.config.settings import POBB_DEFAULT_EPSILON

__all__ = [
    "COUPLINGS",
    "Coupling",
    "Estimand",
    "KnobState",
    "check_couplings",
    "estimand_doc",
    "knob_label",
    "resolve_knob_states",
]


def knob_label(path: str) -> str:
    """Short display name for a knob — drops the ``optimization.`` /
    ``optimization.mechanisms.<group>.`` prefix. Shared by the CLI diagnostic and
    the webapp config-map so both name a knob identically."""
    short = path.removeprefix("optimization.")
    if short.startswith("mechanisms."):
        short = short.split(".", 2)[-1]
    return short


class Estimand(StrEnum):
    """The statistical quantity a knob moves — the axis a statistician groups by.

    A knob may touch more than one. Couplings are *between knobs that share an
    estimand*: that shared quantity is what goes ill-defined when they disagree.
    """

    SELECTION = "selection"
    DIFFICULTY = "difficulty"
    DISCRIMINATION = "discrimination"
    ABILITY = "ability"
    GATE = "gate"
    STOPPING = "stopping"
    ESCALATION = "escalation"
    SEARCH = "search"
    SPEND = "spend"
    DISPLAY = "display"


_ESTIMAND_DOC: dict[Estimand, str] = {
    Estimand.SELECTION: "Which samples get scored each round — the subset the fitness is measured over.",
    Estimand.DIFFICULTY: "The per-sample difficulty ruler δ (1PL Rasch) used to difficulty-adjust scores.",
    Estimand.DISCRIMINATION: "The per-sample discrimination aₛ (2PL) — how sharply a sample separates able from unable candidates; only estimated where a data-rich dataset graduates.",
    Estimand.ABILITY: "The candidate ability θ — difficulty-adjusted skill, the metric the gate compares.",
    Estimand.GATE: "The round-promotion / improvement gate — what counts as 'better' and is kept.",
    Estimand.STOPPING: "The early-abort / elimination rules that stop measuring a candidate before budget.",
    Estimand.ESCALATION: "The L1/L2/L3 patience ladder — when the loop escalates strategy or halts.",
    Estimand.SEARCH: "The optimizer search space + data binding the loop explores.",
    Estimand.SPEND: "The budget ceilings (USD / tokens) that halt the cycle.",
    Estimand.DISPLAY: "What number the operator reads — no effect on the data or the decision.",
}


def estimand_doc(estimand: Estimand) -> str:
    """Plain-language one-liner for an estimand (teach, don't dump)."""
    return _ESTIMAND_DOC[estimand]


# Per-knob estimand tags. Dotted ``CampaignConfig`` path → the estimand(s) the
# knob moves. Completeness-guarded at import (see bottom): a new config leaf with
# no tag fails the import, so a knob can never ship invisible to the map.
_KNOB_ESTIMANDS: dict[str, frozenset[Estimand]] = {
    "dataset_name": frozenset({Estimand.SEARCH}),
    "sp_budget_ttest": frozenset({Estimand.SELECTION}),
    "exclude_nodes": frozenset({Estimand.SEARCH}),
    "pipeline_overrides": frozenset({Estimand.SEARCH}),
    "optimizer_narrowing": frozenset({Estimand.SEARCH}),
    "scoring": frozenset({Estimand.GATE}),
    "headline_metric": frozenset({Estimand.DISPLAY}),
    "dataset_split": frozenset({Estimand.DISPLAY}),
    "optimization.max_rounds": frozenset({Estimand.ESCALATION, Estimand.SPEND}),
    "optimization.lives.start": frozenset({Estimand.ESCALATION, Estimand.SPEND}),
    "optimization.lives.cap": frozenset({Estimand.ESCALATION, Estimand.SPEND}),
    "optimization.l1_patience": frozenset({Estimand.ESCALATION}),
    "optimization.n_variants": frozenset({Estimand.SEARCH}),
    "optimization.optimizer_set": frozenset({Estimand.SEARCH}),
    "optimization.replicate_survivors": frozenset({Estimand.SEARCH, Estimand.SPEND}),
    "optimization.improvement_threshold": frozenset({Estimand.GATE}),
    "optimization.l2_patience": frozenset({Estimand.ESCALATION}),
    "optimization.l3_patience": frozenset({Estimand.ESCALATION}),
    "optimization.degradation_threshold": frozenset({Estimand.STOPPING}),
    "optimization.elimination_n_min": frozenset(
        {Estimand.STOPPING, Estimand.ABILITY, Estimand.DIFFICULTY}
    ),
    "optimization.pobb_epsilon": frozenset({Estimand.STOPPING}),
    "optimization.pobb_lock_in": frozenset({Estimand.STOPPING}),
    "optimization.pobb_lock_in_n_min": frozenset({Estimand.STOPPING}),
    "optimization.spend_budget_usd": frozenset({Estimand.SPEND}),
    "optimization.token_budget": frozenset({Estimand.SPEND}),
    "optimization.origin_gate": frozenset({Estimand.GATE}),
    "optimization.forbidden_axes_strict": frozenset({Estimand.SEARCH}),
    "optimization.rebase_capability": frozenset({Estimand.ESCALATION}),
    "optimization.terminate_capability": frozenset({Estimand.ESCALATION}),
    "optimization.exploration.seed_heatmap_from_archive": frozenset({Estimand.DIFFICULTY}),
    "optimization.exploration.enable_2pl_graduation": frozenset(
        {Estimand.DISCRIMINATION, Estimand.DIFFICULTY, Estimand.ABILITY}
    ),
    "optimization.mechanisms.selection.per_round_resubset": frozenset(
        {Estimand.SELECTION, Estimand.GATE}
    ),
    "optimization.mechanisms.selection.online_reorder": frozenset({Estimand.SELECTION}),
    "optimization.mechanisms.elimination.epsilon_elimination": frozenset({Estimand.STOPPING}),
    "optimization.mechanisms.elimination.deterministic_dominance": frozenset({Estimand.STOPPING}),
    "optimization.mechanisms.elimination.degradation_fatal_fastpath": frozenset(
        {Estimand.STOPPING}
    ),
    "optimization.mechanisms.elimination.leader_lock_in": frozenset({Estimand.STOPPING}),
    "optimization.mechanisms.elimination.equivalence_elimination": frozenset({Estimand.STOPPING}),
}

# Hardcoded statistical constants that participate in couplings — not config
# leaves, but knobs in the statistical sense: they bound the estimators. Surfaced
# in the map so a statistician sees the floors that gate when θ becomes meaningful.
_CONST_ESTIMANDS: dict[str, frozenset[Estimand]] = {
    "const.POBB_DEFAULT_EPSILON": frozenset({Estimand.STOPPING}),
}
_CONST_VALUES: dict[str, Any] = {
    "const.POBB_DEFAULT_EPSILON": POBB_DEFAULT_EPSILON,
}

# Field defaults pulled once from the model so the inert-knob predicates compare
# against the real default and never drift if a default changes.
_POBB_LOCK_IN_DEFAULT = OptimizationConfig.model_fields["pobb_lock_in"].default
_POBB_LOCK_IN_NMIN_DEFAULT = OptimizationConfig.model_fields["pobb_lock_in_n_min"].default


def _sel(c: CampaignConfig) -> Any:
    return c.optimization.mechanisms.selection


def _elim(c: CampaignConfig) -> Any:
    return c.optimization.mechanisms.elimination


@dataclass(frozen=True)
class Coupling:
    """A declared relationship between knobs that share an estimand.

    ``predicate`` returns True when the *current* config is in the violating
    combination. ``severity``:

    - ``collision`` — the combination makes a statistical quantity ill-defined
      (soundness bug); the preflight gate warns loudly.
    - ``inert`` — a tuned numeric knob no-ops because its enabling toggle is off
      (silent waste, not a soundness bug).
    - ``info`` — a permanent relationship worth seeing in the map; usually never
      "violated" (predicate stays False), it's shown so the operator knows the
      knobs co-move.
    """

    name: str
    knobs: tuple[str, ...]
    estimand: Estimand
    relation: str
    consequence: str
    severity: str
    predicate: Callable[[CampaignConfig], bool]


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
            "cross-round comparator now reads ONE fixed δ ruler in θ — the stall "
            "replayer, c0_ok, the round-winner election and PoBB elimination all via "
            "fit_theta_given_delta on cycle.delta_scale — so the accuracy-space collision "
            "is resolved. Residual: a sample absent from the δ bank is flat (δ=0)."
        ),
        consequence=(
            "Resubset is comparability-coherent; flipping the default ON is the operator's "
            "deliberate step (fork-compare to validate). The one residual: while the bank "
            "is thin (< elimination_n_min grade-A samples), unbanked samples score "
            "flat, so heavy per-candidate drift on a cold bank is only partially "
            "difficulty-adjusted — it warms toward full adjustment as the archive grows."
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
        relation="pobb_epsilon only governs anything while the epsilon_elimination toggle is ON.",
        consequence=(
            "pobb_epsilon was tuned away from its default but epsilon_elimination is "
            "OFF, so the loser-elimination rule never fires — the knob is inert and "
            "every round runs full budget."
        ),
        severity="inert",
        predicate=lambda c: (
            (not _elim(c).epsilon_elimination)
            and c.optimization.pobb_epsilon != POBB_DEFAULT_EPSILON
        ),
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
                c.optimization.pobb_lock_in != _POBB_LOCK_IN_DEFAULT
                or c.optimization.pobb_lock_in_n_min != _POBB_LOCK_IN_NMIN_DEFAULT
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
            "The operator reads accuracy/composite as the headline while θ decides the "
            "winner — the lineage can show a higher-accuracy candidate losing, "
            "unexplained. Set headline_metric='ability' under resubset."
        ),
        severity="info",
        predicate=lambda c: bool(_sel(c).per_round_resubset) and c.headline_metric != "ability",
    ),
    Coupling(
        name="graduation_self_gated_on_holdout",
        knobs=(
            "optimization.exploration.enable_2pl_graduation",
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
    Coupling(
        name="selection_basis_pair",
        knobs=(
            "optimization.mechanisms.selection.per_round_resubset",
            "optimization.mechanisms.selection.online_reorder",
        ),
        estimand=Estimand.SELECTION,
        relation=(
            "per_round_resubset picks the between-round subset; within a round the "
            "order is always the deterministic shared round order (build_round_order "
            "— seed-miss win opportunities first), identical for every candidate. "
            "online_reorder is INERT (kept on-disk for the config shrink pass)."
        ),
        consequence=(
            "A permanent relationship — shown so the basis is legible. No violation; "
            "changing per_round_resubset redefines what 'the subset' a fitness "
            "number was measured over means."
        ),
        severity="info",
        predicate=lambda c: False,
    ),
)


@dataclass(frozen=True)
class KnobState:
    """One knob's resolved provenance + estimand tags — a row of the config map."""

    path: str
    value: Any
    source: str  # default | campaign | required | constant
    estimands: tuple[Estimand, ...]


def _unwrap_optional(annotation: object) -> object:
    """Strip ``X | None`` down to ``X``; pass anything else through."""
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        non_none = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


# Submodels whose sub-fields are NOT knobs (display metadata) — descent stops here
# so the leaf is the whole unit, matching the estimand tags above.
_UNIT_SUBTREES: frozenset[tuple[str, ...]] = frozenset({("dataset_split",)})


def _full_leaves(model_cls: type[BaseModel], prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """Full-depth leaf paths of *model_cls* — descends every nested model except a
    ``_UNIT_SUBTREES`` entry (kept whole)."""
    out: list[tuple[str, ...]] = []
    for name, fld in model_cls.model_fields.items():
        path = (*prefix, name)
        if path in _UNIT_SUBTREES:
            out.append(path)
            continue
        inner = _unwrap_optional(fld.annotation)
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            out.extend(_full_leaves(inner, path))
        else:
            out.append(path)
    return out


def _field_default(path: str) -> tuple[Any, bool]:
    """``(default, is_required)`` for a dotted ``CampaignConfig`` path. A field with
    a ``default_factory`` resolves to its produced value (e.g. ``[]`` / ``{}``)."""
    model: type[BaseModel] = CampaignConfig
    fld = None
    parts = path.split(".")
    for i, part in enumerate(parts):
        fld = model.model_fields[part]
        if i < len(parts) - 1:
            inner = _unwrap_optional(fld.annotation)
            assert isinstance(inner, type) and issubclass(inner, BaseModel)
            model = inner
    assert fld is not None
    if fld.is_required():
        return None, True
    if fld.default_factory is not None:
        return fld.default_factory(), False  # type: ignore[call-arg]
    return fld.default, False


def _walk(data: Any, path: str) -> Any:
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def resolve_knob_states(config: CampaignConfig) -> list[KnobState]:
    """Every knob's effective value + source layer + estimands — the provenance map.

    ``source`` is ``required`` (no default, operator must set), ``campaign``
    (operator set it away from the default), ``default`` (the Pydantic default
    rode through), or ``constant`` (a hardcoded statistical floor).
    """
    dumped = config.model_dump(mode="json")
    states: list[KnobState] = []
    for path, estimands in _KNOB_ESTIMANDS.items():
        value = _walk(dumped, path)
        default, required = _field_default(path)
        source = "required" if required else ("campaign" if value != default else "default")
        states.append(KnobState(path, value, source, tuple(sorted(estimands))))
    for path, estimands in _CONST_ESTIMANDS.items():
        states.append(KnobState(path, _CONST_VALUES[path], "constant", tuple(sorted(estimands))))
    return states


def check_couplings(config: CampaignConfig) -> list[Coupling]:
    """The declared couplings currently in their violating combination."""
    return [c for c in COUPLINGS if c.predicate(config)]


# Completeness guard — every CampaignConfig leaf must carry an estimand tag, and
# every tag must name a real leaf. A new knob with no tag would ship invisible to
# the coupling map (the exact silent gap this module closes); a stale tag means a
# removed knob. Either fails the import, beside the registry it guards — same
# discipline as config_diff's _FIELD_SCOPES guard.
_actual = {".".join(p) for p in _full_leaves(CampaignConfig)}
_untagged = sorted(_actual - set(_KNOB_ESTIMANDS))
_stale = sorted(set(_KNOB_ESTIMANDS) - _actual)
if _untagged or _stale:
    raise RuntimeError(
        "config_coupling estimand tags out of sync with CampaignConfig: "
        f"untagged leaves {_untagged}; stale tags {_stale}. "
        "Tag each new leaf with its Estimand(s) in _KNOB_ESTIMANDS."
    )
del _actual, _untagged, _stale
