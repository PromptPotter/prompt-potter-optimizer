"""PipelineSchema — backend-agnostic LLM-pipeline description."""

import enum
import hashlib
import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS

# Prompt-decomposition fields the prompt editor owns — excluded from the
# operator-editable node-config surface (they live in `param_keys` too, but the
# steer panel edits them through `PromptFieldsEditor`, not the config widgets).
_PROMPT_OWNED_FIELDS = frozenset(PROMPT_STRING_FIELDS) | {"few_shot_examples", "plan"}

# Structured-output schema fields owned by the output-schema view. TWO reasons to
# fence them off:
#   1. Display — excluded from the operator lock-editor config surface the same way
#      prompt fields are: the schema is one concept shown ONCE as the "Structured
#      output" tree (`NodeOutputSchemaView`) — `output_schema` is its content,
#      `schema_family`/`schema_version` its registry identity. Surfacing them ALSO
#      as config chips duplicates the structured output.
#   2. Optimizer — they are STRUCTURAL, not tunables: the output schema is the
#      pipeline's wire contract, and a mutated `output_schema` (e.g. an L1 variant
#      that replaced it with a raw `{{format_string}}` template) breaks the backend
#      ("Schema must contain 'properties'"). So `node_param_keys` strips them from
#      the optimizer's emittable surface — UNCONDITIONALLY, unlike model/provider
#      (`PARAM_FORBIDDEN_KEYS`), which have an ablation unlock; the schema never does.
#      `answer_field` is the same structural contract by another name: it names WHICH
#      slot of `output_schema` carries the answer, so a mutated one makes the executor
#      destructure the wrong field and grade every sample against reasoning prose.
SCHEMA_OWNED_FIELDS = frozenset(
    {"output_schema", "schema_family", "schema_version", "answer_field"}
)

# The `param_types` values that make a param NESTED — a container the optimizer edits
# one level deep rather than a scalar it replaces. Naming them once keeps the three
# readers agreeing: `apply_node_overlay` (merge one level, siblings survive — `array`
# replaces wholesale, since a merged ordering is meaningless), `node_config_schema`
# (no scalar widget exists, so no widget), and `build_l1_output_schema` (the emitted
# sub-schema, whose value space is the param's own, not the node's).
NESTED_PARAM_TYPES = frozenset({"object", "array"})

# The one nested param a campaign must UNLOCK before its L1 may emit it
# (`OptimizationConfig.schema_field_rename`): renaming a field on the optimizer's own
# output schema is the strongest lever and the only one that can break a parser. Named
# here, beside the other structural param constants, because two layers must agree on the
# literal without importing each other: `build_l1_output_schema` (drops it from the emitted
# schema when locked, so the LLM cannot emit a key that does not exist) and the
# `rebase_capability` directive (offers L2/L3 the unlock only where a node declares it).
SCHEMA_RENAME_PARAM = "output_schema_field_names"


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


# The dependency kind a ``candidate_source`` node raises, and the file that
# fulfils it on disk. A candidate_source node ranks each query against a target
# library; without one the pool is just the answers already in the dataset (a
# degenerate pool). The library is the "4th required input" — beyond
# pipeline + dataset + origin — surfaced in the ingest UI, dropped in place, and
# committed alongside the per-pipeline origin as ``candidate_library.txt``.
CANDIDATE_LIBRARY = "candidate_library"
CANDIDATE_LIBRARY_FILE = "candidate_library.txt"


class PipelineDependency(BaseModel):
    """A categorical input a pipeline requires beyond (pipeline + dataset + origin),
    derived from its **node types** — not hardcoded per backend.

    A ``candidate_source`` node needs a target library to rank against; a future
    enricher might need a knowledge base. The requirement is read off the node
    taxonomy (``NodeType``), so a new connector declares one node type and gets
    detection for free. Surfaced in the check-in / ingest UI so the operator sees
    *which* input is missing and drops it in place — never a hidden bootstrap
    default. ``node`` names the node(s) it serves (comma-joined when one input feeds
    several); ``hint`` says how to fulfil it.
    """

    model_config = {"frozen": True}

    kind: str
    node: str
    title: str
    hint: str


def dependencies_from_node_types(
    node_type_by_name: Mapping[str, NodeType],
) -> tuple[PipelineDependency, ...]:
    """Derive a pipeline's required inputs from its node-type map (categorical).

    The single derivation both ingest (connector-declared ``node_types`` for the
    active steps) and a live :class:`PipelineSchema` share, so the two never drift.
    Today one arm: a pipeline with any ``CANDIDATE_SOURCE`` node raises ONE
    ``candidate_library`` dependency — every candidate_source node ranks against the
    same shared term index, so the library is one input, not one-per-node (hence the
    dependency is collapsed, ``node`` listing the nodes it serves). New
    node-type→input rules add an arm here, nowhere else.
    """
    deps: list[PipelineDependency] = []
    candidate_sources = sorted(
        name
        for name, node_type in node_type_by_name.items()
        if node_type == NodeType.CANDIDATE_SOURCE
    )
    if candidate_sources:
        served = ", ".join(candidate_sources)
        deps.append(
            PipelineDependency(
                kind=CANDIDATE_LIBRARY,
                node=served,
                title="Candidate library",
                hint=(
                    f"The candidate-source stage ({served}) ranks each query against a target "
                    "library. Drop the full target list (one entry per line, or a single-column "
                    "CSV/Excel) so ranking isn't limited to the answers already in your data."
                ),
            )
        )
    return tuple(deps)


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
    prompt-bearing (the injection point for the candidate prompt) and names the
    template variables. The input-side companion to :class:`NodeOutputSchema`."""

    model_config = {"frozen": True}

    template_variables: list[str] = Field(default_factory=list)


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
    def is_single_node(self) -> bool:
        """A bare one-node scoring pipeline (TermNorm ``llm_only``) — the model
        answers each query directly, with no retrieval / web search / ranking
        stage. The first-class predicate that replaces scattered
        ``len(active_steps) <= 1`` arithmetic and literal ``"llm_only"`` checks:
        the acute case for the lock invariant (locking the only node leaves the
        optimizer nothing to tune) and the redundant-node-row UI guard."""
        return len(self.nodes) == 1

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
        ``schema_version`` — see ``SCHEMA_OWNED_FIELDS``). Config-only keys not advertised as tunable (e.g.
        ``provider: groq``) bundle in too, so the operator sees the WHOLE node as
        one unit. Unlike :meth:`optimizer_locks`, model/provider are INCLUDED
        (the operator isn't the optimizer — the seed overlay outranks the
        dataset). Widget ``kind`` resolves from ``model`` (→ ``available_models``)
        → ``param_allowed_values`` (→ enum) → ``param_types`` (number/bool/string;
        ``param_types`` covers config-only keys too, see ``_infer_param_types``).
        ``forbidden_strict`` only sets the display-only ``optimizer_locked`` flag.

        A param declared ``object``/``array`` (:data:`NESTED_PARAM_TYPES`) carries no
        widget and is skipped: the three widget kinds are all scalar, and a nested
        value in a text box is a corrupt edit waiting to happen. These params are
        optimizer-only (L4's ``layout`` / ``output_schema_descriptions``); the operator
        reads them in the searchpoint diff, not here.
        """
        locked = PARAM_FORBIDDEN_KEYS if forbidden_strict else frozenset()
        out: dict[str, list[NodeConfigParam]] = {}
        for n in self.nodes:
            params: list[NodeConfigParam] = []
            for key in sorted(
                (n.param_keys | set(n.current_config)) - _PROMPT_OWNED_FIELDS - SCHEMA_OWNED_FIELDS
            ):
                if n.param_types.get(key) in NESTED_PARAM_TYPES:
                    continue
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

        `SCHEMA_OWNED_FIELDS` (`output_schema` + schema registry identity) are
        stripped UNCONDITIONALLY — they are the pipeline's structural wire contract,
        not tunables; an L1 variant that mutated `output_schema` would break the
        backend. Unlike model/provider there is no unlock: the schema is never an
        optimizer axis. Stripping here keeps `build_l1_output_schema` from declaring
        them, so the LLM can't emit a key the schema omits.
        """
        out: dict[str, set[str]] = {}
        for step in self.nodes:
            keys = set(step.param_keys) - PARAM_FORBIDDEN_KEYS - SCHEMA_OWNED_FIELDS
            if not forbidden_strict and self.available_models and step.is_llm:
                keys = keys | {"model"}
            if keys:
                out[step.name] = keys
        return out

    def prompt_node_names(self) -> list[str]:
        """Node names whose output is affected by the prompt — ``prompt_info`` set."""
        return [node.name for node in self.nodes if node.prompt_info is not None]


__all__ = [
    "CANDIDATE_LIBRARY",
    "CANDIDATE_LIBRARY_FILE",
    "NodeConfigParam",
    "NodeOutputSchema",
    "NodePromptInfo",
    "NodeType",
    "ObservationMapping",
    "PipelineDependency",
    "PipelineNode",
    "PipelineSchema",
    "PipelineView",
    "PipelineViewEdge",
    "PipelineViewNode",
    "dependencies_from_node_types",
    "stable_hash",
]
