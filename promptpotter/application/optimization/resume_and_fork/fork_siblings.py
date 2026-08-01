"""Unified fork-mint primitive — :func:`_mint_fork` dispatches on trigger.

Every trigger mints through here; the rebase-family triggers share one on-disk shape and
differ only as an audit discriminator, which is why they cannot each grow a mint path. A
fork is a new *cycle* inside the **same campaign** — every cycle lands flat under
``campaigns/{campaign_id}/cycles/``, never nested under its parent.
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
from promptpotter.domain.results import RoundResult
from promptpotter.domain.run_records import UNATTRIBUTED_OPERATOR, ForkSpec, ForkTrigger
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
    """Resume detected divergence and forked into a sibling cycle."""

    new_cycle_id: str
    new_resumed_from_round: int


def _fork_sibling_setup(
    campaign_store: CampaignStore,
    campaign_id: str,
    session_id: str,
    parent_cycle_id: str,
    new_cycle_id: str,
    *,
    from_round: int,
    payload: ForkSpec,
    sweep_batch_id: str | None = None,
    source_file: str | None = None,
) -> str:
    """Common plumbing: dir create, FORK_CUT append, pointer + log. Returns ``now_iso``."""
    parent_dir = campaign_store.cycle_dir(campaign_id, parent_cycle_id)
    new_dir = campaign_store.cycle_dir(campaign_id, new_cycle_id)
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

    save_active_pointer(campaign_store.workspace, session_id, campaign_id, new_cycle_id)
    logger.info(
        "Forked %s → %s at round %d [trigger=%s] (active pointer retargeted)",
        parent_cycle_id,
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


def _mint_fork(
    campaign_store: CampaignStore,
    campaign_id: str,
    session_id: str,
    parent_cycle_id: str,
    fork_from_round: int,
    payload: ForkSpec,
    *,
    surviving_rounds: list[RoundResult] | None = None,
    sweep_batch_id: str | None = None,
    sweep_source_file: str | None = None,
) -> str:
    """Single entry point for fork creation. Dispatches on ``payload.trigger``.

    The new cycle lands in the same campaign (``campaign_id``), and its active pointer
    is retargeted in ``campaign_store``'s OWN workspace. That used to be a separate
    ``(tenant_id, projects_root=None)`` pair, defaulting to the process-global
    workspace — so the L4 auto-rebase, which passes an inner cycle's store and omitted
    the root, pointed the operator's real ``active_session.json`` at a campaign under
    ``.inner/`` and every per-cycle route 404'd until they re-ran ``resume``.
    ``SCORING_DIVERGENCE`` requires ``surviving_rounds``.
    ``L{2,3}_REBASE`` / ``OPERATOR_REWIND`` synthesize ``surviving_rounds``
    from the parent's persisted rounds if the caller omits them.
    ``OPERATOR_SWEEP`` requires ``sweep_batch_id`` + ``sweep_source_file``.
    ``OPERATOR_DIAG`` takes neither.

    ``fork_from_round`` is MECHANICAL — how many of the parent's rounds this fork
    lifts (``0`` = a clean offshoot that lifts none). ``ForkSpec.from_round`` is
    PROVENANCE — which round the cut was taken from. A rebase makes them equal, so
    the seam back-fills the spec when its author left it unset: five of six triggers
    otherwise wrote ``index.json::fork.from_round = null`` and the two readers
    disagreed about the cut (the lineage tree fell back to scanning the parent ledger;
    the mask projection had no fallback and silently read ``None``).

    An author who DOES know the round keeps it. A steered fork lifts nothing yet is
    cut from a real round's candidate — overwriting it here wrote ``from_round: 0``
    beside a round-3 ``from_candidate_id``, so the label lookup searched round 0,
    missed, and ``origin.py::try_inherit_fork_origin`` silently re-scored the origin
    instead of inheriting it.
    """
    if payload.from_round is None:
        payload = payload.model_copy(update={"from_round": fork_from_round})
    if payload.trigger in _REBASE_TRIGGERS:
        if payload.trigger is ForkTrigger.SCORING_DIVERGENCE and surviving_rounds is None:
            raise ValueError("_mint_fork(SCORING_DIVERGENCE) requires surviving_rounds")
        if surviving_rounds is None:
            # L2_REBASE / L3_REBASE / OPERATOR_REWIND: lift rounds 0..fork_from_round-1
            # from the parent's round files.
            surviving_rounds = campaign_store.load_rounds_range(
                campaign_id, parent_cycle_id, 0, fork_from_round - 1
            )
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        suffix = hashlib.sha256(f"{parent_cycle_id}|{ts}".encode()).hexdigest()[:8]
        new_cycle_id = f"{parent_cycle_id}_fork_{suffix}"
        now = _fork_sibling_setup(
            campaign_store,
            campaign_id,
            session_id,
            parent_cycle_id,
            new_cycle_id,
            from_round=fork_from_round,
            payload=payload,
        )
        campaign_store.save_rebase_fork(
            campaign_id,
            parent_cycle_id,
            new_cycle_id,
            surviving_rounds=surviving_rounds,
            forked_at=now,
            forked_from_round=fork_from_round,
        )
        campaign_store.copy_parent_rounds_and_candidates(
            campaign_id,
            parent_cycle_id,
            new_cycle_id,
            before_round=fork_from_round,
        )
        if payload.seed is not None:
            # An L2/L3 rebase carrying a config unlock writes its seed like any other
            # seeded cycle. `read_cycle_seed` scans THIS cycle's own ledger, so without
            # the record the unlock would live only in memory: it would hold for the
            # in-process run and silently re-lock on the first `resume` of the fork.
            campaign_store.write_cycle_seed(campaign_id, new_cycle_id, payload.seed)
    elif payload.trigger is ForkTrigger.OPERATOR_DIAG:
        if fork_from_round != 0:
            raise ValueError(
                f"_mint_fork(OPERATOR_DIAG) requires fork_from_round=0, got {fork_from_round}"
            )
        new_cycle_id = _next_diag_sibling_id(campaign_store, campaign_id, parent_cycle_id)
        now = _fork_sibling_setup(
            campaign_store,
            campaign_id,
            session_id,
            parent_cycle_id,
            new_cycle_id,
            from_round=0,
            payload=payload,
        )
        campaign_store.write_fresh_sibling(
            campaign_id, parent_cycle_id, new_cycle_id, "diag", forked_at=now
        )
    elif payload.trigger is ForkTrigger.OPERATOR_STEERED:
        if fork_from_round != 0:
            raise ValueError(
                f"_mint_fork({payload.trigger.value}) requires fork_from_round=0, "
                f"got {fork_from_round}"
            )
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        suffix = hashlib.sha256(f"{parent_cycle_id}|operator|{ts}".encode()).hexdigest()[:8]
        new_cycle_id = f"{root_cycle_id(parent_cycle_id)}_fork_{suffix}"
        now = _fork_sibling_setup(
            campaign_store,
            campaign_id,
            session_id,
            parent_cycle_id,
            new_cycle_id,
            from_round=0,
            payload=payload,
        )
        # Clean-offshoot fork from the lineage/control panel (endorse or steered):
        # fresh sibling index (no parent-round copy, round numbering restarts at 1);
        # the origin re-scores from the selected/edited searchpoint at bootstrap. The
        # ForkSpec provenance lands on index.json::fork via the single fork-block
        # writer below.
        campaign_store.write_fresh_sibling(
            campaign_id, parent_cycle_id, new_cycle_id, "fork", forked_at=now
        )
        # The steered seed (edited searchpoint + reconciled limits) rides its own
        # read-once home; the ledger FORK_CUT still carries it as SoT.
        if payload.seed is not None:
            campaign_store.write_cycle_seed(campaign_id, new_cycle_id, payload.seed)
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
            campaign_id,
            session_id,
            parent_cycle_id,
            new_cycle_id,
            from_round=0,
            payload=payload,
            sweep_batch_id=sweep_batch_id,
            source_file=sweep_source_file,
        )
        campaign_store.write_fresh_sibling(
            campaign_id,
            parent_cycle_id,
            new_cycle_id,
            "sweep",
            sweep_batch_id=sweep_batch_id,
            forked_at=now,
        )
    # The lineage-read fork block — serialized from the one typed ForkSpec (no
    # hand-built per-trigger dict). The heavy `seed` payload is excluded; it rides
    # the fork's ledger as its own read-once `CycleSeedRecord`.
    campaign_store.update(
        campaign_id, new_cycle_id, {"fork": payload.model_dump(mode="json", exclude={"seed"})}
    )
    return new_cycle_id


def cleanup_stub_fork_if_empty(
    *,
    campaign_store: CampaignStore,
    campaign_id: str,
    session_id: str,
    cycle_id: str,
    parent_cycle_id: str,
) -> tuple[bool, str]:
    """Delete a freshly-minted fork's dir if it never advanced past round 0;
    retarget the active pointer back to *parent_cycle_id* when the fork was
    active. Returns ``(deleted, reason)``.

    ``CampaignStore.try_delete_stub_cycle`` enforces the file-system guards
    (n_rounds=0, no descendants, not root); this helper layers
    active-pointer policy on top.
    """
    workspace = campaign_store.workspace
    _, _, active_cid = read_active_pointer(workspace)
    was_active = active_cid == cycle_id
    if was_active:
        save_active_pointer(workspace, session_id, campaign_id, parent_cycle_id)
    try:
        deleted, reason = campaign_store.try_delete_stub_cycle(campaign_id, cycle_id)
    except Exception as exc:
        logger.warning("Stub cleanup raised for %s: %s", cycle_id, exc)
        if was_active:
            save_active_pointer(workspace, session_id, campaign_id, cycle_id)
        return False, str(exc)
    if not deleted and was_active:
        save_active_pointer(workspace, session_id, campaign_id, cycle_id)
        logger.info("Stub cleanup skipped for %s (%s); active pointer restored", cycle_id, reason)
    elif deleted:
        logger.info("Stub fork cleaned up: %s (parent=%s)", cycle_id, parent_cycle_id)
    return deleted, reason


def mint_operator_fork(
    *,
    stores: Stores,
    campaign_id: str,
    cycle_id: str,
    from_round: int,
    from_candidate_id: str,
    seed: CycleSeed,
    steered_by: str,
) -> str:
    """The operator-initiated fork entry — the control-plane seam
    (``CommandDispatcher``, fork-cycle) calls this. Builds the typed
    :class:`ForkSpec` and delegates to :func:`_mint_fork`; there is no parallel
    fork-creation path.

    Every operator fork is ``operator_steered``: a clean offshoot (fresh ledger,
    numbering restarts at round 1) carrying *seed* (the chosen searchpoint's
    evolved prompt + config + reconciled run limits — recorded, not forbidden:
    operators may act, we record it), appended to the fork's ledger as a
    ``CycleSeedRecord`` and re-scored as the fork's origin at bootstrap.
    """
    parent_index = stores.campaigns.load(campaign_id, cycle_id) or {}
    spec = ForkSpec(
        trigger=ForkTrigger.OPERATOR_STEERED,
        reason=f"operator-steered fork from {cycle_id}",
        issued_by=steered_by or UNATTRIBUTED_OPERATOR,
        from_round=from_round,
        from_candidate_id=from_candidate_id or None,
        seed=seed,
    )
    return _mint_fork(
        stores.campaigns,
        campaign_id,
        str(parent_index.get("parent_session_id", "")),
        cycle_id,
        0,
        spec,
    )
