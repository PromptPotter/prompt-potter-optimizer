"""Fork a cycle at a divergence point — HITL continuation.

``optimize`` halts with :class:`ResumeDivergenceError` when a recorded
decision re-derives to a different outcome under the current scorer. At
that point the user has two options:

1. Revert ``campaign.json::scoring`` to the original policy and resume
   the recorded trajectory.
2. Commit to the new policy by running ``fork`` — this mints a fresh
   ``cycle_id`` seeded with trials ``0..R-1`` from the parent (copied
   verbatim, where ``R`` is the divergent round) and a ``parent_cycle_id``
   pointer on its ``index.json``. Round ``R`` itself is not copied — it's
   the round the new cycle will re-run under the current scorer. The
   original cycle is untouched; the active session pointer is retargeted
   to the new cycle.

Traces in ``library/dataset_runs/`` are content-addressed and shared —
nothing is copied there. Only the per-cycle trial/candidate JSON tree
under ``campaigns/{cycle_id}/`` is duplicated, which is small.

The fork point is read from ``campaigns/{old_cycle_id}/fork_hint.json``
(written by the resume-divergence walker). Callers that fork without a
hint (exploratory branching) can pass ``fork_from_round`` explicitly.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from promptpotter.infrastructure.store.base import read_json_optional, write_json
from promptpotter.infrastructure.store.stores import Stores, save_active_pointer

__all__ = ["fork_cycle", "load_fork_hint"]

logger = logging.getLogger(__name__)


def _mint_fork_cycle_id(old_cycle_id: str) -> str:
    """Derive a stable-looking new cycle id rooted at the parent."""
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = hashlib.sha256(f"{old_cycle_id}|{ts}".encode()).hexdigest()[:8]
    return f"{old_cycle_id}_fork_{suffix}"


def load_fork_hint(stores: Stores, cycle_id: str) -> dict[str, Any] | None:
    """Read ``campaigns/{cycle_id}/fork_hint.json``; return None if absent."""
    path = stores.campaigns.campaign_dir(cycle_id) / "fork_hint.json"
    return read_json_optional(path)


def fork_cycle(
    stores: Stores,
    tenant_id: str,
    session_id: str,
    backend_id: str,
    old_cycle_id: str,
    fork_from_round: int,
) -> str:
    """Mint a new cycle that redoes round ``fork_from_round`` of ``old_cycle_id``.

    Copies ``index.json`` (rewriting ``campaign_id``, adding
    ``parent_cycle_id`` + ``forked_from_round``), plus trials
    ``0..fork_from_round - 1`` and their candidates verbatim. Round
    ``fork_from_round`` is the one the new cycle will re-run, so it is
    deliberately NOT copied — carrying it over would bring the divergent
    decision that triggered the fork along for the ride. Retargets
    ``.promptpotter/active_session.json`` at the new cycle.

    Returns the new ``cycle_id``.
    """
    old_dir = stores.campaigns.campaign_dir(old_cycle_id)
    if not old_dir.exists():
        raise LookupError(f"cycle {old_cycle_id!r} not found at {old_dir}")

    new_cycle_id = _mint_fork_cycle_id(old_cycle_id)
    new_dir = stores.campaigns.campaign_dir(new_cycle_id)
    if new_dir.exists():
        raise FileExistsError(f"forked cycle dir already exists: {new_dir}")

    _copy_index(old_dir, new_dir, old_cycle_id, new_cycle_id, fork_from_round)
    _copy_trials(old_dir, new_dir, fork_from_round)
    _copy_candidates(old_dir, new_dir, fork_from_round)

    # Remove fork_hint.json from the old cycle — consumed. The new cycle
    # starts clean; if it diverges again later, a fresh hint is written.
    (old_dir / "fork_hint.json").unlink(missing_ok=True)

    save_active_pointer(tenant_id, session_id, new_cycle_id)
    logger.info(
        "Forked cycle %s → %s at round %d; active pointer retargeted",
        old_cycle_id,
        new_cycle_id,
        fork_from_round,
    )
    return new_cycle_id


def _copy_index(
    old_dir: Path,
    new_dir: Path,
    old_cycle_id: str,
    new_cycle_id: str,
    fork_from_round: int,
) -> None:
    src = old_dir / "index.json"
    data = read_json_optional(src) or {}

    # Keep surviving trials (rounds 0..R) in the index; recompute best.
    survivors = [t for t in (data.get("trials") or []) if t.get("round", -1) < fork_from_round]
    best_acc = 0.0
    best_trial_id: str | None = None
    for t in survivors:
        if t.get("accuracy", 0.0) > best_acc:
            best_acc = t["accuracy"]
            best_trial_id = t.get("trial_id")

    new_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    data.update(
        {
            "campaign_id": new_cycle_id,
            "parent_cycle_id": old_cycle_id,
            "forked_from_round": fork_from_round,
            "forked_at": now,
            "trials": survivors,
            "n_trials": len(survivors),
            "best_accuracy": best_acc,
            "best_trial_id": best_trial_id,
            "status": "resumed",
            "updated_at": now,
        }
    )
    write_json(new_dir / "index.json", data)


def _copy_trials(old_dir: Path, new_dir: Path, fork_from_round: int) -> None:
    src = old_dir / "trials"
    dst = new_dir / "trials"
    if not src.exists():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for p in sorted(src.glob("trial_*.json")):
        try:
            n = int(p.stem.removeprefix("trial_"))
        except ValueError:
            continue
        if n < fork_from_round:
            shutil.copyfile(p, dst / p.name)


def _copy_candidates(old_dir: Path, new_dir: Path, fork_from_round: int) -> None:
    src = old_dir / "candidates"
    dst = new_dir / "candidates"
    if not src.exists():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for p in sorted(src.glob("round_*.json")):
        try:
            n = int(p.stem.removeprefix("round_"))
        except ValueError:
            continue
        if n < fork_from_round:
            shutil.copyfile(p, dst / p.name)
