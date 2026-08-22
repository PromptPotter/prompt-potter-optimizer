"""Connector protocol — adding one is intentionally LOCAL to ``connectors/<name>.py``, and the loop dispatches on a
connector's DECLARED capability rather than on its name."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.domain.connector import SessionProtocol, WireAdapter
from promptpotter.domain.pipeline_schema import NodeType
from promptpotter.shared.errors import PotterError

if TYPE_CHECKING:
    from pathlib import Path

    import httpx

# How a connector's backend runs, so the loop dispatches on a *declared*
# capability instead of branching on the connector name. ``remote_http`` posts
# to a live ``/matches`` endpoint (TermNorm + any external backend);
# ``in_process`` runs the query in this process with no HTTP transport — today an
# inner PromptPotter cycle (L4 self-recursion). ``BackendClient.run_query`` dispatches an
# ``in_process`` connector to its ``in_process_run`` hook. A future hosted/worker
# execution mode extends this enum without touching the loop.
ConnectorExecution = Literal["remote_http", "in_process"]

# What one press of the concurrency control buys. ``round`` is the wire shape, spent by the
# round that scored under it. ``batch`` is for a backend whose sample is a whole run, where a
# round is hours: the operator names how many launch together and the press is spent by them.
ConcurrencyArming = Literal["round", "batch"]

# What ONE MEASURED ROW is called on this backend: ``sample`` everywhere; ``cell`` on the
# recursion, where one row is an entire inner campaign. DECLARED, never sniffed off a row.
MeasuredUnit = Literal["sample", "cell"]


def unit_plural(unit: MeasuredUnit) -> str:
    return f"{unit}s"


def unit_count(n: int, unit: MeasuredUnit) -> str:
    """``1 cell`` / ``3 cells`` — the ONE place a measured row is counted in words."""
    return f"{n} {unit if n == 1 else unit_plural(unit)}"


# The in-process execution arm: ``(query, payload) -> resp`` where ``payload`` is
# the connector's ``wire_adapter`` output and ``resp`` is the same ``{"data": {…}}``
# shape ``measure_sample`` parses from an HTTP ``/matches`` body (so the scorer
# reads an in-process result identically to a remote one). Required on (and only
# on) an ``in_process`` connector — the registry guard enforces the pairing.
InProcessRun = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

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


class BackendUnreachableError(PotterError):
    """The configured backend isn't responding (503). Carries backend type + URL on ``details`` so the ``PotterError`` seam
    composes the envelope without re-parsing the message."""

    http_status = 503
    code = "backend_unreachable"

    def __init__(self, backend_type: str, backend_url: str, detail: str = "") -> None:
        self.backend_type = backend_type
        self.backend_url = backend_url
        self.detail = detail
        super().__init__(
            f"Backend '{backend_type}' at {backend_url} is not reachable. "
            f"Start the backend and try again." + (f" ({detail})" if detail else ""),
            details={"backend_type": backend_type, "backend_url": backend_url},
        )


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
    """Backend experiment data → ``(queries, index_terms)``."""

    experiment_file: str = ""
    """Filename of an on-disk experiment doc in the dataset's config dir, read +
    passed to :attr:`extract_experiment` when the dataset ships no CSV/loader
    samples. The in-process ``promptpotter`` connector sets ``inner_tasks.yaml`` —
    its outer "samples" ARE the inner tasks declared there, not a sample table.
    Empty (default) = samples come from the loader registry / tenant upload only."""

    execution: ConnectorExecution = "remote_http"
    """How this connector's backend runs — the dispatch capability the loop
    reads instead of branching on ``name``. ``remote_http`` (default) posts to
    a live ``/matches`` endpoint; ``in_process`` runs in this process via
    ``in_process_run`` (no HTTP). ``BackendClient.run_query`` dispatches on this."""

    max_cells_in_flight: int = 2
    """Most samples of one candidate the scoring walk may hold in flight once armed. Declared
    here rather than read off ``execution``, a transport fact: ``dspy`` and ``promptpotter`` are
    both ``in_process`` and want opposite answers. ``1`` opts out."""

    concurrency_arming: ConcurrencyArming = "round"
    """What one press of the concurrency control buys — see :data:`ConcurrencyArming`."""

    measured_unit: MeasuredUnit = "sample"
    """What one measured row of this backend is CALLED — see :data:`MeasuredUnit`."""

    in_process_run: InProcessRun | None = None

    expected_revision: str | None = None

    version_check: VersionCheck | None = None

    preflight: PreflightFn | None = None
    """Async ``(backend_url) -> None`` — reachability probe. Raises
    :class:`BackendUnreachableError` when the connector reports the backend
    is down. ``None`` opts the connector out (in-process backends like
    ``promptpotter`` have nothing to probe)."""

    auth_token: AuthTokenFn | None = None

    identity_config: Callable[[Path], dict[str, dict[str, Any]]] | None = None
    """Per-node config entries that are part of MEASUREMENT IDENTITY but not
    wire tunables — folded into ``resolve_pipeline_config_params`` so the
    origin cycle id and the archive's node-config reuse key change whenever
    the backend's effective revision does. Receives the resolved dataset config
    dir so a connector can fold dataset-scoped inner behavior into the
    fingerprint. The canonical user is the in-process ``promptpotter``
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
    this to clamp ``llm_only.reasoning_effort`` — origin floor ``low`` plus a
    ``param_allowed_values`` set with ``medium``/``high`` crossed out, so the
    optimizer can never escalate reasoning campaign-wide (a cost rail, not just
    an origin). Empty mapping means "no seed; the backend schema stands."
    Draft ``pipeline_overlay`` (operator edits) layers on top of this."""


__all__ = [
    "AuthTokenFn",
    "BackendUnreachableError",
    "ConcurrencyArming",
    "Connector",
    "ConnectorExecution",
    "InProcessRun",
    "MeasuredUnit",
    "PreflightFn",
    "SessionProtocol",
    "VersionCheck",
    "WireAdapter",
    "unit_count",
    "unit_plural",
]
