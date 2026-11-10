"""A fork is a new CYCLE in the SAME campaign, flat under ``cycles/``, never nested under its parent."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from promptpotter.application.pipeline_resolve import cycle_config, permitted_narrowing
from promptpotter.application.served_dashboard import fork_remainder
from promptpotter.domain.arms import Proposed
from promptpotter.domain.bench import PartitionRecord
from promptpotter.domain.campaign_config import CycleConfig, LaidSpan
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.paired_reading import ArmKey, RoundArm
from promptpotter.domain.pipeline_params import PipelineParams
from promptpotter.domain.pipeline_schema import NodeSearchNarrowing
from promptpotter.domain.run_records import (
    FORK_DIRECTION,
    BenchCheckpointKind,
    CycleSeedRecord,
    ForkDirection,
    ForkSpec,
    ForkTrigger,
    ResumeCheckpointRecord,
)
from promptpotter.domain.run_standing import line_reference
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.layout import campaign_cycles_dir, root_cycle_id
from promptpotter.infrastructure.store.session_pointer import (
    cleanup_stub_fork_if_empty,
    save_active_pointer,
)
from promptpotter.infrastructure.store.stores import owned_campaign
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ConflictError, PayloadInvalidError
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.identity import acting_principal
from promptpotter.shared.measurement_context import MeasurementRole

if TYPE_CHECKING:
    from promptpotter.domain.run_records import CycleSeed
    from promptpotter.infrastructure.store.campaign_store.ledger_scan import StandingRounds
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
    from promptpotter.infrastructure.store.stores import Stores
    from promptpotter.shared.identity import Principal

logger = logging.getLogger(__name__)


def _fork_sibling_setup(
    stores: Stores,
    parent: CycleHop,
    child: CycleHop,
    *,
    from_round: int,
    payload: ForkSpec,
    seed: CycleSeed | None,
) -> None:
    campaign_store = stores.campaigns
    campaign = campaign_store.load_campaign(child.campaign_id)
    if campaign is None:
        raise ConflictError(f"campaign {child.campaign_id} holds no manifest to fork under")
    # Decided before anything is written: a refusal here leaves no minted, unstamped child.
    babysat = CycleConfig.laid(
        cycle_config(stores, campaign, None).config,
        [*cycle_config(stores, campaign, parent).spans, LaidSpan(seed=seed, scoring_lock=None)],
    ).steers_outside(permitted_narrowing(stores, campaign))
    parent_dir = campaign_store.cycle_dir(parent)
    new_dir = campaign_store.cycle_dir(child)
    if new_dir.exists():
        raise FileExistsError(f"forked cycle dir already exists: {new_dir}")
    new_dir.mkdir(parents=True)

    # The FORK_CUT's own offset: a parent still running appends past it, never into the fork.
    cut = CycleEventLog.open(CycleDir(parent_dir)).append(
        ResumeCheckpointRecord(
            kind=BenchCheckpointKind.FORK_CUT,
            node=None,
            inputs_ref={"from_round": from_round},
            outcome=child.cycle_id,
            data={"forked_at": utcnow_iso(), "fork": payload.model_dump(mode="json")},
        )
    )
    logger.info(
        "Forked %s → %s at round %d [trigger=%s]",
        parent.cycle_id,
        child.cycle_id,
        from_round,
        payload.trigger.value,
    )
    campaign_store.mint_fork_cycle(child, parent_dir, payload, cut=cut + 1, cut_round=from_round)
    if seed is not None:
        # Without its own record a rebase's config unlock silently re-locks on the fork's first `resume`.
        campaign_store.append(child, CycleSeedRecord(seed=seed))
    # decisions.md § 0005: stamped once, at the mint every fork takes, its own steer or its line's.
    if babysat:
        campaign_store.record_intervention(child, kind="disallowed_model_override")


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


def _fork_suffix(*parts: str) -> str:
    """The clock, at MICROSECOND resolution, is all that separates two forks of one parent: *parts* cannot."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return stable_hash([*parts, stamp], length=8)


def _sibling_id(campaign_store: CampaignStore, parent: CycleHop, payload: ForkSpec) -> str:
    """Fixed length at any depth: the line is the `CycleMintedRecord` chain, never the id."""
    if payload.trigger is ForkTrigger.OPERATOR_DIAG:
        return _next_diag_sibling_id(campaign_store, parent.campaign_id, parent.cycle_id)
    return f"{root_cycle_id(parent.cycle_id)}_fork_{_fork_suffix(parent.cycle_id, payload.trigger)}"


def fork_edge(standing: StandingRounds, cut_round: int, named: RoundArm | None) -> RoundArm:
    """The parent CANDIDATE a fork branches from: *named* — a round's re-read named as the
    candidate it re-read — else the pick the parent's line stood on going into *cut_round*: its
    origin until an election crowned another."""
    if named is not None:
        arm = standing.arms.get((named.round, named.arm))
        if arm is None:
            raise PayloadInvalidError(f"round {named.round} of the parent holds no arm {named.arm}")
        if arm.state.refused:
            raise PayloadInvalidError(
                f"{arm.state.label} of round {named.round} is a refused proposal, keyed by its "
                "slot: it measured no individual to fork from"
            )
        if arm.state.role is not MeasurementRole.PARENT:
            return named
        if (slot := arm.state.slot) is None:
            raise PayloadInvalidError(
                f"round {named.round} re-read {named.arm}, which no round of the parent walked: "
                "name the candidate itself"
            )
        return RoundArm(round=slot.round, arm=named.arm)
    # Off the announced arms: a --diag line walks its origin and closes no round 0.
    origin = next(
        (
            key
            for (r, key), arm in standing.arms.items()
            if r == 0 and arm.state.role is MeasurementRole.ORIGIN
        ),
        None,
    )
    if origin is None:
        raise ConflictError("the parent's line measured no origin to branch from")
    below = [held.close for r, held in sorted(standing.rounds.items()) if r < max(cut_round, 1)]
    return line_reference(below, origin=origin)


@dataclass(frozen=True, slots=True)
class ForkCut:
    """A fork cut and minted, its parent's line not yet moved: ``take`` is the fork's act on its
    parent, ``withdraw`` deletes a cut whose launch was refused."""

    parent: CycleHop
    child: CycleHop
    direction: ForkDirection

    def take(self, stores: Stores) -> str:
        save_active_pointer(stores.campaigns.workspace, self.child)
        # An offshoot's parent keeps running, hence the DIRECTION.
        if self.direction is ForkDirection.SUPERSEDE and stores.campaigns.mark_superseded(
            self.parent, self.child.cycle_id
        ):
            stores.void_inbox(self.parent)
        return self.child.cycle_id

    def withdraw(self, stores: Stores) -> None:
        # Best-effort: it must never mask the refusal its caller re-raises.
        try:
            deleted, reason = cleanup_stub_fork_if_empty(
                campaign_store=stores.campaigns, hop=self.child
            )
        except Exception:
            logger.exception("fork %s: refused, and its cut could not be deleted", self.child)
            return
        if not deleted:
            logger.warning("fork %s: refused, and its cut was kept (%s)", self.child, reason)


def cut_fork(
    stores: Stores,
    parent: CycleHop,
    fork_from_round: int,
    *,
    trigger: ForkTrigger,
    reason: str,
    issued_by: Principal,
    named: RoundArm | None = None,
    seed: CycleSeed | None = None,
) -> ForkCut:
    """``fork_from_round`` is how many parent rounds the cut lifts, recorded as its ``cut_round``."""
    campaign_store = stores.campaigns
    direction = FORK_DIRECTION[trigger]
    # Only a superseding cut continues the parent's line, so only it lifts the parent's rounds.
    if direction is not ForkDirection.SUPERSEDE and fork_from_round != 0:
        raise ValueError(
            f"cut_fork({trigger.value}) branches from the origin and lifts no parent "
            f"round, so fork_from_round must be 0; got {fork_from_round}"
        )
    if campaign_store.last(parent, PartitionRecord) is None:
        raise ConflictError(
            f"cycle {parent.cycle_id} holds no drawn partition over its chain, "
            "so no fork can stand on it"
        )
    standing = campaign_store.standing_rounds(parent, moment=None)
    # A rewind in place refuses past its last close (`rewind_enters`); so does the cut.
    if (
        direction is ForkDirection.SUPERSEDE
        and fork_from_round > (-1 if standing.last_closed is None else standing.last_closed) + 1
    ):
        raise PayloadInvalidError(
            f"round {fork_from_round}: the parent's completed rounds are "
            f"{'none' if standing.last_closed is None else f'0..{standing.last_closed}'}"
        )
    edge = fork_edge(standing, fork_from_round, named)
    payload = ForkSpec(trigger=trigger, reason=reason, issued_by=issued_by, from_candidate=edge)
    child = parent.model_copy(update={"cycle_id": _sibling_id(campaign_store, parent, payload)})
    _fork_sibling_setup(
        stores,
        parent,
        child,
        from_round=fork_from_round,
        payload=payload,
        seed=seed,
    )
    return ForkCut(parent=parent, child=child, direction=direction)


def declare_steered_values(
    seed: CycleSeed, narrowing: Mapping[str, NodeSearchNarrowing]
) -> CycleSeed:
    """Without it the fork runs ``model=X`` beside a permitted set that excludes X; X is what the
    fork runs, its candidate's own under the operator's edit."""
    declared = dict(seed.optimizer_narrowing)
    for node, cfg in seed.runs().items():
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
            declared[node] = (own or NodeSearchNarrowing()).model_copy(
                update={"param_allowed_values": values}
            )
    return seed.model_copy(update={"optimizer_narrowing": declared})


def cut_diag_sibling(*, stores: Stores, hop: CycleHop) -> ForkCut:
    return cut_fork(
        stores,
        hop,
        0,
        trigger=ForkTrigger.OPERATOR_DIAG,
        reason="diag-sibling BFS exploration",
        issued_by=acting_principal(stores.identity),
    )


def cut_operator_fork(
    *,
    stores: Stores,
    hop: CycleHop,
    from_round: int,
    seed: CycleSeed,
    from_candidate_id: str = "",
    keep_rounds: bool = False,
    reason: str = "",
) -> ForkCut:
    campaign = owned_campaign(stores, hop.campaign_id)
    named = RoundArm(round=from_round, arm=ArmKey(from_candidate_id)) if from_candidate_id else None
    if named is not None:
        seed = seed.model_copy(update={"origin_params": _candidate_params(stores, hop, named)})
    # Against what the parent's nodes permit, so the child lays over what its parent ran.
    seed = declare_steered_values(seed, permitted_narrowing(stores, campaign, hop))
    if not keep_rounds:
        # The remainder the parent's dashboard serves, so the fork's caps are the ones on screen.
        caps = fork_remainder(stores, hop).under(seed.config_overrides)
        seed = seed.model_copy(update={"config_overrides": caps})
    return cut_fork(
        stores,
        hop,
        from_round if keep_rounds else 0,
        trigger=ForkTrigger.OPERATOR_REWIND if keep_rounds else ForkTrigger.OPERATOR_STEERED,
        reason=reason
        or (
            f"operator-applied from round {from_round} of {hop.cycle_id}"
            if keep_rounds
            else f"operator-steered fork from {hop.cycle_id}"
        ),
        issued_by=acting_principal(stores.identity),
        named=named,
        seed=seed,
    )


def _candidate_params(stores: Stores, hop: CycleHop, named: RoundArm) -> dict[str, Any] | None:
    """Each node's config as the named candidate's record resolved it; ``None`` where it was
    proposed and never walked, so it resolved none."""
    arm = stores.campaigns.standing_rounds(hop, moment=None).arms.get((named.round, named.arm))
    if arm is None or isinstance(arm.state, Proposed):
        return None
    return PipelineParams.model_validate(arm.state.resolved_params).laid_nodes
