"""Composite ``Stores`` bundle — frozen dataclass + builder.

Also owns :class:`OptimizerCallCache` and :func:`hash_call` — content-addressed
cache for optimizer LLM responses, keyed by SHA-256 of byte-identical inputs.
Cross-cycle / cross-fork by construction; mirrors :class:`MeasurementArchive`'s
file-per-record pattern at ``<base_dir>/archive/optimizer_calls/{hash}.json``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from promptpotter.infrastructure.store.backend_store import BackendStore
from promptpotter.infrastructure.store.base import (
    read_json_optional,
    validate_path_component,
    write_json,
)
from promptpotter.infrastructure.store.campaign_store import CampaignStore
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive
from promptpotter.infrastructure.store.paths import (
    DEFAULT_DATASETS_ROOT,
    DEFAULT_PROJECTS_ROOT,
    DEFAULT_TENANT_ID,
)
from promptpotter.infrastructure.store.session_store import SessionStore
from promptpotter.infrastructure.store.sweep_store import SweepStore
from promptpotter.shared.hashing import HASH_TRUNCATE

logger = logging.getLogger(__name__)


def hash_call(
    *,
    messages: list[dict[str, str]],
    model: str | None,
    provider: str,
    temperature: float,
    json_schema: dict | None,
    response_model: str | None = None,
) -> str:
    """SHA-256 (truncated to 24 hex) of the byte-identical LLM-call inputs.

    ``response_model`` is the Pydantic model's ``__name__`` when the call
    used typed output (see ``OPTIMIZER_RESPONSE_MODELS``) — keeping it in
    the key lets a cached typed response and a cached dict response live
    side-by-side without colliding.
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
    """File-backed cache for optimizer LLM responses keyed by input hash.

    Consumed by :func:`promptpotter.application.optimization.dispatch.llm_call.llm_call`
    — if a hash hits, the stored ``LLMResponse.model_dump()`` is replayed and
    the real LLM call is skipped.
    """

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
    """Composite bundle of focused stores rooted at a per-tenant ``base_dir``.

    Construct via :func:`build_stores`. ``base_dir`` is the tenant root
    (``{projects_root}/{tenant_id}/``). Sessions and campaigns are peer
    trees under the tenant; a campaign records its parent session via
    ``index.json::parent_session_id``.
    """

    base_dir: Path
    tenant_id: str
    backends: BackendStore
    sessions: SessionStore
    campaigns: CampaignStore
    sweeps: SweepStore
    archive: MeasurementArchive
    optimizer_calls: OptimizerCallCache


def build_stores(
    projects_root: Path | str | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    datasets_root: Path | str | None = None,
) -> Stores:
    """Assemble a :class:`Stores` bundle rooted under a tenant.

    ``projects_root`` defaults to ``<repo_root>/.promptpotter/projects``.
    ``tenant_id`` defaults to ``"default"`` — the single-user CLI tenant.
    ``datasets_root`` defaults to ``<repo_root>/datasets`` — named dataset
    caches live there to survive ``.promptpotter/`` resets. Tests pass
    ``tmp_path`` to isolate from the real repo tree.
    """
    validate_path_component(tenant_id)
    root = Path(projects_root) if projects_root else DEFAULT_PROJECTS_ROOT
    tenant_dir = root / tenant_id
    ds_root = Path(datasets_root) if datasets_root else DEFAULT_DATASETS_ROOT
    return Stores(
        base_dir=tenant_dir,
        tenant_id=tenant_id,
        backends=BackendStore(tenant_dir, ds_root),
        sessions=SessionStore(tenant_dir),
        campaigns=CampaignStore(tenant_dir),
        sweeps=SweepStore(tenant_dir),
        archive=MeasurementArchive(tenant_dir),
        optimizer_calls=OptimizerCallCache(tenant_dir),
    )


__all__ = ["OptimizerCallCache", "Stores", "build_stores", "hash_call"]
