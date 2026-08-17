"""The unified fork-mint primitive; every trigger mints here and none may grow its own path. A fork is
a new CYCLE in the SAME campaign, flat under ``cycles/``, never nested under its parent."""

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
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.results import RoundResult
from promptpotter.domain.run_records import (
    FORK_DIRECTION,
    UNATTRIBUTED_OPERATOR,
    ForkDirection,
    ForkSpec,
    ForkTrigger,
)
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.layout import campaign_cycles_dir, root_cycle_id
from promptpotter.infrastructure.store.session_pointer import (
    read_active_pointer,
    save_active_pointer,
)
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.domain.run_records import CycleSeed
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)

__all__ = ["ForkResult", "_mint_fork", "cleanup_stub_fork_if_empty", "mint_operator_fork"]


class ForkResult(NamedTuple):
    new_cycle_id: str
    new_resumed_from_round: int


def _fork_sibling_setup(
    campaign_store: CampaignStore,
    parent: CycleHop,
    session_id: str,
    new_cycle_id: str,
    *,
    from_round: int,
    payload: ForkSpec,
    sweep_batch_id: str | None = None,
    source_file: str | None = None,
) -> str:
    parent_dir = campaign_store.cycle_dir(parent)
    new_dir = campaign_store.cycle_dir(parent.model_copy(update={"cycle_id": new_cycle_id}))
    if new_dir.exists():
        raise FileExistsError(f"forked cycle dir already exists: {new_dir}")
    new_dir.mkdir(parents=True, exist_ok=True)

    now = utcnow_iso()
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

    save_active_pointer(
        campaign_store.workspace,
        session_id,
        parent.model_copy(update={"cycle_id": new_cycle_id}),
    )
    logger.info(
        "Forked %s → %s at round %d [trigger=%s] (active pointer retargeted)",
        parent.cycle_id,
        new_cycle_id,
        from_round,
        payload.trigger.value,
    )
    return now


def _next_diag_sibling_id(
    campaign_store: CampaignStore, campaign_id: str, parent_cycle_id: str
) -> str:
    """Next ``{root}_diag_NNN`` id; siblings root at the family root."""
    root_id = root_cycle_id(parent_cycle_id)
    cycles_dir = campaign_cycles_dir(campaign_store.campaign_root_dir(campaign_id))
    pattern = re.compile(rf"^{re.escape(root_id)}_diag_(\d+)$")
    max_n = 0
    if cycles_dir.is_dir():
        for entry in cycles_dir.iterdir():
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
# site.
_REBASE_TRIGGERS = frozenset(
    {
        ForkTrigger.SCORING_DIVERGENCE,
        ForkTrigger.L2_REBASE,
        ForkTrigger.L3_REBASE,
        ForkTrigger.OPERATOR_REWIND,
    }
)

# A non-rebase branches from the origin and lifts no parent round, so its only cut is 0.
_ZERO_ROUND_TRIGGERS = frozenset(ForkTrigger) - _REBASE_TRIGGERS


def _fork_suffix(*parts: str) -> str:
    """8-hex id suffix over *parts* + the wall clock, which is what separates two forks cut from one
    parent — the *parts* alone cannot, since every rebase of a given parent passes the same tuple.
    The stamp is therefore read at MICROSECOND resolution: at the second resolution it carried, two
    forks cut inside one second hashed identically and the later one landed on the earlier one's id."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return hashlib.sha256("|".join((*parts, stamp)).encode()).hexdigest()[:8]


def _mint_fork(
    campaign_store: CampaignStore,
    parent: CycleHop,
    session_id: str,
    fork_from_round: int,
    payload: ForkSpec,
    *,
    surviving_rounds: list[RoundResult] | None = None,
    sweep_batch_id: str | None = None,
    sweep_source_file: str | None = None,
) -> str:
    """Single entry point, dispatching on ``payload.trigger``. ``fork_from_round`` is MECHANICAL (how many
    parent rounds this lifts); ``ForkSpec.from_round`` is PROVENANCE (which round the cut came from)."""
    if payload.from_round is None:
        payload = payload.model_copy(update={"from_round": fork_from_round})
    if payload.trigger in _ZERO_ROUND_TRIGGERS and fork_from_round != 0:
        raise ValueError(
            f"_mint_fork({payload.trigger.value}) branches from the origin and lifts no parent "
            f"round, so fork_from_round must be 0; got {fork_from_round}"
        )
    if payload.trigger in _REBASE_TRIGGERS:
        if payload.trigger is ForkTrigger.SCORING_DIVERGENCE and surviving_rounds is None:
            raise ValueError("_mint_fork(SCORING_DIVERGENCE) requires surviving_rounds")
        if surviving_rounds is None:
            # L2_REBASE / L3_REBASE / OPERATOR_REWIND: lift rounds 0..fork_from_round-1
            # from the parent's round files.
            surviving_rounds = campaign_store.load_rounds_range(parent, 0, fork_from_round - 1)
        new_cycle_id = f"{parent.cycle_id}_fork_{_fork_suffix(parent.cycle_id)}"
        now = _fork_sibling_setup(
            campaign_store,
            parent,
            session_id,
            new_cycle_id,
            from_round=fork_from_round,
            payload=payload,
        )
        campaign_store.save_rebase_fork(
            parent.campaign_id,
            parent.cycle_id,
            new_cycle_id,
            surviving_rounds=surviving_rounds,
            forked_at=now,
            forked_from_round=fork_from_round,
        )
        campaign_store.copy_parent_rounds_and_candidates(
            parent.campaign_id,
            parent.cycle_id,
            new_cycle_id,
            before_round=fork_from_round,
        )
        if payload.seed is not None:
            # An L2/L3 rebase carrying a config unlock writes its seed like any other
            # seeded cycle. `read_cycle_seed` scans THIS cycle's own ledger, so without
            # the record the unlock would live only in memory: it would hold for the
            # in-process run and silently re-lock on the first `resume` of the fork.
            campaign_store.write_cycle_seed(
                CycleHop(campaign_id=parent.campaign_id, cycle_id=new_cycle_id), payload.seed
            )
    elif payload.trigger is ForkTrigger.OPERATOR_DIAG:
        new_cycle_id = _next_diag_sibling_id(campaign_store, parent.campaign_id, parent.cycle_id)
        now = _fork_sibling_setup(
            campaign_store,
            parent,
            session_id,
            new_cycle_id,
            from_round=0,
            payload=payload,
        )
        campaign_store.write_fresh_sibling(
            parent.campaign_id, parent.cycle_id, new_cycle_id, "diag", forked_at=now
        )
    elif payload.trigger is ForkTrigger.OPERATOR_STEERED:
        new_cycle_id = (
            f"{root_cycle_id(parent.cycle_id)}_fork_{_fork_suffix(parent.cycle_id, 'operator')}"
        )
        now = _fork_sibling_setup(
            campaign_store,
            parent,
            session_id,
            new_cycle_id,
            from_round=0,
            payload=payload,
        )
        # Clean-offshoot fork from the lineage/control panel (endorse or steered):
        # fresh sibling index (no parent-round copy, round numbering restarts at 1);
        # the origin re-scores from the selected/edited searchpoint at init. The
        # ForkSpec provenance lands on index.json::fork via the single fork-block
        # writer below.
        campaign_store.write_fresh_sibling(
            parent.campaign_id, parent.cycle_id, new_cycle_id, "fork", forked_at=now
        )
        # The steered seed (edited searchpoint + reconciled limits) rides its own
        # read-once home; the ledger FORK_CUT still carries it as SoT.
        if payload.seed is not None:
            campaign_store.write_cycle_seed(
                CycleHop(campaign_id=parent.campaign_id, cycle_id=new_cycle_id), payload.seed
            )
    elif payload.trigger is ForkTrigger.OPERATOR_SWEEP:
        if sweep_batch_id is None or sweep_source_file is None:
            raise ValueError(
                "_mint_fork(OPERATOR_SWEEP) requires sweep_batch_id + sweep_source_file"
            )
        if "_" in sweep_batch_id:
            raise ValueError(f"sweep_batch_id must not contain underscores; got {sweep_batch_id!r}")
        suffix = _fork_suffix(parent.cycle_id, sweep_source_file)
        new_cycle_id = f"{parent.cycle_id}_sweep_{sweep_batch_id}_{suffix}"
        now = _fork_sibling_setup(
            campaign_store,
            parent,
            session_id,
            new_cycle_id,
            from_round=0,
            payload=payload,
            sweep_batch_id=sweep_batch_id,
            source_file=sweep_source_file,
        )
        campaign_store.write_fresh_sibling(
            parent.campaign_id,
            parent.cycle_id,
            new_cycle_id,
            "sweep",
            sweep_batch_id=sweep_batch_id,
            forked_at=now,
        )
    # The lineage-read fork block — serialized from the one typed ForkSpec (no
    # hand-built per-trigger dict). The heavy `seed` payload is excluded; it rides
    # the fork's ledger as its own read-once `CycleSeedRecord`.
    campaign_store.update(
        CycleHop(campaign_id=parent.campaign_id, cycle_id=new_cycle_id),
        {"fork": payload.model_dump(mode="json", exclude={"seed"})},
    )
    # THE LINE MOVED — said where the cut is made. An offshoot's parent keeps running, which
    # is why this asks the DIRECTION, not merely whether a fork happened.
    if FORK_DIRECTION.get(payload.trigger) is ForkDirection.SUPERSEDE:
        campaign_store.mark_superseded(parent)
    return new_cycle_id


def cleanup_stub_fork_if_empty(
    *,
    campaign_store: CampaignStore,
    hop: CycleHop,
    parent_cycle_id: str,
) -> tuple[bool, str]:
    """THE stub-deletion path — the runner's cleanup, ``delete-cycle`` and ``cleanup-empty-cycles``
    all come here, so pointer discipline has one home. The session id comes off the POINTER, never
    the caller, who may have none. Spend banking is the store's, inside the delete itself."""
    workspace = campaign_store.workspace
    session_id, _, active_cid = read_active_pointer(workspace)
    was_active = active_cid == hop.cycle_id
    if was_active:
        save_active_pointer(
            workspace, session_id, CycleHop(campaign_id=hop.campaign_id, cycle_id=parent_cycle_id)
        )
    try:
        deleted, reason = campaign_store.try_delete_stub_cycle(hop)
    except Exception as exc:
        logger.warning("Stub cleanup raised for %s: %s", hop.cycle_id, exc)
        if was_active:
            save_active_pointer(workspace, session_id, hop)
        return False, str(exc)
    if not deleted and was_active:
        save_active_pointer(workspace, session_id, hop)
        logger.info(
            "Stub cleanup skipped for %s (%s); active pointer restored", hop.cycle_id, reason
        )
    elif deleted:
        logger.info("Stub fork cleaned up: %s (parent=%s)", hop.cycle_id, parent_cycle_id)
    return deleted, reason


def mint_operator_fork(
    *,
    stores: Stores,
    hop: CycleHop,
    from_round: int,
    from_candidate_id: str,
    seed: CycleSeed,
    steered_by: str,
) -> str:
    """The operator-initiated fork entry; no parallel creation path exists. Every operator fork is a clean
    offshoot carrying *seed* — recorded, not forbidden: operators may act, and we record that they did."""
    parent_index = stores.campaigns.load(hop) or {}
    spec = ForkSpec(
        trigger=ForkTrigger.OPERATOR_STEERED,
        reason=f"operator-steered fork from {hop.cycle_id}",
        issued_by=steered_by or UNATTRIBUTED_OPERATOR,
        from_round=from_round,
        from_candidate_id=from_candidate_id or None,
        seed=seed,
    )
    return _mint_fork(
        stores.campaigns,
        hop,
        str(parent_index.get("parent_session_id", "")),
        0,
        spec,
    )
