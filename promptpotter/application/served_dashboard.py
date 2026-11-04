from __future__ import annotations

import time
from typing import Any, Literal, NamedTuple

from pydantic import Field

from promptpotter.application.jobs.quota import next_launch_budget
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.pipeline_resolve import cycle_config
from promptpotter.domain.backend import ServedBackpressure
from promptpotter.domain.campaign import ceiling_meter
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.cycle_paths import Cut, CycleDir, CycleHop
from promptpotter.domain.dashboard_rows import RoundHealth, RunLimits, ServedRound, served_rounds
from promptpotter.domain.origin_panel import overlap_line
from promptpotter.domain.paired_reading import ArmName
from promptpotter.domain.phases import (
    PRODUCER_STATE_INFO,
    RUN_PHASE_INFO,
    ErrorRecord,
    PauseReading,
    ProducerReading,
    RunAdmission,
    RunPhase,
    RunState,
)
from promptpotter.domain.round_health import HEALTH_GRADE_INFO
from promptpotter.domain.run_records import ForkRemainder
from promptpotter.domain.run_standing import closed_after_origin
from promptpotter.domain.said import Said, pinned
from promptpotter.domain.scoring import anchored_criterion_dials
from promptpotter.domain.spend import Budget, CeilingMeter, MeteredSpend
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.projections.live_dashboard.projection import fold_at
from promptpotter.infrastructure.projections.live_dashboard.state import (
    DashboardFacts,
    LiveDashboardState,
)
from promptpotter.infrastructure.runtime_flags import (
    derive_run_state,
    effective_lookahead,
    requested_lookahead,
    standing_controls,
)
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    VerifyLedger,
    scan_ledger_verify,
)
from promptpotter.infrastructure.store.layout import CycleLayout, cycle_dir_for
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.clock import duration_words


class WarmingDashboard(StrictModel):
    """What a cycle with no dashboard state serves, at 200 rather than 404 so a surface renders
    "initialising" instead of appearing offline: a cycle replayed to a moment inside the check-in
    its draft's ledger brought in has declared nothing to stand a state on (``RunWiringRecord``),
    and no other cycle is without one. The run phase is served here as everywhere — a body with none is
    what lets a browser invent one."""

    warming_up: Literal[True] = True
    campaign_id: str
    cycle_id: str
    run_phase: RunPhase
    producer: ProducerReading
    admission: RunAdmission
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


LookaheadShut = Literal["one_at_a_time", "settled"]
# `SampleLookahead` worded; `{name}` is the field of that name.
LookaheadWord = Literal[
    LookaheadShut,
    "no_further_cell",
    "more_affordable",
    "holds_every_round",
    "holds_this_round",
    "discarded",
    "depth_one",
    "depth_many",
]
LOOKAHEAD_WORDS: dict[LookaheadWord, str] = {
    "one_at_a_time": "This backend runs one sample at a time — a single call has no latency to "
    "overlap.",
    "settled": "not available now",
    "no_further_cell": "holds no further cell",
    "more_affordable": "more affordable",
    "holds_every_round": "Every round, scoring holds as many calls as the stop rules allow, up to "
    "{ceiling}. A cut discards at most one call. The measurement is unchanged and the cycle is "
    "NOT marked babysat.",
    "holds_this_round": "Runs this round's scoring with this many calls in flight, up to "
    "{pick_max}, then resets to 1. A cut discards at most one call. The measurement is unchanged "
    "and the cycle is NOT marked babysat.",
    "discarded": "{discards} discarded so far.",
    "depth_one": "One at a time",
    "depth_many": "Hold {cells} in flight",
}


class LookaheadDepth(StrictModel):
    """One depth the look-ahead control offers: that many calls held in flight."""

    cells: int
    fill: Literal["full", "part"] | None = Field(
        description="`full` where that many calls are out now, `part` where the gauge allows that "
        "many; null past both."
    )
    pickable: bool = Field(
        description="Whether a press of this depth is admitted now: the control applies, the run "
        "admits `set-sample-lookahead` and the next walk can take that many."
    )


class SampleLookahead(StrictModel):
    """The sample look-ahead: the depth in force, the gauge of calls held ahead, and what its
    control offers. A replay takes no armed control, so it reads depth 1 and nothing armed."""

    depth: int = Field(
        description="The depth IN FORCE — how many cells the walk holds in flight: the armed "
        "request bounded by `ceiling`."
    )
    auto: bool = Field(
        description="Whether that depth outlives its round — the operator's auto-arm."
    )
    ceiling: int = Field(
        description="The backend's `max_cells_in_flight`; 1 says the control does not apply."
    )
    in_flight: int = Field(description="Calls out now; 0 once no producer is appending.")
    allowed: int = Field(description="Calls the gauge admits out now; 0 with `in_flight`.")
    most: int = Field(
        description="The most calls the stop rules allow out; between phases, the next round's."
    )
    scoring_now: bool = Field(
        description="Whether a walk holds or admits any call now; otherwise `most` speaks for the "
        "next round alone."
    )
    pick_max: int = Field(description="The deepest press the next walk can take.")
    affordable: int | None = Field(
        description="How many more cells the spend limits admit, where money and not the armed "
        "depth holds the walk (`LOOKAHEAD_WORDS`); null where it does not."
    )
    cell_reserve_usd: float | None = Field(
        description="What each call out holds of the ceiling; null where no book binds."
    )
    discards: int = Field(description="Calls a cut has discarded so far.")
    auto_refusal: LookaheadShut | None = Field(
        description="Why the auto-arm switch is shut: `one_at_a_time` where holding calls ahead "
        "does not apply to this backend at all, `settled` once the cycle has. A mode, not a "
        "press, so a paused run takes it for its relaunch; null where offered."
    )
    depths: list[LookaheadDepth] = Field(
        description="Each depth the control draws, 1 to `ceiling`."
    )


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


def _observed(state: LiveDashboardState, *, live: bool) -> ArmName | None:
    current = state.current_round
    if live and (rows := current.candidates):
        named = [c for c in rows if c.reading.arm == state.measuring_arm]
        return (named or rows)[-1].reading.arm
    closed = [r for r in state.rounds if r.candidates]
    return closed[-1].candidates[-1].reading.arm if closed else None


def _walk(state: LiveDashboardState, hop: CycleHop, *, live: bool) -> SampleWalk | None:
    """``None`` where nothing is walking: `current_round` rows linger after a stop."""
    current = state.current_round
    if not live:
        return None
    rows = (
        []
        if any(r.round == current.round and r.candidates for r in state.rounds)
        else (current.candidates)
    )
    measured = [s.position for s in rows[-1].samples] if rows else []
    in_flight = state.current_position
    key = f"{hop.cycle_id}:{current.round}:{len(rows) - 1 if rows else '-'}"
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
    campaign_id: str
    cycle_id: str
    backpressure: ServedBackpressure | None

    run_phase: RunPhase = Field(
        description="The cycle's phase, derived at the read (`derive_run_state`) — never the "
        "runner's own `declared_phase`, which says `running` forever after a kill."
    )
    producer: ProducerReading = Field(description="The cycle's producer, derived with the phase.")
    admission: RunAdmission = Field(
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
    composite_fitness_weights: dict[str, float] | None = Field(
        description="`composite_fitness_formula` as `{term: weight}`, where it IS an anchored "
        "criterion — what a scoring form's dials seed from. Null says the formula cannot carry "
        "them, so the form opens on its expression rather than guessing."
    )
    composite_fitness_anchors: dict[str, float] | None = Field(
        description="The level each anchored dial in it is read against, by term."
    )
    lookahead: SampleLookahead
    spend_metered: MeteredSpend | None = Field(
        description="The cycle's spend under the campaign's ceiling meter, a manifest fact no "
        "ledger record carries; null where the campaign's manifest is gone."
    )
    spend_metered_by_round: dict[str, MeteredSpend] | None = Field(
        description="`spend_metered` per round, in round order."
    )
    campaign_metered_usd: float | None = Field(
        description="What the campaign's ceiling has counted in its meter, every cycle of it "
        "once, deleted ones included — the figure `run_limits.budget.usd` binds, of which "
        "`spend_metered` is this cycle's own. Null on a replay and where the manifest is gone."
    )
    campaign_metered_tokens: int | None = Field(
        description="The same count in billed tokens, the figure `run_limits.budget.tokens` "
        "binds; null with `campaign_metered_usd`."
    )
    allotment: Budget | None = Field(
        description="This cycle's share of the campaign's ceiling — the most it may have metered "
        "in all, and what its run's book binds; null where no run holds a share, and on a replay."
    )
    fork_remainder: ForkRemainder | None = Field(
        description="What an offshoot of this cycle starts under; null on a replay."
    )
    observed: ArmName | None = Field(
        description="The arm a surface following the run shows (`_observed`); null before any "
        "arm exists."
    )
    walk: SampleWalk | None = Field(
        description="The scoring walk in flight; null where no producer is appending, and on a "
        "replay."
    )
    round_axis: RoundAxis
    said: Said | None = Field(
        description="The run's pinned line (`run_said`), its producer among the rows; null where "
        "nothing needs the operator."
    )


def producer_row(producer: ProducerReading) -> Said:
    """Read against the clock, so only the dashboard, never the listing, carries it."""
    info, silent = PRODUCER_STATE_INFO[producer.state], producer.silent_for_s
    clocked = "" if silent is None else f" for {duration_words(silent)}"
    return Said(
        key=f"producer_{producer.state.value}",
        tone=info.tone,
        label=info.label,
        detail=None if info.detail is None else info.detail + clocked,
    )


def run_said(
    error: ErrorRecord | None,
    run_phase: RunPhase,
    health: RoundHealth | None,
    *clocked: Said,
) -> Said | None:
    """The loudest of the run's fault, its last round's critical health and the *clocked* rows a
    read with a clock adds, where loud enough: the one line each medium pins for this run."""
    rows = list(clocked)
    if error is not None:
        rows.append(Said(key="run_fault", tone="danger", label=error.message, detail=error.detail))
    # A run holding for the operator says why on its own card.
    if (
        health is not None
        and health.grade == "critical"
        and health.reading is not None
        and not RUN_PHASE_INFO[run_phase].awaits_operator
    ):
        rows.append(
            Said(
                key="round_critical",
                tone=HEALTH_GRADE_INFO["critical"].tone,
                label=health.reading.title,
                detail=health.reading.advice,
            )
        )
    return pinned(rows)


class _Standing(NamedTuple):
    """Only the PRESENT holds these; a replay takes none — a past moment never had them."""

    run_limits: RunLimits
    allotment: Budget | None
    lookahead: int
    lookahead_auto: bool
    verify: VerifyLedger
    # A pass still running on the ledger that no attached run and no paid pass's job scores.
    orphaned: bool
    fork_remainder: ForkRemainder
    # ``None`` where the manifest, and so the meter, is gone.
    campaign_metered: Budget | None
    at: float


def binding_run_limits(
    stores: Stores, hop: CycleHop, config: CampaignConfig | None, run: RunState
) -> RunLimits | None:
    """``None`` on a cycle that has declared no wiring yet."""
    held = fold_at(Cut(cycle=CycleDir(cycle_dir_for(stores.base_dir, hop)), hop=hop, offset=None))
    return None if held is None else _binding(stores, hop, config, run, held.run_limits)


def _binding(
    stores: Stores, hop: CycleHop, config: CampaignConfig | None, run: RunState, folded: RunLimits
) -> RunLimits:
    """*folded* carries the cycle's standing budget already; the campaign's ceiling rides its root.
    A next launch keeps the fold's optimizer limits under the budget it would be admitted at."""
    if config is not None and not run.producer.appending:
        return folded.model_copy(
            update={"budget": next_launch_budget(config, stores=stores, hop=hop)}
        )
    return stores.campaigns.armed_run_limits(hop, folded)


def _allotment(stores: Stores, hop: CycleHop, run: RunState) -> Budget | None:
    # The share the book opened over, or the reshare that last moved it; no run, no share.
    if not run.producer.appending:
        return None
    held = stores.campaigns.read_cycle_share(hop)
    return None if held is None else held.allotment


def _remainder(stores: Stores, hop: CycleHop, limits: RunLimits | None) -> ForkRemainder:
    return ForkRemainder.of(
        rounds_closed=closed_after_origin(
            list(stores.campaigns.standing_rounds(hop, moment=None).rounds)
        ),
        rounds=None if limits is None else limits.budget.rounds,
    )


def fork_remainder(stores: Stores, hop: CycleHop) -> ForkRemainder:
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    run = derive_run_state(cycle_dir_for(stores.base_dir, hop))
    config = None if campaign is None else cycle_config(stores, campaign, hop).config
    return _remainder(stores, hop, binding_run_limits(stores, hop, config, run))


def _lookahead(
    state: LiveDashboardState,
    *,
    run: RunState,
    standing: _Standing | None,
    in_flight: int,
    allowed: int,
) -> SampleLookahead:
    ceiling = state.max_cells_in_flight
    depth = 1 if standing is None else effective_lookahead(standing.lookahead, ceiling)
    affordable, most = state.lookahead_affordable, state.lookahead_most
    # Both verdicts read the depth and the gauge as just corrected.
    held_by_money = (
        affordable
        if standing is not None
        and affordable is not None
        and in_flight + affordable < min(depth, allowed)
        else None
    )
    pick_max = max(1, min(ceiling, most)) if most > 0 else ceiling
    auto = standing is not None and standing.lookahead_auto
    pressable = ceiling > 1 and run.admission.lookahead_refusal == ""
    return SampleLookahead(
        depth=depth,
        auto=auto,
        ceiling=ceiling,
        in_flight=in_flight,
        allowed=allowed,
        most=most,
        scoring_now=in_flight > 0 or allowed > 0,
        pick_max=pick_max,
        affordable=held_by_money,
        cell_reserve_usd=state.cell_reserve_usd,
        discards=state.sample_lookahead_discards,
        auto_refusal="one_at_a_time"
        if ceiling <= 1
        else "settled"
        if RUN_PHASE_INFO[run.run_phase].settled
        else None,
        depths=[
            LookaheadDepth(
                cells=n,
                fill="full" if n <= in_flight else "part" if n <= allowed else None,
                pickable=pressable and n <= pick_max,
            )
            for n in range(1, ceiling + 1)
        ],
    )


def _serve(
    state: LiveDashboardState,
    *,
    hop: CycleHop,
    run: RunState,
    meter: CeilingMeter | None,
    standing: _Standing | None,
) -> ServedDashboard:
    """*standing* is ``None`` for a replay: the moment it folded, and nothing armed since."""
    banked: dict[str, Any] = {name: getattr(state, name) for name in DashboardFacts.model_fields}
    in_flight, allowed = state.in_flight, state.lookahead_allowed
    # A hold is read against the clock: a past moment has none, and a dead producer holds nothing.
    banked["backpressure"] = None
    if standing is not None:
        banked["run_limits"] = standing.run_limits
        if not run.producer.appending:
            # A killed run never publishes its closing zero, so it would go on reporting calls out.
            banked["waiting_on"] = None
            in_flight = allowed = 0
        if standing.orphaned:
            banked["verification"] = [
                m.orphaned() if m.state == "running" else m for m in state.verification
            ]
        elif (held := state.backpressure) is not None:
            banked["backpressure"] = ServedBackpressure.at(held, standing.at)
    verify = None if standing is None else standing.verify
    rounds = served_rounds(
        state.rounds,
        {} if verify is None else {key: read for key, (_, read) in verify.graded.items()},
        state.level_series,
    )
    formula = state.composite_fitness_formula
    dials = None if formula is None else anchored_criterion_dials(formula)
    live = standing is not None and run.producer.appending
    line_round = next((r for r in reversed(rounds) if overlap_line(r.overlap)), None)
    campaign_metered = None if standing is None else standing.campaign_metered
    axis = _round_axis(state, live=live)
    return ServedDashboard(
        **banked | {"rounds": rounds},
        campaign_id=hop.campaign_id,
        cycle_id=hop.cycle_id,
        run_phase=run.run_phase,
        producer=run.producer,
        admission=run.admission,
        pause=run.pause,
        overlap_line_round=None if line_round is None else line_round.round,
        overlap_line_n=None if line_round is None else len(line_round.overlap.positions),
        composite_fitness_weights=None
        if dials is None
        else {name: d.weight for name, d in dials.items()},
        composite_fitness_anchors=None
        if dials is None
        else {name: d.anchor for name, d in dials.items() if d.anchor is not None},
        lookahead=_lookahead(
            state, run=run, standing=standing, in_flight=in_flight, allowed=allowed
        ),
        spend_metered=None if meter is None else MeteredSpend.of(state.spend, meter),
        spend_metered_by_round=None
        if meter is None
        else {
            key: MeteredSpend.of(spent, meter)
            for key, spent in sorted(state.spend_by_round.items(), key=lambda kv: int(kv[0]))
        },
        campaign_metered_usd=None if campaign_metered is None else campaign_metered.usd_amount,
        campaign_metered_tokens=None if campaign_metered is None else campaign_metered.token_amount,
        allotment=None if standing is None else standing.allotment,
        fork_remainder=None if standing is None else standing.fork_remainder,
        observed=_observed(state, live=live),
        walk=_walk(state, hop, live=live),
        round_axis=axis,
        said=run_said(
            state.error,
            run.run_phase,
            rounds[-1].health if rounds else None,
            producer_row(run.producer),
        ),
    )


def served_dashboard(
    stores: Stores, hop: CycleHop, *, at: int | None = None
) -> ServedDashboard | WarmingDashboard:
    """``at`` replays to that ledger offset: live ``run_phase`` and ``producer``, nothing armed."""
    cycle_path = cycle_dir_for(stores.base_dir, hop)
    run = derive_run_state(cycle_path)
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    meter = None if campaign is None else ceiling_meter(campaign.declaration.purpose)

    state = fold_at(Cut(cycle=CycleDir(cycle_path), hop=hop, offset=at))
    if state is None:
        return WarmingDashboard(
            campaign_id=hop.campaign_id,
            cycle_id=hop.cycle_id,
            run_phase=run.run_phase,
            producer=run.producer,
            admission=run.admission,
            pause=run.pause,
        )
    if at is not None:
        return _serve(state, hop=hop, run=run, meter=meter, standing=None)
    config = None if campaign is None else cycle_config(stores, campaign, hop).config
    limits = _binding(stores, hop, config, run, state.run_limits)
    armed = standing_controls(cycle_path)
    return _serve(
        state,
        hop=hop,
        run=run,
        meter=meter,
        standing=_Standing(
            run_limits=limits,
            allotment=_allotment(stores, hop, run),
            lookahead=requested_lookahead(armed),
            lookahead_auto=armed.lookahead is not None and armed.lookahead.auto,
            verify=scan_ledger_verify(CycleLayout(cycle_path).ledger),
            orphaned=any(m.state == "running" for m in state.verification)
            and not run.producer.attached
            and JobRegistry.attach(stores.jobs_dir).pass_on(stores.campaigns.workspace, hop)
            is None,
            fork_remainder=_remainder(stores, hop, limits),
            campaign_metered=None
            if meter is None
            else stores.campaigns.metered_spend(hop.campaign_id, meter),
            at=time.time(),
        ),
    )
