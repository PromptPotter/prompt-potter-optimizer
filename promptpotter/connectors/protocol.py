"""Connector protocol — adding one is intentionally LOCAL to ``connectors/<name>.py``, and the loop dispatches on a
connector's DECLARED capability rather than on its name."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from promptpotter.domain.connector import (
    ConnectorExecution,
    MeasuredUnit,
    SessionProtocol,
    WireAdapter,
)
from promptpotter.domain.pipeline_schema import NodeType

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
    """Backend experiment data → ``(queries, index_terms)``, each a ``{"query", "ground_truth"}``.

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
    """Most samples of one candidate the scoring walk may hold in flight once armed. Declared
    here rather than read off ``execution``, a transport fact: ``dspy`` and ``promptpotter`` are
    both ``in_process`` and want opposite answers. ``1`` opts out.

    **This is the whole of what a connector may say about concurrency — never how long an operator
    arming lasts.** A connector cannot see whether the walk in front of it sits inside a round;
    ``_bind_run_controls`` binds an arming only under ``run_optimization``, so the round spends
    every press, and a screen declares its depth at launch instead
    (``application/diagnostics/seed_screen.py``)."""

    measured_unit: MeasuredUnit = "sample"
    """What one measured row of this backend is CALLED: ``cell`` where it is a whole inner campaign
    or agent episode, else ``sample``. Declared, never sniffed off a row."""

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
    layouts + engine + the dataset's ``inner_tasks.yaml`` inner-run config), so
    without this an origin edit silently reuses stale measurements recorded
    under the old behavior. The connector's ``wire_adapter`` must strip these
    reserved keys from the outbound payload. ``None`` = the backend's revision
    is not part of identity (remote backends use the advisory ``version_check``
    instead)."""

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
    "VersionCheck",
]
