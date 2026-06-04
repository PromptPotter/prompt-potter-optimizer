"""Connector protocol — bundles backend-specific hooks under one shape.

A connector packages everything PromptPotter needs to talk to a specific
backend kind. Additional connectors are added by writing one new file in
this package.

Hooks:

- ``wire_adapter`` — outbound HTTP body shape for ``BackendClient.run_query``.
- ``session_factory`` — fresh ``SessionProtocol`` instance per ``BackendClient``.
- ``extract_experiment`` — backend experiment-data → ``(queries, index_terms)``.
- ``preflight`` — async reachability probe invoked before any command that
  needs the backend live (R2). Raises :class:`BackendUnreachableError`
  when the connector reports the backend is down; in-process connectors
  (e.g. ``promptpotter``) opt out by leaving the field ``None``.

Adding a connector is intentionally local to ``connectors/<name>.py`` — no
edits to ``application/config.py`` or ``infrastructure/backend.py``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.domain.connector import SessionProtocol, WireAdapter

if TYPE_CHECKING:
    import httpx

# How a connector's backend runs, so the loop dispatches on a *declared*
# capability instead of branching on the connector name. ``remote_http`` posts
# to a live ``/matches`` endpoint (TermNorm + any external backend);
# ``in_process`` runs an inner PromptPotter cycle (L4 self-recursion) with no
# HTTP transport. The inner-cycle execution path itself is Lane C3 (see
# ``docs/specs/m12-multi-connector.md`` § Track 1.5) — until it lands,
# ``run_query`` raises on an ``in_process`` connector. A future hosted/worker
# execution mode extends this enum without touching the loop.
ConnectorExecution = Literal["remote_http", "in_process"]

# Bootstrap calls a connector's version_check once at session init with the
# BackendClient's live httpx client + base_url; the return is the backend's
# self-reported revision string, or None when the backend is silent.
VersionCheck = Callable[["httpx.AsyncClient", str], Awaitable[str | None]]

# R2: reachability probe — called from the launcher's three command paths
# (mint-campaign, mint-campaign-from-draft, start-run) before the applier
# touches the backend. Raises :class:`BackendUnreachableError` when the
# connector reports its backend is down.
PreflightFn = Callable[[str], Awaitable[None]]


class BackendUnreachableError(RuntimeError):
    """Raised by ``Connector.preflight`` when the configured backend isn't
    responding.

    Carries the backend type + URL so the API layer's central 503 mapping
    (in :class:`CommandDispatcher._record_and_apply`) can compose the
    ``details.backend_type`` + ``details.backend_url`` envelope without
    re-parsing the message.
    """

    def __init__(self, backend_type: str, backend_url: str, detail: str = "") -> None:
        self.backend_type = backend_type
        self.backend_url = backend_url
        self.detail = detail
        super().__init__(
            f"Backend '{backend_type}' at {backend_url} is not reachable. "
            f"Start the backend and try again." + (f" ({detail})" if detail else "")
        )


@dataclass(frozen=True)
class Connector:
    """Frozen bundle of the backend-specific hooks."""

    name: str
    """Lowercase id matching ``pipeline.json::backend_type`` and
    ``pipeline_schema.name.lower()``."""

    wire_adapter: WireAdapter
    """Outbound payload shaper for ``BackendClient.run_query``."""

    session_factory: Callable[[], SessionProtocol]
    """Fresh session instance per ``BackendClient`` — sessions hold per-client state."""

    extract_experiment: Callable[[dict[str, Any]], tuple[list[dict[str, Any]], list[str]]]
    """Backend experiment data → ``(queries, index_terms)``."""

    execution: ConnectorExecution = "remote_http"
    """How this connector's backend runs — the dispatch capability the loop
    reads instead of branching on ``name``. ``remote_http`` (default) posts to
    a live ``/matches`` endpoint; ``in_process`` runs an inner cycle (L4). The
    ``in_process`` execution path is Lane C3 and not yet wired — ``BackendClient.run_query``
    raises a pointed ``NotImplementedError`` for it rather than failing as a
    confusing transport error against a backend that isn't there."""

    expected_revision: str | None = None
    """Backend revision (git SHA, semver, …) this PromptPotter rev expects.
    Paired with ``version_check`` — bootstrap WARNs on drift. ``None`` opts out."""

    version_check: VersionCheck | None = None
    """Async ``(http, base_url) -> str | None`` — reads the backend's
    self-reported revision. Mirrors :class:`SessionProtocol` shape so connectors
    stay layer-clean (no ``BackendClient`` import). ``None`` opts out."""

    preflight: PreflightFn | None = None
    """Async ``(backend_url) -> None`` — reachability probe. Raises
    :class:`BackendUnreachableError` when the connector reports the backend
    is down. ``None`` opts the connector out (in-process backends like
    ``promptpotter`` have nothing to probe)."""

    default_pipeline: tuple[str, ...] = ()
    """First-tenant default pipeline step list — the launcher's chat-first
    ingest seeds ``pipeline.json::pipelines.default`` from this when a draft
    has no explicit override. Empty tuple means "no override; use the
    backend's ``GET /pipeline`` default." TermNorm sets this to
    ``("llm_only",)`` so a fresh CSV upload skips the heavy nodes
    (``web_search``, ``fuzzy_matching``, ``entity_profiling``,
    ``token_matching``, ``llm_ranking``) — those are the right default
    for the production benchmark but wrong for a tenant's first run."""

    default_exclude_nodes: tuple[str, ...] = ()
    """Nodes the connector recommends excluding by default. Slotted into
    the seed ``campaign.json::exclude_nodes`` block. Empty tuple means
    "exclude nothing."
    """

    default_optimization: tuple[tuple[str, Any], ...] = ()
    """Frozen ``(key, value)`` overrides slotted into the seed
    ``campaign.json::optimization`` block. Lets a connector ship
    domain-specific defaults (e.g. TermNorm pins ``n_variants=3``) without
    the launcher hard-coding the values. Empty mapping means "use
    :class:`OptimizationConfig` schema defaults verbatim." The required
    fields (``improvement_threshold`` / ``degradation_threshold``) MUST be
    present here when the connector intends to seed them — there is no
    silent schema default."""

    default_node_config: Mapping[str, Any] = field(default_factory=dict)
    """Per-node ``pipeline.json::nodes.{name}`` overlay the chat-first ingest
    seeds into a fresh dataset's committed ``pipeline.json``. Keyed by node
    name; each value is a node overlay (``config`` floor + ``optimizer``
    constraints) merged onto the backend's live ``GET /pipeline`` schema (the
    overlay's ``config``/``optimizer`` sub-blocks shallow-merge, so a partial
    clamp narrows the backend schema rather than clobbering it). TermNorm uses
    this to clamp ``llm_only.reasoning_effort`` — origin floor ``low`` plus a
    ``param_allowed_values`` set with ``medium``/``high`` crossed out, so the
    optimizer can never escalate reasoning campaign-wide (a cost rail, not just
    an origin). Empty mapping means "no seed; the backend schema stands."
    Draft ``pipeline_overlay`` (operator edits) layers on top of this."""

    def to_dict(self) -> dict[str, Any]:
        """Identity projection — ``name`` only; callables are not JSON-able."""
        return {"name": self.name}


__all__ = [
    "BackendUnreachableError",
    "Connector",
    "ConnectorExecution",
    "PreflightFn",
    "SessionProtocol",
    "VersionCheck",
    "WireAdapter",
]
