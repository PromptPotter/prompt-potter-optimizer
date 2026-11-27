"""A fork is a new CYCLE in the SAME campaign, flat under ``cycles/``, never nested under its parent."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from promptpotter.application.bench.resume_and_fork.decisions import decision
from promptpotter.application.initialization.session import drawn_partition
from promptpotter.application.jobs.launcher.admission import destroying
from promptpotter.application.jobs.launcher.opening import land_job
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.jobs.shares import cycle_share, stand_held_budget
from promptpotter.application.pipeline_resolve import (
    cycle_config,
    declared_config,
    permitted_narrowing,
)
from promptpotter.application.served_dashboard import fork_remainder
from promptpotter.domain.arms import ArmState, Proposed
from promptpotter.domain.campaign import CampaignCycle, ceiling_meter
from promptpotter.domain.campaign_config import CycleConfig, LaidSpan
from promptpotter.domain.command_kinds import ForkCyclePayload
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.paired_reading import ArmKey, RoundArm
from promptpotter.domain.pipeline_overlay import steers_disallowed_model
from promptpotter.domain.pipeline_params import PipelineParams, laid_calls, lay_nodes
from promptpotter.domain.pipeline_schema import NodeSearchNarrowing
from promptpotter.domain.records.run_records import (
    FORK_DIRECTION,
    CycleMintedRecord,
    CycleStart,
    ForkCutFrom,
    ForkDirection,
    ForkSpec,
    ForkTrigger,
    parent_address,
)
from promptpotter.domain.run_standing import line_reference
from promptpotter.domain.spend import Budget
from promptpotter.infrastructure.store.campaign_store.ledger_scan import StandingRounds
from promptpotter.infrastructure.store.session_pointer import cleanup_stub_fork_if_empty
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import (
    MINT_FRESH,
    ConflictError,
    PayloadInvalidError,
)
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.identity import Principal, acting_principal
from promptpotter.shared.measurement_context import MeasurementRole

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session


def _fork_sibling_setup(
    stores: Stores,
    parent: CycleHop,
    child: CycleHop,
    *,
    from_round: int,
    payload: ForkSpec,
    start: CycleStart | None,
) -> None:
    campaign_store = stores.campaigns
    campaign = campaign_store.campaign(child.campaign_id)
    # Decided before anything is written: a refusal here leaves no minted, unstamped child.
    # The babysit act its launch's verdict judged (outside what *parent*'s line permits), or a line
    # steered outside what its campaign's root permits, which every fork of it carries.
    at = CampaignCycle(campaign, parent.cycle_id)
    steered = start is not None and steers_disallowed_model(
        permitted_narrowing(stores, at), laid_calls(start.pipeline_overlay)
    )
    babysat = steered or fork_config(stores, at, start).steers_outside(
        permitted_narrowing(stores, campaign.root_cycle)
    )
    parent_dir = campaign_store.cycle_dir(parent)
    new_dir = campaign_store.cycle_dir(child)
    if new_dir.exists():
        raise FileExistsError(f"forked cycle dir already exists: {new_dir}")

    # The FORK_CUT's own offset: a parent still running appends past it, never into the fork.
    cut = campaign_store.append(
        parent,
        decision(ForkCutFrom(from_round=from_round, fork=payload), child.cycle_id, node=None),
    )
    if cut is None:
        raise RuntimeError(f"{child.cycle_id}: a muted scope landed no cut to mint the fork at")
    # ONE record: the cut, the start and the babysat stamp (decisions.md § 0005) land as one fact.
    campaign_store.mint_cycle(
        child,
        CycleMintedRecord(
            parent=parent_address(new_dir, parent_dir),
            forked_at_offset=cut + 1,
            cut_round=from_round,
            fork=payload,
            start=start,
            babysat=babysat,
        ),
    )


def fork_config(stores: Stores, parent: CampaignCycle, start: CycleStart | None) -> CycleConfig:
    """What a fork of *parent* under *start* runs, read before its cut: the declaration under its
    parent's line and its own start."""
    return CycleConfig.laid(
        declared_config(stores, parent.campaign).config,
        [*cycle_config(stores, parent).spans, LaidSpan(start=start, scoring_lock=None)],
    )


def _sibling_id(root_cycle_id: str, parent: CycleHop, payload: ForkSpec) -> str:
    """Fixed length at any depth: the line is the `CycleMintedRecord` chain, never the id. The
    clock, at MICROSECOND resolution, is all that separates two forks of one parent."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return (
        f"{root_cycle_id}_fork_{stable_hash([parent.cycle_id, payload.trigger, stamp], length=8)}"
    )


def fork_edge(standing: StandingRounds, cut_round: int, named: RoundArm | None) -> RoundArm:
    """The parent CANDIDATE a fork branches from: *named* — a round's re-read named as the
    candidate it re-read — else the pick the parent's line stood on going into *cut_round*: its
    origin until an election crowned another."""
    if named is not None:
        arm = standing.arms.get((named.round, named.arm))
        if arm is None:
            raise PayloadInvalidError(f"round {named.round} of the parent holds no arm {named.arm}")
        if arm.state.invalid:
            raise PayloadInvalidError(
                f"{arm.state.label} of round {named.round} is a refused proposal, keyed by its "
                "position: it measured no individual to fork from"
            )
        if arm.state.role.is_proposal:
            return named
        if (position := arm.state.position) is None:
            raise PayloadInvalidError(
                f"round {named.round} re-read {named.arm}, which no round of the parent walked: "
                "name the candidate itself"
            )
        return RoundArm(round=position.round, arm=named.arm)
    # Off the announced arms: a line whose origin walk never closed still names its origin.
    origin = next(
        (
            key
            for (r, key), arm in standing.arms.items()
            if r == 0 and arm.state.role is MeasurementRole.ORIGIN
        ),
        None,
    )
    if origin is None:
        raise ConflictError(
            "the parent's line measured no origin to branch from", next_acts=(MINT_FRESH,)
        )
    below = [held.close for r, held in sorted(standing.rounds.items()) if r < max(cut_round, 1)]
    return line_reference(below, origin=origin)


@dataclass(frozen=True, slots=True)
class ForkCut:
    """A fork cut and minted, its parent's line not yet moved: ``take`` is the fork's act on its
    parent, ``withdraw`` deletes a cut whose launch was refused."""

    parent: CycleHop
    child: CycleHop
    direction: ForkDirection

    def said(self, stores: Stores) -> str:
        """The parent's REBASED stop's detail: the fork its line continues on, as its mint holds it."""
        minted = stores.campaigns.last(self.child, CycleMintedRecord)
        assert minted is not None and minted.fork is not None, "a cut mints its fork"
        by, reason = minted.fork.issued_by.display, minted.fork.reason
        return f"forked at round {minted.cut_round} onto {self.child.cycle_id} by {by}: {reason}"

    def take(self, stores: Stores) -> str:
        # An offshoot's parent keeps running, hence the DIRECTION.
        if self.direction is ForkDirection.SUPERSEDE:
            stores.mark_superseded(self.parent, self.child.cycle_id)
        return self.child.cycle_id

    def withdraw(self, stores: Stores, job_registry: JobRegistry, refusal: BaseException) -> None:
        # A cleanup fault never masks *refusal*, which its caller re-raises: it rides it as a note.
        child = self.child
        try:
            with destroying(stores, job_registry, child.campaign_id, cycle_id=child.cycle_id):
                cleanup_stub_fork_if_empty(campaign_store=stores.campaigns, hop=child)
        except Exception as fault:  # noqa: BLE001
            refusal.add_note(
                f"the fork's stub cycle {child.cycle_id} was kept: its withdrawal failed ({fault!r})"
            )


def cut_fork(
    stores: Stores,
    parent: CycleHop,
    fork_from_round: int,
    *,
    trigger: ForkTrigger,
    reason: str,
    issued_by: Principal,
    named: RoundArm | None = None,
    start: CycleStart | None = None,
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
    drawn_partition(campaign_store, parent)
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
    root_cycle_id = campaign_store.campaign(parent.campaign_id).root_cycle_id
    child = parent.model_copy(update={"cycle_id": _sibling_id(root_cycle_id, parent, payload)})
    _fork_sibling_setup(
        stores,
        parent,
        child,
        from_round=fork_from_round,
        payload=payload,
        start=start,
    )
    return ForkCut(parent=parent, child=child, direction=direction)


def cut_run_fork(
    session: Session,
    fork_from_round: int,
    *,
    trigger: ForkTrigger,
    reason: str,
    issued_by: Principal,
    named: RoundArm | None = None,
    start: CycleStart | None = None,
) -> ForkCut:
    """THE in-run cut, a rebase's and a divergence's, under the run's admission gate with its job
    landed on the child, as `ForkLaunch.stand` cuts an operator's: no set-limits between them
    re-shares the campaign without the fork. Its share is what the line has left, since
    *session*'s metered spend is the campaign's."""
    held = session.state.held
    if held is None:
        raise RuntimeError("a run forks only over what admission held for it")
    stores, parent = session.store, session.hop
    job_registry = JobRegistry.attach(stores.jobs_dir)
    with job_registry.admission_gate():
        job = job_registry.get(held.job_id)
        if job is None:
            raise RuntimeError(f"a run forks only on its own job, and {held.job_id} is gone")
        cut = cut_fork(
            stores,
            parent,
            fork_from_round,
            trigger=trigger,
            reason=reason,
            issued_by=issued_by,
            named=named,
            start=start,
        )
        live = cycle_share(stores, job)
        spent = stores.campaigns.cycle_metered(parent, ceiling_meter(session.purpose))
        allotment = Budget(
            usd=None if live.usd is None else max(0.0, live.usd - spent.usd_amount),
            tokens=None if live.tokens is None else max(0, live.tokens - spent.token_amount),
        )
        land_job(stores, job_registry, job.job_id, cut.child, allotment)
    stand_held_budget(stores, cut.child, held)
    return cut


def declare_steered_values(
    start: CycleStart, narrowing: Mapping[str, NodeSearchNarrowing]
) -> CycleStart:
    """Without it the fork runs ``model=X`` beside a permitted set that excludes X; X is what the
    fork runs, its candidate's own under the operator's edit."""
    declared = dict(start.optimizer_narrowing)
    for node, cfg in start.runs().items():
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
    return start.model_copy(update={"optimizer_narrowing": declared})


def operator_start(stores: Stores, payload: ForkCyclePayload, start: CycleStart) -> CycleStart:
    """*start* as `parse_cycle_start` froze it, laid for the fork's cut: a named candidate's fork
    runs its prompts and its params, the operator's edit over both."""
    hop = CycleHop(campaign_id=payload.campaign_id, cycle_id=payload.cycle_id)
    if (named := _named(payload)) is not None and (arm := _candidate(stores, hop, named)):
        start = start.model_copy(
            update={"origin_prompts": {**arm.individual.prompts, **start.origin_prompts}}
        )
        # A proposal never walked resolved no params.
        if not isinstance(arm, Proposed):
            params = PipelineParams.model_validate(arm.resolved_params).laid_nodes
            start = start.model_copy(
                update={"origin_params": lay_nodes(params, payload.start.cleared)}
            )
    # Against what the parent's nodes permit, so the child lays over what its parent ran.
    parent = CampaignCycle(stores.campaigns.campaign(hop.campaign_id), hop.cycle_id)
    start = declare_steered_values(start, permitted_narrowing(stores, parent))
    if payload.keep_rounds:
        return start
    # The remainder the parent's dashboard serves, so the fork's caps are the ones on screen.
    caps = fork_remainder(stores, hop).under(start.config_overrides)
    return start.model_copy(update={"config_overrides": caps})


def cut_operator_fork(stores: Stores, payload: ForkCyclePayload, start: CycleStart) -> ForkCut:
    """*start* as `parse_cycle_start` froze it; `operator_start` lays it for the cut."""
    hop = CycleHop(campaign_id=payload.campaign_id, cycle_id=payload.cycle_id)
    keep, at = payload.keep_rounds, payload.round
    return cut_fork(
        stores,
        hop,
        at if keep else 0,
        trigger=ForkTrigger.OPERATOR_REWIND if keep else ForkTrigger.OPERATOR_STEERED,
        reason=payload.reason
        or (
            f"operator-applied from round {at} of {hop.cycle_id}"
            if keep
            else f"operator-steered fork from {hop.cycle_id}"
        ),
        issued_by=acting_principal(stores.identity),
        named=_named(payload),
        start=operator_start(stores, payload, start),
    )


def _named(payload: ForkCyclePayload) -> RoundArm | None:
    if not payload.candidate_id:
        return None
    return RoundArm(round=payload.round, arm=ArmKey(payload.candidate_id))


def _candidate(stores: Stores, hop: CycleHop, named: RoundArm) -> ArmState | None:
    arm = stores.campaigns.standing_rounds(hop, moment=None).arms.get((named.round, named.arm))
    return None if arm is None else arm.state
