"""A fork is a new CYCLE in the SAME campaign, flat under ``cycles/``, never nested under its parent."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application.bench.resume_and_fork.decisions import (
    record_decision,
)
from promptpotter.application.served_dashboard import fork_remainder
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.pipeline_overlay import (
    allowed_values_from_narrowing,
    node_config_items,
)
from promptpotter.domain.pipeline_schema import NodeSearchNarrowing
from promptpotter.domain.run_records import (
    FORK_DIRECTION,
    BenchCheckpointKind,
    ForkDirection,
    ForkSpec,
    ForkTrigger,
)
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.layout import campaign_cycles_dir, root_cycle_id
from promptpotter.infrastructure.store.session_pointer import save_active_pointer
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import PayloadInvalidError, graceful
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.identity import acting_principal_id

if TYPE_CHECKING:
    from promptpotter.domain.run_records import CycleSeed
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)

__all__ = [
    "ForkResult",
    "declare_steered_values",
    "mint_diag_sibling",
    "mint_fork",
    "mint_operator_fork",
]


class ForkResult(NamedTuple):
    new_cycle_id: str
    new_resumed_from_round: int


def _fork_sibling_setup(
    campaign_store: CampaignStore,
    parent: CycleHop,
    new_cycle_id: str,
    *,
    from_round: int,
    payload: ForkSpec,
) -> None:
    parent_dir = campaign_store.cycle_dir(parent)
    new_dir = campaign_store.cycle_dir(parent.model_copy(update={"cycle_id": new_cycle_id}))
    if new_dir.exists():
        raise FileExistsError(f"forked cycle dir already exists: {new_dir}")
    new_dir.mkdir(parents=True, exist_ok=True)

    record_data: dict[str, Any] = {
        "forked_at": utcnow_iso(),
        "fork": payload.model_dump(mode="json"),
    }

    with graceful("FORK_CUT decision append failed"):
        record_decision(
            CycleEventLog.open(CycleDir(parent_dir)),
            BenchCheckpointKind.FORK_CUT,
            {"from_round": from_round},
            new_cycle_id,
            node=None,
            data=record_data,
        )

    save_active_pointer(
        campaign_store.workspace, parent.model_copy(update={"cycle_id": new_cycle_id})
    )
    logger.info(
        "Forked %s → %s at round %d [trigger=%s] (active pointer retargeted)",
        parent.cycle_id,
        new_cycle_id,
        from_round,
        payload.trigger.value,
    )
    # After the FORK_CUT, so the cut the mint reads off the parent's ledger includes it.
    campaign_store.mint_fork_cycle(parent, new_cycle_id, payload, from_round=from_round)
    if payload.seed is not None:
        # Without its own record a rebase's config unlock silently re-locks on the fork's first `resume`.
        campaign_store.write_cycle_seed(
            parent.model_copy(update={"cycle_id": new_cycle_id}), payload.seed
        )


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

# A non-rebase branches from the origin and lifts no parent round, so its only cut is 0.
_ZERO_ROUND_TRIGGERS = frozenset(ForkTrigger) - _REBASE_TRIGGERS


def _fork_suffix(*parts: str) -> str:
    """The clock, at MICROSECOND resolution, is all that separates two forks of one parent: *parts* cannot."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return stable_hash([*parts, stamp], length=8)


def mint_fork(
    campaign_store: CampaignStore,
    parent: CycleHop,
    fork_from_round: int,
    payload: ForkSpec,
) -> str:
    """``fork_from_round`` is MECHANICAL (parent rounds lifted); ``ForkSpec.from_round`` is PROVENANCE."""
    if payload.from_round is None:
        payload = payload.model_copy(update={"from_round": fork_from_round})
    if payload.trigger in _ZERO_ROUND_TRIGGERS and fork_from_round != 0:
        raise ValueError(
            f"mint_fork({payload.trigger.value}) branches from the origin and lifts no parent "
            f"round, so fork_from_round must be 0; got {fork_from_round}"
        )
    if payload.trigger in _REBASE_TRIGGERS:
        new_cycle_id = f"{parent.cycle_id}_fork_{_fork_suffix(parent.cycle_id)}"
    elif payload.trigger is ForkTrigger.OPERATOR_DIAG:
        new_cycle_id = _next_diag_sibling_id(campaign_store, parent.campaign_id, parent.cycle_id)
    else:
        new_cycle_id = (
            f"{root_cycle_id(parent.cycle_id)}_fork_{_fork_suffix(parent.cycle_id, 'operator')}"
        )
    _fork_sibling_setup(
        campaign_store,
        parent,
        new_cycle_id,
        from_round=fork_from_round,
        payload=payload,
    )
    # An offshoot's parent keeps running, hence the DIRECTION.
    if FORK_DIRECTION.get(payload.trigger) is ForkDirection.SUPERSEDE:
        campaign_store.mark_superseded(parent, new_cycle_id)
    return new_cycle_id


def declare_steered_values(seed: CycleSeed, narrowing: Mapping[str, Any] | None) -> CycleSeed:
    """Without it the fork runs ``model=X`` beside a permitted set that excludes X."""

    origin = allowed_values_from_narrowing(narrowing)
    declared = dict(seed.optimizer_narrowing)
    for node, cfg in node_config_items(seed.pipeline_overlay):
        listed = origin.get(node, {})
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
            issued_by=acting_principal_id(stores.identity),
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
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    seed = declare_steered_values(
        seed, campaign.config.get("optimizer_narrowing") if campaign else None
    )
    if keep_rounds and seed.origin_prompt_fields:
        raise PayloadInvalidError(
            "keep_rounds lifts the parent's round 0 as its origin, so the seed must not "
            "declare origin_prompt_fields; fork without keep_rounds to start from an edited origin"
        )
    if not keep_rounds:
        # The remainder the parent's dashboard serves, so the fork's caps are the ones on screen.
        caps = fork_remainder(stores, hop).under(seed.config_overrides)
        seed = seed.model_copy(update={"config_overrides": caps})
    if not from_candidate_id and not keep_rounds:
        origin_round = stores.campaigns.standing_rounds(hop).rounds.get(0)
        if origin_round is not None and origin_round.close.candidate_scores:
            from_candidate_id = origin_round.close.candidate_scores[0].candidate_id
    spec = ForkSpec(
        trigger=ForkTrigger.OPERATOR_REWIND if keep_rounds else ForkTrigger.OPERATOR_STEERED,
        reason=reason
        or (
            f"operator-applied from round {from_round} of {hop.cycle_id}"
            if keep_rounds
            else f"operator-steered fork from {hop.cycle_id}"
        ),
        issued_by=acting_principal_id(stores.identity),
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
