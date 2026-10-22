"""A fork is a new CYCLE in the SAME campaign, flat under ``cycles/``, never nested under its parent."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from promptpotter.application.bench.resume_and_fork.decisions import (
    record_decision,
)
from promptpotter.application.campaign_config import effective_config
from promptpotter.application.served_dashboard import fork_remainder
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import NodeSearchNarrowing
from promptpotter.domain.run_records import (
    BenchCheckpointKind,
    ForkDirection,
    ForkSpec,
    ForkTrigger,
)
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.layout import campaign_cycles_dir, root_cycle_id
from promptpotter.infrastructure.store.session_pointer import save_active_pointer
from promptpotter.infrastructure.store.stores import owned_campaign
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ConflictError, PayloadInvalidError, graceful
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.identity import acting_principal

if TYPE_CHECKING:
    from promptpotter.domain.run_records import CycleSeed
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)


def _fork_sibling_setup(
    campaign_store: CampaignStore,
    parent: CycleHop,
    child: CycleHop,
    *,
    from_round: int,
    payload: ForkSpec,
) -> None:
    parent_dir = campaign_store.cycle_dir(parent)
    new_dir = campaign_store.cycle_dir(child)
    if new_dir.exists():
        raise FileExistsError(f"forked cycle dir already exists: {new_dir}")
    new_dir.mkdir(parents=True, exist_ok=True)

    with graceful("FORK_CUT decision append failed"):
        record_decision(
            CycleEventLog.open(CycleDir(parent_dir)),
            BenchCheckpointKind.FORK_CUT,
            {"from_round": from_round},
            child.cycle_id,
            node=None,
            data={"forked_at": utcnow_iso(), "fork": payload.model_dump(mode="json")},
        )

    save_active_pointer(campaign_store.workspace, child)
    logger.info(
        "Forked %s → %s at round %d [trigger=%s] (active pointer retargeted)",
        parent.cycle_id,
        child.cycle_id,
        from_round,
        payload.trigger.value,
    )
    # Read after the FORK_CUT, the only moment the offset is true of a parent still running.
    campaign_store.mint_fork_cycle(
        child,
        parent_dir,
        payload,
        cut=CycleEventLog.open(CycleDir(parent_dir)).next_offset,
        from_round=from_round,
    )
    if payload.seed is not None:
        # Without its own record a rebase's config unlock silently re-locks on the fork's first `resume`.
        campaign_store.write_cycle_seed(child, payload.seed)


def _next_diag_sibling_id(
    campaign_store: CampaignStore, campaign_id: str, parent_cycle_id: str
) -> str:
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


_REBASE_TRIGGERS = frozenset(
    {
        ForkTrigger.SCORING_DIVERGENCE,
        ForkTrigger.OPTIMIZER_REBASE,
        ForkTrigger.OPERATOR_REWIND,
    }
)


def _fork_suffix(*parts: str) -> str:
    """The clock, at MICROSECOND resolution, is all that separates two forks of one parent: *parts* cannot."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return stable_hash([*parts, stamp], length=8)


def _sibling_id(campaign_store: CampaignStore, parent: CycleHop, payload: ForkSpec) -> str:
    if payload.trigger in _REBASE_TRIGGERS:
        return f"{parent.cycle_id}_fork_{_fork_suffix(parent.cycle_id)}"
    if payload.trigger is ForkTrigger.OPERATOR_DIAG:
        return _next_diag_sibling_id(campaign_store, parent.campaign_id, parent.cycle_id)
    return f"{root_cycle_id(parent.cycle_id)}_fork_{_fork_suffix(parent.cycle_id, 'operator')}"


def mint_fork(
    campaign_store: CampaignStore,
    parent: CycleHop,
    fork_from_round: int,
    payload: ForkSpec,
) -> str:
    """``fork_from_round`` is MECHANICAL (parent rounds lifted); ``ForkSpec.from_round`` is PROVENANCE."""
    if payload.from_round is None:
        payload = payload.model_copy(update={"from_round": fork_from_round})
    # A cut continuing no scale branches from the origin and lifts no parent round.
    if not payload.traits.continues_scale and fork_from_round != 0:
        raise ValueError(
            f"mint_fork({payload.trigger.value}) branches from the origin and lifts no parent "
            f"round, so fork_from_round must be 0; got {fork_from_round}"
        )
    if campaign_store.read_bank_partition(parent) is None:
        raise ConflictError(
            f"cycle {parent.cycle_id} holds no drawn partition over its chain, "
            "so no fork can stand on it"
        )
    child = parent.model_copy(update={"cycle_id": _sibling_id(campaign_store, parent, payload)})
    _fork_sibling_setup(
        campaign_store,
        parent,
        child,
        from_round=fork_from_round,
        payload=payload,
    )
    # An offshoot's parent keeps running, hence the DIRECTION.
    if payload.traits.direction is ForkDirection.SUPERSEDE:
        campaign_store.mark_superseded(parent, child.cycle_id)
    return child.cycle_id


def declare_steered_values(
    seed: CycleSeed, narrowing: Mapping[str, NodeSearchNarrowing]
) -> CycleSeed:
    """Without it the fork runs ``model=X`` beside a permitted set that excludes X."""
    declared = dict(seed.optimizer_narrowing)
    for node, cfg in node_config_items(seed.pipeline_overlay):
        origin = narrowing.get(node)
        listed = origin.param_allowed_values if origin else {}
        own = declared.get(node)
        values = dict(own.param_allowed_values) if own else {}
        for param, value in cfg.items():
            if param in values or (param != "model" and param not in listed):
                continue
            steered = str(value)
            values[param] = [steered, *(v for v in listed.get(param) or () if v != steered)]
        if values:
            declared[node] = NodeSearchNarrowing(
                param_keys=own.param_keys if own else None, param_allowed_values=values
            )
    return seed.model_copy(update={"optimizer_narrowing": declared})


def mint_diag_sibling(*, stores: Stores, hop: CycleHop) -> str:
    return mint_fork(
        stores.campaigns,
        hop,
        0,
        ForkSpec(
            trigger=ForkTrigger.OPERATOR_DIAG,
            reason="diag-sibling BFS exploration",
            issued_by=acting_principal(stores.identity),
        ),
    )


def mint_operator_fork(
    *,
    stores: Stores,
    hop: CycleHop,
    from_round: int,
    seed: CycleSeed,
    from_candidate_id: str = "",
    keep_rounds: bool = False,
    reason: str = "",
) -> str:
    campaign = owned_campaign(stores, hop.campaign_id)
    seed = declare_steered_values(seed, effective_config(campaign.declaration).optimizer_narrowing)
    if not keep_rounds:
        # The remainder the parent's dashboard serves, so the fork's caps are the ones on screen.
        caps = fork_remainder(stores, hop).under(seed.config_overrides)
        seed = seed.model_copy(update={"config_overrides": caps})
    standing = stores.campaigns.standing_rounds(hop, moment=None)
    if from_candidate_id:
        named = standing.arms.get((from_round, ArmKey(from_candidate_id)))
        if named is not None and named.state.refused:
            raise PayloadInvalidError(
                f"{named.state.label} of round {from_round} is a refused proposal, keyed by its "
                "slot: it measured no individual to fork from"
            )
    elif not keep_rounds:
        origin_round = standing.rounds.get(0)
        if origin_round is not None and origin_round.close.head is not None:
            from_candidate_id = origin_round.close.origin.individual.id
    spec = ForkSpec(
        trigger=ForkTrigger.OPERATOR_REWIND if keep_rounds else ForkTrigger.OPERATOR_STEERED,
        reason=reason
        or (
            f"operator-applied from round {from_round} of {hop.cycle_id}"
            if keep_rounds
            else f"operator-steered fork from {hop.cycle_id}"
        ),
        issued_by=acting_principal(stores.identity),
        from_round=from_round,
        from_candidate_id=from_candidate_id or None,
        seed=seed,
    )
    return mint_fork(
        stores.campaigns,
        hop,
        from_round if keep_rounds else 0,
        spec,
    )
