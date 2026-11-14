from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from pydantic import Field

from promptpotter.application import optimizers
from promptpotter.application.knobs import ConfigNodeKnobs, node_knobs
from promptpotter.application.optimizer_manifest import (
    SelectedOptimizer,
    running_prompt,
    select_optimizer,
)
from promptpotter.application.optimizers.nodes import KnobDraw, MintFacts
from promptpotter.domain.campaign_config import OptimizerChoice
from promptpotter.domain.cell_terms import CellTerm
from promptpotter.domain.pipeline_manifest import prompt_key
from promptpotter.domain.pipeline_schema import (
    CapabilityMenu,
    KnobLayers,
    NestedPipelineRef,
    NodeConfigParam,
    NodeOutputSchema,
    NodeReach,
    PipelineSchema,
    PipelineView,
    reach_map,
)
from promptpotter.domain.pool import Access, Pool, PoolEntry, PoolOffer, PoolSource, kept, offers
from promptpotter.domain.refusal import Refusal
from promptpotter.domain.said import Tone
from promptpotter.domain.search_point import SHOTS_KEY
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.llm.capabilities import resolve_schema_menu
from promptpotter.infrastructure.store.dataset_access import campaign_defaults
from promptpotter.shared.plugin_registry import BUILT_IN
from promptpotter.shared.worded import Worded

if TYPE_CHECKING:
    from promptpotter.application.optimizers.nodes import Initializer


class KnobRef(StrictModel):
    """One knob of one optimizer node, written to `optimization.nodes.{node}.config.{knob}`."""

    node: str = Field(description="The manifest node that owns the knob")
    knob: str = Field(description="The knob's name under `nodes.{node}.config`")


class PoolDraw(StrictModel):
    """The nodes that read a pool under one knob: one knob, one access, however many readers."""

    switch: KnobRef = Field(description="The knob that sets `access`")
    access: Access = Field(
        description="How they draw under this config: `off` not at all, `open` a window to "
        "suggest from, `closed` an entry's id only"
    )
    nodes: list[str] = Field(
        description="Every node reading the pool as this knob sets it, in walk order; potter's "
        "critique, L2 and L3 read it as `l1_generate` is shown it, to lay out what L1 sees"
    )


class PoolFill(StrictModel):
    """One part of a candidate a pool's entries in play reach: the place a surface draws the pool."""

    part: str = Field(description="A prompt field a target prompt node opens to search, or `shots`")
    entries: int = Field(description="How many entries in play land there")
    most: int | None = Field(
        description="The most one candidate carries there: the shots any member writing them may "
        "attach; null for a field, whose text a candidate takes or adapts"
    )


class PackOffer(StrictModel):
    """One block pack this account reads, as a pool's `packs` knob names it."""

    name: str = Field(description="The value `packs` holds for it")
    entries: int = Field(
        description="The pool entries it holds that this run admits, one per part a snippet suits"
    )
    on: bool = Field(description="Whether `packs` names it under this config")


class PoolRow(StrictModel):
    pool: str = Field(description="The pool's state kind, e.g. `block_pool`")
    source: PoolSource = Field(description="Where its entries come from")
    initializer: str = Field(description="The run-init node writing it once, before round 0")
    active: list[str] = Field(description="The initializer's `active` paths: the entries in play")
    offered: list[PoolOffer] = Field(
        description="Every path `active` may name — `*` first, each leading run of an entry's "
        "group before what it holds, each entry id — with what it reads as and the entries it "
        "keeps; a demo pool's are the split its Start draws, none before that split is known"
    )
    drawn_by: list[PoolDraw]
    fills: list[PoolFill] = Field(
        description="The candidate parts its entries in play reach under this config, in render "
        "order, shots last: empty where no member draws it or nothing is in play, and then a "
        "surface draws the pool nowhere"
    )
    packs: list[PackOffer] = Field(
        description="The block packs it may draw from, the install's and the account's, in name "
        "order; none for a pool no pack feeds or no member draws, and none before the facts a "
        "Start mints against are known"
    )
    refused: list[Refusal] = Field(
        description="The packs it would offer but refuses, each with why and its pack in "
        "`details.pack`, served beside `packs`: one bad pack never hides the rest"
    )


class OptimizerNodePrompt(StrictModel):
    """The prompt an optimizer node runs under a config, before any L4 override."""

    fields: dict[str, str] = Field(description="Every prompt field by name, its edits laid on")
    edited: list[str] = Field(
        description="The fields this config sets over the template, each written to "
        "`optimization.nodes.{node}.config.{field}`"
    )
    version: str = Field(description="The `prompt_version` the node's config names")
    versions_declared: int = Field(
        description="How many versions the manifest declares for the node's prompt family — a "
        "surface showing this one says so where there are more"
    )


class ProposerRow(StrictModel):
    node: str = Field(description="The manifest node making the arms")
    makes: str = Field(description="What its arms are, in plain words: the manifest node's `makes`")


class OptimizerSetup(StrictModel):
    """What the selected optimizer runs under a config, derived once for check-in and campaign:
    its graph, knobs, prompts and pools as that config selects them."""

    view: PipelineView | None = Field(
        description="The graph topology — the same shape a campaign pipeline serves"
    )
    measurement_node: str = Field(
        description="The node of `view` that runs the measurement — where a campaign's pipeline "
        "nests under this graph, as each `nests[].node` names it per campaign"
    )
    node_output_schema: dict[str, NodeOutputSchema | None]
    model_capabilities: CapabilityMenu = Field(
        description="Optimizer-LOCKED is not unpriced: the model is fixed, but which effort rungs "
        "it accepts and what a round costs are the facts every other node's rows need too"
    )
    main: list[KnobRef] = Field(description="The knobs a setup surface leads with, in order")
    knobs: list[ConfigNodeKnobs] = Field(
        description="Every member taking a knob, in declared order, but a pool initializer: its "
        "knobs are its `pools` row"
    )
    prompts: dict[str, OptimizerNodePrompt] = Field(
        description="By node, each node running a prompt; a node naming no `prompt_family` has "
        "no entry"
    )
    arms_per_round: int = Field(description="Arms each round tests")
    proposers: list[ProposerRow] = Field(
        description="The nodes making each round's arms under this config, in walk order: one "
        "that opens or adds arms and adds none here is off, and absent"
    )
    pools: list[PoolRow] = Field(description="What the members draw from")
    node_config_schema: dict[str, list[NodeConfigParam]] = Field(
        description="Per optimizer node, its typed config rows at this config — an llm node's "
        "call config and prompt fields, a member's knobs: what its pipeline graph and node detail "
        "draw"
    )
    reach: dict[str, NodeReach] = Field(
        description="Where the search reaches per optimizer node at this config, summed off "
        "`node_config_schema`"
    )


class CampaignPipelineResponse(StrictModel):
    """One campaign's pipeline at one searchpoint — the body of ``GET /campaigns/{id}/pipeline``.
    The peer of ``resolve_dataset`` one question up: that seam answers which bytes are on disk,
    this one which values a campaign runs (``architecture.md`` § Two resolution seams)."""

    campaign_id: str
    cycle_id: str
    dataset_name: str
    connector: str = Field(
        description="The connector the course runs on, as `dataset.yaml::pipeline.backend_type` "
        "names it: the id `GET /backends` lists"
    )
    self_optimization: bool = Field(
        description="This campaign optimizes the optimizer itself (L4): its connector is "
        "`promptpotter`, and one measured row is a whole inner campaign, so it has no per-sample "
        "data of its own"
    )
    optimizer: str = Field(
        description="The optimizer manifest the addressed course runs — the one answer a surface "
        "reads which optimizer's graph, knobs and analytics apply by, a check-in's draft included"
    )
    optimizer_setup: OptimizerSetup = Field(
        description="That optimizer as the addressed course runs it — the manifest's knobs under "
        "the campaign's and the cycle seed's overlays, its arms and its pools"
    )
    params: dict[str, Any] = Field(
        description="Resolved config as the engine holds it — the bytes each arm of a round file "
        "carries as `resolved_params`, which makes that field this endpoint's check"
    )
    node_config_schema: dict[str, list[NodeConfigParam]]
    view: PipelineView | None
    node_output_schema: dict[str, NodeOutputSchema | None]
    model_capabilities: CapabilityMenu = Field(
        description="Keyed by the provider and model each node's rows carry AT this searchpoint"
    )
    reach: dict[str, NodeReach]
    nests: list[NestedPipelineRef] = Field(
        description="The inner pipelines this chain nests, one per dataset its cells measure — "
        "the L4 drill-in, on this read"
    )
    is_single_node: bool
    cell_terms: list[CellTerm] = Field(
        description="The per-cell terms this campaign's connector adds to `CELL_TERM_META`'s core "
        "ones, each a key of the connector's cell report a criterion, dial or lens may name"
    )


SteerOutside = Literal["babysat", "refused"]
SteerVerdict = Literal["clean", SteerOutside]


class SteerOutsideInfo(NamedTuple):
    title: str
    detail: str
    tone: Tone


STEER_OUTSIDE_INFO: Worded[SteerOutside, SteerOutsideInfo] = Worded(
    {
        "babysat": SteerOutsideInfo(
            "This steer leaves what the campaign permits, so the branch goes grade C.",
            "Steering to it marks this branch — and every round after it — operator-babysat: excluded "
            "from clean comparison, origin reuse, and the L4 rollup. The measurement is still "
            "recorded; it just isn't a clean one. Pick a permitted model to keep a clean branch.",
            "warn",
        ),
        "refused": SteerOutsideInfo(
            "This steer leaves what the campaign permits.",
            "Steering outside it is a babysit act this account may not take. Pick a permitted model.",
            "danger",
        ),
    }
)


class ForkPreviewResponse(StrictModel):
    """What `POST /commands/fork-cycle` would decide about this steer, asked without forking."""

    steer: SteerVerdict = Field(
        description=(
            "`clean` where the overlay stays inside what each node permits: its recorded schema's "
            "value spaces, the dataset's optimizer blocks among them, under the campaign's narrowing. "
            "Outside it — a responder never sanctioned, or a cost lever no permitted set can "
            "sanction — the fork is the babysit act: `babysat` for a caller holding "
            "`campaign.babysit` (the branch is graded C), `refused` for any other."
        )
    )
    permitted: dict[str, list[str]] = Field(
        description="Each node's permitted models, the set `steer` is decided against"
    )


class DatasetPipelineResponse(StrictModel):
    """One dataset's pipeline as DECLARED — the body of ``GET /datasets/{name}/pipeline``. Topology
    only: the level a ``nests`` pointer names, which no campaign has to have run."""

    connector: str = Field(
        description="The connector the definition declares, as `CampaignPipelineResponse.connector` names it"
    )
    view: PipelineView | None
    node_config_schema: dict[str, list[NodeConfigParam]] = Field(
        description="Every param each node carries, valueless — what `reach` is summed over"
    )
    reach: dict[str, NodeReach]
    nests: list[NestedPipeline] = Field(
        description="The pipelines this one nests in turn, one per dataset its cells measure, "
        "each read whole down to its innermost: the only wire naming them before a cell has spawned"
    )


class NestedPipeline(NestedPipelineRef):
    pipeline: DatasetPipelineResponse | None = Field(description="Null where it did not resolve")
    unread: str | None = Field(
        description="Why `pipeline` is null: the typed refusal, or that it nests a dataset above it"
    )


class OptimizerEntry(StrictModel):
    """One optimizer this install can run, as a picker offers it."""

    name: str = Field(description="The manifest name, as `optimization.optimizer` names it")
    version: str = Field(description="The manifest's own version")
    paper: str | None = Field(
        description="The citation a paper preset reproduces; its declared knob values are that "
        "paper's configuration. Null for an optimizer reproducing none"
    )
    origin: str | None = Field(
        description="The installed package that registered it, as `<distribution>: <entry point>`; "
        "null for one shipped with PromptPotter"
    )


class OptimizerRoster(StrictModel):
    """The optimizers this install can run, the default first: one per manifest the registry holds."""

    default: str = Field(description="What a campaign naming no `optimization.optimizer` runs")
    optimizers: list[OptimizerEntry] = Field(description="The roster, the default first")


def _pool_draws(selected: SelectedOptimizer, pool: type[Pool]) -> list[PoolDraw]:
    grouped: dict[tuple[str, str], PoolDraw] = {}
    for member in selected.members:
        if not isinstance(draw := member.draws.get(pool), KnobDraw):
            continue
        ref = KnobRef(node=selected.knob_owner(member, draw.knobs), knob=draw.knob)
        held = grouped.setdefault(
            (ref.node, ref.knob),
            PoolDraw(switch=ref, access=selected.access(member, pool), nodes=[]),
        )
        held.nodes.append(member.name)
    return list(grouped.values())


def _pool_fills(
    selected: SelectedOptimizer, initializer: Initializer, held: list[PoolEntry], opened: list[str]
) -> list[PoolFill]:
    in_play = set(kept(initializer.active(selected), ((e.id, e.group) for e in held)))
    landed = Counter(initializer.pool.part(e) for e in held if e.id in in_play)
    return [
        PoolFill(
            part=part,
            entries=landed[part],
            most=selected.shot_cap if part == SHOTS_KEY else None,
        )
        for part in opened
        if landed[part]
    ]


def _pack_offers(
    selected: SelectedOptimizer, initializer: Initializer, mint: MintFacts | None
) -> tuple[list[PackOffer], list[Refusal]]:
    if (named := initializer.packs(selected)) is None or mint is None:
        return [], []
    offered = [
        PackOffer(
            name=name,
            entries=len(mint.shelf.entries([name], admits=mint.admits)),
            on=name in named,
        )
        for name in mint.shelf.names()
    ]
    return offered, mint.shelf.refusals()


def _opened_parts(target: PipelineSchema) -> list[str]:
    """Each part an edit may write, in the target nodes' own field order, then the shots."""
    nodes = target.prompt_nodes()
    fields = [f for node in nodes for f in node.open_prompt_fields]
    return list(dict.fromkeys([*fields, *([SHOTS_KEY] if nodes else [])]))


def _node_prompts(selected: SelectedOptimizer) -> dict[str, OptimizerNodePrompt]:
    out: dict[str, OptimizerNodePrompt] = {}
    declared = list(selected.manifest.resolved_prompts)
    for node in selected.llm_nodes:
        config = selected.node_config(node)
        if prompt_key(config) is None:
            continue
        family = config.prompt_family
        out[node] = OptimizerNodePrompt(
            fields=running_prompt(node, config, selected.manifest).model_dump(),
            edited=list(config.prompt_edits()),
            version=str(config.prompt_version or ""),
            versions_declared=sum(k.partition("/")[0] == family for k in declared),
        )
    return out


def _pool_row(
    selected: SelectedOptimizer,
    initializer: Initializer,
    mint: MintFacts | None,
    opened: list[str],
) -> PoolRow:
    held = initializer.held(selected, mint)
    drawn = any(initializer.pool in selected.draws(member) for member in selected.members)
    packs, refused = _pack_offers(selected, initializer, mint) if drawn else ([], [])
    return PoolRow(
        pool=initializer.pool.kind,
        source=initializer.pool.source,
        initializer=initializer.name,
        active=initializer.active(selected),
        offered=offers(held),
        drawn_by=_pool_draws(selected, initializer.pool),
        fills=_pool_fills(selected, initializer, held, opened) if drawn else [],
        packs=packs,
        refused=refused,
    )


def optimizer_setup(
    selected: SelectedOptimizer,
    mint: MintFacts | None,
    target: PipelineSchema,
    workspace: Path,
    knob_layers: KnobLayers,
) -> OptimizerSetup:
    opened = _opened_parts(target)
    schema = selected.schema
    # An optimizer node's config is no steer: nothing binds its rows.
    rows = schema.node_config_schema(selected.steered_axes, steer_bound=False, sources=knob_layers)
    return OptimizerSetup(
        view=schema.view,
        measurement_node=selected.plan.measurement,
        node_output_schema=schema.node_output_schemas(),
        # Per tenant: a hand-authored catalogue override lives in their workspace.
        model_capabilities=resolve_schema_menu(schema, workspace=workspace),
        main=[
            KnobRef(node=node, knob=knob)
            for node, _, knob in (ref.partition(".") for ref in selected.manifest.main)
        ],
        knobs=node_knobs(selected),
        prompts=_node_prompts(selected),
        arms_per_round=selected.arms_per_round,
        proposers=[ProposerRow(node=p.name, makes=selected.makes(p)) for p in selected.proposing],
        pools=[_pool_row(selected, i, mint, opened) for i in selected.plan.initializers],
        node_config_schema=rows,
        reach=reach_map(rows),
    )


def optimizer_roster() -> OptimizerRoster:
    default = campaign_defaults()["optimization"]["optimizer"]
    names = sorted(optimizers.MANIFESTS.table, key=lambda n: (n != default, n))
    origins = optimizers.MANIFESTS.origins
    entries = []
    for name in names:
        selected = select_optimizer(OptimizerChoice(optimizer=name))
        origin = None if origins[name] == BUILT_IN else origins[name]
        entries.append(
            OptimizerEntry(name=name, version=selected.version, paper=selected.paper, origin=origin)
        )
    return OptimizerRoster(default=default, optimizers=entries)
