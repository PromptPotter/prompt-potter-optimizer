"""Unified fork-mint primitive — :func:`_mint_fork` dispatches on trigger.

Wired triggers: ``SCORING_DIVERGENCE``, ``OPERATOR_DIAG``,
``OPERATOR_SWEEP``. Each differs in inheritance, cycle-id encoding, and
dir routing — the match arm below owns those mechanics; shared setup
lives in :func:`_fork_sibling_setup`. Cycle-id encoding and dir layout
are owned by :mod:`promptpotter.infrastructure.store.paths`.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application.optimization.resume_and_fork.decisions import (
    ResumeCheckpointKind,
    record_decision,
)
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import ForkPayload, ForkTrigger
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store import (
    read_active_pointer,
    root_cycle_id,
    save_active_pointer,
)
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.infrastructure.store import CampaignStore

logger = logging.getLogger(__name__)

__all__ = ["ForkResult", "_mint_fork", "cleanup_stub_fork_if_empty"]


class ForkResult(NamedTuple):
    """Resume detected divergence and forked into a sibling cycle."""

    new_cycle_id: str
    new_resumed_from_round: int


def _fork_sibling_setup(
    campaign_store: CampaignStore,
    tenant_id: str,
    session_id: str,
    parent_cycle_id: str,
    new_cycle_id: str,
    *,
    from_round: int,
    payload: ForkPayload,
    sweep_batch_id: str | None = None,
    source_file: str | None = None,
) -> str:
    """Common plumbing: dir create, FORK_CUT append, pointer + log. Returns ``now_iso``.

    ``data.fork`` carries the typed payload; ``source_file`` /
    ``sweep_batch_id`` stay at top level so ``existing_fork_source_files``
    dedup keeps working without parsing the typed payload.
    """
    parent_dir = campaign_store.campaign_dir(parent_cycle_id)
    new_dir = campaign_store.campaign_dir(new_cycle_id)
    if new_dir.exists():
        raise FileExistsError(f"forked cycle dir already exists: {new_dir}")
    new_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC).isoformat()
    record_data: dict[str, Any] = {
        "forked_at": now,
        "fork": payload.model_dump(mode="json"),
    }
    if source_file is not None:
        record_data["source_file"] = source_file
    if sweep_batch_id is not None:
        record_data["sweep_batch_id"] = sweep_batch_id

    with graceful("FORK_CUT decision append failed"):
        record_decision(
            CycleEventLog.open(CycleDir(parent_dir)),
            ResumeCheckpointKind.FORK_CUT,
            {"from_round": from_round},
            new_cycle_id,
            data=record_data,
        )

    save_active_pointer(tenant_id, session_id, new_cycle_id)
    logger.info(
        "Forked %s → %s at round %d [trigger=%s] (active pointer retargeted)",
        parent_cycle_id,
        new_cycle_id,
        from_round,
        payload.trigger.value,
    )
    return now


def _next_diag_sibling_id(campaign_store: CampaignStore, parent_cycle_id: str) -> str:
    """Next ``{root}_diag_NNN`` id; siblings root at the family root so the BFS tree stays one level deep."""
    root_id = root_cycle_id(parent_cycle_id)
    diag_dir = campaign_store.campaign_dir(root_id) / "diag"
    pattern = re.compile(rf"^{re.escape(root_id)}_diag_(\d+)$")
    max_n = 0
    if diag_dir.is_dir():
        for entry in diag_dir.iterdir():
            if not entry.is_dir():
                continue
            m = pattern.match(entry.name)
            if m:
                max_n = max(max_n, int(m.group(1)))
    return f"{root_id}_diag_{max_n + 1:03d}"


# Fork mint creates the on-disk dir + index.json + ledger inheritance +
# active-pointer retarget BEFORE the fork's first round runs. An
# interrupt between this call and round-1-commit would leave a stub
# (n_rounds=0). The orchestration layer guards against that via
# ``cleanup_stub_fork_if_empty`` below — wrapped around every fork run
# site (``runner/entry.py::run_optimization`` for divergence,
# ``sweep/sweep_runner.py`` for sweep batches). HITL forks minted via
# the API endpoint don't go through orchestration; the operator owns
# their lifecycle and the webapp's "Clean up N stubs" button is the
# escape hatch for abandoned HITL forks.
def _mint_fork(
    campaign_store: CampaignStore,
    tenant_id: str,
    session_id: str,
    parent_cycle_id: str,
    fork_from_round: int,
    payload: ForkPayload,
    *,
    surviving_rounds: list[dict[str, Any]] | None = None,
    sweep_batch_id: str | None = None,
    sweep_source_file: str | None = None,
) -> str:
    """Single entry point for fork creation. Dispatches on ``payload.trigger``.

    ``SCORING_DIVERGENCE`` requires ``surviving_rounds`` (parent rounds <
    ``fork_from_round`` to inherit). ``OPERATOR_SWEEP`` requires
    ``sweep_batch_id`` + ``sweep_source_file`` for idempotent dedup.
    ``OPERATOR_DIAG`` takes neither.

    After per-trigger mint, ``index.json::fork.trigger`` is stamped for
    cross-fork lineage queries.
    """
    if payload.trigger is ForkTrigger.SCORING_DIVERGENCE:
        if surviving_rounds is None:
            raise ValueError("_mint_fork(SCORING_DIVERGENCE) requires surviving_rounds")
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        suffix = hashlib.sha256(f"{parent_cycle_id}|{ts}".encode()).hexdigest()[:8]
        new_cycle_id = f"{parent_cycle_id}_fork_{suffix}"
        now = _fork_sibling_setup(
            campaign_store,
            tenant_id,
            session_id,
            parent_cycle_id,
            new_cycle_id,
            from_round=fork_from_round,
            payload=payload,
        )
        campaign_store.save_divergence_fork(
            parent_cycle_id,
            new_cycle_id,
            surviving_rounds=surviving_rounds,
            forked_at=now,
            forked_from_round=fork_from_round,
        )
        campaign_store.copy_parent_rounds_and_candidates(
            parent_cycle_id,
            new_cycle_id,
            before_round=fork_from_round,
        )
    elif payload.trigger is ForkTrigger.OPERATOR_DIAG:
        if fork_from_round != 0:
            raise ValueError(
                f"_mint_fork(OPERATOR_DIAG) requires fork_from_round=0, got {fork_from_round}"
            )
        new_cycle_id = _next_diag_sibling_id(campaign_store, parent_cycle_id)
        now = _fork_sibling_setup(
            campaign_store,
            tenant_id,
            session_id,
            parent_cycle_id,
            new_cycle_id,
            from_round=0,
            payload=payload,
        )
        campaign_store.save_diag_fork(parent_cycle_id, new_cycle_id, forked_at=now)
    elif payload.trigger is ForkTrigger.OPERATOR_SWEEP:
        if sweep_batch_id is None or sweep_source_file is None:
            raise ValueError(
                "_mint_fork(OPERATOR_SWEEP) requires sweep_batch_id + sweep_source_file"
            )
        if "_" in sweep_batch_id:
            raise ValueError(f"sweep_batch_id must not contain underscores; got {sweep_batch_id!r}")
        if fork_from_round != 0:
            raise ValueError(
                f"_mint_fork(OPERATOR_SWEEP) requires fork_from_round=0, got {fork_from_round}"
            )
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        suffix = hashlib.sha256(f"{parent_cycle_id}|{ts}|{sweep_source_file}".encode()).hexdigest()[
            :8
        ]
        new_cycle_id = f"{parent_cycle_id}_sweep_{sweep_batch_id}_{suffix}"
        now = _fork_sibling_setup(
            campaign_store,
            tenant_id,
            session_id,
            parent_cycle_id,
            new_cycle_id,
            from_round=0,
            payload=payload,
            sweep_batch_id=sweep_batch_id,
            source_file=sweep_source_file,
        )
        campaign_store.save_sweep_fork(
            parent_cycle_id,
            new_cycle_id,
            sweep_batch_id=sweep_batch_id,
            forked_at=now,
        )
    else:
        raise NotImplementedError(f"ForkTrigger.{payload.trigger.name} not wired")

    campaign_store.update("", new_cycle_id, {"fork": {"trigger": payload.trigger.value}})
    return new_cycle_id


def cleanup_stub_fork_if_empty(
    *,
    campaign_store: CampaignStore,
    tenant_id: str,
    session_id: str,
    cycle_id: str,
    parent_cycle_id: str,
) -> tuple[bool, str]:
    """Delete a freshly-minted fork's dir if it never advanced past
    round 0; retarget the active pointer back to *parent_cycle_id* when
    the fork was the active cycle. Returns ``(deleted, reason)``.

    The inverse of :func:`_mint_fork` for the stub case — invoked by
    orchestration cleanup paths (``runner/entry.py::run_optimization``
    for divergence forks, ``sweep/sweep_runner.py`` per sweep payload)
    so an interrupt between fork-mint and round-1-commit doesn't leave
    a stub on disk. ``CampaignStore.try_delete_stub_cycle`` enforces
    the file-system guards (n_rounds=0, no descendants, not root); this
    helper layers active-pointer policy on top.

    Pointer policy: if the active pointer is on *cycle_id*, retarget it
    to *parent_cycle_id* before attempting the delete (the store guard
    doesn't check the pointer; if we didn't retarget, a follow-up
    ``resume`` would point at a deleted dir). If the delete fails for a
    reason other than active-cycle (e.g., descendants), revert the
    pointer so the fork stays reachable.
    """
    _, _, active_cid = read_active_pointer()
    was_active = active_cid == cycle_id
    if was_active:
        save_active_pointer(tenant_id, session_id, parent_cycle_id)
    try:
        deleted, reason = campaign_store.try_delete_stub_cycle(cycle_id)
    except Exception as exc:
        # Don't mask the caller's finally — log and return False.
        logger.warning("Stub cleanup raised for %s: %s", cycle_id, exc)
        if was_active:
            save_active_pointer(tenant_id, session_id, cycle_id)
        return False, str(exc)
    if not deleted and was_active:
        save_active_pointer(tenant_id, session_id, cycle_id)
        logger.info(
            "Stub cleanup skipped for %s (%s); active pointer restored",
            cycle_id,
            reason,
        )
    elif deleted:
        logger.info("Stub fork cleaned up: %s (parent=%s)", cycle_id, parent_cycle_id)
    return deleted, reason
