"""Composite ``Stores`` bundle + content-addressed ``OptimizerCallCache``.

Cache is SHA-256-keyed, cross-cycle/cross-fork; file-per-record at
``<base_dir>/archive/optimizer_calls/{hash}.json`` (mirror of MeasurementArchive).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from promptpotter.domain.identity import TenantId
from promptpotter.infrastructure.store.backend_store import BackendStore
from promptpotter.infrastructure.store.campaign_store import CampaignStore
from promptpotter.infrastructure.store.checkin_draft_store import CheckinDraftStore
from promptpotter.infrastructure.store.diagnostic_run_store import DiagnosticRunStore
from promptpotter.infrastructure.store.io import (
    read_json_optional,
    write_json,
)
from promptpotter.infrastructure.store.layout import (
    DEFAULT_DATASETS_ROOT,
    DEFAULT_PROJECTS_ROOT,
)
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive
from promptpotter.infrastructure.store.session_store import SessionStore
from promptpotter.infrastructure.store.sweep_store import SweepStore
from promptpotter.infrastructure.store.tenant_dataset_store import TenantDatasetStore
from promptpotter.infrastructure.store.user_store import UserStore
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
) -> str:
    """SHA-256 (24 hex) of byte-identical LLM-call inputs.

    ``response_model`` (Pydantic ``__name__``) in the key lets typed + dict
    responses cohabit without colliding.
    """
    blob = json.dumps(
        {
            "messages": messages,
            "model": model,
            "provider": provider,
            "temperature": temperature,
            "json_schema": json_schema,
            "response_model": response_model,
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

    ``projects_root`` is the parent-of-all-tenant-dirs (the workspace root).
    The relation is fixed: ``base_dir = projects_root / identity.tenant_id``.
    Use ``projects_root`` directly — do not re-walk ``base_dir.parent``.

    ``shared_root`` is the workspace root holding the two CONTENT-ADDRESSED caches
    (``archive`` + ``optimizer_calls``). It equals ``projects_root`` for every normal
    run and DIVERGES only inside an L4 inner sandbox, where campaign state is rooted at
    ``.inner/<cycle_id>`` but the caches must stay tenant-global. Keys are content
    hashes, so a hit is the same measurement by construction — isolating them would not
    make a run safer, only re-pay for it (and redraw the origin's stochastic score, which
    the outer fitness subtracts). It is a field rather than a re-walk of ``projects_root``
    so it survives recursion: an L5 sandbox reads its parent's ``shared_root``, not its own.

    ``benchmarks_root`` is the install-global ``datasets/`` dir (repo benchmarks,
    shared across tenants). Access to it is capability-gated — go through
    ``store/dataset_access.py``, never read this path directly from a handler.
    """

    base_dir: Path
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
        """Convenience accessor — equals ``self.identity.tenant_id``."""
        return self.identity.tenant_id


def build_stores(
    identity: IdentityContext,
    *,
    projects_root: Path | str | None = None,
    datasets_root: Path | str | None = None,
    shared_root: Path | str | None = None,
) -> Stores:
    """Assemble an :class:`IdentityContext`-rooted :class:`Stores` bundle.

    Defaults: ``projects_root=<repo>/.promptpotter/projects``,
    ``datasets_root=<repo>/datasets`` (survives ``.promptpotter/`` resets).
    Tests pass ``tmp_path``. The tenant slug rides ``identity.tenant_id`` —
    use :func:`~promptpotter.shared.identity.default_identity` for Stage-0
    single-operator callers.

    ``shared_root`` roots the two content-addressed caches (``archive`` +
    ``optimizer_calls``) somewhere other than ``projects_root``. Its ONE caller is the
    L4 inner sandbox (``inner_recursion``), which must isolate campaign STATE without
    isolating the tenant-global measurement cache. Omit it and the caches sit under
    ``projects_root``, as they do for every non-recursive run.
    """
    root = Path(projects_root) if projects_root else DEFAULT_PROJECTS_ROOT
    tenant_dir = root / identity.tenant_id
    shared = Path(shared_root) if shared_root else root
    shared_tenant = shared / identity.tenant_id
    ds_root = Path(datasets_root) if datasets_root else DEFAULT_DATASETS_ROOT
    return Stores(
        base_dir=tenant_dir,
        projects_root=root,
        shared_root=shared,
        benchmarks_root=ds_root,
        identity=identity,
        backends=BackendStore(tenant_dir, ds_root),
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


__all__ = ["OptimizerCallCache", "Stores", "build_stores", "hash_call"]
