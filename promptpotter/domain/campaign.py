"""Two ``new`` calls on an unchanged declaration share the root cycle id and origin score, then diverge."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal, TypedDict, Unpack, assert_never

from pydantic import ConfigDict, Field, field_validator

from promptpotter.domain.bench import DatasetSplit
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.paired_reading import CellSet, instrument_of
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import ParamSource
from promptpotter.domain.run_records import LaunchClockRecord
from promptpotter.domain.spend import Budget, CeilingMeter, money_axes_only
from promptpotter.domain.strict_model import FrozenModel, StrictModel
from promptpotter.domain.treatment import Treatment
from promptpotter.shared.hashing import stable_hash


class BenchSet(FrozenModel):
    """The bench set and grader behind a headline; two compare only where every field agrees."""

    # `instrument_of`: the id a pair refuses two members across.
    instrument_id: str
    dataset_hash: str
    split: DatasetSplit | None
    bench_rows: CellSet
    scorer_id: str
    models: dict[str, str]
    origin: str


def bench_set_of(
    *,
    dataset_hash: str,
    split: DatasetSplit | None,
    bench_rows: CellSet,
    scorer_id: str,
    origin_params: Mapping[str, Any] | None,
    origin: str,
) -> BenchSet:
    return BenchSet(
        instrument_id=instrument_of(dataset_hash, origin_params),
        dataset_hash=dataset_hash,
        split=split,
        bench_rows=bench_rows,
        scorer_id=scorer_id,
        models={
            node: str(cfg["model"])
            for node, cfg in node_config_items(dict(origin_params or {}))
            if "model" in cfg
        },
        origin=origin,
    )


class StoppingRule(FrozenModel):
    """What stops a run short of its budget, so no arm outlasts another on a stall the other was
    stopped for."""

    lives: dict[str, int] | None
    convergence_patience: int | None


class DeterminismClamp(StrictModel):
    """What a campaign PINS on every optimizer call — how the draw is made and which host makes
    it. Applied last, so it beats the node's file config and any per-call override alike."""

    temperature: float | None = Field(
        None,
        ge=0.0,
        le=2.0,
        description=(
            "Sampling temperature EVERY optimizer node runs at, overriding its own — including "
            "`l1_generate`'s `temperature`, the dominant run-to-run noise source. "
            "`None` (default) leaves each node the value its pipeline file declares."
        ),
    )
    seed: int | None = Field(
        None,
        description=(
            "Sampling seed every optimizer node sends, or draws its per-call seed from where it "
            "asks several things in one step, so concurrent asks stay distinct draws. Temperature "
            "0 pins the distribution and not the draw, so without a seed the provider is still "
            "free to sample differently on identical input. It rides `hash_call`, so two "
            "campaigns differing only here bank separate replies rather than serving one "
            "campaign's answer under the other's name. `None` (default) sends none."
        ),
    )
    route_order: list[str] | None = Field(
        None,
        description=(
            "The upstream HOSTS behind the gateway, in order, every optimizer call is routed "
            "to (the gateway's own `provider_name`s — read them off `served_by` in the ledger, "
            "never from a catalogue). Hosts of one model disagree systematically, so an "
            "unpinned route makes a measurement whose producer nothing can name. `None` "
            "(default) lets the gateway re-rank per call."
        ),
    )


class Arm(FrozenModel):
    """The head-to-head a campaign is an arm of, frozen at mint."""

    head_to_head_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    arm_key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class Optimize(FrozenModel):
    """Every measurement and steer on disk is admissible."""

    kind: Literal["optimize"] = "optimize"


class Compare(FrozenModel):
    """A run of *arm*, and every L4 cell spawned under it: it reads only what a fresh run would."""

    kind: Literal["compare"] = "compare"
    arm: Arm


# What a run is FOR (ADR-0009), declared once: every scope, stamp and meter derives from it.
Purpose = Annotated[Optimize | Compare, Field(discriminator="kind")]


class Environment(FrozenModel):
    """What the target runs on and is graded by (``target``: the set keys of its node models, host
    pins, chain, framing and grader), behind ``connector``, whose identity contributions derive
    from it and the dataset."""

    connector: str = Field(min_length=1)
    target: dict[str, Any]


class SharedPart(FrozenModel):
    """What every arm of a head-to-head declares alike: the record its arms join on."""

    dataset_hash: str
    run_seed: int | None
    # Only the loop keys `PINNED_LOOP_KEYS` names.
    loop: dict[str, Any]
    budget: Budget
    environment: Environment
    bench_defaults_pin: str


class RunDeclaration(FrozenModel):
    """What a campaign, an L4 seat and a head-to-head arm each ARE: every part is what a layer
    named, never a default restated, beside ``bench_defaults_pin``, the bench defaults it is a
    delta over as they stood when it was declared — its optimizer's are its campaign's ``Treatment``."""

    dataset_hash: str = Field(min_length=1)
    run_seed: int | None
    # The loop's own set keys, less the budget and the run seed.
    loop: dict[str, Any]
    # Only the axes that differ from the default.
    budget: Budget
    environment: Environment
    # Part of what an arm IS, so two arms of one content never share a run id.
    purpose: Purpose
    bench_defaults_pin: str = Field(min_length=1)

    @property
    def digest(self) -> str:
        return stable_hash(self.model_dump(mode="json"))

    def shared_part(self) -> SharedPart:
        return SharedPart(
            dataset_hash=self.dataset_hash,
            run_seed=self.run_seed,
            loop=_pinned(self.loop, PINNED_LOOP_KEYS),
            budget=self.budget,
            environment=self.environment,
            bench_defaults_pin=self.bench_defaults_pin,
        )


# The loop keys every arm of a head-to-head runs alike.
PINNED_LOOP_KEYS: dict[str, Any] = {
    "dataset_split": True,
    "bench_trigger": True,
    "optimization": {"lives": True, "convergence_patience": True, "determinism": True},
}


def _pinned(loop: Mapping[str, Any], keys: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, sub in keys.items():
        if key not in loop:
            continue
        value = loop[key]
        out[key] = _pinned(value, sub) if isinstance(sub, dict) else value
    return out


class RunsWithParam(StrictModel):
    """One setting a campaign's root course runs with, and the layer that chose it."""

    node: str
    key: str = Field(description="The config grid's own param key (`NodeConfigParam.key`)")
    value: Any
    source: ParamSource


class VendorModels(StrictModel):
    """The models of one vendor that a campaign's root course runs."""

    vendor: str = Field(
        description="Who TRAINED them: the namespace of the model id, or the bare id where it "
        "names none. Lowercased, routing suffix dropped."
    )
    models: list[str] = Field(
        description="Full ids, de-duplicated, routing suffix kept: it routes and bills, so it "
        "names a different run."
    )


class CampaignRunsWith(StrictModel):
    """What a campaign's root course runs with — the root's pipeline resolution, cut to settings."""

    params: list[RunsWithParam] = Field(
        description="Scalar settings in active-step order, `model` included; no prompt text"
    )
    vendors: list[VendorModels] = Field(
        description="Every model in `params`, grouped under its vendor in first-seen order"
    )
    optimizer: str = Field(description="The optimizer manifest the root course runs")
    budget_rounds: int | None = Field(
        description="The DECLARED `budget.rounds`, not the armed one: 0 = origin only, null = "
        "no rounds axis"
    )


class HeadToHeadRecord(FrozenModel):
    """``head_to_heads/{id}.json``: what its arms declare alike, and the origin each resolved to —
    an arm declaring another is refused at its mint."""

    head_to_head_id: str
    created_at: str
    origin: str
    shared: SharedPart
    # The tenant ruler's version at open: every arm reads it plus its own lines, never another arm's.
    ruler_version: int = Field(ge=0)


def ceiling_meter(purpose: Purpose) -> CeilingMeter:
    match purpose:
        case Optimize():
            return "bill"
        case Compare():
            return "search_incurred"
        case _:
            assert_never(purpose)


def comparison_line(purpose: Purpose) -> str:
    match purpose:
        case Optimize():
            return "not controlled — optimizes with every measurement and steer"
        case Compare(arm=arm):
            return f"controlled — arm {arm.arm_key} of head-to-head {arm.head_to_head_id}"
        case _:
            assert_never(purpose)


def worked_seconds(launches: Sequence[LaunchClockRecord]) -> float | None:
    """Less each launch's gates, a human's, and the time its cells were not allowed to spend."""
    worked = [
        max(0.0, run.clock.elapsed_s - run.clock.gate_s - (run.clock.unworked_s or 0.0))
        for run in launches
        if run.clock.elapsed_s is not None
    ]
    return sum(worked) if worked else None


type LifecycleStatus = Literal["active", "archived", "deleted"]
LIFECYCLE_STATUS_LABELS: dict[LifecycleStatus, str] = {
    "active": "Active",
    "archived": "Archived",
    "deleted": "Deleted",
}
type LifecycleFilter = Literal["active", "archived", "deleted", "all"]


class CampaignEdit(TypedDict, total=False):
    label: str
    dataset_name: str
    lifecycle_status: LifecycleStatus
    lifecycle_changed_at: str
    lifecycle_reason: str
    backend_id: str
    backend_url: str
    standing_budget: Budget


class CampaignOwner(FrozenModel):
    """Read off a manifest of ANY shape, so its owner can delete one this build cannot read whole."""

    # `extra="ignore"`: the rest of the manifest is the part whose shape may have moved.
    model_config = ConfigDict(extra="ignore")

    owner_user_id: str = "default"


class Campaign(CampaignOwner):
    """Identity, declaration and visibility INTENT only, never run state."""

    model_config = ConfigDict(extra="forbid")

    campaign_id: str
    dataset_name: str
    label: str = ""
    created_at: str
    root_cycle_id: str
    # The ORIGIN's content: two declarations on one origin share it, never their root.
    root_content_hash: str
    treatment: Treatment
    declaration: RunDeclaration
    backend_id: str = ""
    # Every later launch of any of its cycles runs here.
    backend_url: str = ""
    lifecycle_status: LifecycleStatus = "active"
    lifecycle_changed_at: str = ""
    lifecycle_reason: str = ""
    # The money axes an operator named (`set-limits`, a launch flag), laid over the declared budget.
    standing_budget: Budget = Budget()

    _money_axes = field_validator("standing_budget")(money_axes_only)

    @property
    def root_hop(self) -> CycleHop:
        """Re-pairing at a call site risks another campaign's root: siblings share a ``root_cycle_id``."""
        return CycleHop(campaign_id=self.campaign_id, cycle_id=self.root_cycle_id)

    @property
    def connector(self) -> str:
        """FROZEN at mint: a campaign outlives its dataset dir, so re-pointing a slug must not re-kind it."""
        return self.declaration.environment.connector

    def edited(self, **changes: Unpack[CampaignEdit]) -> Campaign:
        return Campaign.model_validate({**self.model_dump(mode="json"), **changes})

    @property
    def origin_id(self) -> str:
        return self.root_content_hash
