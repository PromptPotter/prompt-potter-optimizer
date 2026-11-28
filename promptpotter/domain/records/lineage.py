"""A cycle's lineage: its mint and start, the stop its runner banked, a supersede, an arm's act, its spawn."""

from __future__ import annotations

import enum
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from promptpotter.domain.cycle_result import CycleResult, WallClock
from promptpotter.domain.fork import ConfigOverrides, ForkSpec
from promptpotter.domain.opt_search_point import NodePrompt
from promptpotter.domain.paired_reading import ArmKey, RoundArm
from promptpotter.domain.pass_kinds import PASSES
from promptpotter.domain.pipeline_params import NodeCall, laid_calls, lay_nodes
from promptpotter.domain.pipeline_schema import NodeSearchNarrowing
from promptpotter.domain.records import CycleRecord
from promptpotter.domain.strict_model import FrozenModel, StrictModel
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.measurement_context import MeasurementRole


class OriginSource(enum.StrEnum):
    FORK_START = "fork_start"
    CAMPAIGN_ORIGIN = "campaign_origin"
    # The start carries no origin: an L2/L3 rebase replays its own C0, which has none to stamp.
    REPLAYED = ""


OPERATOR_ORIGIN_SOURCES = frozenset(OriginSource) - {OriginSource.REPLAYED}


class CycleStart(FrozenModel):
    """One cycle's own delta, laid over its parent's laid starts (`CycleConfig.laid`)."""

    origin_prompts: dict[str, NodePrompt] = Field(
        default_factory=dict,
        description="C0's prompt loci, per prompt node; empty when the start carries no origin.",
    )
    origin_params: dict[str, dict[str, Any]] | None = Field(
        default=None,
        description="The params twin of `origin_prompts`: a fork off a candidate runs that "
        "candidate's record (`resolved_params`), laid under `pipeline_overlay`, the operator's "
        "edit. None on a start off no candidate.",
    )
    pipeline_overlay: dict[str, NodeCall] = Field(default_factory=dict)
    optimizer_narrowing: dict[str, NodeSearchNarrowing] = Field(
        default_factory=dict,
        description="Per-fork search-space lock edits, laid on its parent's by part "
        "(`NodeSearchNarrowing`'s fields): `param_keys` subsets, `prompt_fields_held` joins, "
        "`param_allowed_values` replaces per param. Empty for an unedited fork or a "
        "campaign-from-origin start.",
    )
    config_overrides: ConfigOverrides = Field(default_factory=ConfigOverrides)
    origin_source: OriginSource = Field(
        default=OriginSource.REPLAYED,
        description=(
            "Which act started C0 — 'fork_start' | 'campaign_origin', naming its lineage's "
            "`changes_description`; empty when the start carries no origin (an L2/L3 rebase "
            "replays its own)."
        ),
    )

    @model_validator(mode="after")
    def _origin_needs_provenance(self) -> CycleStart:
        if self.origin_prompts and not self.origin_source:
            raise ValueError("origin_prompts set without an origin_source stamp")
        return self

    def runs(self) -> dict[str, dict[str, Any]]:
        """Each node's config the fork runs: its candidate's record under the operator's edit."""
        return lay_nodes(self.origin_params or {}, laid_calls(self.pipeline_overlay))


class SpawnedBy(StrictModel):
    """The outer work-item an L4 inner cycle was spawned to measure; an arm's address places the run under its candidate."""

    round: int = Field(description="Outer round; 0 is the origin (C0).")
    individual_id: str | None = Field(
        default=None,
        description="The outer individual's `OptSearchPoint.id` — stable across rounds, and the "
        "outer ARM's key where `role` is an arm's. Null outside a measured pass.",
    )
    role: MeasurementRole | None = Field(
        default=None,
        description="WHY this cell ran. A pass beside the arms (an overlap top-up) is spawned outside the round's shared order, and reading one as the individual's own panel cell makes a repaired round unreproducible. Null outside a measured pass.",
    )
    cycle_id: str | None = Field(
        default=None,
        description="The outer cycle whose pass spawned it — the asker, which a fork moves off "
        "the cycle owning the sandbox. Null outside a measured pass.",
    )
    task: str = Field(
        description="The panel cell this run measured — the outer query, e.g. `justlogic/d234/seed-0`, spelled off what the cell IS (`runner/inner/tasks.py::InnerTasks.query_of`), never its seat's id. The individual does NOT identify a run: every task runs for every individual, so one individual's spawns are as many as the panel has cells and are told apart only by this."
    )

    @property
    def arm(self) -> RoundArm | None:
        """The outer arm that asked, where an arm's pass did; any other pass names none."""
        if self.role is None or not PASSES[self.role].arm:
            return None
        if self.individual_id is None:
            return None
        return RoundArm(round=self.round, arm=ArmKey(self.individual_id))


class CycleMintedRecord(CycleRecord):
    """One per cycle's own ledger, after only the lines it adopted, and its first own fact: a chain
    is walked off it alone. A cycle runs every start its chain's mints hold, root first."""

    record_type: Literal["cycle_minted"] = "cycle_minted"
    # The parent's cycle dir relative to this one's, POSIX-separated: `../<id>` for a sibling.
    parent: str | None = None
    forked_at_offset: int | None = None
    # The first round the fork runs itself: every round below it is its parent's line's.
    cut_round: int | None = None
    fork: ForkSpec | None = None
    start: CycleStart | None = None
    # decisions.md § 0005: its start steers outside the permitted narrowing, read as a `BABYSAT` act.
    babysat: bool = False
    timestamp: str = Field(default_factory=utcnow_iso)

    @model_validator(mode="after")
    def _fork_is_whole(self) -> CycleMintedRecord:
        parts = (self.parent, self.forked_at_offset, self.cut_round, self.fork)
        if any(p is None for p in parts) and any(p is not None for p in parts):
            raise ValueError(
                "a fork carries its parent, forked_at_offset, cut_round and fork together"
            )
        return self

    def parent_cycle_dir(self, cycle_dir: Path) -> Path | None:
        """The ONE resolution of the parent this record names, from the dir of the cycle it mints."""
        return None if self.parent is None else Path(os.path.normpath(cycle_dir / self.parent))


class CycleFinalRecord(CycleRecord):
    """Banked only where the cycle's own runner stops it; a later ``running`` retires it."""

    record_type: Literal["cycle_final"] = "cycle_final"
    final: CycleResult
    # The round the stop left unclosed; ``None`` for a stop at a boundary.
    interrupted_round: int | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class LaunchClockRecord(CycleRecord):
    """Banked by the cycle holding the campaign's line, at every launch end, a pause's included."""

    record_type: Literal["launch_clock"] = "launch_clock"
    started_at: str
    finished_at: str
    # Folded across every cycle of the line; only the runner knows a launch's endpoints.
    clock: WallClock
    timestamp: str = Field(default_factory=utcnow_iso)


class CycleSupersededRecord(CycleRecord):
    """On the LEFT-BEHIND side of a supersede cut; a later ``running`` here takes the line back."""

    record_type: Literal["cycle_superseded"] = "cycle_superseded"
    successor_cycle_id: str
    timestamp: str = Field(default_factory=utcnow_iso)


# The act a babysat mint (`CycleMintedRecord.babysat`) reads as.
BABYSAT = "disallowed_model_override"


class ArmAct(enum.StrEnum):
    """An act on an arm, admitted like on any campaign; on a head-to-head's arm it is the arm's
    exit, from which its line reads uncontrolled (decisions.md § 0009)."""

    REWIND = "rewind"
    FORK_ON_DIVERGENCE = "fork_on_divergence"
    NO_DIVERGENCE_CHECK = "no_divergence_check"
    FORK = "fork"
    SUBMIT_ARMS = "submit_arms"
    SKIP = "skip"
    SET_LIMITS = "set_limits"

    @property
    def babysits(self) -> bool:
        """decisions.md § 0005: an act inside the loop makes the cycle babysat for good."""
        return self in (ArmAct.SUBMIT_ARMS, ArmAct.SKIP)


class ArmActRecord(CycleRecord):
    """ONE record per act, on the arm's own line and never a fork's, so deleting the fork leaves
    it standing: the arm's exit and, where the act `babysits`, the cycle's intervention."""

    record_type: Literal["arm_act"] = "arm_act"
    act: ArmAct
    timestamp: str = Field(default_factory=utcnow_iso)


class SpawnedRecord(CycleRecord):
    """Appended at every open, so the last names the work-item the cycle last measured for."""

    record_type: Literal["spawned"] = "spawned"
    spawned_by: SpawnedBy
    timestamp: str = Field(default_factory=utcnow_iso)
