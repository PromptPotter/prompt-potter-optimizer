"""Fork-mint helpers — divergence forks + diag/sweep siblings.

Three sibling kinds, all minted by appending a ``DecisionKind.FORK_CUT``
to the *parent's* ledger (so resume can read the fork's identity from
the parent's history) and writing a fresh dir under the family root:

* :func:`fork_at_divergence` — replay-driven; inherits parent rounds
  before the divergence point so deterministic replay matches.
* :func:`fork_for_diag_sibling` — clean-slate BFS sibling rooted at
  round 0 (no parent inheritance, no short-circuit on l1_generate).
* :func:`fork_for_sweep_sibling` — clean-slate sibling stamped with
  an operator-authored ``SweepPayload`` before the round loop runs.

The cycle-id encoding (``{root}_fork_{hex}`` vs ``{root}_diag_NNN``
vs ``{parent}_sweep_{batch}_{hex}``) and the directory layout
(``forks/``, ``diag/``, ``sweeps/``) are owned by
:mod:`promptpotter.infrastructure.store.paths`; this module just
chooses the id and calls into the store writer.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application.optimization.resume_and_fork.decisions import (
    DecisionKind,
    record_decision,
)
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import SweepPayload
from promptpotter.infrastructure.ledger import CycleLedger
from promptpotter.infrastructure.store import root_cycle_id, save_active_pointer
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.infrastructure.store import CampaignStore

logger = logging.getLogger(__name__)

__all__ = [
    "ForkResult",
    "fork_at_divergence",
    "fork_for_diag_sibling",
    "fork_for_sweep_sibling",
]


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
    fork_data: dict[str, Any] | None = None,
    log_extra: str = "",
) -> str:
    """Common fork plumbing: dir create, FORK_CUT append, pointer + log.

    Returns ``now_iso`` — pass to the matching ``CampaignStore.save_*_fork``
    so its ``forked_at`` matches the FORK_CUT decision record.
    """
    parent_dir = campaign_store.campaign_dir(parent_cycle_id)
    new_dir = campaign_store.campaign_dir(new_cycle_id)
    if new_dir.exists():
        raise FileExistsError(f"forked cycle dir already exists: {new_dir}")
    new_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC).isoformat()
    with graceful("FORK_CUT decision append failed"):
        record_decision(
            CycleLedger.open(CycleDir(parent_dir)),
            DecisionKind.FORK_CUT,
            {"from_round": from_round},
            new_cycle_id,
            data={"forked_at": now, **(fork_data or {})},
        )

    save_active_pointer(tenant_id, session_id, new_cycle_id)
    logger.info(
        "Forked %s → %s at round %d%s (active pointer retargeted)",
        parent_cycle_id,
        new_cycle_id,
        from_round,
        log_extra,
    )
    return now


def fork_at_divergence(
    campaign_store: CampaignStore,
    tenant_id: str,
    session_id: str,
    old_cycle_id: str,
    fork_from_round: int,
    surviving_rounds: list[dict[str, Any]],
) -> str:
    """Divergence-fork that inherits parent's < fork_from_round artifacts (deterministic replay).

    NOT for clean-slate siblings (sweep/diag) — those would short-circuit
    L1 on the inherited round-0 checkpoint. Use :func:`fork_for_diag_sibling`
    or :func:`fork_for_sweep_sibling`.
    """
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = hashlib.sha256(f"{old_cycle_id}|{ts}".encode()).hexdigest()[:8]
    new_cycle_id = f"{old_cycle_id}_fork_{suffix}"

    now = _fork_sibling_setup(
        campaign_store,
        tenant_id,
        session_id,
        old_cycle_id,
        new_cycle_id,
        from_round=fork_from_round,
    )
    campaign_store.save_divergence_fork(
        old_cycle_id,
        new_cycle_id,
        surviving_rounds=surviving_rounds,
        forked_at=now,
        forked_from_round=fork_from_round,
    )
    campaign_store.copy_parent_rounds_and_candidates(
        old_cycle_id, new_cycle_id, before_round=fork_from_round
    )
    return new_cycle_id


def _next_diag_sibling_id(campaign_store: CampaignStore, parent_cycle_id: str) -> str:
    """Next ``{root}_diag_NNN`` id; siblings always root at the family root so the BFS tree stays one level deep."""
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


def fork_for_diag_sibling(
    campaign_store: CampaignStore,
    tenant_id: str,
    session_id: str,
    parent_cycle_id: str,
) -> str:
    """Mint a diag-BFS sibling rooted at round 0; records ``FORK_CUT`` and retargets the active pointer."""
    new_cycle_id = _next_diag_sibling_id(campaign_store, parent_cycle_id)
    now = _fork_sibling_setup(
        campaign_store,
        tenant_id,
        session_id,
        parent_cycle_id,
        new_cycle_id,
        from_round=0,
        fork_data={"kind": "diag_sibling"},
    )
    campaign_store.save_diag_fork(parent_cycle_id, new_cycle_id, forked_at=now)
    return new_cycle_id


def fork_for_sweep_sibling(
    campaign_store: CampaignStore,
    tenant_id: str,
    session_id: str,
    parent_cycle_id: str,
    sweep_batch_id: str,
    payload_source_file: str,
    payload: SweepPayload,
) -> str:
    """Sweep-batch sibling — clean slate. cycle_id encodes _sweep_{batch_id}_;
    sweep_batch_id must not contain '_' (cycle-id regex splits on it).
    """
    if "_" in sweep_batch_id:
        raise ValueError(f"sweep_batch_id must not contain underscores; got {sweep_batch_id!r}")
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = hashlib.sha256(f"{parent_cycle_id}|{ts}|{payload_source_file}".encode()).hexdigest()[
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
        fork_data={
            "kind": "sweep_fork",
            "sweep_batch_id": sweep_batch_id,
            "source_file": payload_source_file,
            "sweep_payload": payload.model_dump(mode="json"),
        },
        log_extra=f" [batch={sweep_batch_id}, payload={payload_source_file}]",
    )
    campaign_store.save_sweep_fork(
        parent_cycle_id, new_cycle_id, sweep_batch_id=sweep_batch_id, forked_at=now
    )
    return new_cycle_id
