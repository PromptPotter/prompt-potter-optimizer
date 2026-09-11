import enum
import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import ConfigDict, Field

from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS
from promptpotter.domain.strict_model import StrictModel

# Prompt-decomposition fields the prompt editor owns — excluded from the
# operator-editable node-config surface (they live in `param_keys` too, but the
# steer panel edits them through `PromptFieldsEditor`, not the config widgets).
_PROMPT_OWNED_FIELDS = frozenset(PROMPT_STRING_FIELDS) | {"few_shot_examples", "plan"}

MOVABLE_AGENTS: tuple[str, ...] = ("l1", "l2")
"""Who may move a search axis — the closed set behind ``NodeConfigParam.movable_by``, in the
order it is emitted (how often each fires). ``l1`` the generator, every round, over the target's
axes; ``l2`` escalation, on a stall, over the OPTIMIZER's own.

The OPERATOR is deliberately absent: they may move anything by fork, so listing them would make
every axis movable and the field would say nothing. An outer optimizer (L4) needs no member
either — at that depth the inner loop IS a target pipeline and its axes are ``l1``'s, one level
up. That is the recursion working; a per-depth agent name would be a second spelling of it."""

# The INLINE contract an LLM node answers under: the shape, and which slot in it IS the answer.
# The pair, not the four below — `schema_family`/`schema_version` name a registry entry the
# backend owns instead. The parser TYPES both (`output_schema` as `object`, so a fork's overlay
# merges one level rather than replacing a schema wholesale).
OUTPUT_SCHEMA_KEY = "output_schema"
OUTPUT_CONTRACT_KEYS: tuple[str, str] = (OUTPUT_SCHEMA_KEY, "answer_field")

# Structured-output fields fenced off from the OPTIMIZER — and from the optimizer alone. They are
# STRUCTURAL: a mutated `output_schema` breaks the backend ("Schema must contain 'properties'"),
# and a mutated `answer_field` makes the executor destructure the wrong slot and grade every
# sample against reasoning prose. `node_param_keys` strips them UNCONDITIONALLY, unlike
# `schema_field_rename`, which has an ablation unlock.
#
# **A search rule, never a display one.** The rows carry these, shut and saying why
# (`never_axis`); only :data:`OUTPUT_SCHEMA_KEY` is subtracted there, because it is a tree and the
# webapp's `NodeSurface.tsx::OutputContract` renders it as one.
SCHEMA_OWNED_FIELDS = frozenset({*OUTPUT_CONTRACT_KEYS, "schema_family", "schema_version"})

# The `param_types` values that make a param NESTED — a container the optimizer edits
# one level deep rather than a scalar it replaces. Naming them once keeps the three
# readers agreeing: `apply_node_overlay` (merge one level, siblings survive — `array`
# replaces wholesale, since a merged ordering is meaningless), `node_config_schema`
# (a row read as text, since no scalar widget could edit one), and `build_l1_response_schema`
# (the emitted sub-schema, whose value space is the param's own, not the node's).
NESTED_PARAM_TYPES = frozenset({"object", "array"})

# The one nested param a campaign must UNLOCK before its L1 may emit it
# (`OptimizationConfig.schema_field_rename`): renaming a field on the optimizer's own
# output schema is the strongest lever and the only one that can break a parser. Named
# here, beside the other structural param constants, because two layers must agree on the
# literal without importing each other: `build_l1_response_schema` (drops it from the emitted
# schema when locked, so the LLM cannot emit a key that does not exist) and the
# `rebase_capability` directive (offers L2/L3 the unlock only where a node declares it).
SCHEMA_RENAME_PARAM = "output_schema_field_names"

# The core structured-output lever: rewrite the JSON-Schema `description` strings of a TARGET
# node's own output schema. A `description` is the only natural language inside the field-filling
# loop and no code reads it, so it is free to move on ANY node that declares an `output_schema` —
# unlike the field NAME (the wire + grading contract). ONE string param per field, keyed by its
# dotted path (`description_key`), so a field locks like any param: synthesized at parse time
# (`pipeline_parsing.py`), folded into the wire schema at `OptSearchPoint.to_job_search_point`.
# See `docs/concepts/structured-output.md`.
SCHEMA_DESCRIPTION_PREFIX = "output_schema_descriptions."


def description_key(path: str) -> str:
    return SCHEMA_DESCRIPTION_PREFIX + path


def description_path(key: str) -> str | None:
    """The field path a description key names, ``None`` for any other param."""
    return (
        key[len(SCHEMA_DESCRIPTION_PREFIX) :] if key.startswith(SCHEMA_DESCRIPTION_PREFIX) else None
    )


def _fields_of(schema: object) -> dict[str, object] | None:
    """The property map a path's next segment is looked up in: through a nullable ``anyOf`` and
    through array ``items`` — a list's elements are described under the list's own path — but
    never through a ``$ref``, whose target this schema does not carry."""
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


def description_paths(json_schema: object) -> list[str]:
    """Every describable field, parent before child in schema order — the order the fields
    generate in. A name holding ``.`` is refused: its path would name two fields at once."""
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


def described_field(json_schema: object, path: str) -> dict[str, object] | None:
    """The property schema *path* names — where its ``description`` is read and written."""
    node: dict[str, object] | None = None
    fields = _fields_of(json_schema)
    for name in path.split("."):
        sub = (fields or {}).get(name)
        if not isinstance(sub, dict):
            return None
        node, fields = sub, _fields_of(sub)
    return node


# Whether the node uses its schema AT ALL — the one lever over the structured-output contract
# that is not `SCHEMA_OWNED_FIELDS`. `json` sends the declared `output_schema` + `answer_field`;
# `text` sends NEITHER, so the node answers in prose and the matcher's own contract
# (`formula/matchers.py::EXTRACTION_NOTES`) reads the label out of it. UNSET is not a third
# state: `schema_toggle_default` says what an unset node runs and the fold writes nothing, so a
# configuration nobody moved keeps the hash it was measured under.
SCHEMA_TOGGLE_PARAM = "response_format"
ANSWER_AS_JSON = "json"
ANSWER_AS_TEXT = "text"


def schema_toggle_default(node: "PipelineNode") -> str:
    """The ONE reading of an unset toggle — the fold and the served row both ask here, so the wire
    and the panel cannot disagree about what "unset" meant."""
    return ANSWER_AS_JSON if node.output_schema else ANSWER_AS_TEXT


def _stated_permitted(
    permitted: list[str], options: list[str], absent: list[str]
) -> list[str] | None:
    """A row's served ``permitted``: the set, wherever it is not the menu OR not what an ABSENT
    entry resolves to. The editor writes a set out only when told it differs, so one equal to the
    menu but not to the declaration — a widening folded into the menu, a toggle ticked to its whole
    space — would be dropped on its next emit and resolve back to the declaration."""
    return permitted if permitted != options or set(permitted) != set(absent) else None


def declares_llm_run(
    *,
    name: str,
    mappings: Iterable["ObservationMapping"],
    wire_type: "NodeKind | None",
    langfuse_type: str,
) -> bool:
    """The BROAD "this node runs an LLM" test. Free-standing because the parser must ask it BEFORE
    the model is built; :attr:`PipelineNode.runs_llm` is the same question with one in hand."""
    return (
        name == "llm_only"
        or any(m.is_llm for m in mappings)
        or wire_type is NodeKind.GENERATION
        or langfuse_type == "generation"
    )


def stable_hash(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


class NodeType(enum.StrEnum):
    NONE = ""
    CANDIDATE_SOURCE = "candidate_source"
    RANKER = "ranker"
    ENRICHER = "enricher"
    CACHE = "cache"


class NodeKind(enum.StrEnum):
    """What a node IS — the CLOSED vocabulary a manifest's ``type:`` may name.

    Three families, and the third is the one that needed naming. A THINKING node runs a model, so a
    model, a reasoning rung and a temperature are its own. A retrieval or plumbing node moves data.
    A **GATEWAY** node runs another PIPELINE: every tunable it appears to have belongs to the
    pipeline it hands off to, which is why it carries no config of its own — and why one stray key
    on it drew a padlocked ``reasoning_effort`` row on the single node in the optimizer graph that
    does not reason. ``node_config_schema`` derives a node's params from ``current_config``, so
    without a kind to ask, a config key was left deciding what the node was.

    Closed rather than an open string because the open one had to be read by SHAPE
    (``startswith("llm")``), which cannot tell a DECLARED type from a DERIVED view kind — and
    ``presentation/teleprompter.py`` writes ``"llm"``, which is the latter. Five members below
    still spell one concept — "runs a model" — and collapsing them is a dataset migration rather
    than a rename, so until it happens each is NAMED here rather than matched by shape: a spelling
    nobody listed is now refused at parse instead of silently reading as a tool.
    """

    # Thinking — it runs a model.
    LLM = "llm"
    GENERATION = "generation"
    LLM_OPTIMIZER = "llm/optimizer"
    OPTIMIZER_PROMPT = "optimizer_prompt"
    AGENT = "agent"
    # Retrieval and plumbing — it moves data.
    RETRIEVER = "retriever"
    TOOL = "tool"
    CACHE = "cache"
    # Gateway — it runs another pipeline rather than doing the work itself. The WIRE spelling stays
    # `measurement`: three manifests, the served `PipelineViewNode.kind` and the webapp's own
    # measurement arm already say it, and a second word for one concept is what this enum exists to
    # stop. `GATEWAY` is what the code says, because "it hands off" is the fact every reader wants.
    GATEWAY = "measurement"


# A real choice WITHIN the type, so it is asserted rather than derived (`promptpotter/CLAUDE.md`
# § Ask the typed predicate): the members are listed, and the assert is what catches a new kind
# added above without deciding which family it joins.
THINKING_KINDS: frozenset[NodeKind] = frozenset(
    {
        NodeKind.LLM,
        NodeKind.GENERATION,
        NodeKind.LLM_OPTIMIZER,
        NodeKind.OPTIMIZER_PROMPT,
        NodeKind.AGENT,
    }
)
assert frozenset(NodeKind) >= THINKING_KINDS


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
    describes what ``l1_generate`` returns. The rename lever (``SCHEMA_RENAME_PARAM``) acts on that
    optimizer side; the description keys act on this one.
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
    # `None` is "the producer declared no type", a real state (`teleprompter.py` writes one) and
    # deliberately not a member: an UNDECLARED node reading as some default kind is the silence
    # this enum replaces. Anything else is refused at parse.
    wire_type: NodeKind | None = None
    node_type: NodeType = NodeType.NONE
    param_keys: set[str] = Field(default_factory=set)
    # What ``narrow`` TOOK AWAY — the axes this dataset declared and this campaign closed.
    # Without it the two reasons an axis is shut are one state on the wire: "the dataset never
    # offered it" (nobody acted) and "the operator held it at mint" (someone did, and could
    # have chosen otherwise). Only the second is a lock, and only the second is worth a click.
    param_keys_held: set[str] = Field(default_factory=set)
    # Params a CAMPAIGN spoke for, as against a dataset default — indistinguishable once `narrow()`
    # merges both into `param_allowed_values`, and a model's ladder replaces one but not the other.
    param_values_narrowed: set[str] = Field(default_factory=set)
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
        return declares_llm_run(
            name=self.name,
            mappings=self.observation_mappings,
            wire_type=self.wire_type,
            langfuse_type=self.langfuse_type,
        )

    @property
    def description_keys(self) -> list[str]:
        """Its description params in schema order — the order every surface lists them in."""
        if self.output_schema is None:
            return []
        return [description_key(p) for p in description_paths(self.output_schema.json_schema)]


ParamSource = Literal["backend", "dataset", "campaign", "seed", "evolved", "identity", "unset"]
"""WHICH LAYER set a resolved param's value, stamped BY the merge (last writer wins), never diffed
against it. Each member is described beside its producer in ``api-openapi.yaml::ParamSource``;
``backend`` is the CHECK-IN arm's floor (a captured declaration, where a campaign read has a
dataset file) and ``identity`` is unoverridable, so no surface may offer to edit it."""


class NodeConfigParam(StrictModel):
    """One param a node carries — the COMPLETE per-node list, which is what lets a reader sum
    `movable_by` into the node's whole search space.

    `kind` names the surface that OWNS the param: `model`/`enum` → a select,
    `number`/`bool`/`string` → a typed input, `prompt` → the prompt editor's (a
    `PromptTemplate` decomposition field), `description` → one output-schema field's prose, which
    the schema tree draws where a host has one, `nested` → structured, read as text and typed by
    nobody (`NESTED_PARAM_TYPES`). Do not re-filter this list at the source, and do not read
    `nested` as unrenderable — L2's layout is nested, and an axis an operator may be shown.

    **A lock is (axis, AGENT), never an axis alone.** `movable_by` names the agents that may
    move this param right now — the vocabulary is `MOVABLE_AGENTS` — and the empty list is the
    only true lock: nobody may, and changing it costs a fork.

    Two fields say WHY a shut axis is shut, and the difference is the whole point:
    `never_axis` = it could never be one, naming WHICH construction forbids it — `cost_lever`
    (`PARAM_FORBIDDEN_KEYS`: `provider`, `route_order`, set against a measured capture) or
    `schema_owned` (`SCHEMA_OWNED_FIELDS`: the structured-output contract, which the LLM cannot
    emit a key of at all). `held` = the dataset offered it and this campaign closed it
    (`param_keys_held` — somebody's decision, and reversible). Neither = plain configuration.
    **`model` is in none of them**: it is an ordinary axis whose openness is its node's own
    `param_keys` answer, and its permitted values are `param_allowed_values["model"]` (see
    `PipelineSchema.model_options`).

    A reason rather than a flag because the browser had to guess it from the key's name to say
    anything, and every guess it could make was the cost-lever sentence."""

    model_config = ConfigDict(frozen=True)

    key: str
    value: Any = None
    kind: (
        str  # "model" | "enum" | "number" | "bool" | "string" | "prompt" | "description" | "nested"
    )
    options: list[str] = Field(default_factory=list)
    description: str = ""
    never_axis: Literal["", "cost_lever", "schema_owned"] = ""
    movable_by: list[str] = Field(default_factory=list)
    held: bool = False
    # "unset" on a DATASET-scoped read, which has no campaign to attribute to; a campaign read
    # stamps every row from the merge that produced it.
    source: ParamSource = "unset"
    # What the BABYSIT gate accepts without tainting, where that differs from the menu above OR
    # from what an absent entry resolves to (`_stated_permitted`). `None` = neither, which is NOT
    # `[]` (nothing may be picked). `options` is a UNION, so narrowing cannot become a one-way
    # ratchet; the gate enforces the narrower list.
    permitted: list[str] | None = None


class NodeReach(StrictModel):
    """How far the search reaches on ONE node, off the SAME rows a surface renders — summed in the
    browser it was counted against whichever schema the caller held. The denominator is what is
    OPENABLE, so a param no agent moves is SHUT, not exempt; a node ABSENT is unknown, not shut."""

    model_config = ConfigDict(frozen=True)

    open: int
    openable: int
    agents: list[str] = Field(default_factory=list)
    held: bool = False
    state: Literal["open", "partial", "locked", "nothing"]


def node_reach(params: list[NodeConfigParam]) -> NodeReach:
    """Take the node's OWN rows, so the picture and the count cannot disagree about one node."""
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
    """Every door that serves ``node_config_schema`` serves the reading OVER it, off the same rows —
    four do, and while any one omitted it the browser summed its own against whichever schema the
    caller happened to hold, which on a campaign read was the DATASET's."""
    return {node: node_reach(node_rows) for node, node_rows in rows.items()}


class NestedPipelineRef(StrictModel):
    """Which node of THIS pipeline runs another whole pipeline, and whose. Both halves are
    derived from ``inner_tasks.yaml``, never declared a second time. Null on an ordinary dataset.

    Here rather than in the router that used to declare it, because the CAMPAIGN resolution has to
    carry it too and ``application/`` cannot import ``presentation/``. Its derivation lives beside
    the resolution (``application/pipeline_resolve.py::nested_pipeline_ref``), which is what makes
    the shape reachable from both doors without either owning the other."""

    node: str = Field(description="Node id in this pipeline whose measurement runs `dataset`.")
    dataset: str = Field(description="Slug of the pipeline that node runs; fetch it the same way.")


class ModelCapability(StrictModel):
    """What ONE model accepts and costs — resolved server-side, served per model id.

    Keyed by MODEL rather than folded into the `reasoning_effort` param row, because the answer
    changes the moment the operator picks a different model and a surface must be able to say so
    with no round-trip.

    `reasoning_efforts` `None` is UNKNOWN and never "unsupported": a caller keeps the node's own
    declared ladder untouched. Rendering an absent answer as "no" silently deletes a real search
    axis, which is the failure this type was written after. `reasoning_note` is populated in EVERY
    arm — unknown, no effort knob, operator override, full ladder — because a reason that appears
    only sometimes is a state nothing can report.

    Every card field is optional and a surface renders only what is present. Populated from the
    provider catalogue snapshot, which is a third party's claim and goes stale on their schedule.
    """

    model_config = ConfigDict(frozen=True)

    model: str
    reasoning_efforts: list[str] | None
    reasoning_note: str
    # The GENERAL case of the row above: which keys we would put on the request that this model
    # does not accept, so a setting the provider silently drops is REPORTED rather than rendered as
    # live. Scoped to what `openai_compat.chat` actually sends (`PROVIDER_REQUEST_PARAMS`) — a
    # catalogue has no opinion on a backend's own `max_sites`, and marking one would be a
    # confident wrong answer. `None` is UNKNOWN and never "accepts everything": same rule as
    # `reasoning_efforts`, because striking a row on an absent answer deletes a real setting.
    # `[]` is the real "takes everything we send".
    unsupported_params: list[str] | None = None
    # Rungs measured to produce the SAME call here. `None` UNMEASURED, `[]` measured-all-distinct —
    # the same absent-answer arm as above. Never subtracted from `reasoning_efforts`: an axis keeps
    # every value it can legally take, and a caveat is not a filter.
    indistinct_efforts: list[str] | None = None
    # Which layer answered: "override" (operator-authored), "openrouter" (fetched snapshot),
    # "unknown". Shown so a narrowed list can say whose claim it is.
    source: str
    display_name: str = ""
    context_length: int | None = None
    max_output_tokens: int | None = None
    input_usd_per_mtok: float | None = None
    output_usd_per_mtok: float | None = None
    modality: str = ""
    moderated: bool | None = None
    fetched_at: str = ""


CAPABILITY_ANSWERED_PARAMS: frozenset[str] = frozenset({"reasoning_effort"})
"""Params a :class:`ModelCapability` answers FOR — one member per answer field it carries. The
assert below pins that pairing: a param named here with no answer field empties its axis in
silence."""

_MODEL_ANSWER_FIELDS: frozenset[str] = frozenset(
    f.removesuffix("s") for f in ModelCapability.model_fields if f.endswith("_efforts")
)
assert CAPABILITY_ANSWERED_PARAMS.issubset(_MODEL_ANSWER_FIELDS)


class NodeSearchNarrowing(StrictModel):
    """A campaign's own declaration over the dataset's, and the two halves do NOT compose the same
    way — see :meth:`PipelineSchema.narrow`.

    ``param_keys`` SUBSETS: the dataset's ``pipeline.yaml`` declares the maximum tunable surface
    and a campaign may only close axes within it — the prompt-decomposition fields included, so
    one left out is a prompt field the optimizer may not rewrite.

    ``param_allowed_values`` REPLACES: a value space is not a permission over the dataset's, it is
    this campaign's statement of what its axis ranges over, with the dataset's list as the default
    it starts from. That is what lets an operator ADD a value — a model the catalogue predates, a
    reasoning rung a node never listed — through the channel that already carries the narrowing."""

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
    # What each selectable model answers for its own knobs (`infrastructure/llm/capabilities.py`),
    # read through `param_options`. Rides the schema and NOT the identity — `sp_hash` folds node
    # configs — so a refreshed snapshot re-keys nothing. Empty is UNKNOWN, never "accepts nothing".
    model_capabilities: dict[str, ModelCapability] = Field(default_factory=dict)

    # DERIVED ON READ, never cached at init: `narrow()` and `filter_to_steps()` build with
    # `model_copy`, which skips `model_post_init`, so a cached index answered with pre-copy nodes.

    @property
    def _node_map(self) -> dict[str, "PipelineNode"]:
        # Indexed over DECLARED nodes: "is there a node called X" and "what type is its
        # param" are questions about the manifest, not about this round's chain — an
        # escalation node resolving to None merges its nested params shallow and loses
        # every sibling key.
        return {n.name: n for n in (self.declared_nodes or self.nodes)}

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
        return frozenset(
            m.pipeline_key
            for n in self.nodes
            if n.observation_name and n.observation_mappings
            for m in n.observation_mappings
        )

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

    def model_options(self, node: "PipelineNode") -> list[str]:
        """The PERMITTED model set for one node — its own ``param_allowed_values["model"]`` when
        declared, else the pipeline's ``available_models`` catalogue. PREFERS where
        :meth:`selectable_models` unions; the contrast is argued there. The search path reaches it
        through :meth:`param_options`, never directly.

        **Empty is a real answer** — no catalogue and no declaration means the axis has no value
        space, and a caller must emit nothing rather than an unbounded string the LLM would fill
        with an invented model id."""
        declared = node.param_allowed_values.get("model")
        return list(declared) if declared else list(self.available_models)

    def param_options(
        self, node: "PipelineNode", param: str, *, model: str | None = None
    ) -> list[str] | None:
        """The permitted VALUE SET for ONE axis — ``model`` included, which is why no caller
        branches on the param name. Which layer answers: ``infrastructure/CLAUDE.md``.

        Three answers, and no caller may collapse two: ``None`` is no declared space, ``[]`` is
        declared with nothing legal left, a list is the space. Falsy-testing the first two together
        turns an over-narrowed axis into an unbounded one.

        *model* overrides the node's current pick, because the two axes move together: a candidate
        proposing a model and a rung at once is judged against the model it would run on."""
        if param == "model":
            # Delegated, not duplicated — `model_options` owns the catalogue-vs-declaration PREFER
            # rule, and :meth:`node_config_schema` reads it for the served `permitted` set.
            return self.model_options(node)
        declared = list(node.param_allowed_values.get(param) or ()) or None
        refused = self._refused(node, param, model)
        if param == SCHEMA_TOGGLE_PARAM and declared is not None:
            # Both bounds resolve after :meth:`narrow`, so no declaration can widen them back: no
            # `output_schema` is nothing to switch TO, and a refused key cannot be asked for one.
            # Named here rather than left to :meth:`_refused`, whose general rule ("the value it
            # is running") reads an unset toggle as no legal value at all.
            if refused is not None or node.output_schema is None:
                return [ANSWER_AS_TEXT]
            return declared
        if refused is not None:
            return refused
        answered = self._answering(node, param, model)
        offered = answered.reasoning_efforts if answered else None
        if offered is None:
            return declared
        # A campaign closing is an ADR-0005-gated act: the model may still strike a rung it refuses,
        # never hand back one the operator took away. Only a dataset default is replaced outright.
        if param in node.param_values_narrowed and declared is not None:
            return [rung for rung in offered if rung in set(declared)]
        return list(offered)

    def pinned(self, node: "PipelineNode", param: str) -> bool:
        """Is this axis's value space a single value? Then every "mutation" of it emits the value
        already there, so it is not something an agent can search: listed anyway it costs a
        catalogue line and a wire-schema property every round, and spends a variant's one mutation
        on a byte-identical call the round still scores. The browser derives the same rule for its
        own controls (`webapp/CLAUDE.md`: one permitted value IS the pin), and asking it here is
        what stops the two disagreeing about whether an axis is live.

        Two neighbours are deliberately NOT pinned. ``None`` is no declared space at all — a
        free-valued param, the one shape a search moves without a menu. And ``[]`` is an axis
        narrowed until nothing is legal: the readers that ADVERTISE an axis already skip it, while
        :func:`validate_overrides` must keep it, or a value arriving on it reports as an unknown
        param instead of naming which side — the campaign or the model — struck it."""
        options = self.param_options(node, param)
        return options is not None and len(options) == 1

    def param_indistinct(self, node: "PipelineNode", param: str) -> list[str]:
        """Values MEASURED to produce the same call on the node's current model — legal and
        offered, so this narrows nothing and is only ever reported. Two candidates separated by one
        of these are one configuration measured twice.

        Empty covers "nothing measured" and "all distinct" alike: a caller does the same with
        each."""
        answered = self._answering(node, param, None)
        if answered is None or not answered.indistinct_efforts:
            return []
        offered = set(self.param_options(node, param) or ())
        return [v for v in answered.indistinct_efforts if v in offered]

    def _refused(self, node: "PipelineNode", param: str, model: str | None) -> list[str] | None:
        """The space left when the picked model does not ACCEPT this key at all — its current value
        alone, which is one value and therefore the pin. ``None`` = not refused, ask on.

        A key outside the endpoint's `supported_parameters` is dropped on the way out, so every
        value it could take produces a byte-identical call — and the round scores the difference
        anyway, spending arms on an axis that reaches no wire. This is `openai_compat`'s own
        warning about an axis outside ``PROVIDER_REQUEST_PARAMS`` ('open it, search it, never move
        it'), applied to the case where the KEY is ours and the MODEL is the one refusing. It was
        reported to the operator (the ⊘ badge) and never enforced on the search.

        Unknown strikes nothing: ``unsupported_params is None`` is a catalogue that said nothing,
        and reading an absent answer as "no" deletes a real axis — the rule the capability layer
        exists for."""
        caps = self._capability(node, model)
        if caps is None or caps.unsupported_params is None:
            return None
        if param not in caps.unsupported_params:
            return None
        current = node.current_config.get(param)
        return [str(current)] if isinstance(current, str | int | float | bool) else []

    def _capability(self, node: "PipelineNode", model: str | None) -> "ModelCapability | None":
        """The capability of the model that will RUN this node, whatever the param."""
        if not self.model_capabilities:
            return None
        picked = model if model is not None else node.current_config.get("model")
        if not isinstance(picked, str) or not picked:
            return None
        return self.model_capabilities.get(picked)

    def _answering(
        self, node: "PipelineNode", param: str, model: str | None
    ) -> "ModelCapability | None":
        """The capability speaking for this ``(node, param)``, or ``None``. Shared by
        :meth:`param_options` and :meth:`param_indistinct` so the two cannot disagree about which
        model answers."""
        if param not in CAPABILITY_ANSWERED_PARAMS:
            return None
        return self._capability(node, model)

    def selectable_models(self) -> list[str]:
        """Every model a surface here can put on screen — the catalogue UNION each node's own
        permitted set and the value it currently carries.

        A UNION, deliberately, where :meth:`model_options` PREFERS: that one answers "what bounds
        this node", so a declared set replaces the catalogue — right for the run, wrong here.
        A model row's menu is the catalogue plus whatever the node permits, so narrowing to one
        model must not cost the capabilities of the models still on the menu; switching between
        them re-answers the reasoning ladder with no round-trip, and that is the whole reason this
        is resolved for a SET rather than for the pick.

        And it is not ``available_models`` alone. That is the ADMIN's catalogue, and a model the
        OPERATOR typed deliberately rides ``param_allowed_values.model`` instead
        (``draft_build._origin_pipeline_json`` states why merging the two would erase the one thing
        that marks a value as theirs) — so asking the catalogue could not, by construction, answer
        for a typed model. Picking one resolved no capabilities at all, and the card carrying its
        context, price and modality rendered nothing, silently, on the very surface where the model
        is chosen and the spend is committed.
        """
        models = set(self.available_models)
        for node in self.config_nodes:
            models.update(node.param_allowed_values.get("model", ()))
            if isinstance(picked := node.current_config.get("model"), str) and picked:
                models.add(picked)
        return sorted(models)

    def node_config_schema(
        self,
        l2_axes: dict[str, set[str]] | None = None,
        *,
        values: Mapping[str, Mapping[str, object]] | None = None,
        sources: Mapping[str, Mapping[str, ParamSource]] | None = None,
        model_menu: list[str] | None = None,
        declared: "PipelineSchema | None" = None,
    ) -> dict[str, list[NodeConfigParam]]:
        """COMPLETE by contract, so a reader answers "may anything move here?" by summing
        ``movable_by``. A param dropped here is invisible to every caller — filter downstream.

        *l2_axes* is ``{node: {param}}`` the ESCALATION layers may move. A schema cannot know
        whether it is the optimizer's own manifest or a target pipeline, so the one route that
        serves the manifest passes it (``routers/active.py``) and everyone else passes nothing.
        Its source is ``dispatch/schemas.py::L2_NODE_AXES`` — the same table L2's own override
        parsing reads, so the picture and the parser cannot disagree about L2's reach.

        *values* / *sources* / *model_menu* / *declared* are a CAMPAIGN read's answer written over
        the schema's own: the resolved value per param, the layer that won it, and the menus as a
        union with what was declared BEFORE narrowing — :meth:`narrow` REPLACES an enum's allowed
        values, so reading the narrowed list as the menu makes unticking a rung a one-way ratchet.
        ``permitted`` carries the narrower half, ``None`` where the two do not differ."""
        # A model row is synthesized on the carrier only when no node OWNS a model —
        # otherwise the native row (justlogic's `llm_only.model`) is authoritative.
        model_declared = any(
            "model" in (n.param_keys | set(n.current_config)) for n in self.config_nodes
        )
        model_carrier = None if model_declared else self._model_carrier()
        out: dict[str, list[NodeConfigParam]] = {}
        for n in self.config_nodes:
            params: list[NodeConfigParam] = []
            resolved = (values or {}).get(n.name, n.current_config)
            stamped = (sources or {}).get(n.name, {})
            # `param_keys_held` joins the union: an axis the operator closed whose value was
            # never written to `current_config` (`max_tokens`, declared and unset) otherwise
            # leaves the surface entirely — the one row that most needed to say it was held.
            keys = n.param_keys | n.param_keys_held | set(n.current_config)
            declared_node = declared.get_node(n.name) if declared else None
            # The SCHEMA is a tree — names, types, enums, the prose under each field — and the
            # webapp renders it as one beside these rows (`NodeSurface.tsx::OutputContract`).
            # Authoring one belongs there, under the `response_format` toggle.
            for key in sorted(keys - {OUTPUT_SCHEMA_KEY}):
                options: list[str] = []
                permitted: list[str] | None = None
                # UNSET is a value the node RUNS, not one nobody chose — the fold writes nothing,
                # so an empty row would report a JSON-answering node as answering in prose.
                unset = schema_toggle_default(n) if key == SCHEMA_TOGGLE_PARAM else None
                if (path := description_path(key)) is not None:
                    # Unset, a field says what its schema says: the inline one the config holds,
                    # else the declaration.
                    kind = "description"
                    field = described_field(
                        resolved.get(OUTPUT_SCHEMA_KEY)
                        or (n.output_schema.json_schema if n.output_schema else None),
                        path,
                    )
                    unset = str((field or {}).get("description") or "")
                elif key in _PROMPT_OWNED_FIELDS:
                    kind = "prompt"
                elif n.param_types.get(key) in NESTED_PARAM_TYPES:
                    kind = "nested"
                elif key == "model":
                    # The CATALOGUE, and only the catalogue — never the permitted set. Two things
                    # rest on that. Narrowing must not shrink `options`, or unticking a model
                    # would be a one-way ratchet the operator could not undo. And a value the
                    # operator TYPED is exactly one this list does not carry, which is the only
                    # thing that lets a surface mark it as theirs rather than the admin's.
                    kind, options = "model", list(model_menu or self.available_models)
                    allowed = self.model_options(n)
                    permitted = _stated_permitted(
                        allowed,
                        options,
                        declared.model_options(declared_node)
                        if declared and declared_node
                        else allowed,
                    )
                elif key in n.param_allowed_values:
                    # `permitted` is what THIS CAMPAIGN declared, and never the model-resolved
                    # space, because it is also what the editor emits back as the narrowing
                    # (`nodeConfig.ts::nodeNarrowing`). Resolving it here would let a repaint
                    # bake one model's refusals into the operator's own declaration — and an
                    # axis resolving to nothing would come back as a closed one.
                    kind = "enum"
                    narrowed = list(n.param_allowed_values[key])
                    absent = (
                        list(declared_node.param_allowed_values.get(key, ()))
                        if declared_node
                        else narrowed
                    )
                    # The toggle's whole space is its menu, as the catalogue is a model's: on a node
                    # with no schema `json` sits unticked, and ticking it asks for one. The run
                    # refuses it until one exists (`param_options`).
                    menu = (
                        [ANSWER_AS_TEXT, ANSWER_AS_JSON] if key == SCHEMA_TOGGLE_PARAM else absent
                    )
                    options = list(dict.fromkeys([*menu, *narrowed]))
                    permitted = _stated_permitted(narrowed, options, absent)
                else:
                    t = n.param_types.get(key, "string")
                    kind = (
                        "number"
                        if t in ("number", "integer")
                        else "bool"
                        if t == "boolean"
                        else "string"
                    )
                # Who may move this axis, in ``MOVABLE_AGENTS`` order — one source per agent,
                # so a member added to that tuple has to be given one here. The two constructions
                # that can never be axes short-circuit to the empty list and SAY WHICH; `model`
                # does NOT — it is an ordinary axis and answers here exactly as the node's
                # `param_keys` says. A config-only key (in current_config, not param_keys) admits
                # neither agent: it is a setting, not an axis.
                never: Literal["", "cost_lever", "schema_owned"] = (
                    "cost_lever"
                    if key in PARAM_FORBIDDEN_KEYS
                    else "schema_owned"
                    if key in SCHEMA_OWNED_FIELDS
                    else ""
                )
                reach = {"l1": n.param_keys, "l2": (l2_axes or {}).get(n.name, set())}
                movable = (
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
                        # A key that could never be an axis is never HELD, however it left
                        # `param_keys`: nobody closed an axis — there was none to close, and
                        # saying otherwise puts a padlock on a cost lever.
                        held=not never and key in n.param_keys_held,
                    )
                )
            if n.name == model_carrier and self.available_models:
                # Synthesized carrier row: the node never DECLARED a model, so nothing here is an
                # axis — plain configuration (`movable_by=[]`, `held=False`), operator-editable on
                # a fork because the seed overlay outranks the dataset. `never_axis` stays
                # empty — that names a construction nobody may search; this is simply a key the
                # node did not open.
                params.append(
                    NodeConfigParam(
                        key="model",
                        value=resolved.get("model", n.current_config.get("model")),
                        kind="model",
                        options=list(model_menu or self.available_models),
                        permitted=(
                            self.model_options(n)
                            if self.model_options(n) != list(model_menu or self.available_models)
                            else None
                        ),
                        description="Optimizer model for this node — install-global by "
                        "default, operator-steerable on a fork.",
                        source=stamped.get("model", "unset"),
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
        """**Keys SUBSET, values REPLACE** — the two halves protect different things, and only the
        first is the maximum-surface contract. Empty narrowing is a no-op and a node absent from
        the mapping is unchanged.

        ``param_keys`` intersects: a campaign may close an axis the dataset opened, never open one
        it closed. ``param_allowed_values`` assigns: the value space is the campaign's own
        declaration, with the dataset's list as its default — which is what lets an operator ADD a
        model or a reasoning rung that no `pipeline.yaml` on disk carries."""
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
                            # Accumulated, not assigned: narrowing composes (campaign config,
                            # then the frozen snapshot, then a fork seed), and a later pass
                            # must not forget what an earlier one closed.
                            "param_keys_held": n.param_keys_held | (n.param_keys - keys),
                            "param_allowed_values": allowed,
                            # Accumulated like the keys above; `param_options` reads it so a
                            # model's ladder cannot reopen what a campaign deliberately closed. A
                            # list restating the declaration closed nothing.
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
        prompt_node = next(iter(self.prompt_node_names()), None)
        for step in self.config_nodes:
            declared = set(step.param_keys) - PARAM_FORBIDDEN_KEYS - SCHEMA_OWNED_FIELDS
            # The node the prompt renders onto takes its prompt fields through the
            # `prompt_fields_updates` slot, never as node params: one carrier, so one lock.
            if step.name == prompt_node:
                declared -= _PROMPT_OWNED_FIELDS
            keys = {k for k in declared if not self.pinned(step, k)}
            if keys:
                out[step.name] = keys
        return out

    def prompt_node_names(self) -> list[str]:
        return [node.name for node in self.nodes if node.prompt_info is not None]

    def open_prompt_fields(self) -> list[str]:
        """The decomposition fields L1 may rewrite — the prompt node's open ``param_keys``, in
        ``PROMPT_STRING_FIELDS`` order. Only the FIRST prompt node's: it is the one
        ``to_job_search_point`` renders the searchpoint's prompt onto. Empty where none renders."""
        names = self.prompt_node_names()
        node = self.get_node(names[0]) if names else None
        return [f for f in PROMPT_STRING_FIELDS if node is not None and f in node.param_keys]


__all__ = [
    "CANDIDATE_LIBRARY",
    "CANDIDATE_LIBRARY_FILE",
    "MOVABLE_AGENTS",
    "THINKING_KINDS",
    "NestedPipelineRef",
    "NodeConfigParam",
    "NodeKind",
    "NodeOutputSchema",
    "NodePromptInfo",
    "NodeType",
    "ObservationMapping",
    "ParamSource",
    "PipelineDependency",
    "PipelineNode",
    "PipelineSchema",
    "PipelineView",
    "PipelineViewEdge",
    "PipelineViewNode",
    "dependencies_from_node_types",
    "stable_hash",
]
