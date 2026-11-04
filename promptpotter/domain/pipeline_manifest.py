from __future__ import annotations

from collections.abc import Collection, Mapping
from functools import cached_property
from typing import Any, Literal, Self

from pydantic import Field, TypeAdapter, model_validator

from promptpotter.domain.opt_search_point import PROMPT_FIELDS, OptimizerPromptTemplate
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.search_point import CALL_CONFIG_KEYS, NodeConfig
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.hashing import shapes_optimizer_prompt


@shapes_optimizer_prompt
def resolved_key(family: object, version: object) -> str | None:
    """The `resolved_prompts` / `resolved_schemas` key a family and version name."""
    if not family:
        return None
    return f"{family}/{version}" if version is not None else str(family)


@shapes_optimizer_prompt
def prompt_key(config: NodeConfig) -> str | None:
    """The `resolved_prompts` key a node's config names; ``None`` where it runs no prompt."""
    return resolved_key(config.prompt_family, config.prompt_version)


def typed_config(cfg: Mapping[str, object]) -> NodeConfig:
    """*cfg*'s call keys typed, and on a node running a prompt the prompt fields it sets over the
    template; any other key is its member's knob, which the member's own model types."""
    calls = {k: v for k, v in cfg.items() if k in CALL_CONFIG_KEYS}
    runs_prompt = resolved_key(calls.get("prompt_family"), calls.get("prompt_version"))
    if not (edits := {f: cfg[f] for f in PROMPT_FIELDS if f in cfg} if runs_prompt else {}):
        return NodeConfig.model_validate(calls)
    OptimizerPromptTemplate.model_validate(edits)
    return NodeConfig.model_validate({**calls, "prompt_edits": edits})


def laid_config(
    owner: str, node: str, cfg: Mapping[str, object], *, knobs: Collection[str]
) -> NodeConfig:
    """`typed_config`, refusing a key that is neither a call key, one of *knobs* nor, on a node
    running a prompt, a prompt field (`optimization.nodes.{node}.config.{field}`)."""
    config = typed_config(cfg)
    prompt = set(PROMPT_FIELDS) if prompt_key(config) is not None else set()
    if unknown := sorted(set(cfg) - CALL_CONFIG_KEYS - set(knobs) - prompt):
        raise ValueError(
            f"{owner} node {node!r} names {unknown} in its config, which is neither a call "
            f"setting ({sorted(CALL_CONFIG_KEYS)}), one of its knobs ({sorted(knobs)}) nor, on a "
            f"node running a prompt, a prompt field ({list(PROMPT_FIELDS)})."
        )
    return config


class ManifestNode(FrozenModel):
    type: NodeKind
    description: str | None = None
    # What a proposer's arms are, in plain words, as a setup surface names them; prose, so no treatment reads it.
    makes: str | None = None
    # The backend-shaped menu of the node's searchable params (`pipeline_parsing.py`).
    optimizer: dict[str, Any] | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class PipelineManifest(FrozenModel):
    """A `pipeline.yaml` typed at load — an optimizer's, or the bench's check-in."""

    name: str
    version: str
    paper: str | None = None
    available_models: list[str]
    nodes: dict[str, ManifestNode]
    pipelines: dict[str, list[str]]
    # `node.knob` refs a setup surface leads with, in order; presentation, so no treatment reads it.
    main: list[str]
    # Whether `initial_population` is a round of its own or heads round 1 (`RoundPlan.walk`).
    initial_population_walks: Literal["alone", "before_default"] | None = None
    resolved_prompts: dict[str, OptimizerPromptTemplate] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _call_keys_typed(self) -> Self:
        # Typed as selection types them, so a landing refuses a mistyped call key (`max_tokens:
        # 8000.0`) where a launch would; a knob is its member's to type, at selection.
        for name, node in self.nodes.items():
            if node.type is NodeKind.LLM:
                try:
                    typed_config(node.config)
                except ValueError as exc:
                    raise ValueError(f"llm node {name!r}: {exc}") from exc
        return self

    def laid_over(self, overlay: Mapping[str, Mapping[str, Any]]) -> PipelineManifest:
        nodes = {
            name: node.model_copy(update={"config": {**node.config, **overlay[name]}})
            if name in overlay
            else node
            for name, node in self.nodes.items()
        }
        return self.model_copy(update={"nodes": nodes})


class ManifestSwap(FrozenModel):
    # Takes the swapped node's place in every walk, as manifest `of` declares and configures it.
    node: str
    of: str


class ManifestSwitch(FrozenModel):
    """A switch: the whole manifest ``extends`` names, each node ``swap`` names replaced by another manifest's node of its type."""

    name: str
    version: str
    paper: str | None = None
    extends: str
    swap: dict[str, ManifestSwap]
    # Its own, never what it extends: a swapped node takes its knobs with it.
    main: list[str]


AUTHORED_MANIFEST: TypeAdapter[PipelineManifest | ManifestSwitch] = TypeAdapter(
    PipelineManifest | ManifestSwitch
)
CHECKIN_NODE = "checkin"


class CheckinPipelineManifest(PipelineManifest):
    """The bench's check-in `pipeline.yaml`: one llm node, `checkin`, whose config holds no knob."""

    @cached_property
    def config(self) -> NodeConfig:
        return laid_config("the check-in", CHECKIN_NODE, self.nodes[CHECKIN_NODE].config, knobs=())

    @model_validator(mode="after")
    def _one_checkin(self) -> Self:
        # A second node is one no reader of `config` would run.
        node = self.nodes.get(CHECKIN_NODE)
        if list(self.nodes) != [CHECKIN_NODE] or node is None or node.type is not NodeKind.LLM:
            raise ValueError(
                f"the check-in manifest declares {sorted(self.nodes)}; it declares one llm node, "
                f"{CHECKIN_NODE!r}"
            )
        _ = self.config
        return self


CHECKIN_MANIFEST = TypeAdapter(CheckinPipelineManifest)
