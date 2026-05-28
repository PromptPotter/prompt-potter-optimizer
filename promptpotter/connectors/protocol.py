"""Connector protocol — bundles backend-specific hooks under one shape.

A connector packages everything PromptPotter needs to talk to a specific
backend kind. Additional connectors are added by writing one new file in
this package.

Hooks:

- ``wire_adapter`` — outbound HTTP body shape for ``BackendClient.run_query``.
- ``session_factory`` — fresh ``SessionProtocol`` instance per ``BackendClient``.
- ``extract_experiment`` — backend experiment-data → ``(queries, index_terms)``.
- ``resolve_ground_truth`` — single-query ground-truth lookup over experiment
  data (currently unused by core paths but reserved for trace-ingestion flows).
- ``preflight`` — async reachability probe invoked before any command that
  needs the backend live (R2). Raises :class:`BackendUnreachableError`
  when the connector reports the backend is down; in-process connectors
  (e.g. ``promptpotter``) opt out by leaving the field ``None``.

Adding a connector is intentionally local to ``connectors/<name>.py`` — no
edits to ``application/config.py`` or ``infrastructure/backend.py``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.domain.connector import SessionProtocol, WireAdapter

if TYPE_CHECKING:
    import httpx

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

    resolve_ground_truth: Callable[[dict[str, Any], str], str | None]
    """Resolve ground truth for a single query string from experiment data."""

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
    the seed ``campaign.json::exclude_nodes`` block. TermNorm sets this to
    ``("llm_ranking",)`` because that node is currently broken
    (~50% ``json_validate_failed``). Empty tuple means "exclude nothing."
    """

    default_optimization: tuple[tuple[str, Any], ...] = ()
    """Frozen ``(key, value)`` overrides slotted into the seed
    ``campaign.json::optimization`` block. Lets a connector ship
    domain-specific defaults (e.g. TermNorm pins ``n_variants=3`` to keep
    first-time Groq-tier tenants under the 8k TPM cap on the L1 meta-prompt)
    without the launcher hard-coding the values. Empty tuple means "use
    :class:`OptimizationConfig` schema defaults verbatim." The required
    fields (``improvement_threshold`` / ``degradation_threshold``) MUST be
    present here when the connector intends to seed them — there is no
    silent schema default."""

    def to_dict(self) -> dict[str, Any]:
        """Identity projection — ``name`` only; callables are not JSON-able."""
        return {"name": self.name}


__all__ = [
    "BackendUnreachableError",
    "Connector",
    "PreflightFn",
    "SessionProtocol",
    "VersionCheck",
    "WireAdapter",
]
