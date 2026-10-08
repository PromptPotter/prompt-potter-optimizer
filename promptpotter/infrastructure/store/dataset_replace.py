"""The data-critical *Replace* path. An in-place overwrite would falsify every prior result — a campaign reads its dataset LIVE
by name — so the old data moves to ``{slug}-vN`` with its dependents, freeing the canonical name LAST, marker written first.

A JOURNAL across three stores: a marker sits under ``.migrations/pending/`` while they are out of
step, ``build_stores`` replays one a crash left, and one file lock orders both across processes."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from filelock import FileLock, Timeout

from promptpotter.infrastructure.store.io import read_json_optional, unlink_robust, write_json
from promptpotter.shared.clock import utcnow_iso

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)

__all__ = ["NothingToReplaceError", "ReplaceResult", "heal_pending_replacements", "replace_dataset"]

_PENDING_DIR = "pending"
_LOCK = ".lock"


@dataclass(frozen=True, slots=True)
class ReplaceResult:
    """Outcome of one version-and-repoint. ``versioned_to`` is the archival name
    the old data now lives under; the counts are what moved with it."""

    slug: str
    versioned_to: str
    repointed_campaigns: int
    restamped_measurements: int


class NothingToReplaceError(Exception):
    """Replace was asked for a slug with no committed dataset behind it — a race / stale-client guard, surfaced as a
    clean 409 rather than a half-run migration."""

    def __init__(self, slug: str) -> None:
        self.slug = slug
        super().__init__(f"no committed dataset named {slug!r} to replace")


def replace_dataset(*, stores: Stores, slug: str) -> ReplaceResult:
    """Does **not** land the new data — the caller re-ingests under the now-free *slug* through
    the normal draft/check-in flow."""
    mig_dir = stores.tenant_datasets.migrations_dir()
    mig_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(str(mig_dir / _LOCK)):
        _replay_pending(stores, mig_dir)
        if not stores.tenant_datasets.slug_exists(slug):
            raise NothingToReplaceError(slug)
        versioned = stores.tenant_datasets.suggest_free_version(slug)
        marker = mig_dir / _PENDING_DIR / f"{uuid.uuid4().hex[:16]}.json"
        write_json(marker, {"from": slug, "to": versioned, "created_at": utcnow_iso()})
        result = _apply(stores, mig_dir, marker)
    logger.info(
        "replaced dataset %s: archived as %s (%d campaigns, %d measurements repointed)",
        slug,
        versioned,
        result.repointed_campaigns,
        result.restamped_measurements,
    )
    return result


def heal_pending_replacements(stores: Stores) -> None:
    """Replay every replace a crash left half-applied. A held lock is a LIVE applier, which
    finishes its own."""
    mig_dir = stores.tenant_datasets.migrations_dir()
    if not (mig_dir / _PENDING_DIR).is_dir():
        return
    lock = FileLock(str(mig_dir / _LOCK))
    try:
        lock.acquire(timeout=0)
    except Timeout:
        return
    try:
        _replay_pending(stores, mig_dir)
    finally:
        lock.release()


def _replay_pending(stores: Stores, mig_dir: Path) -> None:
    for marker in sorted((mig_dir / _PENDING_DIR).glob("*.json")):
        logger.warning("recovering interrupted dataset replace %s", marker.name)
        _apply(stores, mig_dir, marker)


def _apply(stores: Stores, mig_dir: Path, marker: Path) -> ReplaceResult:
    """Each step skips work already done, so replaying a marker is safe at any point it stopped."""
    record = read_json_optional(marker) or {}
    slug, versioned = str(record["from"]), str(record["to"])
    stores.tenant_datasets.version_dataset(slug, versioned)
    result = ReplaceResult(
        slug=slug,
        versioned_to=versioned,
        repointed_campaigns=stores.campaigns.repoint_dataset(slug, versioned),
        restamped_measurements=stores.archive.restamp_dataset(slug, versioned),
    )
    write_json(
        mig_dir / marker.name,
        {
            **record,
            "completed_at": utcnow_iso(),
            "repointed_campaigns": result.repointed_campaigns,
            "restamped_measurements": result.restamped_measurements,
        },
    )
    unlink_robust(marker)
    return result
