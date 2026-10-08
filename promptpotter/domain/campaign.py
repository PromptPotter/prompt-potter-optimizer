"""``Campaign`` — one declared effort, holding a root cycle plus fork/diag descendants FLAT under ``cycles/``. Two
``new`` calls on an unchanged declaration share the root cycle id and origin score, then diverge from round 1."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import ConfigDict, Field

from promptpotter.domain.bench import BenchPasses, DatasetSplit
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import stable_hash
from promptpotter.domain.run_records import WallClock
from promptpotter.domain.spend import CeilingMeter
from promptpotter.domain.strict_model import StrictModel


class Treatment(StrictModel):
    """Which optimizer ran, as it decides its behaviour: two runs are one treatment only where
    ``digest`` agrees. No measurement key reads it, so an arm two treatments propose replays free."""

    model_config = ConfigDict(frozen=True)

    optimizer: str
    version: str
    # Per llm node, what shapes its call — the values each round stamps and a resume diverges on.
    prompt_hashes: dict[str, str]
    # Per member node, its validated knobs: an edit is a new treatment, and a policy diff to resume.
    knobs: dict[str, dict[str, Any]]
    # The code deciding what its prompts say (`OptimizerRuntime.source_digest`).
    source: str

    @property
    def digest(self) -> str:
        return stable_hash(self.model_dump(mode="json"))


class Instrument(StrictModel):
    """What graded a bench headline: two are one quantity only where every field agrees."""

    model_config = ConfigDict(frozen=True)

    dataset_name: str
    # The bank's rows, order-independent: two banks re-cut under one name share ids, not content.
    dataset_hash: str | None
    split: DatasetSplit | None
    # A digest of the held-out ids `bank_partition.json` names — what the split and its seed drew.
    bench_rows: str
    # The run's grader, as `ScorerSetup.scorer_id` names it.
    scorer_id: str
    # node -> model at the origin: the target every selection's bench pass ran through.
    models: dict[str, str]
    # The origin's content hash (`Campaign.root_content_hash`): its prompt, framing, node params
    # and the search rows, as `build_origin_cycle_id` folds them.
    origin: str


def bench_instrument(
    *,
    dataset_name: str,
    dataset_hash: str | None,
    split: DatasetSplit | None,
    bench_ids: Iterable[int],
    scorer_id: str,
    origin_params: Mapping[str, Any] | None,
    origin: str,
) -> Instrument:
    held_out = ",".join(str(i) for i in sorted(bench_ids))
    return Instrument(
        dataset_name=dataset_name,
        dataset_hash=dataset_hash,
        split=split,
        bench_rows=hashlib.sha256(held_out.encode()).hexdigest()[:12],
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


class HeadToHeadRecord(StrictModel):
    """``head_to_heads/{id}.json``: one declared comparison — the instrument every arm is graded
    under and the budget each may spend. Its arms are the campaigns whose ``arm`` names it, and
    this is the one copy of the split and budget they run under: their snapshots carry neither."""

    model_config = ConfigDict(frozen=True)

    head_to_head_id: str
    created_at: str
    instrument: Instrument
    budget: ArmBudget


class ArmRequest(StrictModel):
    """A mint's ask to run as an arm: the head-to-head to join — declared by its first arm — and
    this arm's key in it."""

    model_config = ConfigDict(frozen=True)

    head_to_head_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    arm_key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class Arm(StrictModel):
    """Which declared head-to-head a campaign runs as an arm of, frozen at mint. Its presence is
    what makes the campaign CONTROLLED."""

    model_config = ConfigDict(frozen=True)

    head_to_head_id: str
    arm_key: str
    treatment_digest: str


def ceiling_meter(arm: Arm | None) -> CeilingMeter:
    return "bill" if arm is None else "search_incurred"


class Launch(StrictModel):
    """One launch of the campaign's line, and where its wall clock went across every cycle it ran."""

    model_config = ConfigDict(frozen=True)

    started_at: str
    finished_at: str
    clock: WallClock


class ArmCost(StrictModel):
    """What the campaign's line spent reaching its result: every cycle on it, every launch."""

    model_config = ConfigDict(frozen=True)

    # Calls that reached a provider; a replay reached no wire.
    calls: int
    launches: list[Launch]

    @property
    def worked_s(self) -> float | None:
        """Each launch's clock less its origin gate, a human's, and the time its cells were not
        allowed to spend; ``None`` where no launch has both endpoints."""
        worked = [
            max(0.0, run.clock.elapsed_s - run.clock.gate_s - (run.clock.unworked_s or 0.0))
            for run in self.launches
            if run.clock.elapsed_s is not None
        ]
        return sum(worked) if worked else None


class CampaignResult(StrictModel):
    """``campaigns/{id}/result.json``: the campaign's result as FACTS, rewritten by the cycle
    answering for its line at every launch end. The bench score is read off them, never stored."""

    model_config = ConfigDict(frozen=True)

    # The cycle answering for the line when this was written — the root, or where rebases led.
    cycle_id: str
    # `None` where nothing is held out, or before the origin's pass.
    bench: BenchPasses | None
    cost: ArmCost


class Campaign(StrictModel):
    """Frozen manifest — identity, config and operator VISIBILITY INTENT only, never run state: that
    is per-cycle, or the line's :class:`CampaignResult`."""

    model_config = ConfigDict(frozen=True)

    campaign_id: str
    dataset_name: str
    label: str = ""
    created_at: str
    root_cycle_id: str
    root_content_hash: str = ""
    # `None` only on an unstarted check-in, which has not chosen what it runs.
    treatment: Treatment | None = None
    arm: Arm | None = None
    backend_id: str = ""
    # Connector KIND, FROZEN at mint: a campaign OUTLIVES its dataset dir, so re-pointing a slug
    # must not re-kind a campaign that already measured under the old one.
    backend_type: str = ""
    owner_user_id: str = "default"
    # VISIBILITY only — the authoring phase is NOT here. `.runtime/checkin.flag` on the root cycle
    # owns it (`runtime_flags.py::is_checkin`), which is also what `derive_run_phase` serves.
    lifecycle_status: Literal["active", "archived", "deleted"] = "active"
    lifecycle_changed_at: str = ""
    lifecycle_reason: str = ""
    config: dict[str, Any] = Field(default_factory=dict)

    @property
    def root_hop(self) -> CycleHop:
        """This campaign's root cycle as the pair that addresses it. Re-pairing at a call site risks one campaign's id with
        another's root cycle — easy, because a content-addressed ``root_cycle_id`` is shared by siblings."""
        return CycleHop(campaign_id=self.campaign_id, cycle_id=self.root_cycle_id)


__all__ = [
    "Arm",
    "ArmBudget",
    "ArmCost",
    "ArmRequest",
    "Campaign",
    "CampaignResult",
    "HeadToHeadRecord",
    "Instrument",
    "Launch",
    "Treatment",
    "bench_instrument",
    "ceiling_meter",
]
