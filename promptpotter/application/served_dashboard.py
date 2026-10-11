from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Literal, NamedTuple

from pydantic import Field, ValidationError

from promptpotter.application.jobs.quota import next_launch_ceiling
from promptpotter.application.pipeline_resolve import resolve_campaign_config
from promptpotter.application.runner.campaign_result import read_cycle_bench
from promptpotter.domain.backend import ServedBackpressure
from promptpotter.domain.bench import BenchScore
from promptpotter.domain.campaign import Campaign, ceiling_meter
from promptpotter.domain.cycle_listing import RunStatus
from promptpotter.domain.cycle_paths import Cut, CycleDir, CycleHop
from promptpotter.domain.dashboard_rows import RunLimits, ServedRound, served_rounds
from promptpotter.domain.paired_reading import ArmPointer
from promptpotter.domain.phases import (
    PauseReading,
    ProducerReading,
    RunAdmission,
    RunPhase,
    RunState,
    stop_next_step,
)
from promptpotter.domain.results import VerifyStrategy, closed_after_origin
from promptpotter.domain.run_records import ForkRemainder
from promptpotter.domain.scoring import anchored_criterion_dials
from promptpotter.domain.spend import CeilingMeter, MeteredSpend, SpendCeilings
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.projections.live_dashboard.projection import fold_at
from promptpotter.infrastructure.projections.live_dashboard.state import (
    DashboardFacts,
    LiveDashboardState,
)
from promptpotter.infrastructure.runtime_flags import (
    armed_run_limits,
    derive_run_state,
    effective_lookahead,
    requested_lookahead,
    standing_controls,
    verify_stale_after,
)
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    VerifyLedger,
    scan_ledger_run_wiring,
    scan_ledger_spend,
    scan_ledger_verify,
)
from promptpotter.infrastructure.store.io import read_json_optional
from promptpotter.infrastructure.store.layout import CycleLayout, cycle_dir_for
from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)

__all__ = [
    "SampleWalk",
    "ServedDashboard",
    "VerifyPassProgress",
    "WarmingDashboard",
    "binding_run_limits",
    "fork_remainder",
    "served_dashboard",
]


class VerifyPassProgress(StrictModel):
    """``verify_pass`` on a served dashboard — one candidate being re-scored on unseen cells."""

    label: str = Field(description="The candidate the pass re-scores, as its row is labelled.")
    round: int = Field(description="That candidate's own round; 0 is the origin.")
    rows: int = Field(description="Unseen search cells the pass sends.")
    strategy: VerifyStrategy


class WarmingDashboard(StrictModel):
    """What a cycle with no dashboard state serves, at 200 rather than 404 so a surface renders
    "initialising" instead of appearing offline: a cycle in check-in — now, or at the moment a
    replay folds to — has declared nothing to stand a state on (``RunWiringRecord``), and no
    other cycle is without one. The run phase is served here as everywhere — a body with none is
    what lets a browser invent one."""

    warming_up: Literal[True] = True
    campaign_id: str
    cycle_id: str
    run_phase: RunPhase
    producer: ProducerReading
    run_admission: RunAdmission
    pause: PauseReading | None


class SampleWalk(StrictModel):
    """The scoring walk of the arm being measured: the axis it DECLARED plus a cursor. Past the
    cursor is declared, never promised — an eliminator can stop the arm before its tail."""

    ids: list[int] = Field(
        description="The declared order; before the arm declares one, the cells it has measured "
        "and the one in flight."
    )
    cursor: int = Field(
        description="Where the walk stands in `ids`: the cell in flight, else the one after the "
        "last measured."
    )
    key: str = Field(description="Changes when the walk does — a new arm is a new axis.")


class RoundAxis(StrictModel):
    """The rounds a round-scoped surface may show."""

    completed: list[int] = Field(
        description="Rounds that closed WITH measurements, in round order. A round that closed "
        "before it measured anything has no searchpoint to show and is not listed."
    )
    live: int | None = Field(
        description="The round in flight; null where no producer is appending, on a replay, and "
        "once that round is in `completed` — `current_round` lingers after a stop."
    )
    position: int | None = Field(
        description="The round the run stands at: `live`, else the newest of `completed`. Null "
        "before any round measured."
    )


def _round_axis(state: LiveDashboardState, *, live: bool) -> RoundAxis:
    completed = [r.round for r in state.rounds if r.candidates]
    current = state.current_round.round
    in_flight = current if live and current not in completed else None
    return RoundAxis(
        completed=completed,
        live=in_flight,
        position=in_flight if in_flight is not None else completed[-1] if completed else None,
    )


def _observed(state: LiveDashboardState, *, live: bool) -> ArmPointer | None:
    current = state.current_round
    if live and (rows := current.candidates):
        measuring = current.measurement_node is not None and (
            current.active_node == current.measurement_node
        )
        named = [c for c in rows if measuring and c.reading.arm.label == state.candidate]
        return (named or rows)[-1].reading.arm
    closed = [r for r in state.rounds if r.candidates]
    arm = closed[-1].candidates[-1].reading.arm if closed else None
    return arm if arm is not None and arm.candidate_id else None


def _walk(state: LiveDashboardState, *, live: bool) -> SampleWalk | None:
    """``None`` where nothing is walking: `current_round` rows linger after a stop."""
    current = state.current_round
    if not live:
        return None
    rows = (
        []
        if any(r.round == current.round and r.candidates for r in state.rounds)
        else (current.candidates)
    )
    measured = [s.sample_id for s in rows[-1].samples if s.sample_id is not None] if rows else []
    in_flight = state.current_sample_id
    key = f"{state.cycle_id}:{current.round}:{len(rows) - 1 if rows else '-'}"
    if axis := state.declared_sample_order:
        # Anchored on a cell's own position, so a walk off the declared order cannot slide the cursor.
        if in_flight in axis:
            return SampleWalk(ids=axis, cursor=axis.index(in_flight), key=key)
        last = len(axis) - 1
        if measured and measured[-1] in axis:
            return SampleWalk(ids=axis, cursor=min(axis.index(measured[-1]) + 1, last), key=key)
        return SampleWalk(ids=axis, cursor=min(len(measured), last), key=key)
    ids = measured if in_flight is None or measured[-1:] == [in_flight] else [*measured, in_flight]
    return SampleWalk(ids=ids, cursor=len(ids) - 1, key=key) if ids else None


class ServedDashboard(DashboardFacts):
    """A cycle's dashboard as it is SERVED: the fold's facts, and beside them what only the
    serving seam can say — the run phase and its producer, the controls armed now, the campaign's
    own meter, and every reading a surface would otherwise decide for itself. Nothing here reaches
    disk, and every field is required: one builder (``_serve``) states them all, for the head and
    for a replay alike. What the banked state holds as an INPUT to one of those readings
    (``LiveDashboardState``'s own fields) is not served beside it."""

    # The served rounds narrow the banked ones; nothing assigns through this list.
    rounds: list[ServedRound]  # type: ignore[assignment]
    backpressure: ServedBackpressure | None

    run_phase: RunPhase = Field(
        description="The cycle's phase, derived at the read (`derive_run_state`) — never the "
        "runner's own `declared_phase`, which says `running` forever after a kill."
    )
    status: RunStatus = Field(
        description="How the cycle reads: `run_phase` and `stop_reason` as one word and one mark."
    )
    next_step: str = Field(
        description="What the operator does now, as the cycle's `stop_reason` states it "
        "(`STOP_REASON_INFO::next_step`) — the sentence the terminal and `review.md` advise. "
        "Empty where the cycle has not stopped, or its reason states that nothing is owed."
    )
    producer: ProducerReading = Field(description="The cycle's producer, derived with the phase.")
    run_admission: RunAdmission = Field(
        description="Which run verbs the cycle admits now (`RunState.admission`): the verb the "
        "run control offers or the sentence where it offers none, and each verb's refusal — the "
        "same answer the dispatcher refuses on."
    )
    pause: PauseReading | None = Field(
        description="Why the cycle reads `paused` — what caused it, the stop it was left on and "
        "what the operator does next (`RunState.pause`); null in every other phase."
    )
    overlap_line_round: int | None = Field(
        description="Which of `rounds` carries the newest overlap line that was read — the one "
        "set of cells its members' rates may be set side by side on."
    )
    overlap_line_n: int | None = Field(
        description="How many cells that line is read on — its round's origin panel, which every "
        "member drawn on it holds whole. Null with `overlap_line_round`."
    )
    verify_pass: VerifyPassProgress | None = Field(
        description="A verify pass in flight on one of this cycle's candidates, null outside one."
    )
    composite_fitness_weights: dict[str, float] | None = Field(
        description="`composite_fitness_formula` as `{term: weight}`, where it IS an anchored "
        "criterion — what a scoring form's dials seed from. Null says the formula cannot carry "
        "them, so the form opens on its expression rather than guessing."
    )
    composite_fitness_anchors: dict[str, float] | None = Field(
        description="The level each anchored dial in it is read against, by term."
    )
    sample_lookahead: int = Field(
        description="The depth IN FORCE — how many cells the walk holds in flight: the armed "
        "request bounded by `max_cells_in_flight`. 1 on a replay, which takes no armed control."
    )
    sample_lookahead_auto: bool = Field(
        description="Whether that depth outlives its round — the operator's auto-arm."
    )
    lookahead_money_hold: str | None = Field(
        description="How many more cells the spend limits admit, in words, where money and not "
        "the armed depth holds the walk; null where it does not."
    )
    lookahead_pick_max: int = Field(description="The deepest press the next walk can take.")
    lookahead_unavailable: str = Field(
        description="Why holding calls ahead does not apply to this backend at all "
        "(`max_cells_in_flight` of 1); empty where it applies."
    )
    lookahead_explained: str = Field(
        description="What the armed depth does, as the control explains itself: the mode in "
        "force, its bound and the calls discarded so far — or `lookahead_unavailable`."
    )
    spend_metered: MeteredSpend | None = Field(
        description="The cycle's spend under the campaign's ceiling meter, a manifest fact no "
        "ledger record carries; null where the campaign's manifest is gone."
    )
    spend_metered_by_round: dict[str, MeteredSpend] | None = Field(
        description="`spend_metered` per round, in round order."
    )
    fork_remainder: ForkRemainder | None = Field(
        description="What an offshoot of this cycle starts under; null on a replay."
    )
    observed: ArmPointer | None = Field(
        description="The arm a surface following the run shows (`_observed`); null before any "
        "arm exists."
    )
    walk: SampleWalk | None = Field(
        description="The scoring walk in flight; null where no producer is appending, and on a "
        "replay."
    )
    round_axis: RoundAxis


class _Standing(NamedTuple):
    """Only the PRESENT holds these; a replay takes none — a past moment never had them."""

    run_limits: RunLimits
    lookahead: int
    lookahead_auto: bool
    verify: VerifyLedger
    verify_stale_after: float | None
    fork_remainder: ForkRemainder
    bench_score: BenchScore | None
    at: float


def binding_run_limits(
    stores: Stores, hop: CycleHop, campaign: Campaign | None, run: RunState
) -> RunLimits | None:
    """``None`` on a cycle that has declared no wiring — one still in check-in."""
    wiring = scan_ledger_run_wiring(ledger_chain(CycleDir(cycle_dir_for(stores.base_dir, hop))))
    return None if wiring is None else _binding(stores, hop, campaign, run, wiring.run_limits)


def _binding(
    stores: Stores, hop: CycleHop, campaign: Campaign | None, run: RunState, declared: RunLimits
) -> RunLimits:
    limits = declared
    if campaign is not None and campaign.config and not run.producer.appending:
        config = resolve_campaign_config(stores, campaign, hop)
        limits = limits.model_copy(
            update={
                "ceiling": next_launch_ceiling(config, stores=stores, hop=hop),
                "max_rounds": config.optimization.max_rounds or None,
            }
        )
    return armed_run_limits(cycle_dir_for(stores.base_dir, hop), limits)


def _remainder(
    stores: Stores, hop: CycleHop, limits: RunLimits | None, meter: CeilingMeter | None
) -> ForkRemainder:
    chain = ledger_chain(CycleDir(cycle_dir_for(stores.base_dir, hop)))
    return ForkRemainder.of(
        rounds_closed=closed_after_origin(list(stores.campaigns.standing_rounds(hop).rounds)),
        max_rounds=None if limits is None else limits.max_rounds,
        metered_usd=None
        if meter is None
        else MeteredSpend.of(scan_ledger_spend(chain).spend, meter).metered_usd,
        ceiling=SpendCeilings() if limits is None else limits.ceiling,
    )


def fork_remainder(stores: Stores, hop: CycleHop) -> ForkRemainder:
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    run = derive_run_state(cycle_dir_for(stores.base_dir, hop))
    return _remainder(
        stores,
        hop,
        binding_run_limits(stores, hop, campaign, run),
        None if campaign is None else ceiling_meter(campaign.arm),
    )


_ONE_AT_A_TIME = "This backend runs one sample at a time — a single call has no latency to overlap."


def _money_hold(affordable: int) -> str:
    return "holds no further cell" if affordable <= 0 else f"{affordable} more affordable"


def _lookahead_explained(*, auto: bool, ceiling: int, pick_max: int, discards: int) -> str:
    held = (
        f"Every round, scoring holds as many calls as the stop rules allow, up to {ceiling}"
        if auto
        else f"Runs this round's scoring with this many calls in flight, up to {pick_max}, "
        "then resets to 1"
    )
    discarded = f" ({discards} discarded)" if discards else ""
    return (
        f"{held}. A cut discards at most one call. The measurement is unchanged and the cycle "
        f"is NOT marked babysat{discarded}."
    )


def _serve(
    state: LiveDashboardState,
    *,
    run: RunState,
    meter: CeilingMeter | None,
    standing: _Standing | None,
) -> ServedDashboard:
    """*standing* is ``None`` for a replay: the moment it folded, and nothing armed since."""
    ceiling = state.max_cells_in_flight
    depth = 1 if standing is None else effective_lookahead(standing.lookahead, ceiling)
    banked: dict[str, Any] = {name: getattr(state, name) for name in DashboardFacts.model_fields}
    # A hold is read against the clock: a past moment has none, and a dead producer holds nothing.
    banked["backpressure"] = None
    if standing is not None:
        banked["run_limits"] = standing.run_limits
        if standing.bench_score is not None:
            # The stored one is as the run last declared it; askability moves with the producer.
            banked["bench_score"] = standing.bench_score
        if not run.producer.appending:
            # A killed run never publishes its closing zero, so it would go on reporting calls out.
            banked.update(in_flight=0, lookahead_allowed=0, waiting_on=None)
        elif (held := state.backpressure) is not None:
            banked["backpressure"] = ServedBackpressure.at(held, standing.at)
    verify = None if standing is None else standing.verify
    rounds = served_rounds(
        state.rounds,
        {} if verify is None else {label: read for label, (_, read) in verify.graded.items()},
    )
    opened = None if verify is None else verify.open
    stale_after = None if standing is None else standing.verify_stale_after
    formula = state.composite_fitness_formula
    dials = None if formula is None else anchored_criterion_dials(formula)
    affordable, most = state.lookahead_affordable, state.lookahead_most
    # Both lookahead verdicts read the depth and the gauge as just corrected.
    money_hold = (
        _money_hold(affordable)
        if standing is not None
        and affordable is not None
        and banked["in_flight"] + affordable < min(depth, banked["lookahead_allowed"])
        else None
    )
    pick_max = max(1, min(ceiling, most)) if most is not None and most > 0 else ceiling
    auto = standing is not None and standing.lookahead_auto
    unavailable = _ONE_AT_A_TIME if ceiling <= 1 else ""
    live = standing is not None and run.producer.appending
    line_round = next((r for r in reversed(rounds) if r.overlap_line), None)
    return ServedDashboard(
        **banked | {"rounds": rounds},
        run_phase=run.run_phase,
        status=RunStatus.of(run.run_phase, state.stop_reason),
        next_step=stop_next_step(state.stop_reason),
        producer=run.producer,
        run_admission=run.admission,
        pause=run.pause,
        overlap_line_round=None if line_round is None else line_round.round,
        overlap_line_n=None if line_round is None else len(line_round.overlap.sample_ids),
        verify_pass=VerifyPassProgress(
            label=opened.label, round=opened.round, rows=opened.rows, strategy=opened.strategy
        )
        if opened is not None
        and standing is not None
        and stale_after is not None
        and standing.at < stale_after
        else None,
        composite_fitness_weights=None
        if dials is None
        else {name: d.weight for name, d in dials.items()},
        composite_fitness_anchors=None
        if dials is None
        else {name: d.anchor for name, d in dials.items() if d.anchor is not None},
        sample_lookahead=depth,
        sample_lookahead_auto=auto,
        lookahead_money_hold=money_hold,
        lookahead_pick_max=pick_max,
        lookahead_unavailable=unavailable,
        lookahead_explained=unavailable
        or _lookahead_explained(
            auto=auto,
            ceiling=ceiling,
            pick_max=pick_max,
            discards=state.sample_lookahead_discards,
        ),
        spend_metered=None if meter is None else MeteredSpend.of(state.spend, meter),
        spend_metered_by_round=None
        if meter is None
        else {
            key: MeteredSpend.of(spent, meter)
            for key, spent in sorted(state.spend_by_round.items(), key=lambda kv: int(kv[0]))
        },
        fork_remainder=None if standing is None else standing.fork_remainder,
        observed=_observed(state, live=live),
        walk=_walk(state, live=live),
        round_axis=_round_axis(state, live=live),
    )


def _head_state(cycle_path: Path, hop: CycleHop) -> LiveDashboardState | None:
    """``None`` where the cycle's ledger has declared no wiring."""
    cut = Cut(cycle=CycleDir(cycle_path), hop=hop)
    try:
        stored = read_json_optional(CycleLayout(cycle_path).dashboard)
        if stored is None:
            return fold_at(cut)
        state = LiveDashboardState.model_validate(stored)
        if (state.campaign_id, state.cycle_id) == (hop.campaign_id, hop.cycle_id):
            return state
        why = "names another cycle"
    except (json.JSONDecodeError, ValidationError) as exc:
        why = type(exc).__name__
    logger.warning(
        "dashboard.json of %s/%s does not read as this cycle's under this build — served off a "
        "refold of its ledger (%s)",
        hop.campaign_id,
        hop.cycle_id,
        why,
    )
    return fold_at(cut)


def served_dashboard(
    stores: Stores, hop: CycleHop, *, at: int | None = None
) -> ServedDashboard | WarmingDashboard:
    """``at`` replays to that ledger offset: live ``run_phase`` and ``producer``, nothing armed."""
    cycle_path = cycle_dir_for(stores.base_dir, hop)
    run = derive_run_state(cycle_path)
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    meter = None if campaign is None else ceiling_meter(campaign.arm)

    state = (
        _head_state(cycle_path, hop)
        if at is None
        else fold_at(Cut(cycle=CycleDir(cycle_path), hop=hop, offset=at))
    )
    if state is None:
        return WarmingDashboard(
            campaign_id=hop.campaign_id,
            cycle_id=hop.cycle_id,
            run_phase=run.run_phase,
            producer=run.producer,
            run_admission=run.admission,
            pause=run.pause,
        )
    if at is not None:
        return _serve(state, run=run, meter=meter, standing=None)
    limits = _binding(stores, hop, campaign, run, state.run_limits)
    armed = standing_controls(cycle_path)
    return _serve(
        state,
        run=run,
        meter=meter,
        standing=_Standing(
            run_limits=limits,
            lookahead=requested_lookahead(armed),
            lookahead_auto=armed.lookahead is not None and armed.lookahead.auto,
            verify=scan_ledger_verify(CycleLayout(cycle_path).ledger),
            verify_stale_after=verify_stale_after(cycle_path),
            fork_remainder=_remainder(stores, hop, limits, meter),
            bench_score=None if campaign is None else read_cycle_bench(stores, campaign, hop),
            at=time.time(),
        ),
    )
