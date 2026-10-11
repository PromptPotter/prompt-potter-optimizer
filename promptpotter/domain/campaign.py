"""Two ``new`` calls on an unchanged declaration share the root cycle id and origin score, then diverge."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal, TypedDict, Unpack

from pydantic import ConfigDict, Field

from promptpotter.domain.bench import DatasetSplit
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.paired_reading import instrument_of
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.run_records import WallClock
from promptpotter.domain.spend import CeilingMeter
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.hashing import stable_hash


class Treatment(StrictModel):
    """Two runs are one treatment only where ``digest`` agrees; no measurement key reads it."""

    model_config = ConfigDict(frozen=True)

    optimizer: str
    version: str
    # Per llm node; a resume diverges on these.
    prompt_hashes: dict[str, str]
    # Per member node: an edit is a new treatment, and a policy diff to resume.
    knobs: dict[str, dict[str, Any]]
    source: str

    @property
    def digest(self) -> str:
        return stable_hash(self.model_dump(mode="json"))


class BenchSet(StrictModel):
    """The bench set and grader behind a headline; two compare only where every field agrees."""

    model_config = ConfigDict(frozen=True)

    # `instrument_of`: the id a pair refuses two members across.
    instrument_id: str
    # Order-independent content: two banks re-cut under one name share ids, not this.
    dataset_hash: str
    split: DatasetSplit | None
    bench_rows: str
    scorer_id: str
    models: dict[str, str]
    origin: str


def bench_set_of(
    *,
    dataset_name: str,
    dataset_hash: str,
    split: DatasetSplit | None,
    bench_ids: Iterable[int],
    scorer_id: str,
    origin_params: Mapping[str, Any] | None,
    origin: str,
) -> BenchSet:
    return BenchSet(
        instrument_id=instrument_of(dataset_name, origin_params),
        dataset_hash=dataset_hash,
        split=split,
        bench_rows=stable_hash([str(i) for i in sorted(bench_ids)]),
        scorer_id=scorer_id,
        models={
            node: str(cfg["model"])
            for node, cfg in node_config_items(dict(origin_params or {}))
            if "model" in cfg
        },
        origin=origin,
    )


class ArmBudget(StrictModel):
    """What each arm of a head-to-head may spend, equal by declaration."""

    model_config = ConfigDict(frozen=True)

    # The SEARCH's incurred USD, replays priced; the bench pass is metered beside it.
    usd: float | None
    max_rounds: int | None
    determinism: dict[str, Any] | None
    # The stopping rule, so no arm outlasts another on a stall the other was stopped for.
    lives: dict[str, int] | None
    convergence_patience: int | None


class HeadToHeadRecord(StrictModel):
    """``head_to_heads/{id}.json``: the ONE copy of the split and budget its arms run under."""

    model_config = ConfigDict(frozen=True)

    head_to_head_id: str
    created_at: str
    bench_set: BenchSet
    budget: ArmBudget


class ArmRequest(StrictModel):
    model_config = ConfigDict(frozen=True)

    head_to_head_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    arm_key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class Arm(StrictModel):
    """The head-to-head a campaign is an arm of, frozen at mint; its presence makes it controlled."""

    model_config = ConfigDict(frozen=True)

    head_to_head_id: str
    arm_key: str
    treatment_digest: str


def ceiling_meter(arm: Arm | None) -> CeilingMeter:
    return "bill" if arm is None else "search_incurred"


def comparison_line(arm: Arm | None) -> str:
    if arm is None:
        return "not controlled — optimizes with every measurement and steer"
    return f"controlled — arm {arm.arm_key} of head-to-head {arm.head_to_head_id}"


class Launch(StrictModel):
    model_config = ConfigDict(frozen=True)

    started_at: str
    finished_at: str
    clock: WallClock


class ArmCost(StrictModel):
    model_config = ConfigDict(frozen=True)

    # Calls that reached a provider; a replay reached no wire.
    calls: int
    launches: list[Launch]

    @property
    def worked_s(self) -> float | None:
        """Less each launch's origin gate, a human's, and the time its cells were not allowed to spend."""
        worked = [
            max(0.0, run.clock.elapsed_s - run.clock.gate_s - (run.clock.unworked_s or 0.0))
            for run in self.launches
            if run.clock.elapsed_s is not None
        ]
        return sum(worked) if worked else None


class CampaignResult(StrictModel):
    """``campaigns/{id}/result.json``, rewritten at every launch end by the cycle holding the line."""

    model_config = ConfigDict(frozen=True)

    # The cycle answering for the line when this was written — the root, or where rebases led.
    cycle_id: str
    cost: ArmCost


type LifecycleStatus = Literal["active", "archived", "deleted"]
LIFECYCLE_STATUS_LABELS: dict[LifecycleStatus, str] = {
    "active": "Active",
    "archived": "Archived",
    "deleted": "Deleted",
}
# `checkin` narrows `active`, asked of the root cycle's flag.
type LifecycleFilter = Literal["active", "archived", "deleted", "checkin", "all"]


class CampaignEdit(TypedDict, total=False):
    label: str
    dataset_name: str
    lifecycle_status: LifecycleStatus
    lifecycle_changed_at: str
    lifecycle_reason: str
    root_content_hash: str
    treatment: dict[str, Any]
    backend_id: str
    backend_url: str
    backend_type: str
    config: dict[str, Any]


class Campaign(StrictModel):
    """Identity, config and visibility INTENT only, never run state; no measurement rewrites ``config``."""

    model_config = ConfigDict(frozen=True)

    campaign_id: str
    dataset_name: str
    label: str = ""
    created_at: str
    root_cycle_id: str
    # Both `None` only on an unstarted check-in, which has not chosen what it runs.
    root_content_hash: str | None = None
    treatment: Treatment | None = None
    arm: Arm | None = None
    backend_id: str = ""
    # Every later launch of any of its cycles runs here. Empty on an unstarted check-in.
    backend_url: str = ""
    # FROZEN at mint: a campaign outlives its dataset dir, so re-pointing a slug must not re-kind it.
    backend_type: str = ""
    owner_user_id: str = "default"
    # VISIBILITY only: the authoring phase is the root cycle's ledger's (`runtime_flags.py::is_checkin`).
    lifecycle_status: LifecycleStatus = "active"
    lifecycle_changed_at: str = ""
    lifecycle_reason: str = ""
    config: dict[str, Any] = Field(default_factory=dict)

    @property
    def root_hop(self) -> CycleHop:
        """Re-pairing at a call site risks another campaign's root: siblings share a ``root_cycle_id``."""
        return CycleHop(campaign_id=self.campaign_id, cycle_id=self.root_cycle_id)

    def edited(self, **changes: Unpack[CampaignEdit]) -> Campaign:
        return Campaign.model_validate({**self.model_dump(mode="json"), **changes})

    @property
    def origin_id(self) -> str:
        """A campaign still authoring its origin is an origin of its own, never grouped with the unstamped."""
        return self.root_content_hash or self.campaign_id


__all__ = [
    "Arm",
    "ArmBudget",
    "ArmCost",
    "ArmRequest",
    "BenchSet",
    "Campaign",
    "CampaignEdit",
    "CampaignResult",
    "HeadToHeadRecord",
    "Launch",
    "LifecycleFilter",
    "LifecycleStatus",
    "Treatment",
    "bench_set_of",
    "ceiling_meter",
    "comparison_line",
]
