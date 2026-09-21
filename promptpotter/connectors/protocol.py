"""Connector protocol — adding one is intentionally LOCAL to ``connectors/<name>.py``, and the loop dispatches on a
connector's DECLARED capability rather than on its name."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from promptpotter.domain.connector import (
    CellEnvelopeSeconds,
    ConnectorExecution,
    MeasuredUnit,
    SessionProtocol,
    WireAdapter,
)
from promptpotter.domain.pipeline_schema import NodeSpendBound, NodeType
from promptpotter.domain.value_tree import Delivery

if TYPE_CHECKING:
    from pathlib import Path

    import httpx


@dataclass(frozen=True)
class InProcessWorkload:
    experiment: Mapping[str, Any] | None
    program: object | None


# What a client built only to probe a backend holds: it runs no query.
PROBE_WORKLOAD = InProcessWorkload(experiment=None, program=None)


# The in-process execution arm: ``(workload, query, payload) -> resp`` where ``payload`` is
# the connector's ``wire_adapter`` output and ``resp`` is the same ``{"data": {…}}``
# shape ``measure_sample`` parses from an HTTP ``/matches`` body (so the scorer
# reads an in-process result identically to a remote one). Required on (and only
# on) an ``in_process`` connector — the registry guard enforces the pairing.
InProcessRun = Callable[[InProcessWorkload, str, dict[str, Any]], Awaitable[dict[str, Any]]]

# Parsed ``experiment_file`` → the document every reader sees, with anything it only NAMES (a
# published roster) resolved to what it names.
ExperimentResolver = Callable[[Mapping[str, Any]], dict[str, Any]]

# Run init calls a connector's version_check once, with the
# BackendClient's live httpx client + base_url; the return is the backend's
# self-reported revision string, or None when the backend is silent.
VersionCheck = Callable[["httpx.AsyncClient", str], Awaitable[str | None]]

# R2: reachability probe — called from the launcher's three command paths
# (mint-campaign, start-checkin, start-run) before the applier touches the
# backend. Raises :class:`BackendUnreachableError` when the connector reports
# its backend is down.
PreflightFn = Callable[[str], Awaitable[None]]

# The connector's wire credential, read at client-construction time (not at import,
# so an env change lands without a reimport). ``None`` return = send no auth header.
AuthTokenFn = Callable[[], str | None]

# ``(node name, node config) → bound``: what one run of that node can bill, derived from the
# config this process sends it.
SentSpendBound = Callable[[str, Mapping[str, Any]], NodeSpendBound | None]

# ``pipeline_params → Delivery``: where a backend offers more than one channel, which one carries
# the prompt is the campaign's instrument choice, so it is resolved from the params a run hashes.
PromptDelivery = Callable[[dict[str, Any] | None], Delivery]


def _in_the_request(pipeline_params: dict[str, Any] | None) -> Delivery:
    return "request"


@dataclass(frozen=True)
class Connector:
    name: str
    """Lowercase id matching ``pipeline.yaml::backend_type`` and
    ``pipeline_schema.name.lower()``."""

    wire_adapter: WireAdapter
    """Outbound payload shaper for ``BackendClient.run_query``."""

    session_factory: Callable[[], SessionProtocol]
    """Fresh session instance per ``BackendClient`` — sessions hold per-client state."""

    extract_experiment: Callable[[dict[str, Any]], tuple[list[dict[str, Any]], list[str]]]
    """Backend experiment data → ``(queries, index_terms)``, each a ``{"query", "ground_truth"}``,
    plus ``source_pin`` where the query text does not say everything the cell was measured on (a
    task's resolved commit) — it is part of the sample's content address, ``Sample.key``.

    **The answer shape — owned by** ``connectors/CLAUDE.md`` § The answer shape: a query yielding
    ``ground_truth: None`` declares it here, and never a second time anywhere else."""

    experiment_file: str = ""
    """Filename of an on-disk experiment doc in the dataset's config dir, read +
    passed to :attr:`extract_experiment` when the dataset ships no CSV/loader
    samples. The in-process ``promptpotter`` connector sets ``inner_tasks.yaml`` —
    its outer "samples" ARE the inner tasks declared there, not a sample table.
    Empty (default) = samples come from the loader registry / tenant upload only."""

    resolve_experiment: ExperimentResolver | None = None
    """Applied by ``dataset_access.py::dataset_experiment`` to every read of the file."""

    execution: ConnectorExecution = "remote_http"
    """How this connector's backend runs — the dispatch capability the loop
    reads instead of branching on ``name``. ``remote_http`` (default) posts to
    a live ``/matches`` endpoint; ``in_process`` runs in this process via
    ``in_process_run`` (no HTTP). ``BackendClient.run_query`` dispatches on this."""

    max_cells_in_flight: int = 2
    """Most calls a scoring round may hold in flight once armed — every candidate's cells and the
    catch-up calls that pair them, together. Declared here rather than read off ``execution``, a
    transport fact: ``dspy`` and ``promptpotter`` are both ``in_process`` and want opposite
    answers. ``1`` opts out.

    **This is the whole of what a connector may say about concurrency — never how long an operator
    arming lasts.** A connector cannot see whether the walk in front of it sits inside a round;
    ``_bind_run_controls`` binds an arming only under ``run_optimization``, so the round spends
    every press, and a screen declares its depth at launch instead
    (``application/diagnostics/seed_screen.py``)."""

    cells_hold_the_machine: bool = False
    """Whether one cell holds a resource of THIS machine for its life — a container. Then
    :attr:`max_cells_in_flight` bounds the machine, not one run: every run on it takes from one pool
    of that many slots (``infrastructure/backend.py::MachineSlots``), so two runs each at their own
    depth cannot together outrun the box. ``False`` (default): a cell's cost is the provider's or
    the backend's, and each run answers for its own depth."""

    holds_own_sends: bool = False
    """Whether every paid send a cell makes is admitted and billed on its own, where it is made —
    by this process's LLM clients, by the litellm meter (``infrastructure/llm/litellm_sends.py``),
    or by the connector around a send it cannot see into (``infrastructure/llm/spend_book.py``) —
    so the cell is no send of its own. Where :attr:`sent_spend_bound` bounds the cell, it RESERVES
    that bound: it starts only where its worst case fits, and its sends draw on the reservation.
    ``False`` (default): the cell is held whole as ONE send, at the bound the backend serves per
    node (``PipelineNode.spend_bound``) or the one :attr:`sent_spend_bound` derives, and billed off
    its reply; a backend with neither cannot run under a spend ceiling. ``True`` for the recursion
    and ``harbor``; ``dspy`` pays through litellm outside the meter, so it is billed per cell."""

    sent_spend_bound: SentSpendBound | None = None
    """For a backend whose limits are the ones THIS process sends it, the bound one run of a node
    can bill, derived from the node config the wire adapter sends — so the hold and what the
    backend enforces are one set of numbers. ``None`` (default): the backend serves its own
    (``PipelineNode.spend_bound``). The function answering ``None``: that config sends no limit,
    and the cell cannot run under a spend ceiling."""

    cancel_stops_billing: bool = False
    """Whether cancelling a cell that is already sent stops what it bills. ``False`` (default): a
    sent cell is left to land, because the backend finishes it and the provider bills it whether or
    not anyone waits — cancelling would lose the result and learn nothing of the cost. Declared,
    never read off ``execution``: an in-process call that is one provider request bills all the
    same."""

    cell_envelope_s: CellEnvelopeSeconds | None = None
    """Seconds ONE cell of this backend may SPEND, resolved per cell. ``None`` (default) = this
    backend's cells carry no wall-clock bound.

    **It bounds the SUM.** Every await inside a cell is bounded on its own and nothing bounds them
    together, so a throttle storm stretches one cell across tens of minutes with no surface saying
    so. Time the cell was not ALLOWED to spend is handed back at the seam that enforces this
    (``application/scoring/cell_envelope.py``), leaving the cell's OWN work.

    **Reaching it is HALTED, never a zero** — a cut we made is not an answer, so the row carries
    :attr:`~promptpotter.shared.errors.ErrorCategory.HALTED`, no verdict, and no claim on a repair:
    this declaration cuts the next attempt at the same place."""

    measured_unit: MeasuredUnit = "sample"
    """What one measured row of this backend is CALLED: ``cell`` where it is a whole inner campaign
    or agent episode, else ``sample``. Declared, never sniffed off a row."""

    prompt_delivery: PromptDelivery = _in_the_request
    """The CHANNEL the candidate's rendered prompt reaches the model by, resolved per run from the
    params it hashes and read by ``PipelineSchema.value_tree``.

    ``request`` — in the message that carries the task, so it always arrives. Three of the four
    connectors, and the reason this is the default.

    ``artifact_body`` — written into the environment as an Agent Skill, where the harness shows the
    model only the frontmatter and the BODY arrives only if the model opens the file. A value on
    this channel **may never arrive**, which no param name says and no roster of keys could; the
    connector owes an arrival observation beside it (``harbor.py::SKILL_KEY``). Declared here and
    not inferred from ``execution`` or ``measured_unit``: an in-process agent backend could just as
    well put the prompt in the request, and ``harbor`` does exactly that under
    ``skill_delivery: system_prompt``."""

    required_observation_keys: tuple[str, ...] = ()
    """Observation keys this backend ALWAYS emits; ``wiring.py::_verify_required_observation_keys``
    raises at init unless the schema maps each. Empty = the backend guarantees none.

    **Why an undeclared key is a wrong number rather than drift — owned by**
    ``connectors/CLAUDE.md`` § Conventions."""

    answer_key: str | None = None
    """The ``data`` key carrying this cell's ANSWER TEXT. ``None`` (default) = ``predicted`` comes
    from the terminal ranker, as on every ranked-label backend.

    **Why this is not the answer-shape flag, and the four things that follow from the split —
    owned by** ``connectors/CLAUDE.md`` § The answer shape."""

    in_process_run: InProcessRun | None = None

    expected_revision: str | None = None

    version_check: VersionCheck | None = None

    preflight: PreflightFn | None = None
    """Async ``(backend_url) -> None`` — reachability probe. Raises
    :class:`BackendUnreachableError` when the connector reports the backend
    is down. ``None`` opts the connector out (in-process backends like
    ``promptpotter`` have nothing to probe)."""

    auth_token: AuthTokenFn | None = None

    completion_check: Callable[[], None] | None = None
    """Run where the table completes (``wiring.py::complete_registries``), so what it raises stops
    the server at boot and a run at init; ``None`` checks nothing."""

    identity_config: (
        Callable[[Path, Mapping[str, Any] | None], dict[str, dict[str, Any]]] | None
    ) = None
    """Per-node config entries that are part of MEASUREMENT IDENTITY but not
    wire tunables — folded into ``resolve_pipeline_config_params`` so the
    origin cycle id and the archive's node-config reuse key change whenever
    the backend's effective revision does. Receives the resolved dataset config
    dir and the resolved experiment, so a connector can fold dataset-scoped inner
    behavior into the fingerprint. The canonical user is the in-process ``promptpotter``
    connector: its backend IS the inner optimizer (optimizer prompt origin +
    layouts + engine + the inner benchmark's config), so
    without this an origin edit silently reuses stale measurements recorded
    under the old behavior. The connector's ``wire_adapter`` must strip these
    reserved keys from the outbound payload. ``None`` = the backend's revision
    is not part of identity (remote backends use the advisory ``version_check``
    instead).

    **What the whole panel is measured WITH, never which cells it holds.** A cell's own identity
    rides its row from :attr:`extract_experiment` as ``source_pin`` (``Sample.source_pin``);
    folding the task list in here re-keys every cell a panel already had the moment it grows."""

    default_pipeline: tuple[str, ...] = ()
    """First-tenant default pipeline step list — the launcher's chat-first
    ingest seeds ``pipeline.yaml::pipelines.default`` from this when a draft
    has no explicit override. Empty tuple means "no override; use the
    backend's ``GET /pipeline`` default." TermNorm sets this to
    ``("llm_only",)`` so a fresh CSV upload skips the heavy nodes
    (``web_search``, ``fuzzy_matching``, ``entity_profiling``,
    ``token_matching``, ``llm_ranking``) — those are the right default
    for the production benchmark but wrong for a tenant's first run."""

    default_exclude_nodes: tuple[str, ...] = ()

    default_optimization: tuple[tuple[str, Any], ...] = ()
    """Frozen ``(key, value)`` overrides slotted into the seed
    ``campaign.json::optimization`` block. Lets a connector ship
    domain-specific defaults (e.g. TermNorm pins ``n_variants=3``) without
    the launcher hard-coding the values. Empty mapping means "use
    :class:`OptimizationConfig` schema defaults verbatim." The required
    field (``degradation_threshold``) MUST be present here when the connector
    intends to seed it — there is no silent schema default."""

    node_types: Mapping[str, NodeType] = field(default_factory=dict)
    """Static node→:class:`NodeType` classification, mirroring what the live
    backend's ``GET /pipeline`` reports — declared here so the ingest UI can
    detect a pipeline's required inputs *before* the backend is reached
    (``launcher.draft_pipeline_dependencies`` reads it for the active steps). A
    ``CANDIDATE_SOURCE`` node raises a ``candidate_library`` dependency the
    operator drops in place. Only nodes that carry a dependency-bearing type need
    an entry; unlisted nodes are untyped (no dependency)."""

    default_node_config: Mapping[str, Any] = field(default_factory=dict)
    """Per-node ``pipeline.yaml::nodes.{name}`` overlay the chat-first ingest
    seeds into a fresh dataset's committed ``pipeline.yaml``. Keyed by node
    name; each value is a node overlay (``config`` floor + ``optimizer``
    constraints) merged onto the backend's live ``GET /pipeline`` schema (the
    overlay's ``config``/``optimizer`` sub-blocks shallow-merge, so a partial
    clamp narrows the backend schema rather than clobbering it). TermNorm uses
    this to seed ``llm_only.reasoning_effort`` at ``low``. A seeded
    ``param_allowed_values`` is a DEFAULT, so ``PipelineSchema.param_options``
    replaces it wherever the model has answered — a cost rail has to be a check-in
    narrowing, which intersects. Empty mapping means "no seed; the backend schema stands."
    Draft ``pipeline_overlay`` (operator edits) layers on top of this."""

    available_models: tuple[str, ...] = ()
    """The model catalogue the chat-first ingest writes into a fresh dataset's
    ``pipeline.yaml::available_models`` — the MENU an origin's permitted set is
    picked from. Admin-owned, the target-side twin of
    ``assets/optimizer/pipeline.yaml::available_models``: extend it as this install
    gains access to more models. Three DISTINCT layers, and collapsing any two is
    the confusion this field exists to prevent — this is what is AVAILABLE, a node's
    ``optimizer.param_allowed_values["model"]`` is which of them that node PERMITS
    (both what the optimizer may pick and what a human may steer to un-tainted), and
    ``default_node_config``'s ``config.model`` is where the origin STARTS. Whether
    ``model`` is a search AXIS is a fourth question, answered by that node's
    ``optimizer.param_keys`` alone. Empty tuple means "no menu" — the node's declared
    model stands alone and the ingest UI has nothing to offer. A model listed here
    that needs a ``max_tokens`` floor also needs a ``_MODEL_PROFILES`` entry
    (``infrastructure/llm/registry.py``); preflight cannot floor what it cannot
    profile, and neither list fails loudly when it lags the other."""


__all__ = [
    "PROBE_WORKLOAD",
    "AuthTokenFn",
    "Connector",
    "ExperimentResolver",
    "InProcessRun",
    "InProcessWorkload",
    "PreflightFn",
    "PromptDelivery",
    "VersionCheck",
]
