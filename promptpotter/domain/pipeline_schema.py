import enum
from collections.abc import Collection, Iterable, Mapping
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, model_validator

from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS, PROMPT_STRING_FIELDS
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.value_tree import Delivery, ValueLeaf, visibility_of
from promptpotter.shared.hashing import shapes_optimizer_prompt, stable_hash

# In `param_keys` too, but the prompt editor owns them: node-config widgets exclude them.
_PROMPT_OWNED_FIELDS: Annotated[frozenset[str], shapes_optimizer_prompt] = frozenset(
    PROMPT_STRING_FIELDS
)

type MovableAgent = Literal["proposer", "optimizer"]
MOVABLE_AGENT_LABELS: dict[MovableAgent, str] = {
    "proposer": "the optimizer's proposer, every round",
    "optimizer": "the optimizer itself, mid-run",
}
# No OPERATOR and no L4 member: a fork moves anything, and an outer loop's axes are the proposer's one level up.
MOVABLE_AGENTS: tuple[MovableAgent, ...] = tuple(MOVABLE_AGENT_LABELS)

# The parser types `output_schema` as `object`, so a fork's overlay merges one level.
OUTPUT_SCHEMA_KEY: Annotated[str, shapes_optimizer_prompt] = "output_schema"
OUTPUT_CONTRACT_KEYS: Annotated[tuple[str, str], shapes_optimizer_prompt] = (
    OUTPUT_SCHEMA_KEY,
    "answer_field",
)

# Fenced off from the OPTIMIZER only — a search rule, never a display one: served rows carry them.
SCHEMA_OWNED_FIELDS: Annotated[frozenset[str], shapes_optimizer_prompt] = frozenset(
    {*OUTPUT_CONTRACT_KEYS, "schema_family", "schema_version"}
)

# `apply_node_overlay` merges an `object` one level; an `array` replaces wholesale.
NESTED_PARAM_TYPES: Annotated[frozenset[str], shapes_optimizer_prompt] = frozenset(
    {"object", "array"}
)

# The one nested param a campaign must UNLOCK before L1 may emit it (potter's `schema_field_rename`).
SCHEMA_RENAME_PARAM: Annotated[str, shapes_optimizer_prompt] = "output_schema_field_names"

SCHEMA_DESCRIPTION_PREFIX: Annotated[str, shapes_optimizer_prompt] = "output_schema_descriptions."


@shapes_optimizer_prompt
def description_key(path: str) -> str:
    return SCHEMA_DESCRIPTION_PREFIX + path


@shapes_optimizer_prompt
def description_path(key: str) -> str | None:
    return (
        key[len(SCHEMA_DESCRIPTION_PREFIX) :] if key.startswith(SCHEMA_DESCRIPTION_PREFIX) else None
    )


@shapes_optimizer_prompt
def _fields_of(schema: object) -> dict[str, object] | None:
    """Never through a ``$ref``, whose target this schema does not carry."""
    while isinstance(schema, dict) and "$ref" not in schema:
        arms = [
            a for a in schema.get("anyOf") or () if isinstance(a, dict) and a.get("type") != "null"
        ]
        if len(arms) == 1:
            schema = arms[0]
        elif schema.get("type") == "array":
            schema = schema.get("items")
        else:
            props = schema.get("properties")
            return props if isinstance(props, dict) else None
    return None


@shapes_optimizer_prompt
def description_paths(json_schema: object) -> list[str]:
    out: list[str] = []

    def walk(schema: object, prefix: str) -> None:
        for name, sub in (_fields_of(schema) or {}).items():
            if "." in name:
                raise ValueError(f"output schema field {prefix + name!r}: a name may not hold '.'")
            if isinstance(sub, dict):
                out.append(prefix + name)
                walk(sub, f"{prefix}{name}.")

    walk(json_schema, "")
    return out


@shapes_optimizer_prompt
def described_field(json_schema: object, path: str) -> dict[str, object] | None:
    node: dict[str, object] | None = None
    fields = _fields_of(json_schema)
    for name in path.split("."):
        sub = (fields or {}).get(name)
        if not isinstance(sub, dict):
            return None
        node, fields = sub, _fields_of(sub)
    return node


# UNSET is not a third state: `schema_toggle_default` reads it and the fold writes nothing.
SCHEMA_TOGGLE_PARAM: Annotated[str, shapes_optimizer_prompt] = "response_format"
ANSWER_AS_JSON: Annotated[str, shapes_optimizer_prompt] = "json"
ANSWER_AS_TEXT: Annotated[str, shapes_optimizer_prompt] = "text"


def schema_toggle_default(node: "PipelineNode") -> str:
    return ANSWER_AS_JSON if node.output_schema else ANSWER_AS_TEXT


def _stated_permitted(
    permitted: list[str], options: list[str], absent: list[str]
) -> list[str] | None:
    """Stated where it differs from the menu OR from the declaration: the editor drops an unstated set."""
    return permitted if permitted != options or set(permitted) != set(absent) else None


class NodeRole(enum.StrEnum):
    """What a node's OUTPUT is to the scorer; :class:`NodeKind` is the other axis, what it IS."""

    CANDIDATE_SOURCE = "candidate_source"
    RANKER = "ranker"
    ENRICHER = "enricher"
    CACHE = "cache"


class NodeKind(enum.StrEnum):
    """Closed: an unlisted ``type:`` is refused at parse. A GATEWAY runs another pipeline and owns no config."""

    LLM = "llm"
    AGENT = "agent"
    RETRIEVER = "retriever"
    TOOL = "tool"
    CACHE = "cache"
    # `measurement` is the WIRE spelling the manifests and `PipelineViewNode.kind` say.
    GATEWAY = "measurement"
    SAMPLER = "sampler"
    ELIMINATOR = "eliminator"
    SELECTOR = "selector"
    ALGORITHM = "algorithm"
    CONTROLLER = "controller"


THINKING_KINDS: Annotated[frozenset[NodeKind], shapes_optimizer_prompt] = frozenset(
    {NodeKind.LLM, NodeKind.AGENT}
)
assert frozenset(NodeKind) >= THINKING_KINDS

MEMBER_KINDS: Annotated[frozenset[NodeKind], shapes_optimizer_prompt] = frozenset(
    {
        NodeKind.SAMPLER,
        NodeKind.ELIMINATOR,
        NodeKind.SELECTOR,
        NodeKind.ALGORITHM,
        NodeKind.CONTROLLER,
    }
)
assert not (MEMBER_KINDS & THINKING_KINDS) and frozenset(NodeKind) >= MEMBER_KINDS


CANDIDATE_LIBRARY = "candidate_library"
CANDIDATE_LIBRARY_FILE = "candidate_library.txt"


class PipelineDependency(StrictModel):
    """Surfaced to the operator as a missing input, never a hidden fabricated default."""

    model_config = ConfigDict(frozen=True)

    kind: str
    node: str
    title: str
    hint: str


def dependencies_from_node_roles(
    role_by_name: Mapping[str, NodeRole],
) -> tuple[PipelineDependency, ...]:
    deps: list[PipelineDependency] = []
    candidate_sources = sorted(
        name for name, role in role_by_name.items() if role == NodeRole.CANDIDATE_SOURCE
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
    """The structured output a target pipeline node produces, parsed from ``GET /pipeline``."""

    model_config = ConfigDict(frozen=True)

    fields: list[str] = Field(default_factory=list)
    field_descriptions: dict[str, str] = Field(default_factory=dict)
    json_schema: dict[str, Any] = Field(default_factory=dict)


class NodePromptInfo(StrictModel):
    """Its PRESENCE marks the node prompt-bearing: the injection point for the candidate prompt."""

    # `extra="ignore"`: the backend owns this sub-object; PP reads only `template_variables`.
    model_config = ConfigDict(frozen=True, extra="ignore")

    template_variables: list[str] = Field(default_factory=list)


# Exactly what `pipeline_parsing.py::_derive_node_kind` can emit, plus the two `io` ends.
ViewKind = Literal["io", "llm", "tool", "retriever", "cache", "measurement"]

ParamKind = Literal["model", "enum", "number", "bool", "string", "prompt", "description", "nested"]


class PipelineViewNode(StrictModel):
    """One node's place in the flow, as a tier and a rank rather than as pixels.

    Tier 0 is the chain a sample runs, tier n>0 the n-th nested alternative pipeline; rank is the tier-0 position it acts on.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    description: str = ""
    kind: ViewKind
    tier: int = 0
    rank: int = 0


class PipelineViewEdge(StrictModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    # `alternative` runs from the chain's end to a node a controller's alternative pipeline adds.
    kind: Literal["forward", "loop", "directive", "alternative"] = "forward"


class PipelineView(StrictModel):
    """The graph projection derived from a manifest's nodes and pipelines; no manifest declares one."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    nodes: list[PipelineViewNode] = Field(default_factory=list)
    edges: list[PipelineViewEdge] = Field(default_factory=list)


def token_bound_of_bytes(utf8_bytes: int) -> int:
    """An upper bound: no token is shorter than one byte."""
    return utf8_bytes


class LLMSpendBound(StrictModel):
    """In the counts its BACKEND enforces, never a price (``infrastructure/llm/pricing.py::rate_ceiling``)."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["llm"]
    # Every retry and repair included.
    attempts: int = Field(ge=1)
    input_tokens: int = Field(ge=0)
    # No retry lifts it; a node config's own `max_tokens` replaces it.
    max_tokens: int = Field(ge=1)
    # Set where the sender forbade fallbacks: priced at the dearest of THEM. `None`: any host.
    hosts: tuple[str, ...] | None = None

    @model_validator(mode="before")
    @classmethod
    def _served_in_bytes(cls, served: object) -> object:
        if not isinstance(served, Mapping) or "input_bytes" not in served:
            return served
        if "input_tokens" in served:
            raise ValueError("a spend bound names its input once: `input_bytes` or `input_tokens`")
        held = {key: value for key, value in served.items() if key != "input_bytes"}
        return held | {"input_tokens": token_bound_of_bytes(int(served["input_bytes"]))}


class WebSpendBound(StrictModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["web"]
    queries: int = Field(ge=0)
    usd_per_query: float = Field(ge=0)


NodeSpendBound = Annotated[LLMSpendBound | WebSpendBound, Field(discriminator="kind")]


class PipelineNode(StrictModel):
    model_config = ConfigDict(frozen=True)

    name: str
    # `None` is "the producer declared no type" — a real state, never read as a default kind.
    kind: NodeKind | None = None
    role: NodeRole | None = None
    param_keys: set[str] = Field(default_factory=set)
    # What ``narrow`` TOOK AWAY — a lock — as against axes the dataset never offered.
    param_keys_held: set[str] = Field(default_factory=set)
    # Params a CAMPAIGN narrowed: a model's ladder replaces a dataset default, never these.
    param_values_narrowed: set[str] = Field(default_factory=set)
    param_descriptions: dict[str, str] = Field(default_factory=dict)
    param_allowed_values: dict[str, list[str]] = Field(default_factory=dict)
    # JSON-schema type per param; without it L1 may emit stringified numbers that break the wire.
    param_types: dict[str, str] = Field(default_factory=dict)
    observation_name: str | None = None
    observation_mappings: list[ObservationMapping] = Field(default_factory=list)
    output_schema: NodeOutputSchema | None = None
    prompt_info: NodePromptInfo | None = None
    current_config: dict[str, Any] = Field(default_factory=dict)
    # Decided at parse, before `narrow`: no campaign's closing moves which node carries the model rows.
    tunes_llm: bool
    # `None`: its backend bounds nothing. Rides the schema, never the identity.
    spend_bound: NodeSpendBound | None = None

    @property
    def output_keys(self) -> list[str]:
        return [m.pipeline_key for m in self.observation_mappings]

    @property
    def provider(self) -> str:
        """``""`` where its config names none, which prices only a model id no other vendor's list holds."""
        named = self.current_config.get("provider")
        return named if isinstance(named, str) else ""

    @property
    def emits_ranking(self) -> bool:
        """A new ranking node role is admitted here and nowhere else."""
        return self.role in (NodeRole.RANKER, NodeRole.CANDIDATE_SOURCE)

    def ranking_in(self, pipeline_data: Mapping[str, object]) -> list[Any] | None:
        """``None`` where the node emitted nothing: it did not run, which is not an empty ranking."""
        for key in self.output_keys:
            if key in pipeline_data:
                emitted = pipeline_data[key]
                return emitted if isinstance(emitted, list) else []
        return None

    @property
    def is_llm(self) -> bool:
        """Narrow on purpose: an in-process optimizer prompt node runs an LLM but owns no model, so it is exempt."""
        return any(m.is_llm for m in self.observation_mappings)

    @property
    @shapes_optimizer_prompt
    def description_keys(self) -> list[str]:
        """Its description params in schema order — the order every surface lists them in."""
        if self.output_schema is None:
            return []
        return [description_key(p) for p in description_paths(self.output_schema.json_schema)]

    def param_kind(self, key: str) -> ParamKind:
        if description_path(key) is not None:
            return "description"
        if key in _PROMPT_OWNED_FIELDS:
            return "prompt"
        if self.param_types.get(key) in NESTED_PARAM_TYPES:
            return "nested"
        if key == "model":
            return "model"
        if key in self.param_allowed_values:
            return "enum"
        t = self.param_types.get(key, "string")
        return "number" if t in ("number", "integer") else "bool" if t == "boolean" else "string"


# Stamped BY the merge (last writer wins), never diffed against it; `identity` is unoverridable, so no surface edits one.
ParamSource = Literal[
    "backend", "dataset", "campaign", "model_floor", "seed", "evolved", "identity", "unset"
]


class NodeConfigParam(StrictModel):
    """One param a node carries, in the COMPLETE per-node list a reader sums `movable_by` over.

    `kind` names the surface that owns it: `model`/`enum` a select, `number`/`bool`/`string` a typed
    input, `prompt` the prompt editor's, `description` one output-schema field's prose, `nested`
    structured text that is still renderable.
    `movable_by` names the agents that may move it now; the empty list is the only true lock, and
    changing it costs a fork.
    `never_axis` says it could never be an axis (`cost_lever`: a billing key; `schema_owned`: the
    structured-output contract); `held` says this campaign closed an axis the dataset offered,
    reversibly. `model` is in neither: an ordinary axis.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    value: Any = None
    kind: ParamKind
    options: list[str] = Field(default_factory=list)
    description: str = ""
    never_axis: Literal["", "cost_lever", "schema_owned"] = ""
    movable_by: list[MovableAgent] = Field(default_factory=list)
    held: bool = False
    # "unset" on a DATASET-scoped read, which has no campaign to attribute to.
    source: ParamSource = "unset"
    # `None` = not stated (`_stated_permitted`), which is NOT `[]` (nothing may be picked).
    permitted: list[str] | None = None


class NodeReach(StrictModel):
    """How far the search reaches on one node: an unmoved param is SHUT, an absent node unknown."""

    model_config = ConfigDict(frozen=True)

    open: int
    openable: int
    agents: list[MovableAgent] = Field(default_factory=list)
    held: bool = False
    state: Literal["open", "partial", "locked", "nothing"]


def node_reach(params: list[NodeConfigParam]) -> NodeReach:
    openable = [p for p in params if not p.never_axis]
    opened = [p for p in openable if p.movable_by]
    agents = sorted({a for p in opened for a in p.movable_by}, key=MOVABLE_AGENTS.index)
    state: Literal["open", "partial", "locked", "nothing"] = (
        "nothing"
        if not openable
        else "locked"
        if not opened
        else "open"
        if len(opened) == len(openable)
        else "partial"
    )
    return NodeReach(
        open=len(opened),
        openable=len(openable),
        agents=agents,
        held=any(p.held for p in openable),
        state=state,
    )


def reach_map(rows: dict[str, list[NodeConfigParam]]) -> dict[str, NodeReach]:
    return {node: node_reach(node_rows) for node, node_rows in rows.items()}


class NestedPipelineRef(StrictModel):
    """Which node of this pipeline runs another whole pipeline, and whose."""

    node: str = Field(description="Node id in this pipeline whose measurement runs `dataset`.")
    dataset: str = Field(description="Slug of the pipeline that node runs; fetch it the same way.")


class ModelCapability(StrictModel):
    """What one model accepts and what it costs on one provider, resolved server-side.

    `reasoning_efforts` `None` is UNKNOWN, never "unsupported": keep the node's declared ladder.
    `reasoning_note` is populated in every arm. Card fields are optional and a third party's claim,
    except the two prices, which are the rate table's for `provider`.
    """

    model_config = ConfigDict(frozen=True)

    model: str
    provider: str
    reasoning_efforts: list[str] | None
    reasoning_note: str
    # `None` is UNKNOWN, never "accepts everything"; `[]` is the real "takes everything we send".
    unsupported_params: list[str] | None = None
    # `None` UNMEASURED, `[]` all distinct. Never subtracted from `reasoning_efforts`.
    indistinct_efforts: list[str] | None = None
    source: str
    display_name: str = ""
    context_length: int | None = None
    max_output_tokens: int | None = None
    input_usd_per_mtok: float | None = None
    output_usd_per_mtok: float | None = None
    modality: str = ""
    moderated: bool | None = None
    fetched_at: str = ""


# `provider -> model -> capability`; the key is `PipelineNode.provider`, so `""` holds the nodes naming none.
CapabilityMenu = dict[str, dict[str, ModelCapability]]

# One member per `ModelCapability` answer field: a param with no answer field empties its axis in silence.
CAPABILITY_ANSWERED_PARAMS: frozenset[str] = frozenset({"reasoning_effort"})

_MODEL_ANSWER_FIELDS: frozenset[str] = frozenset(
    f.removesuffix("s") for f in ModelCapability.model_fields if f.endswith("_efforts")
)
assert CAPABILITY_ANSWERED_PARAMS.issubset(_MODEL_ANSWER_FIELDS)

# NOMINAL rung order, never a measured cost. `default` omits the field, so it has no position.
_EFFORT_LADDER_ORDER: tuple[str, ...] = ("none", "minimal", "low", "medium", "high")


class NodeSearchNarrowing(StrictModel):
    """A campaign's own declaration over the dataset's, whose two halves compose differently.

    ``param_keys`` SUBSETS the dataset's tunable surface, prompt fields included;
    ``param_allowed_values`` REPLACES its value space, so an operator may ADD a value.
    """

    model_config = ConfigDict(frozen=True)

    param_keys: list[str] | None = None
    param_allowed_values: dict[str, list[str]] = Field(default_factory=dict)


class ParamIntent(StrictModel):
    """What an operator set on one config row: a free param's padlock, or an axis's ticks."""

    model_config = ConfigDict(frozen=True)

    key: str
    open: bool
    allowed: list[str]


def narrowing_of(params: list[NodeConfigParam], intents: list[ParamIntent]) -> NodeSearchNarrowing:
    """A value space is written where it DIFFERS from the menu, never "is a subset": axes may widen."""
    served = {p.key: p for p in params}
    keys: list[str] = []
    allowed: dict[str, list[str]] = {}
    for intent in intents:
        param = served.get(intent.key)
        if param is None:
            raise ValueError(f"{intent.key!r} is not a param of this node")
        axis = param.kind in ("enum", "model")
        if (len(intent.allowed) > 1) if axis else intent.open:
            keys.append(intent.key)
        stated = param.permitted is not None
        if axis and intent.allowed and (stated or set(intent.allowed) != set(param.options)):
            allowed[intent.key] = intent.allowed
    return NodeSearchNarrowing(param_keys=keys, param_allowed_values=allowed)


class ManifestNodeOverlay(StrictModel):
    """One optimizer node's delta over its manifest's ``config``."""

    model_config = ConfigDict(frozen=True)

    config: dict[str, Any] = Field(default_factory=dict)


class PipelineSchema(StrictModel):
    model_config = ConfigDict(frozen=True)

    name: str = ""
    version: str = ""
    description: str = ""
    # Every DECLARED node, on the chain or not; the chain a round runs is `nodes`.
    declared_nodes: list[PipelineNode] = Field(default_factory=list)
    # `default` is the chain a round runs; the manifest digest folds the others.
    pipelines: dict[str, list[str]] = Field(default_factory=lambda: {"default": list[str]()})
    available_models: list[str] = Field(default_factory=list)
    view: PipelineView | None = None
    # Rides the schema, NOT the identity. Empty is UNKNOWN, never "accepts nothing".
    model_capabilities: CapabilityMenu = Field(default_factory=dict)

    @model_validator(mode="after")
    def _names_the_chain_a_round_runs(self) -> "PipelineSchema":
        if "default" not in self.pipelines:
            raise ValueError(
                f"pipelines declares {sorted(self.pipelines)} and no `default`, the chain a round runs"
            )
        return self

    # Derived on read, never cached: `model_copy` (`narrow`, `filter_to_steps`) skips init hooks.

    @property
    def _node_map(self) -> dict[str, "PipelineNode"]:
        # Over DECLARED nodes: an off-chain node resolving to None merges its nested params shallow.
        return {n.name: n for n in self.declared_nodes}

    @property
    def nodes(self) -> list[PipelineNode]:
        """The chain a round RUNS; identity stays on it, since folding every declared node into ``sp_hash`` re-keys each measurement."""
        declared = self._node_map
        return [declared[name] for name in self.pipelines["default"] if name in declared]

    @property
    def active_steps(self) -> tuple[str, ...]:
        return tuple(n.name for n in self.nodes)

    def active_steps_excluding(self, exclude: Iterable[str]) -> list[str]:
        dropped = set(exclude)
        return [n for n in self.active_steps if n not in dropped]

    @property
    def is_single_node(self) -> bool:
        """The acute case for the lock invariant and the node-row UI guard."""
        return len(self.nodes) == 1

    @property
    def observation_keys(self) -> frozenset[str]:
        return frozenset(
            m.pipeline_key
            for n in self.nodes
            if n.observation_name and n.observation_mappings
            for m in n.observation_mappings
        )

    def to_pipeline_params(self) -> dict[str, Any]:
        """The WIRE base only: ``build_origin_cycle_id`` hashes the overlay-merged params instead."""
        return {"steps": list(self.active_steps)}

    def model_options(self, node: "PipelineNode") -> list[str]:
        """Empty is a real answer: no value space, so a caller emits nothing, never a free string."""
        declared = node.param_allowed_values.get("model")
        return list(declared) if declared else list(self.available_models)

    def param_options(
        self, node: "PipelineNode", param: str, *, model: str | None = None
    ) -> list[str] | None:
        """``None`` is no declared space, ``[]`` declared with nothing legal left: never collapse them."""
        if param == "model":
            return self.model_options(node)
        declared = list(node.param_allowed_values.get(param) or ()) or None
        refused = self._refused(node, param, model)
        if param == SCHEMA_TOGGLE_PARAM and declared is not None:
            # Not left to :meth:`_refused`, which reads an unset toggle as no legal value.
            if refused is not None or node.output_schema is None:
                return [ANSWER_AS_TEXT]
            return declared
        if refused is not None:
            return refused
        answered = self._answering(node, param, model)
        offered = answered.reasoning_efforts if answered else None
        if offered is None:
            return declared
        # The model may strike a rung a campaign kept, never hand back one the operator took away.
        if param in node.param_values_narrowed and declared is not None:
            return [rung for rung in offered if rung in set(declared)]
        return list(offered)

    def effort_floor(self, node: "PipelineNode", *, model: str | None) -> str | None:
        """``None`` on an unknown model with no declared ladder: the field is then omitted."""
        offered = self.param_options(node, "reasoning_effort", model=model) or ()
        return next((rung for rung in _EFFORT_LADDER_ORDER if rung in offered), None)

    def pinned(self, node: "PipelineNode", param: str) -> bool:
        """``None`` (free-valued) and ``[]`` (nothing legal) are NOT pinned."""
        options = self.param_options(node, param)
        return options is not None and len(options) == 1

    @shapes_optimizer_prompt
    def param_indistinct(self, node: "PipelineNode", param: str) -> list[str]:
        """Reported only: these values stay legal and offered."""
        answered = self._answering(node, param, None)
        if answered is None or not answered.indistinct_efforts:
            return []
        offered = set(self.param_options(node, param) or ())
        return [v for v in answered.indistinct_efforts if v in offered]

    def _refused(self, node: "PipelineNode", param: str, model: str | None) -> list[str] | None:
        """``None`` = not refused, or unknown: a catalogue that said nothing strikes no axis."""
        caps = self._capability(node, model)
        if caps is None or caps.unsupported_params is None:
            return None
        if param not in caps.unsupported_params:
            return None
        current = node.current_config.get(param)
        return [str(current)] if isinstance(current, str | int | float | bool) else []

    def _capability(self, node: "PipelineNode", model: str | None) -> "ModelCapability | None":
        picked = model if model is not None else node.current_config.get("model")
        if not isinstance(picked, str) or not picked:
            return None
        return self.model_capabilities.get(node.provider, {}).get(picked)

    def _answering(
        self, node: "PipelineNode", param: str, model: str | None
    ) -> "ModelCapability | None":
        if param not in CAPABILITY_ANSWERED_PARAMS:
            return None
        return self._capability(node, model)

    def selectable_models(self) -> list[str]:
        """A UNION where :meth:`model_options` PREFERS: an operator-typed model is in no catalogue."""
        models = set(self.available_models)
        for node in self.declared_nodes:
            models.update(node.param_allowed_values.get("model", ()))
            if isinstance(picked := node.current_config.get("model"), str) and picked:
                models.add(picked)
        return sorted(models)

    def selectable_routes(self) -> list[tuple[str, str]]:
        providers = {node.provider for node in self.declared_nodes}
        return sorted((p, m) for p in providers for m in self.selectable_models())

    def node_config_schema(
        self,
        own_axes: dict[str, set[str]] | None = None,
        *,
        values: Mapping[str, Mapping[str, object]] | None = None,
        sources: Mapping[str, Mapping[str, ParamSource]] | None = None,
        model_menu: list[str] | None = None,
        declared: "PipelineSchema | None" = None,
    ) -> dict[str, list[NodeConfigParam]]:
        """COMPLETE by contract — filter downstream. *declared* is the schema BEFORE narrowing."""
        # A model row is synthesized on the carrier only when no node OWNS a model.
        model_declared = any(
            "model" in (n.param_keys | set(n.current_config)) for n in self.declared_nodes
        )
        model_carrier = None if model_declared else self._model_carrier()
        out: dict[str, list[NodeConfigParam]] = {}
        for n in self.declared_nodes:
            params: list[NodeConfigParam] = []
            resolved = (values or {}).get(n.name, n.current_config)
            stamped = (sources or {}).get(n.name, {})
            # `param_keys_held` joins: a closed axis unset in `current_config` otherwise leaves the surface.
            keys = n.param_keys | n.param_keys_held | set(n.current_config)
            declared_node = declared.get_node(n.name) if declared else None
            for key in sorted(keys - {OUTPUT_SCHEMA_KEY}):
                options: list[str] = []
                permitted: list[str] | None = None
                # UNSET is a value the node RUNS: the fold writes nothing, so serve what it resolves to.
                unset = schema_toggle_default(n) if key == SCHEMA_TOGGLE_PARAM else None
                kind = n.param_kind(key)
                if kind == "description":
                    field = described_field(
                        resolved.get(OUTPUT_SCHEMA_KEY)
                        or (n.output_schema.json_schema if n.output_schema else None),
                        key.removeprefix(SCHEMA_DESCRIPTION_PREFIX),
                    )
                    unset = str((field or {}).get("description") or "")
                elif kind == "model":
                    # The CATALOGUE, never the permitted set: narrowing must not shrink `options`.
                    options = list(model_menu or self.available_models)
                    allowed = self.model_options(n)
                    permitted = _stated_permitted(
                        allowed,
                        options,
                        declared.model_options(declared_node)
                        if declared and declared_node
                        else allowed,
                    )
                elif kind == "enum":
                    # What THIS CAMPAIGN declared, never the model-resolved space: the editor emits it back.
                    narrowed = list(n.param_allowed_values[key])
                    absent = (
                        list(declared_node.param_allowed_values.get(key, ()))
                        if declared_node
                        else narrowed
                    )
                    # The toggle's whole space is its menu; the run refuses `json` until a schema exists.
                    menu = (
                        [ANSWER_AS_TEXT, ANSWER_AS_JSON] if key == SCHEMA_TOGGLE_PARAM else absent
                    )
                    options = list(dict.fromkeys([*menu, *narrowed]))
                    permitted = _stated_permitted(narrowed, options, absent)
                never: Literal["", "cost_lever", "schema_owned"] = (
                    "cost_lever"
                    if key in PARAM_FORBIDDEN_KEYS
                    else "schema_owned"
                    if key in SCHEMA_OWNED_FIELDS
                    else ""
                )
                reach: dict[MovableAgent, Collection[str]] = {
                    "proposer": n.param_keys,
                    "optimizer": (own_axes or {}).get(n.name, set()),
                }
                movable: list[MovableAgent] = (
                    []
                    if never or self.pinned(n, key)
                    else [a for a in MOVABLE_AGENTS if key in reach[a]]
                )
                params.append(
                    NodeConfigParam(
                        key=key,
                        value=resolved.get(key, n.current_config.get(key, unset)),
                        kind=kind,
                        options=options,
                        permitted=permitted,
                        description=n.param_descriptions.get(key, ""),
                        never_axis=never,
                        movable_by=movable,
                        source=stamped.get(key, "unset"),
                        # A key that could never be an axis is never HELD: there was none to close.
                        held=not never and key in n.param_keys_held,
                    )
                )
            if n.name == model_carrier and self.available_models:
                # The node never DECLARED a model: plain configuration, so `never_axis` stays empty.
                menu = list(model_menu or self.available_models)
                allowed = self.model_options(n)
                params.append(
                    NodeConfigParam(
                        key="model",
                        value=resolved.get("model", n.current_config.get("model")),
                        kind="model",
                        options=menu,
                        permitted=_stated_permitted(allowed, menu, allowed),
                        description="Optimizer model for this node — install-global by "
                        "default, operator-steerable on a fork.",
                        source=stamped.get("model", "unset"),
                    )
                )
            out[n.name] = params
        return out

    def _model_carrier(self) -> str | None:
        """ONE carrier, not per-node: an outer L4 search evolves one model fanned across every node."""
        return next((s.name for s in self.nodes if s.tunes_llm), None)

    def node_output_schemas(self) -> dict[str, NodeOutputSchema | None]:
        return {n.name: n.output_schema for n in self.declared_nodes}

    def get_node(self, name: str) -> PipelineNode | None:
        return self._node_map.get(name)

    def filter_to_steps(self, steps: list[str]) -> "PipelineSchema":
        # The declaration too, so a filtered schema cannot still resolve a node it just excluded.
        active = set(steps)
        return self.model_copy(
            update={
                "declared_nodes": [n for n in self.declared_nodes if n.name in active],
                "pipelines": {
                    **self.pipelines,
                    "default": [n for n in self.pipelines["default"] if n in active],
                },
            },
        )

    def narrow(self, narrowing: dict[str, NodeSearchNarrowing] | None) -> "PipelineSchema":
        """Keys SUBSET, values REPLACE (:class:`NodeSearchNarrowing`)."""
        if not narrowing:
            return self

        def _narrowed(source: list[PipelineNode]) -> list[PipelineNode]:
            out: list[PipelineNode] = []
            for n in source:
                nv = narrowing.get(n.name)
                if nv is None:
                    out.append(n)
                    continue
                keys = n.param_keys if nv.param_keys is None else n.param_keys & set(nv.param_keys)
                allowed = {
                    **n.param_allowed_values,
                    **{k: list(v) for k, v in nv.param_allowed_values.items()},
                }
                out.append(
                    n.model_copy(
                        update={
                            "param_keys": keys,
                            # Accumulated: narrowing composes (config, frozen snapshot, fork seed).
                            "param_keys_held": n.param_keys_held | (n.param_keys - keys),
                            "param_allowed_values": allowed,
                            # A list restating the declaration closed nothing.
                            "param_values_narrowed": n.param_values_narrowed
                            | {
                                k
                                for k, v in nv.param_allowed_values.items()
                                if set(v) != set(n.param_allowed_values.get(k, ()))
                            },
                        }
                    )
                )
            return out

        return self.model_copy(update={"declared_nodes": _narrowed(self.declared_nodes)})

    def node_configs(self, pipeline_params: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        """Off-chain nodes LEAD, configured ones only: trailing, ``ReplayFeed`` reads a reusable partial."""
        in_chain = {node.name for node in self.nodes}
        result: list[tuple[str, dict[str, Any]]] = [
            (node.name, cfg)
            for node in self.declared_nodes
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

    @shapes_optimizer_prompt
    def value_tree(self, *, prompt_delivery: Delivery) -> tuple[ValueLeaf, ...]:
        """``prompt_delivery`` is ``Connector.prompt_delivery``: a default would claim "always arrives"."""
        leaves: list[ValueLeaf] = []
        prompt_node = next(iter(self.prompt_node_names()), None)
        for step in self.declared_nodes:
            declared = set(step.param_keys) - PARAM_FORBIDDEN_KEYS - SCHEMA_OWNED_FIELDS
            # The prompt node's prompt fields ride `prompt_fields_updates`, never node params.
            if step.name == prompt_node:
                declared -= _PROMPT_OWNED_FIELDS
            for key in sorted(declared):
                leaves.append(
                    ValueLeaf(
                        path=f"{step.name}.harness.{key}",
                        node=step.name,
                        key=key,
                        kind="config",
                        delivery="harness",
                        mutable=not self.pinned(step, key),
                    )
                )
            if step.name != prompt_node or step.prompt_info is None:
                continue
            if visibility_of(prompt_delivery) == "on_demand":
                # PINNED: a candidate free to write its own advert wins by making itself uninviting.
                leaves.append(
                    ValueLeaf(
                        path=f"{step.name}.artifact.description",
                        node=step.name,
                        key="description",
                        kind="prose",
                        delivery="artifact_meta",
                        mutable=False,
                    )
                )
            for key in self.open_prompt_fields():
                leaves.append(
                    ValueLeaf(
                        path=f"{step.name}.prompt.{key}",
                        node=step.name,
                        key=key,
                        kind="prose",
                        delivery=prompt_delivery,
                        mutable=True,
                    )
                )
        return tuple(leaves)

    def node_param_keys(self) -> dict[str, set[str]]:
        """``prompt_delivery`` changes no key's presence, so any channel serves."""
        out: dict[str, set[str]] = {}
        for leaf in self.value_tree(prompt_delivery="request"):
            if leaf.mutable and leaf.delivery == "harness":
                out.setdefault(leaf.node, set()).add(leaf.key)
        return out

    @shapes_optimizer_prompt
    def prompt_node_names(self) -> list[str]:
        return [node.name for node in self.nodes if node.prompt_info is not None]

    @shapes_optimizer_prompt
    def open_prompt_fields(self) -> list[str]:
        """Only the FIRST prompt node's: the one ``to_job_search_point`` renders the prompt onto."""
        names = self.prompt_node_names()
        node = self.get_node(names[0]) if names else None
        return [f for f in PROMPT_STRING_FIELDS if node is not None and f in node.param_keys]


__all__ = [
    "CANDIDATE_LIBRARY",
    "CANDIDATE_LIBRARY_FILE",
    "MEMBER_KINDS",
    "MOVABLE_AGENTS",
    "MOVABLE_AGENT_LABELS",
    "THINKING_KINDS",
    "LLMSpendBound",
    "ManifestNodeOverlay",
    "MovableAgent",
    "NestedPipelineRef",
    "NodeConfigParam",
    "NodeKind",
    "NodeOutputSchema",
    "NodePromptInfo",
    "NodeRole",
    "NodeSpendBound",
    "ObservationMapping",
    "ParamSource",
    "PipelineDependency",
    "PipelineNode",
    "PipelineSchema",
    "PipelineView",
    "PipelineViewEdge",
    "PipelineViewNode",
    "WebSpendBound",
    "dependencies_from_node_roles",
    "token_bound_of_bytes",
]
