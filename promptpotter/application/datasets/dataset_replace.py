"""``version_and_repoint`` — the data-critical *Replace* path for dataset-name collisions.

An in-place overwrite would falsify every prior result: a campaign reads its dataset
**live** by name, and measurements are stamped with that name. So the old data moves to
an archival ``{slug}-vN`` and its dependents move with it — data, campaign pins,
measurement stamps, each through its owning store — freeing the canonical name last. A
``datasets/.migrations/{id}.json`` marker is written *before* any move and every step is
idempotent, so :func:`recover_pending_replacements` finishes a half-done migration; the
version → repoint order makes the only reachable crash state recoverable, never *wrong*.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from promptpotter.infrastructure.store.io import read_json_optional, write_json
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.clock import utcnow_iso

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReplaceResult:
    """Outcome of one version-and-repoint. ``versioned_to`` is the archival name
    the old data now lives under; the counts are what moved with it."""

    slug: str
    versioned_to: str
    repointed_campaigns: int
    restamped_measurements: int


class NothingToReplaceError(Exception):
    """Replace was asked for a slug with no committed tenant dataset behind it.

    The collision card only offers Replace when the name is taken, so this is a
    race / stale-client guard (the dataset was removed between the 409 and the
    click), surfaced as a clean 409 rather than a half-run migration."""

    def __init__(self, slug: str) -> None:
        self.slug = slug
        super().__init__(f"no committed dataset named {slug!r} to replace")


def version_and_repoint(*, stores: Stores, slug: str) -> ReplaceResult:
    """Heals any prior crashed replace first. Does **not** land the new data — the
    caller re-ingests under the now-free *slug* through the normal draft/check-in flow.
    """
    recover_pending_replacements(stores=stores)
    if not stores.tenant_datasets.slug_exists(slug):
        raise NothingToReplaceError(slug)
    versioned = stores.tenant_datasets.suggest_free_version(slug)
    marker_path = _write_marker(stores, slug=slug, versioned=versioned)
    result = _apply(stores, slug=slug, versioned=versioned)
    _complete_marker(marker_path, result)
    logger.info(
        "replaced dataset %s: archived as %s (%d campaigns, %d measurements repointed)",
        slug,
        versioned,
        result.repointed_campaigns,
        result.restamped_measurements,
    )
    return result


def recover_pending_replacements(*, stores: Stores) -> None:
    """Re-run any migration whose marker is still ``pending`` (a prior crash).

    Idempotent — each step skips work already done, so a fully-completed-but-
    unflipped marker re-applies as a no-op before being marked done. Cheap when
    there's nothing pending (one scan of a usually-absent ``.migrations/`` dir).
    """
    mig_dir = stores.tenant_datasets.migrations_dir()
    if not mig_dir.is_dir():
        return
    for marker_path in sorted(mig_dir.glob("*.json")):
        marker = read_json_optional(marker_path)
        if not isinstance(marker, dict) or marker.get("status") != "pending":
            continue
        slug, versioned = marker.get("from"), marker.get("to")
        if not isinstance(slug, str) or not isinstance(versioned, str) or not slug or not versioned:
            continue
        logger.warning("recovering interrupted dataset replace %s -> %s", slug, versioned)
        result = _apply(stores, slug=slug, versioned=versioned)
        _complete_marker(marker_path, result)


def _apply(stores: Stores, *, slug: str, versioned: str) -> ReplaceResult:
    """The three idempotent moves — data, then pins, then measurements."""
    stores.tenant_datasets.version_dataset(slug, versioned)
    repointed = stores.campaigns.repoint_dataset(slug, versioned)
    restamped = stores.archive.restamp_dataset(slug, versioned)
    return ReplaceResult(
        slug=slug,
        versioned_to=versioned,
        repointed_campaigns=repointed,
        restamped_measurements=restamped,
    )


def _write_marker(stores: Stores, *, slug: str, versioned: str) -> Path:
    mig_id = uuid.uuid4().hex[:16]
    path = stores.tenant_datasets.migrations_dir() / f"{mig_id}.json"
    write_json(
        path,
        {
            "id": mig_id,
            "from": slug,
            "to": versioned,
            "status": "pending",
            "created_at": utcnow_iso(),
        },
    )
    return path


def _complete_marker(path: Path, result: ReplaceResult) -> None:
    data = read_json_optional(path) or {}
    data.update(
        {
            "status": "completed",
            "completed_at": utcnow_iso(),
            "repointed_campaigns": result.repointed_campaigns,
            "restamped_measurements": result.restamped_measurements,
        }
    )
    write_json(path, data)


__all__ = [
    "NothingToReplaceError",
    "ReplaceResult",
    "recover_pending_replacements",
    "version_and_repoint",
]
