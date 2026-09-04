"""The store CONSTRUCTORS live here rather than in the FastAPI dep module — a ``store/`` view
could then not recurse without an ``infrastructure -> presentation`` import."""

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
from promptpotter.infrastructure.store.layout import (
    JUDGE_REUSE_DIR,
    OPTIMIZER_REUSE_DIR,
    inner_sandbox_dir,
    tenant_workspace,
)
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
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
) -> str:
    """The key covers EVERY input that can change the answer, and nothing else.

    ``response_model`` lets typed and dict responses cohabit. ``seed``, ``max_tokens`` and
    ``reasoning_effort`` are decoding inputs exactly as ``temperature`` is — omitting any one of
    them serves one configuration's answer to another. ``max_tokens`` is the one with a reachable
    caller: ``JudgeStage.max_tokens`` is a declared field, so two graders differing only in their
    cap would otherwise share a cached reply, one of them truncated."""
    blob = json.dumps(
        {
            "messages": messages,
            "model": model,
            "provider": provider,
            "temperature": temperature,
            "json_schema": json_schema,
            "response_model": response_model,
            "seed": seed,
            "max_tokens": max_tokens,
            "reasoning_effort": reasoning_effort,
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:HASH_TRUNCATE]


class LLMReuseCache:
    """Content-addressed LLM answers under ONE namespace: on a hash hit the caller replays the
    stored ``LLMResponse.model_dump()`` rather than re-sampling it. Named for the reuse, not for the
    calls — the ledger's ``LLMCallStartRecord`` already owns the chronology of the same events.

    ``namespace`` IS the directory, and the instances never share one. The optimizer's answers and a
    judge's are both LLM replies, but a grader able to read the loop's cache would be a ruler fed by
    the thing it measures — and the two are separately purgeable only while they are separate trees.
    """

    def __init__(self, base_dir: Path, namespace: str):
        self._namespace = namespace
        self._dir = base_dir / namespace

    def _path(self, key: str) -> Path:
        return self._dir / f"{key}.json"

    def load(self, key: str) -> dict[str, Any] | None:
        return read_json_optional(self._path(key))

    def save(self, key: str, value: dict[str, Any]) -> None:
        write_json(self._path(key), value)
        logger.debug("LLMReuseCache[%s]: saved %s", self._namespace, key)


@dataclass(frozen=True)
class Stores:
    """``base_dir`` is this tenant's own root and a ``WorkspaceDir``, not ``projects_root`` one
    segment up: everything keyed on a tenant takes it, so a mix-up is a type error, not an escape."""

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
    optimizer_reuse: LLMReuseCache
    # Scoring's own reuse cache, and a SECOND instance rather than a shared one: see
    # `LLMReuseCache`. Rooted on `shared_root` with the other two, because a judge grading an
    # identical (question, gold, answer) must hit whatever measured it first — including across a
    # campaign delete, and including from inside an L4 sandbox.
    judge_reuse: LLMReuseCache
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
    """``projects_root`` is REQUIRED on purpose — defaulted, it goes unpassed, and a store built
    inside an L4 sandbox then silently addresses the operator's real workspace."""
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
        optimizer_reuse=LLMReuseCache(shared_tenant, OPTIMIZER_REUSE_DIR),
        judge_reuse=LLMReuseCache(shared_tenant, JUDGE_REUSE_DIR),
        diagnostic_runs=DiagnosticRunStore(tenant_dir),
        users=UserStore(tenant_dir),
    )


def inner_sandbox_store(
    stores: Stores, outer_campaign_id: str, outer_cycle_id: str
) -> Stores | None:
    """The campaign is half of the key, not decoration. Anchored on ``shared_root`` — the real
    workspace root, invariant across depth — exactly as the writer is."""
    sandbox_root = inner_sandbox_dir(
        stores.shared_root,
        str(stores.tenant_id),
        CycleHop(campaign_id=outer_campaign_id, cycle_id=outer_cycle_id),
    )
    if not (sandbox_root / stores.tenant_id).is_dir():
        return None
    return build_stores(stores.identity, projects_root=sandbox_root, shared_root=stores.shared_root)


def descend_store(stores: Stores, hops: CyclePath) -> Stores:
    """THE recursive step. Each hop descends INTO that hop's cycle and ``()`` is the caller's own
    store; :func:`inner_sandbox_store` is re-entrant, so depth is unbounded by construction."""
    cur = stores
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


def resolve_cycle_path(stores: Stores, path: CyclePath) -> tuple[Stores, CycleHop]:
    """What makes an inner cycle addressable exactly like a top-level one: the leaf names an entity
    and every hop before it is a descent. Hop 0 is a cycle in the caller's own tree."""
    if not path:
        raise BadRequestError("empty cycle path")
    leaf = path[-1]
    try:
        validate_path_component(leaf.campaign_id)
        validate_path_component(leaf.cycle_id)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    return descend_store(stores, path[:-1]), leaf


__all__ = [
    "LLMReuseCache",
    "Stores",
    "build_stores",
    "descend_store",
    "hash_call",
    "inner_sandbox_store",
    "resolve_cycle_path",
]
