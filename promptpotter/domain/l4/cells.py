"""What an L4 cell runs, in the outer's terms: each cell's optimizer as its chain, its levers and its
measurement plan, never the application's selection of it."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from pydantic import Field

from promptpotter.domain.cells import Seat
from promptpotter.domain.l4.proxies import INNER_RESULT_KEY
from promptpotter.domain.optimizer_template import OptimizerPromptTemplate
from promptpotter.domain.pipeline_schema import (
    DEFAULT_PIPELINE,
    ManifestNodeOverlay,
    NodeKind,
    NodeRole,
    PipelineNode,
)
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.hashing import stable_hash


class InnerTaskSpec(FrozenModel):
    inner_dataset: str
    optimizer_treatment: str
    run_seed: int
    n_samples: int
    n_samples_origin: int | None = None
    n_samples_bench: int
    n_rounds: int
    nodes: dict[str, ManifestNodeOverlay] = Field(default_factory=dict)
    depth_nodes: dict[str, ManifestNodeOverlay] = Field(default_factory=dict)
    inner_model: str | None = None
    inner_provider: str | None = None
    inner_route_order: list[str] | None = None
    inner_optimizer_temperature: float | None = None
    # What measures it beyond the spec: its dataset's node config, campaign identity and estimator.
    instrument: str

    def key(self) -> str:
        """The ONE cell key, depth inside: a deeper cell is another measurand, so no banked row replays it."""
        return stable_hash(self.model_dump(mode="json"))

    def continuation(self, edits: Mapping[str, Any]) -> str:
        """The key less `CONTINUED`, and an arm's *edits*: SUBTRACTED, so a new spec field defaults
        to identity (a needless fork wastes a run, a wrong continue corrupts one)."""
        kept = {k: v for k, v in self.model_dump(mode="json").items() if k not in CONTINUED}
        return stable_hash([kept, edits], length=6)


# How much evidence a continued campaign buys on top of its banked rounds, never what it measures.
CONTINUED: frozenset[str] = frozenset({"n_rounds", "depth_nodes", "n_samples_bench"})
assert set(InnerTaskSpec.model_fields) >= CONTINUED

# `problem_description` and `answer_format` are absent: they carry the injection slots and the output contract.
OUTER_PROMPT_FIELDS: tuple[str, ...] = ("persona", "task_intent", "instruction", "thinking_style")
assert set(OUTER_PROMPT_FIELDS) <= set(OptimizerPromptTemplate.RENDER_ORDER)

_OUTER_KINDS = frozenset({NodeKind.LLM, NodeKind.GATEWAY})


@dataclass(frozen=True)
class CellOptimizer:
    """A cell's optimizer as the outer reads it: its graph, each node's lever, its measurement
    plan and each llm node's base prompt (check-in first, overlay edits laid)."""

    name: str
    declared: tuple[PipelineNode, ...]
    pipelines: Mapping[str, Sequence[str]]
    # What an arm's edit to each node lands on; the knobs are left out, being how much a cell buys.
    levers: Mapping[str, Mapping[str, Any]]
    # Each llm node's lever types the outer may write.
    outer_levers: Mapping[str, Mapping[str, str]]
    # Each escalation the controller fires, by the walks it fires on.
    fired_on: Mapping[str, Sequence[str]]
    # The gateway its measurement plan nests the cell's dataset under.
    measurement: str
    templates: Mapping[str, OptimizerPromptTemplate]

    @cached_property
    def chain(self) -> list[str]:
        kinds = {node.name: node.kind for node in self.declared}
        return [n for n in self.pipelines[DEFAULT_PIPELINE] if kinds.get(n) in _OUTER_KINDS]

    @property
    def terminal(self) -> str:
        """The chain's last llm node; only config-less measurement nodes follow, so only a FULL match replays a row."""
        kinds = {node.name: node.kind for node in self.declared}
        return [n for n in self.chain if kinds[n] is NodeKind.LLM][-1]


@dataclass(frozen=True)
class InnerCell:
    campaign_config: Mapping[str, Any]
    pipeline: Mapping[str, Any] | None
    optimizer: CellOptimizer
    # The digest without the panel's depth, so a deepened cell continues its campaign.
    treatment: str


@dataclass(frozen=True)
class InnerCells:
    """Each cell runs its own optimizer; the outer graph is the union of their nodes, in panel order,
    so a cell added to the panel appends nodes and moves no banked cell's chain."""

    # As declared, in panel order.
    cells: tuple[Seat, ...]
    by_dataset: Mapping[str, InnerCell]
    # Each seat's spec, by id: a pinned seat carries no depth, so it is read here, never off the pin.
    specs: Mapping[str, InnerTaskSpec]
    # Read once per resolve: the outer identity and every cell's campaign id key on this one value.
    estimator: str

    def held(self, cell: Seat) -> InnerCell:
        return self.by_dataset[self.spec(cell).inner_dataset]

    def spec(self, cell: Seat) -> InnerTaskSpec:
        return self.specs[cell.id]

    @property
    def optimizers(self) -> list[CellOptimizer]:
        """One per manifest, in panel order: a manifest's graph is its name's."""
        seen: dict[str, CellOptimizer] = {}
        for cell in self.cells:
            inner = self.held(cell).optimizer
            seen.setdefault(inner.name, inner)
        return list(seen.values())

    @cached_property
    def owners(self) -> Mapping[str, CellOptimizer]:
        """The manifest each outer node is a lever of, over EVERY cell. RAISES where two cells run one
        node differently, overlay included: an arm's edit to it would mean a different prompt in each."""
        held: dict[str, CellOptimizer] = {}
        for cell in self.cells:
            inner = self.held(cell).optimizer
            for node in inner.declared:
                first = held.setdefault(node.name, inner)
                if inner.levers[node.name] != first.levers[node.name]:
                    raise ValueError(
                        f"cell {cell.id!r} runs node {node.name!r} of {inner.name!r} unlike an "
                        f"earlier cell's {first.name!r}: one outer node is one lever, so align "
                        "their campaign overlays or split the panel"
                    )
        return held

    def template(self, name: str) -> OptimizerPromptTemplate | None:
        """*name*'s base prompt in the manifest whose lever it is; ``None`` where no cell runs it."""
        owner = self.owners.get(name)
        return None if owner is None else owner.templates.get(name)

    @property
    def chain(self) -> list[str]:
        return list(dict.fromkeys(n for inner in self.optimizers for n in inner.chain))

    def terminal(self, cell: Seat) -> str:
        return self.held(cell).optimizer.terminal

    def own(self, cell: Seat, arm: Mapping[str, Any]) -> dict[str, Any]:
        """The arm's edit on the nodes this cell's optimizer runs: another manifest's node is not its lever."""
        nodes = {node.name for node in self.held(cell).optimizer.declared}
        return {node: config for node, config in arm.items() if node in nodes}

    def pipeline(self) -> dict[str, Any]:
        """RAISES where two manifests declare one node differently (``owners``)."""
        observed = [{"pipeline_key": INNER_RESULT_KEY}]
        terminals = {inner.terminal for inner in self.optimizers}
        owners = self.owners
        nests: dict[str, list[str]] = {}
        for name, cell in sorted(self.by_dataset.items()):
            nests.setdefault(cell.optimizer.measurement, []).append(name)
        nodes: dict[str, dict[str, Any]] = {}
        walks: dict[str, list[str]] = {}
        fired: dict[str, list[str]] = {}
        for inner in self.optimizers:
            fired |= {n: list(on) for n, on in inner.fired_on.items() if owners[n] is inner}
            for node in inner.declared:
                if owners[node.name] is not inner:
                    continue
                if node.kind is NodeKind.GATEWAY:
                    declared: dict[str, Any] = {
                        "type": node.kind.value,
                        "config": {},
                        "nested_datasets": nests.get(node.name, []),
                    }
                elif node.kind is NodeKind.LLM:
                    levers = dict(inner.outer_levers.get(node.name, {}))
                    terminal = node.name in terminals
                    declared = {
                        "type": node.kind.value,
                        "node_role": NodeRole.RANKER.value if terminal else "",
                        "config": {},
                        "optimizer": {
                            "param_keys": [*OUTER_PROMPT_FIELDS, *levers],
                            "param_types": levers,
                            "observation_name": node.name,
                            "observation_mappings": observed if terminal else [],
                        },
                    }
                else:
                    continue
                nodes[node.name] = declared
            for name, steps in inner.pipelines.items():
                walk = walks.setdefault(name, [])
                walk += [n for n in steps if n in nodes and n not in walk]
        kept = {k: v for k, v in walks.items() if v}
        # An escalation no walk lists runs only as its controller fires it: the outer runs it so.
        for name, on in fired.items():
            if name in nodes and (on := [w for w in on if w in kept]):
                nodes[name]["fired_on"] = on
        return {"nodes": nodes, "pipelines": kept}
