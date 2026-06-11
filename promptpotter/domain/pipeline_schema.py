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

# Structured-output schema fields owned by the output-schema view — excluded from
# the lock-editor config surface the same way prompt fields are. The schema is one
# concept shown ONCE as the "Structured output" tree (`NodeOutputSchemaView`):
# `output_schema` is its content, `schema_family`/`schema_version` its registry
# identity. Surfacing them ALSO as config chips duplicates the structured output.
_SCHEMA_OWNED_FIELDS = frozenset({"output_schema", "schema_family", "schema_version"})


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
    who may still set it on a steered fork via the seed overlay. `optimizer_tunable`
    is the inverse permission the lock editor renders: whether the optimizer may
    currently MOVE this param (it sits in the node's `param_keys`, model gated by
    the campaign-wide strict flag). A config-only key is not tunable; a node whose
    every param is non-tunable is optimizer-fixed (origin-locked)."""

    model_config = {"frozen": True}

    key: str
    value: Any = None
    kind: str  # "model" | "enum" | "number" | "bool" | "string"
    options: list[str] = Field(default_factory=list)
    description: str = ""
    optimizer_locked: bool = False
    optimizer_tunable: bool = False


class NodeSearchNarrowing(BaseModel):
    """A campaign's per-node narrowing of the dataset-declared optimizer search
    space — the third per-campaign search-space lever beside ``exclude_nodes``
    (whole node) and ``forbidden_axes_strict`` (model/provider). The dataset's
    ``pipeline.json`` declares the MAXIMUM tunable surface; a campaign may only
    SUBSET it, frozen into the ``Campaign`` snapshot at mint and applied by
    :meth:`PipelineSchema.narrow`.

    ``param_keys`` is the tunable config subset the optimizer may move (``None`` =
    inherit the node's full declared set; prompt-decomposition fields stay tunable
    regardless — the prompt is always evolved). ``param_allowed_values`` narrows a
    param's enum to a subset of the dataset's allowed set."""

    model_config = {"frozen": True}

    param_keys: list[str] | None = None
    param_allowed_values: dict[str, list[str]] = Field(default_factory=dict)


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
        """``{"steps": [...]}`` sparse scaffold — backend merges per-node config defaults.

        This is the WIRE base only. The origin cycle id no longer derives from it:
        ``build_origin_cycle_id`` hashes the overlay-merged ``session.pipeline_params``
        (connector config included), so the cycle id and the measurement key agree."""
        return {"steps": list(self.active_steps)}

    def node_config_schema(self, forbidden_strict: bool) -> dict[str, list[NodeConfigParam]]:
        """The operator-editable config surface per node — the FULL config the
        node carries (the UNION of ``param_keys`` and the node's actual
        ``current_config``) except the prompt-decomposition fields (the prompt
        editor owns those) and the structured-output schema fields (the
        ``NodeOutputSchemaView`` tree owns ``output_schema`` / ``schema_family`` /
        ``schema_version`` — see ``_SCHEMA_OWNED_FIELDS``). Config-only keys not advertised as tunable (e.g.
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
            for key in sorted(
                (n.param_keys | set(n.current_config)) - _PROMPT_OWNED_FIELDS - _SCHEMA_OWNED_FIELDS
            ):
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
                # Tunable = the optimizer may MOVE this param. model/provider ride
                # the campaign-wide strict flag (forbidden when locked); every other
                # param is tunable iff the node advertises it in `param_keys`. A
                # config-only key (in current_config, not param_keys) is fixed.
                tunable = (
                    not forbidden_strict if key in PARAM_FORBIDDEN_KEYS else key in n.param_keys
                )
                params.append(
                    NodeConfigParam(
                        key=key,
                        value=n.current_config.get(key),
                        kind=kind,
                        options=options,
                        description=n.param_descriptions.get(key, ""),
                        optimizer_locked=key in locked,
                        optimizer_tunable=tunable,
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

    def narrow(self, narrowing: dict[str, NodeSearchNarrowing] | None) -> "PipelineSchema":
        """Return a copy with each node's optimizer search space narrowed to the
        campaign's per-node subset (the third search-space lever beside
        :meth:`exclude` and ``forbidden_axes_strict``).

        A node's ``param_keys`` intersect the campaign subset (``None`` = inherit
        the full set); prompt-decomposition fields are always kept tunable (the
        prompt is owned by the prompt editor, not the lock editor, and is always
        evolved). ``param_allowed_values`` intersect the narrowed enum. Empty
        narrowing is a no-op; a node absent from the mapping is unchanged."""
        if not narrowing:
            return self
        new_nodes: list[PipelineNode] = []
        for n in self.nodes:
            nv = narrowing.get(n.name)
            if nv is None:
                new_nodes.append(n)
                continue
            if nv.param_keys is None:
                keys = n.param_keys
            else:
                kept = set(nv.param_keys)
                keys = (n.param_keys & kept) | (n.param_keys & _PROMPT_OWNED_FIELDS)
            allowed = dict(n.param_allowed_values)
            for param, vals in nv.param_allowed_values.items():
                subset = set(vals)
                allowed[param] = (
                    [v for v in allowed[param] if v in subset] if param in allowed else list(vals)
                )
            new_nodes.append(
                n.model_copy(update={"param_keys": keys, "param_allowed_values": allowed})
            )
        return self.model_copy(update={"nodes": new_nodes})

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

    def node_param_keys(self, forbidden_strict: bool = True) -> dict[str, set[str]]:
        """Optimizer-tunable param keys per node — the SINGLE surface the param
        catalogue, the L1 output schema, and `validate_overrides` all derive from.

        Model/provider optimizability is POLICY, not a per-dataset declaration: a
        file's stale `param_keys` listing of them is inert. When `forbidden_strict`
        (default) the model axis is ABSENT — the optimizer never sees it. When the
        campaign explicitly unlocks AND the dataset advertises `available_models`,
        the `model` axis is synthesized onto each LLM node (value space =
        `available_models`); that is the sole ablation lever. `provider` is never
        an optimizer axis.
        """
        out: dict[str, set[str]] = {}
        for step in self.nodes:
            keys = set(step.param_keys) - PARAM_FORBIDDEN_KEYS
            if not forbidden_strict and self.available_models and step.is_llm:
                keys = keys | {"model"}
            if keys:
                out[step.name] = keys
        return out

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
