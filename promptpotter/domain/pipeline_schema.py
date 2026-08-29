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
    blob = json.dumps(value, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


class NodeType(enum.StrEnum):
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
    """Read off the node taxonomy, so a new connector declares one node type and gets detection
    for free. Surfaced to the operator as a missing input — never a hidden fabricated default."""

    model_config = ConfigDict(frozen=True)

    kind: str
    node: str
    title: str
    hint: str


def dependencies_from_node_types(
    node_type_by_name: Mapping[str, NodeType],
) -> tuple[PipelineDependency, ...]:
    """The single derivation both ingest and a live :class:`PipelineSchema` share, so the two never
    drift. New node-type→input rules add an arm here, nowhere else."""
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
    model_config = ConfigDict(frozen=True)

    pipeline_key: str
    output_field: str | None = None
    is_llm: bool = False


class NodeOutputSchema(StrictModel):
    """Resolved output schema for a TARGET pipeline node — the structured output the
    backend node produces, parsed from ``GET /pipeline``.

    This is the ``output_schema`` the word belongs to. NOT the optimizer's own
    response schema (``dispatch/l1_wire_schema.py::build_l1_response_schema``), which
    describes what ``l1_generate`` returns. The L4 levers named ``output_schema_*``
    act on the optimizer side; the target-side axis is spec-only today.
    """

    model_config = ConfigDict(frozen=True)

    fields: list[str] = Field(default_factory=list)
    field_descriptions: dict[str, str] = Field(default_factory=dict)
    json_schema: dict[str, Any] = Field(default_factory=dict)


class NodePromptInfo(StrictModel):
    """Its PRESENCE marks the node prompt-bearing — the injection point for the candidate prompt.
    The input-side companion to :class:`NodeOutputSchema`."""

    # `extra="ignore"`: the backend owns this sub-object's vocabulary and describes itself
    # to humans there (`family`, `description`); PP reads only `template_variables`.
    model_config = ConfigDict(frozen=True, extra="ignore")

    template_variables: list[str] = Field(default_factory=list)


class PipelineViewNode(StrictModel):
    """One node's place in the flow, as a tier and a rank rather than as pixels.

    Tier 0 is the chain a sample runs and tier n>0 is a node reached only by escalating n
    levels; rank is the tier-0 position it acts on. A renderer maps them to rows and
    columns.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    # Exactly what `pipeline_parsing.py::_derive_node_kind` can emit — a member here the
    # producer cannot produce is one the client styles and captions for nothing.
    kind: str = ""  # "io" | "llm" | "tool" | "retriever" | "cache" | "measurement"
    tier: int = 0
    rank: int = 0


class PipelineViewEdge(StrictModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    kind: str = "forward"  # "forward" | "loop" | "directive" | "escalate"


class PipelineView(StrictModel):
    """The webapp-facing graph projection, derived from a manifest's nodes and pipelines.

    No manifest declares one (:func:`pipeline_parsing.derive_pipeline_view` is the sole
    producer): a hand-written block is a second roster beside the one the engine runs.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    nodes: list[PipelineViewNode] = Field(default_factory=list)
    edges: list[PipelineViewEdge] = Field(default_factory=list)


class PipelineNode(StrictModel):
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
        return [m.pipeline_key for m in self.observation_mappings]

    @property
    def emits_ranking(self) -> bool:
        """Does this node put a ranked list on the wire — the "a sample can be scored off it" signal.
        Asked as a predicate rather than spelled as a set of names at each site, so a new ranking
        node type is admitted here and nowhere else instead of being skipped in silence."""
        return self.node_type in (NodeType.RANKER, NodeType.CANDIDATE_SOURCE)

    @property
    def is_llm(self) -> bool:
        """Narrow on purpose: this is the "the dataset must declare a per-node ``model``" signal. An
        in-process optimizer prompt node runs an LLM but owns no model, so it stays exempt."""
        return any(m.is_llm for m in self.observation_mappings)

    @property
    def runs_llm(self) -> bool:
        """The BROAD signal — mapping, ``generation`` wire type, or the ``llm_only`` sentinel. Model-axis
        carrier selection reads THIS: on ``is_llm`` a self-optimization pipeline resolves no carrier."""
        return (
            self.name == "llm_only"
            or self.is_llm
            or self.wire_type == "generation"
            or self.langfuse_type == "generation"
        )


class NodeConfigParam(StrictModel):
    """One param a node carries — the COMPLETE per-node list, which is what lets
    `optimizer_tunable` sum to the node's whole search space.

    `kind` names the surface that OWNS the param: `model`/`enum` → a select,
    `number`/`bool`/`string` → a typed input, `prompt` → the prompt editor's (a
    `PromptTemplate` decomposition field), `nested` → structured, no scalar widget
    (`NESTED_PARAM_TYPES`). Only the first five are config-editor rows; the last two
    are listed but not rendered. They are listed because dropping them made
    `every(!optimizer_tunable)` answer "optimizer-locked" for a node whose every
    axis is prose — do not re-filter this list at the source.

    `optimizer_locked` = never an optimizer axis (`PARAM_FORBIDDEN_KEYS`), though a
    cap-holding operator may still set it on a steered fork. `optimizer_tunable` =
    the optimizer may currently MOVE it (sits in `param_keys`). A config-only key is
    neither; a node with no tunable param is optimizer-fixed (origin-locked)."""

    model_config = ConfigDict(frozen=True)

    key: str
    value: Any = None
    kind: str  # "model" | "enum" | "number" | "bool" | "string" | "prompt" | "nested"
    options: list[str] = Field(default_factory=list)
    description: str = ""
    optimizer_locked: bool = False
    optimizer_tunable: bool = False


class NodeSearchNarrowing(StrictModel):
    """The dataset's ``pipeline.yaml`` declares the MAXIMUM tunable surface; a campaign may only
    SUBSET it. Prompt-decomposition fields stay tunable regardless — the prompt is always evolved."""

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
    # Every node the manifest DECLARES — a superset of `nodes`, which holds only the ones a
    # round runs. Identity stays on `nodes`: folding these into `sp_hash` re-keys every
    # banked measurement. Empty means "same as `nodes`"; read it through `config_nodes`.
    declared_nodes: list[PipelineNode] = Field(default_factory=list)
    available_models: list[str] = Field(default_factory=list)
    view: PipelineView | None = None

    _node_map: dict[str, "PipelineNode"] = PrivateAttr(default_factory=dict)
    _observation_keys: frozenset[str] = PrivateAttr(default_factory=frozenset)

    def model_post_init(self, __context: Any) -> None:
        # Indexed over DECLARED nodes: "is there a node called X" and "what type is its
        # param" are questions about the manifest, not about this round's chain — an
        # escalation node resolving to None merges its nested params shallow and loses
        # every sibling key.
        self._node_map = {n.name: n for n in (self.declared_nodes or self.nodes)}
        self._observation_keys = frozenset(
            m.pipeline_key
            for n in self.nodes
            if n.observation_name and n.observation_mappings
            for m in n.observation_mappings
        )

    @property
    def active_steps(self) -> tuple[str, ...]:
        return tuple(n.name for n in self.nodes)

    def active_steps_excluding(self, exclude: Iterable[str]) -> list[str]:
        """Callers hold a drop-list but the canonical projection takes a keep-list, so this owns the
        one inversion."""
        dropped = set(exclude)
        return [n for n in self.active_steps if n not in dropped]

    @property
    def is_single_node(self) -> bool:
        """The first-class predicate replacing scattered ``len(active_steps) <= 1`` arithmetic and
        literal ``llm_only`` checks — the acute case for the lock invariant and the node-row UI guard."""
        return len(self.nodes) == 1

    @property
    def observation_keys(self) -> frozenset[str]:
        return self._observation_keys

    def to_pipeline_params(self) -> dict[str, Any]:
        """The WIRE base only. The origin cycle id does NOT derive from it — ``build_origin_cycle_id``
        hashes the overlay-merged params, so the cycle id and the measurement key agree."""
        return {"steps": list(self.active_steps)}

    @property
    def config_nodes(self) -> list[PipelineNode]:
        """The nodes a CONFIG surface covers — every declared one, not just the running chain.
        Read it wherever the answer is "what can the operator see and unlock": a node absent
        from the surface is not a locked node, it is nothing at all."""
        return self.declared_nodes or self.nodes

    def node_config_schema(self) -> dict[str, list[NodeConfigParam]]:
        """COMPLETE by contract, so a reader answers "may the optimizer move anything here?" by summing
        ``optimizer_tunable``. A param dropped here is invisible to every caller — filter downstream."""
        # A model row is synthesized on the carrier only when no node OWNS a model —
        # otherwise the native row (justlogic's `llm_only.model`) is authoritative.
        model_declared = any(
            "model" in (n.param_keys | set(n.current_config)) for n in self.config_nodes
        )
        model_carrier = None if model_declared else self._model_carrier()
        out: dict[str, list[NodeConfigParam]] = {}
        for n in self.config_nodes:
            params: list[NodeConfigParam] = []
            for key in sorted((n.param_keys | set(n.current_config)) - SCHEMA_OWNED_FIELDS):
                options: list[str] = []
                if key in _PROMPT_OWNED_FIELDS:
                    kind = "prompt"
                elif n.param_types.get(key) in NESTED_PARAM_TYPES:
                    kind = "nested"
                elif key == "model":
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
        """ONE carrier, not per-node, so an outer L4 search evolves ONE inner-optimizer model fanned
        across every node. Both the tunable-axis and operator-row readers share it."""
        return next((s.name for s in self.nodes if s.runs_llm), None)

    def node_output_schemas(self) -> dict[str, NodeOutputSchema | None]:
        """The read-only companion to :meth:`node_config_schema`, so the steer panel can show the WHOLE
        node: model + params + prompt + the structured output it produces."""
        return {n.name: n.output_schema for n in self.config_nodes}

    def get_node(self, name: str) -> PipelineNode | None:
        return self._node_map.get(name)

    def filter_to_steps(self, steps: list[str]) -> "PipelineSchema":
        # Both lists, so a filtered schema cannot still resolve a node it just excluded.
        active = set(steps)
        return self.model_copy(
            update={
                "nodes": [n for n in self.nodes if n.name in active],
                "declared_nodes": [n for n in self.declared_nodes if n.name in active],
            },
        )

    def narrow(self, narrowing: dict[str, NodeSearchNarrowing] | None) -> "PipelineSchema":
        """Intersects, never widens; prompt-decomposition fields are always kept tunable. Empty
        narrowing is a no-op and a node absent from the mapping is unchanged."""
        if not narrowing:
            return self

        def _narrowed(source: list[PipelineNode]) -> list[PipelineNode]:
            out: list[PipelineNode] = []
            for n in source:
                nv = narrowing.get(n.name)
                if nv is None:
                    out.append(n)
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
                        [v for v in allowed[param] if v in subset]
                        if param in allowed
                        else list(vals)
                    )
                out.append(
                    n.model_copy(update={"param_keys": keys, "param_allowed_values": allowed})
                )
            return out

        # Both lists, or a narrowed campaign still renders the un-narrowed knobs on every
        # node that runs outside the default chain.
        return self.model_copy(
            update={
                "nodes": _narrowed(self.nodes),
                "declared_nodes": _narrowed(self.declared_nodes),
            }
        )

    def node_configs(self, pipeline_params: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        """Canonical SearchPoint identity: ordered ``[(node, config), ...]`` for hashing.

        Spans what the optimizer may EDIT (:attr:`config_nodes`), not just what this round RUNS — an
        escalation node reached only on a stall still changes the measurement, and keyed on the
        chain alone an edit landing there is indistinguishable from its parent.

        Off-chain nodes LEAD, and only where configured. ``MeasurementArchive.find_by_node_configs``
        matches a prefix whose partial arm forgives divergence past a row's terminal node; an
        off-chain node has no chain position, so trailing it would read as a reusable partial.
        Leading breaks the match at position 0. Configured-only keeps an untouched point on the
        chain-length tuple already banked."""
        in_chain = {node.name for node in self.nodes}
        result: list[tuple[str, dict[str, Any]]] = [
            (node.name, cfg)
            for node in self.config_nodes
            if node.name not in in_chain
            and isinstance(cfg := pipeline_params.get(node.name, {}), dict)
            and cfg
        ]
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
        """The SINGLE surface the param catalogue, the L1 output schema and ``validate_overrides`` all
        derive from — so a key stripped here is one the LLM's schema never declares.

        DECLARED nodes, matching :attr:`config_nodes`: what the optimizer may EDIT is not what
        this round happens to run, or an escalation node reached only on a stall could never
        be told to improve.
        """
        out: dict[str, set[str]] = {}
        for step in self.config_nodes:
            keys = set(step.param_keys) - PARAM_FORBIDDEN_KEYS - SCHEMA_OWNED_FIELDS
            if keys:
                out[step.name] = keys
        return out

    def prompt_node_names(self) -> list[str]:
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
