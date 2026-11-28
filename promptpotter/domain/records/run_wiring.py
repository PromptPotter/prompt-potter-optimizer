"""What a launch wired and run init resolved: the run's wiring, its locked scoring, its resolved pipeline."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from promptpotter.domain.arm_reading import DisplayMetric
from promptpotter.domain.connector import MeasuredUnit
from promptpotter.domain.dashboard_rows import OptimizerLimit
from promptpotter.domain.pipeline_params import NodeCall
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import CapabilityMenu, KnobLayer, PipelineSchema
from promptpotter.domain.records import CycleRecord
from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.domain.treatment import Treatment
from promptpotter.shared.clock import utcnow_iso


class DefaultsPin(FrozenModel):
    """What a declaration is a delta over, in two halves: the bench's defaults, which every arm of
    a head-to-head shares, and its optimizer's whole treatment — manifest, behaviour and prompts —
    which differs by arm by design."""

    bench: str = Field(min_length=1)
    treatment: Treatment


class DefaultsDrift(FrozenModel):
    """Something the run's declaration is a delta over moved since it was declared, so the launch
    runs today's value under the same declaration."""

    declared: DefaultsPin
    running: DefaultsPin

    def moved(self) -> list[str]:
        """``bench`` and each part of the treatment that moved."""
        declared, running = self.declared, self.running
        return [
            *(["bench"] if declared.bench != running.bench else []),
            *(
                part
                for part in Treatment.model_fields
                if getattr(declared.treatment, part) != getattr(running.treatment, part)
            ),
        ]


class BackendRevision(FrozenModel):
    """What a launch read of the backend's revision, beside the one its connector was written against."""

    expected: str
    # `None`: the backend answered with none, or could not be asked (`unread`).
    reported: str | None
    # Why it could not be asked: the failure's class alone, since its message names the backend's address.
    unread: str | None

    def drift(self) -> str | None:
        """``None`` where the backend runs the revision expected; else what differs, in a sentence."""
        if self.reported == self.expected:
            return None
        if self.unread is not None:
            said = f"its revision could not be read ({self.unread})"
        else:
            said = f"it reports revision {self.reported or 'none'}"
        return f"the backend's connector expects revision {self.expected}, and {said}"


class RunWiringRecord(CycleRecord):
    """LAST wins, over the whole chain: a fork stands on its parent's until its own lands."""

    record_type: Literal["run_wiring"] = "run_wiring"
    round_cells_ceiling: int
    # DISPLAY only: seeds a surface's metric toggle; the selector decides on its own objective.
    display_metric: DisplayMetric
    elects_on: DisplayMetric
    max_cells_in_flight: int
    measured_unit: MeasuredUnit
    # The optimizer's gateway node: every scoring pass of the run, the origin's included, runs as it.
    measurement_node: str
    # The launch's declared budget and its optimizer's own bounds; the standing patches lay over them.
    budget: Budget
    optimizer_limits: list[OptimizerLimit]
    # `None` on a mint's record: nothing has moved since the declaration was made.
    defaults_drift: DefaultsDrift | None
    # `None` where the connector checks no revision; a drift is served, never refused (`wiring.py`).
    backend_revision: BackendRevision | None
    timestamp: str = Field(default_factory=utcnow_iso)


class ScoringLockedRecord(CycleRecord):
    """Appended once, as the origin is scored under dials; a config already locked appends none."""

    record_type: Literal["scoring_locked"] = "scoring_locked"
    declared: dict[str, str]
    locked: dict[str, str]
    timestamp: str = Field(default_factory=utcnow_iso)


class PipelineRead(FrozenModel):
    """What run init read: the served pipeline with the dataset's definition laid on."""

    declaration: dict[str, Any]
    # The definition's half of that read, which a campaign outliving its dataset dir still folds.
    dataset_overlay: dict[str, NodeCall]
    # The connector endpoint it named (`DatasetDefinition.endpoint`); `None`: the connector's one.
    endpoint: str | None = None
    # The free tier's launch defaults its mint laid (`free_tier.py::free_tier_overlay`).
    free_tier_overlay: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # Per optimizer-node knob the declaration holds, the last layer that set it (`laid_config`).
    knob_layers: dict[str, dict[str, KnobLayer]] = Field(default_factory=dict)

    def declared_schema(self) -> PipelineSchema:
        """The chain as declared, before a line narrows it or a node is laid on its route."""
        return parse_pipeline_response(self.declaration)


class ResolvedRunRecord(CycleRecord):
    """What a run init resolved, read over the chain: the read LAST wins, the roster FIRST."""

    record_type: Literal["resolved_run"] = "resolved_run"
    read: PipelineRead
    # The roster a connector only NAMES can move under its name; the one every round measured stands.
    experiment: dict[str, Any] | None
    # The menu the run's schema answered with, its reasoning floors included; empty is UNKNOWN.
    model_capabilities: CapabilityMenu
    # The roster this launch resolved where it differs from the one standing, which still stands:
    # its rows are not comparable to the earlier ones. ``None``: the launch resolved the standing one.
    moved_panel: dict[str, Any] | None = None
    timestamp: str = Field(default_factory=utcnow_iso)
