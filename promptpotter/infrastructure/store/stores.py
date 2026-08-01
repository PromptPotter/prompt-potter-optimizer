"""Composite ``Stores`` bundle + content-addressed ``OptimizerCallCache``, and the
descent that reaches a nested one.

Cache is SHA-256-keyed, cross-cycle/cross-fork; file-per-record at
``<base_dir>/archive/optimizer_calls/{hash}.json`` (mirror of MeasurementArchive).

:func:`inner_sandbox_store` / :func:`descend_store` / :func:`resolve_cycle_path` live
here because they CONSTRUCT stores — they are :func:`build_stores` re-entered at a
deeper root, not request plumbing. Keep them out of the FastAPI dep module: a
``store/`` view could then not recurse without an ``infrastructure -> presentation``
import.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from promptpotter.config.paths import benchmark_datasets_root
from promptpotter.domain.cycle_paths import CycleHop, CyclePath, WorkspaceDir
from promptpotter.domain.identity import TenantId
from promptpotter.infrastructure.store.backend_store import BackendStore
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.checkin_draft_store import CheckinDraftStore
from promptpotter.infrastructure.store.diagnostic_run_store import DiagnosticRunStore
from promptpotter.infrastructure.store.io import (
    read_json_optional,
    validate_path_component,
    write_json,
)
from promptpotter.infrastructure.store.layout import inner_sandbox_dir, tenant_workspace
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive
from promptpotter.infrastructure.store.session_store import SessionStore
from promptpotter.infrastructure.store.sweep_store import SweepStore
from promptpotter.infrastructure.store.tenant_dataset_store import TenantDatasetStore
from promptpotter.infrastructure.store.user_store import UserStore
from promptpotter.shared.errors import BadRequestError, NotFoundError
from promptpotter.shared.hashing import HASH_TRUNCATE
from promptpotter.shared.identity import IdentityContext

logger = logging.getLogger(__name__)


def hash_call(
    *,
    messages: list[dict[str, str]],
    model: str | None,
    provider: str,
    temperature: float,
    json_schema: dict[str, Any] | None,
    response_model: str | None = None,
    seed: int | None = None,
) -> str:
    """SHA-256 (24 hex) of byte-identical LLM-call inputs.

    ``response_model`` (Pydantic ``__name__``) in the key lets typed + dict
    responses cohabit without colliding. ``seed`` is a decoding input like
    ``temperature`` — omitting it would serve one seed's answer to another.
    """
    blob = json.dumps(
        {
            "messages": messages,
            "model": model,
            "provider": provider,
            "temperature": temperature,
            "json_schema": json_schema,
            "response_model": response_model,
            "seed": seed,
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:HASH_TRUNCATE]


class OptimizerCallCache:
    """File-backed optimizer-LLM cache; ``llm_call`` replays the stored ``LLMResponse.model_dump()`` on hash hit."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    def _dir(self) -> Path:
        return self._base_dir / "archive" / "optimizer_calls"

    def _path(self, key: str) -> Path:
        return self._dir() / f"{key}.json"

    def load(self, key: str) -> dict[str, Any] | None:
        """Return the cached ``LLMResponse.model_dump()`` dict, or ``None``."""
        return read_json_optional(self._path(key))

    def save(self, key: str, value: dict[str, Any]) -> None:
        """Persist ``value`` under ``key``. ``value`` is ``LLMResponse.model_dump()``."""
        write_json(self._path(key), value)
        logger.debug("OptimizerCallCache: saved %s", key)


@dataclass(frozen=True)
class Stores:
    """Composite of focused stores rooted at the tenant ``base_dir``.

    Sessions + campaigns are peer trees under the tenant; campaign records its
    parent session via ``index.json::parent_session_id``. Construct via :func:`build_stores`.

    ``identity`` carries the full Stage-0 :class:`IdentityContext` — read
    ``identity.tenant_id`` for the tenant slug. There is no ``Stores.tenant_id``
    field (per identity-foundation no-drift gate #4: ``IdentityContext.tenant_id``
    is the only source of tenant scope).

    ``projects_root`` is the parent-of-all-tenant-dirs; ``base_dir`` is this tenant's
    own root under it, a :data:`WorkspaceDir` (``store/layout.py::tenant_workspace``
    owns the relation — do not re-derive it, and do not re-walk ``base_dir.parent``).
    The two differ by one segment and are both ``Path`` on disk, which is why the
    newtype exists: everything keyed on a tenant — the active-session pointer, the
    workspace ledger, every ``*_dir_for`` builder — takes ``base_dir``, and passing
    ``projects_root`` there is now a type error rather than a silent tenant escape.

    ``shared_root`` is the workspace root holding the two CONTENT-ADDRESSED caches
    (``archive`` + ``optimizer_calls``). It equals ``projects_root`` for every normal
    run and DIVERGES only inside an L4 inner sandbox, where campaign state is rooted at
    ``.inner/<key>`` but the caches must stay tenant-global. Keys are content
    hashes, so a hit is the same measurement by construction — isolating them would not
    make a run safer, only re-pay for it (and redraw the origin's stochastic score, which
    the outer fitness subtracts). It is a field rather than a re-walk of ``projects_root``
    so it survives recursion: an L5 sandbox reads its parent's ``shared_root``, not its own.

    ``benchmarks_root`` is the install-global benchmark DEFINITIONS dir (repo
    benchmarks, shared across tenants) — and **read-only**: under a wheel it resolves
    inside ``site-packages`` (``config/paths.py::benchmark_datasets_root``). Nothing
    writes there; a benchmark's materialized rows are the operator's and land in the
    tenant tree. Access is capability-gated — go through ``store/dataset_access.py``,
    never read this path directly from a handler.
    """

    base_dir: WorkspaceDir
    projects_root: Path
    shared_root: Path
    benchmarks_root: Path
    identity: IdentityContext
    backends: BackendStore
    tenant_datasets: TenantDatasetStore
    sessions: SessionStore
    campaigns: CampaignStore
    checkin: CheckinDraftStore
    sweeps: SweepStore
    archive: MeasurementArchive
    optimizer_calls: OptimizerCallCache
    diagnostic_runs: DiagnosticRunStore
    users: UserStore

    @property
    def tenant_id(self) -> TenantId:
        return self.identity.tenant_id


def build_stores(
    identity: IdentityContext,
    *,
    projects_root: Path,
    benchmarks_root: Path | None = None,
    shared_root: Path | None = None,
) -> Stores:
    """Assemble an :class:`IdentityContext`-rooted :class:`Stores` bundle.

    ``projects_root`` is REQUIRED, and that is the whole point: it used to default to
    the process-global ``DEFAULT_PROJECTS_ROOT``, so eleven of twelve callers omitted
    it and a store built anywhere inside an L4 sandbox silently addressed the operator's
    real workspace. An entry point that genuinely means the process-global workspace
    still says so — it just says it out loud, and every caller downstream of one is now
    forced by mypy to pass the root it already holds. The tenant slug rides
    ``identity.tenant_id``; use :func:`~promptpotter.shared.identity.default_identity`
    for Stage-0 single-operator callers.

    ``benchmarks_root`` defaults to the install-global benchmark definitions
    (``<repo>/datasets`` in a checkout, ``assets/benchmarks/`` under a wheel) — that one
    is a property of the INSTALL, not of the workspace, so it has no tenant to escape.

    ``shared_root`` roots the two content-addressed caches (``archive`` +
    ``optimizer_calls``) somewhere other than ``projects_root``. Its ONE caller is the
    L4 inner sandbox (``runner/inner``), which must isolate campaign STATE without
    isolating the tenant-global measurement cache. Omit it and the caches sit under
    ``projects_root``, as they do for every non-recursive run.
    """
    root = projects_root
    tenant_dir = tenant_workspace(root, identity.tenant_id)
    shared = shared_root if shared_root is not None else root
    shared_tenant = shared / identity.tenant_id
    bench_root = benchmarks_root if benchmarks_root is not None else benchmark_datasets_root()
    return Stores(
        base_dir=tenant_dir,
        projects_root=root,
        shared_root=shared,
        benchmarks_root=bench_root,
        identity=identity,
        backends=BackendStore(tenant_dir),
        tenant_datasets=TenantDatasetStore(tenant_dir),
        sessions=SessionStore(tenant_dir),
        campaigns=CampaignStore(tenant_dir),
        checkin=CheckinDraftStore(tenant_dir),
        sweeps=SweepStore(tenant_dir),
        archive=MeasurementArchive(shared_tenant),
        optimizer_calls=OptimizerCallCache(shared_tenant),
        diagnostic_runs=DiagnosticRunStore(tenant_dir),
        users=UserStore(tenant_dir),
    )


def inner_sandbox_store(
    store: Stores, outer_campaign_id: str, outer_cycle_id: str
) -> Stores | None:
    """A :class:`Stores` rooted at the inner sandbox of ``(outer_campaign_id, outer_cycle_id)``.

    L4 (``promptpotter-self``) runs each candidate as a real inner campaign under a flat,
    off-registry sandbox. That sandbox is structurally a normal projects tree, so pointing
    :func:`build_stores` at it lets every existing read work verbatim. ``None`` when the
    cycle spawned no inner campaigns (non-L4, or the loop hasn't recursed yet) — callers
    degrade to an empty list / 404, never an error.

    **The campaign is half of the key, not decoration.** ``cycle_id`` is content-addressed
    on the origin and so is shared by every campaign minted from it; keyed on the cycle
    alone, two campaigns on one origin served each other's inner fan-out through
    ``?descend=`` / ``/tree`` / ``events:subscribe``. :func:`descend_store` validated
    ``hop.campaign_id`` and then dropped it — the caller always knew, the key just refused
    to take it.

    Anchored on ``shared_root`` — the REAL workspace root, invariant across depth — exactly
    as the writer is, and carried into the sandbox store for the same reason.
    ``projects_root`` is NOT a valid anchor: inside a sandbox it already IS the sandbox, so
    an L5 hop would read ``.inner/.inner/…`` while the writer wrote one level up, and the
    sandbox's ``archive`` + ``optimizer_calls`` would root under the sandbox instead of
    staying tenant-global. The two roots coincide at depth 1, which is why neither had fired.
    """
    sandbox_root = inner_sandbox_dir(
        store.shared_root, str(store.tenant_id), outer_campaign_id, outer_cycle_id
    )
    if not (sandbox_root / store.tenant_id).is_dir():
        return None
    return build_stores(store.identity, projects_root=sandbox_root, shared_root=store.shared_root)


def descend_store(store: Stores, hops: CyclePath) -> Stores:
    """Fold ``.inner/<key>`` over *hops* — THE recursive step, and the one
    place a nested store is reached.

    Each hop descends INTO that hop's cycle, so the returned store is the one whose
    tree contains the hop chain's last sandbox; ``()`` is the caller's own store.
    :func:`inner_sandbox_store` is re-entrant, so depth is unbounded by construction
    (L4, L5, … all fold identically). Every component is char-validated before any
    filesystem touch (400 on bad chars); a missing sandbox is a 404.
    """
    cur = store
    for hop in hops:
        try:
            validate_path_component(hop.campaign_id)
            validate_path_component(hop.cycle_id)
        except ValueError as exc:
            raise BadRequestError(str(exc)) from exc
        nxt = inner_sandbox_store(cur, hop.campaign_id, hop.cycle_id)
        if nxt is None:
            raise NotFoundError(f"No inner sandbox for cycle '{hop.cycle_id}'")
        cur = nxt
    return cur


def resolve_cycle_path(store: Stores, path: CyclePath) -> tuple[Stores, CycleHop]:
    """Resolve a :data:`CyclePath` (root → leaf) to ``(Stores, leaf)``.

    What makes an inner cycle addressable exactly like a top-level one: the leaf
    names an entity, and every hop before it is a descent (:func:`descend_store`).
    Hop 0 is a cycle in the caller's own tree.
    """
    if not path:
        raise BadRequestError("empty cycle path")
    leaf = path[-1]
    try:
        validate_path_component(leaf.campaign_id)
        validate_path_component(leaf.cycle_id)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    return descend_store(store, path[:-1]), leaf


__all__ = [
    "OptimizerCallCache",
    "Stores",
    "build_stores",
    "descend_store",
    "hash_call",
    "inner_sandbox_store",
    "resolve_cycle_path",
]
