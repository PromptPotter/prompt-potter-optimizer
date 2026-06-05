"""PipelineSchema — backend-agnostic LLM-pipeline description."""

import enum
import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS

# Prompt-decomposition fields the prompt editor owns — excluded from the
# operator-editable node-config surface (they live in `param_keys` too, but the
# steer panel edits them through `PromptFieldsEditor`, not the config widgets).
_PROMPT_OWNED_FIELDS = frozenset(PROMPT_STRING_FIELDS) | {"few_shot_examples", "plan"}


def stable_hash(value: Any) -> str:
    """Deterministic 16-char hex digest of an arbitrary JSON-able value."""
    blob = json.dumps(value, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


class NodeType(enum.StrEnum):
    """Pipeline node type classification (empty string = untyped)."""

    NONE = ""
    CANDIDATE_SOURCE = "candidate_source"
    RANKER = "ranker"
    ENRICHER = "enricher"
    CACHE = "cache"


class ObservationMapping(BaseModel):
    """Maps one trace observation field to a pipeline_data key."""

    model_config = {"frozen": True}

    pipeline_key: str
    output_field: str | None = None
    is_llm: bool = False


class NodeOutputSchema(BaseModel):
    """Resolved output schema for a pipeline node."""

    model_config = {"frozen": True}

    fields: list[str] = Field(default_factory=list)
    field_descriptions: dict[str, str] = Field(default_factory=dict)
    json_schema: dict[str, Any] = Field(default_factory=dict)


class NodePromptInfo(BaseModel):
    """Describes the prompt a node accepts — its presence marks the node as
    prompt-bearing (the injection point for the candidate prompt); its fields
    name the prompt family + template variables. The input-side companion to
    :class:`NodeOutputSchema`."""

    model_config = {"frozen": True}

    family: str = ""
    template_variables: list[str] = Field(default_factory=list)
    description: str = ""


class PipelineViewNode(BaseModel):
    """Webapp pipeline-graph node."""

    model_config = {"frozen": True}

    id: str
    label: str
    kind: str = ""  # "io" | "llm" | "tool" | "retriever" | "cache" | "measurement" | "phase"


class PipelineViewEdge(BaseModel):
    """Webapp pipeline-graph edge."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    kind: str = "forward"  # "forward" | "loop" | "directive" | "escalate"
    label: str = ""


class PipelineView(BaseModel):
    """Webapp-facing graph projection — ``datasets/_optimizer/pipeline.json::view`` or derived."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    nodes: list[PipelineViewNode] = Field(default_factory=list)
    edges: list[PipelineViewEdge] = Field(default_factory=list)


class PipelineNode(BaseModel):
    """One node in a pipeline (target or optimizer)."""

    model_config = {"frozen": True}

    name: str
    wire_type: str = ""
    short_circuit: bool = False
    node_type: NodeType = NodeType.NONE
    param_keys: set[str] = Field(default_factory=set)
    param_descriptions: dict[str, str] = Field(default_factory=dict)
    param_allowed_values: dict[str, list[str]] = Field(default_factory=dict)
    # JSON-schema type per param — drives structured-output constraint + validate_overrides
    # checks; without it, L1 may emit stringified numbers that break wire payloads.
    param_types: dict[str, str] = Field(default_factory=dict)
    observation_name: str | None = None
    observation_mappings: list[ObservationMapping] = Field(default_factory=list)
    langfuse_type: str = "span"  # "generation" | "tool" | "retriever" | "span"
    output_schema: NodeOutputSchema | None = None
    prompt_info: NodePromptInfo | None = None
    current_config: dict[str, Any] = Field(default_factory=dict)

    @property
    def output_keys(self) -> list[str]:
        """Pipeline_data keys this node writes (derived from observation_mappings)."""
        return [m.pipeline_key for m in self.observation_mappings]

    @property
    def is_llm(self) -> bool:
        """Whether this node makes an LLM call (any mapping has ``is_llm``)."""
        return any(m.is_llm for m in self.observation_mappings)


class NodeConfigParam(BaseModel):
    """One operator-editable config param of a node — the FULL config surface the
    operator-steer panel renders, NOT the optimizer-permutation subset
    (`optimizer_locks`). `kind` drives the input widget (`model`/`enum` → a
    select, `number`/`bool`/`string` → a typed input); `options` lists the
    choices for `model` (from `available_models`) and `enum` (from
    `param_allowed_values`). `optimizer_locked` flags a param the *optimizer* may
    not permute (model/provider under a strict campaign) — shown for the operator,
    who may still set it on a steered fork via the seed overlay."""

    model_config = {"frozen": True}

    key: str
    value: Any = None
    kind: str  # "model" | "enum" | "number" | "bool" | "string"
    options: list[str] = Field(default_factory=list)
    description: str = ""
    optimizer_locked: bool = False


class PipelineSchema(BaseModel):
    """Frozen, backend-agnostic pipeline description; SoT for identity at campaign start."""

    model_config = {"frozen": True}

    name: str = ""
    version: str = ""
    description: str = ""
    nodes: list[PipelineNode] = Field(default_factory=list)
    available_models: list[str] = Field(default_factory=list)
    view: PipelineView | None = None

    _node_map: dict[str, int] = PrivateAttr(default_factory=dict)
    _observation_keys: frozenset[str] = PrivateAttr(default_factory=frozenset)

    def model_post_init(self, __context: Any) -> None:
        self._node_map = {n.name: i for i, n in enumerate(self.nodes)}
        self._observation_keys = frozenset(
            m.pipeline_key
            for n in self.nodes
            if n.observation_name and n.observation_mappings
            for m in n.observation_mappings
        )

    @property
    def active_steps(self) -> tuple[str, ...]:
        """Node names in pipeline order."""
        return tuple(n.name for n in self.nodes)

    @property
    def observation_keys(self) -> frozenset[str]:
        """``pipeline_data`` keys written by all nodes' observation mappings."""
        return self._observation_keys

    def to_pipeline_params(self) -> dict[str, Any]:
        """``{"steps": [...]}`` sparse scaffold — backend merges per-node config defaults."""
        return {"steps": list(self.active_steps)}

    def node_config_schema(self, forbidden_strict: bool) -> dict[str, list[NodeConfigParam]]:
        """The operator-editable config surface per node — the FULL config the
        node carries (the UNION of ``param_keys`` and the node's actual
        ``current_config``) except the prompt-decomposition fields (the prompt
        editor owns those). Config-only keys not advertised as tunable (e.g.
        ``provider: groq``) bundle in too, so the operator sees the WHOLE node as
        one unit. Unlike :meth:`optimizer_locks`, model/provider are INCLUDED
        (the operator isn't the optimizer — the seed overlay outranks the
        dataset). Widget ``kind`` resolves from ``model`` (→ ``available_models``)
        → ``param_allowed_values`` (→ enum) → ``param_types`` (number/bool/string;
        ``param_types`` covers config-only keys too, see ``_infer_param_types``).
        ``forbidden_strict`` only sets the display-only ``optimizer_locked`` flag.
        """
        locked = PARAM_FORBIDDEN_KEYS if forbidden_strict else frozenset()
        out: dict[str, list[NodeConfigParam]] = {}
        for n in self.nodes:
            params: list[NodeConfigParam] = []
            for key in sorted((n.param_keys | set(n.current_config)) - _PROMPT_OWNED_FIELDS):
                if key == "model":
                    kind, options = "model", list(self.available_models)
                elif key in n.param_allowed_values:
                    kind, options = "enum", list(n.param_allowed_values[key])
                else:
                    t = n.param_types.get(key, "string")
                    kind = (
                        "number"
                        if t in ("number", "integer")
                        else "bool"
                        if t == "boolean"
                        else "string"
                    )
                    options = []
                params.append(
                    NodeConfigParam(
                        key=key,
                        value=n.current_config.get(key),
                        kind=kind,
                        options=options,
                        description=n.param_descriptions.get(key, ""),
                        optimizer_locked=key in locked,
                    )
                )
            out[n.name] = params
        return out

    def node_output_schemas(self) -> dict[str, NodeOutputSchema | None]:
        """Per-node structured-output contract (``fields`` / ``field_descriptions`` /
        ``json_schema``) keyed by node name — the read-only companion to
        :meth:`node_config_schema` so the steer panel can show the WHOLE node:
        model + params + prompt + the structured output it produces."""
        return {n.name: n.output_schema for n in self.nodes}

    def has_node(self, name: str) -> bool:
        return name in self._node_map

    def get_node(self, name: str) -> PipelineNode | None:
        idx = self._node_map.get(name)
        return self.nodes[idx] if idx is not None else None

    def filter_to_steps(self, steps: list[str]) -> "PipelineSchema":
        active = set(steps)
        return self.model_copy(
            update={"nodes": [n for n in self.nodes if n.name in active]},
        )

    def exclude(self, names: set[str] | None) -> "PipelineSchema":
        if not names:
            return self
        return self.model_copy(
            update={"nodes": [n for n in self.nodes if n.name not in names]},
        )

    def node_configs(self, pipeline_params: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        """Canonical SearchPoint identity: ordered ``[(node, config), ...]`` for hashing."""
        result: list[tuple[str, dict[str, Any]]] = []
        for node in self.nodes:
            cfg = pipeline_params.get(node.name, {})
            if not isinstance(cfg, dict):
                cfg = {}
            result.append((node.name, cfg))
        return result

    def sp_hash(self, pipeline_params: dict[str, Any]) -> str:
        configs = self.node_configs(pipeline_params)
        return stable_hash(configs) if configs else ""

    def node_param_keys(self) -> dict[str, set[str]]:
        return {step.name: step.param_keys for step in self.nodes if step.param_keys}

    def prompt_node_names(self) -> list[str]:
        """Node names whose output is affected by the prompt — ``prompt_info`` set."""
        return [node.name for node in self.nodes if node.prompt_info is not None]


__all__ = [
    "NodeConfigParam",
    "NodeOutputSchema",
    "NodePromptInfo",
    "NodeType",
    "ObservationMapping",
    "PipelineNode",
    "PipelineSchema",
    "PipelineView",
    "PipelineViewEdge",
    "PipelineViewNode",
    "stable_hash",
]
