"""PipelineSchema — backend-agnostic LLM-pipeline description."""

import enum
import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import ConfigDict, Field, PrivateAttr

from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS
from promptpotter.domain.strict_model import StrictModel

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
# (no scalar widget exists, so no widget), and `build_l1_response_schema` (the emitted
# sub-schema, whose value space is the param's own, not the node's).
NESTED_PARAM_TYPES = frozenset({"object", "array"})

# The one nested param a campaign must UNLOCK before its L1 may emit it
# (`OptimizationConfig.schema_field_rename`): renaming a field on the optimizer's own
# output schema is the strongest lever and the only one that can break a parser. Named
# here, beside the other structural param constants, because two layers must agree on the
# literal without importing each other: `build_l1_response_schema` (drops it from the emitted
# schema when locked, so the LLM cannot emit a key that does not exist) and the
# `rebase_capability` directive (offers L2/L3 the unlock only where a node declares it).
SCHEMA_RENAME_PARAM = "output_schema_field_names"

# The core, always-on structured-output lever: rewrite the JSON-Schema `description`
# strings of a TARGET node's own output schema. A `description` is the only natural
# language inside the field-filling loop and no code reads it, so it is free to move on
# ANY node that declares an `output_schema` — unlike the field NAME (the wire + grading
# contract). Synthesized onto such nodes at parse time (`pipeline_parsing.py`), keyed by
# that node's own fields; emitted by `build_l1_response_schema`; folded into the wire schema
# at `OptSearchPoint.to_job_search_point`. See `docs/concepts/structured-output.md`.
SCHEMA_DESCRIPTIONS_PARAM = "output_schema_descriptions"


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


class PipelineDependency(StrictModel):
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

    model_config = ConfigDict(frozen=True)

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


class ObservationMapping(StrictModel):
    """Maps one trace observation field to a pipeline_data key."""

    model_config = ConfigDict(frozen=True)

    pipeline_key: str
    output_field: str | None = None
    is_llm: bool = False


class NodeOutputSchema(StrictModel):
    """Resolved output schema for a TARGET pipeline node — the structured output the
    backend node produces, parsed from ``GET /pipeline``.

    This is the ``output_schema`` the word belongs to. NOT the optimizer's own
    response schema (``validators/l1_strict.py::build_l1_response_schema``), which
    describes what ``l1_generate`` returns. The L4 levers named ``output_schema_*``
    act on the optimizer side; the target-side axis is spec-only today.
    """

    model_config = ConfigDict(frozen=True)

    fields: list[str] = Field(default_factory=list)
    field_descriptions: dict[str, str] = Field(default_factory=dict)
    json_schema: dict[str, Any] = Field(default_factory=dict)


class NodePromptInfo(StrictModel):
    """Describes the prompt a node accepts — its presence marks the node as
    prompt-bearing (the injection point for the candidate prompt) and names the
    template variables. The input-side companion to :class:`NodeOutputSchema`."""

    # `extra="ignore"`: the backend owns this sub-object's vocabulary and describes itself
    # to humans there (`family`, `description`); PP reads only `template_variables`.
    model_config = ConfigDict(frozen=True, extra="ignore")

    template_variables: list[str] = Field(default_factory=list)


class PipelineViewNode(StrictModel):
    """Webapp pipeline-graph node."""

    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    kind: str = ""  # "io" | "llm" | "tool" | "retriever" | "cache" | "measurement" | "phase"


class PipelineViewEdge(StrictModel):
    """Webapp pipeline-graph edge."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    kind: str = "forward"  # "forward" | "loop" | "directive" | "escalate"
    label: str = ""


class PipelineView(StrictModel):
    """Webapp-facing graph projection — ``datasets/_optimizer/pipeline.json::view`` or derived."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    nodes: list[PipelineViewNode] = Field(default_factory=list)
    edges: list[PipelineViewEdge] = Field(default_factory=list)


class PipelineNode(StrictModel):
    """One node in a pipeline (target or optimizer)."""

    model_config = ConfigDict(frozen=True)

    name: str
    wire_type: str = ""
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
        """Whether this node makes an LLM call PER ITS OBSERVATION MAPPINGS. Narrow
        on purpose: this is the "the dataset must declare a per-node ``model``" signal
        (`config.py::_require_owned_models`). An in-process meta-prompt node (L4) runs
        an LLM but carries no owned model — its model is the install-global optimizer
        config — so it is deliberately NOT ``is_llm`` and stays exempt from that check."""
        return any(m.is_llm for m in self.observation_mappings)

    @property
    def runs_llm(self) -> bool:
        """Whether this node runs an LLM call at all — the BROAD signal, the union of
        the ``is_llm`` mapping, a ``generation`` wire/langfuse type (the backend's
        explicit LLM marker), and the ``llm_only`` sentinel. This is the "could this
        node carry the model axis" predicate: pp-self's ``meta_prompt`` nodes are
        ``generation`` LLM calls with no per-node model, so ``is_llm`` is False for
        them but ``runs_llm`` is True. Model-axis carrier selection reads THIS, not the
        narrower ``is_llm`` — otherwise a self-optimization pipeline resolves no carrier
        and the model axis silently never engages."""
        return (
            self.name == "llm_only"
            or self.is_llm
            or self.wire_type == "generation"
            or self.langfuse_type == "generation"
        )


class NodeConfigParam(StrictModel):
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

    model_config = ConfigDict(frozen=True)

    key: str
    value: Any = None
    kind: str  # "model" | "enum" | "number" | "bool" | "string"
    options: list[str] = Field(default_factory=list)
    description: str = ""
    optimizer_locked: bool = False
    optimizer_tunable: bool = False


class NodeSearchNarrowing(StrictModel):
    """A campaign's per-node narrowing of the dataset-declared optimizer search
    space — the per-campaign search-space lever beside ``exclude_nodes`` (whole
    node); model/provider are always locked. The dataset's
    ``pipeline.json`` declares the MAXIMUM tunable surface; a campaign may only
    SUBSET it, frozen into the ``Campaign`` snapshot at mint and applied by
    :meth:`PipelineSchema.narrow`.

    ``param_keys`` is the tunable config subset the optimizer may move (``None`` =
    inherit the node's full declared set; prompt-decomposition fields stay tunable
    regardless — the prompt is always evolved). ``param_allowed_values`` narrows a
    param's enum to a subset of the dataset's allowed set."""

    model_config = ConfigDict(frozen=True)

    param_keys: list[str] | None = None
    param_allowed_values: dict[str, list[str]] = Field(default_factory=dict)


class PipelineSchema(StrictModel):
    """Frozen, backend-agnostic pipeline description; SoT for identity at campaign start."""

    model_config = ConfigDict(frozen=True)

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

    def active_steps_excluding(self, exclude: Iterable[str]) -> list[str]:
        """Node names surviving the campaign's ``exclude_nodes``, in pipeline order.

        The complement `filter_to_steps` consumes: callers hold a drop-list but the
        canonical projection takes a keep-list, so this owns the one inversion."""
        dropped = set(exclude)
        return [n for n in self.active_steps if n not in dropped]

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

    def node_config_schema(self) -> dict[str, list[NodeConfigParam]]:
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
        model/provider are ALWAYS ``optimizer_locked`` (operator-owned axes the
        optimizer never searches) — a cap-holding operator may still edit them on a
        fork, which taints the branch babysat (ADR-0005 §4).

        A param declared ``object``/``array`` (:data:`NESTED_PARAM_TYPES`) carries no
        widget and is skipped: the three widget kinds are all scalar, and a nested
        value in a text box is a corrupt edit waiting to happen. These params are
        optimizer-only (L4's ``layout`` / ``output_schema_descriptions``); the operator
        reads them in the searchpoint diff, not here.

        When NO node declares ``model`` natively (an install-global-model pipeline —
        pp-self's meta-prompt nodes run the shared optimizer model, own none) the model
        row is SYNTHESIZED onto the carrier from ``available_models`` so the operator's
        fork-model-steer has somewhere to land (without it the steer panel showed
        "no configurable params").
        """
        # A model row is synthesized on the carrier only when no node OWNS a model —
        # otherwise the native row (justlogic's `llm_only.model`) is authoritative.
        model_declared = any("model" in (n.param_keys | set(n.current_config)) for n in self.nodes)
        model_carrier = None if model_declared else self._model_carrier()
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
                # Tunable = the optimizer may MOVE this param. model/provider are
                # operator-owned axes the optimizer never searches (always locked);
                # every other param is tunable iff the node advertises it in
                # `param_keys`. A config-only key (in current_config, not param_keys)
                # is fixed.
                tunable = False if key in PARAM_FORBIDDEN_KEYS else key in n.param_keys
                params.append(
                    NodeConfigParam(
                        key=key,
                        value=n.current_config.get(key),
                        kind=kind,
                        options=options,
                        description=n.param_descriptions.get(key, ""),
                        optimizer_locked=key in PARAM_FORBIDDEN_KEYS,
                        optimizer_tunable=tunable,
                    )
                )
            if n.name == model_carrier and self.available_models:
                # Synthesized carrier model row: optimizer-locked (never searched) yet
                # operator-editable on a fork — the seed overlay outranks the dataset,
                # same posture as a native model row.
                params.append(
                    NodeConfigParam(
                        key="model",
                        value=n.current_config.get("model"),
                        kind="model",
                        options=list(self.available_models),
                        description="Optimizer model for this node — install-global by "
                        "default, operator-steerable on a fork.",
                        optimizer_locked=True,
                        optimizer_tunable=False,
                    )
                )
            out[n.name] = params
        return out

    def _model_carrier(self) -> str | None:
        """The single node the model axis rides — the first LLM-running node in
        declaration order (:attr:`PipelineNode.runs_llm`). One carrier, not per-node, so
        an outer L4 search evolves ONE inner-optimizer model fanned across every node
        (`resolve_node_override` does the fan). Read by both :meth:`node_param_keys`
        (optimizer-tunable axis) and :meth:`node_config_schema` (operator-editable row),
        so the two never target different nodes."""
        return next((s.name for s in self.nodes if s.runs_llm), None)

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

    def narrow(self, narrowing: dict[str, NodeSearchNarrowing] | None) -> "PipelineSchema":
        """Return a copy with each node's optimizer search space narrowed to the
        campaign's per-node subset (the search-space lever beside
        ``exclude_nodes``; model/provider are always locked).

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

    def node_param_keys(self) -> dict[str, set[str]]:
        """Optimizer-tunable param keys per node — the SINGLE surface the param
        catalogue, the L1 output schema, and `validate_overrides` all derive from.

        `PARAM_FORBIDDEN_KEYS` (`model`/`provider`) and `SCHEMA_OWNED_FIELDS`
        (`output_schema` + schema registry identity) are stripped UNCONDITIONALLY.
        Model/provider are operator-owned — set via the dataset overlay or a
        cap-gated fork edit — never an optimizer axis; a file's stale `param_keys`
        listing of them is inert. The schema is the pipeline's structural wire
        contract, not a tunable; an L1 variant that mutated it would break the
        backend. Stripping here keeps `build_l1_response_schema` from declaring
        them, so the LLM can't emit a key the schema omits.
        """
        out: dict[str, set[str]] = {}
        for step in self.nodes:
            keys = set(step.param_keys) - PARAM_FORBIDDEN_KEYS - SCHEMA_OWNED_FIELDS
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
