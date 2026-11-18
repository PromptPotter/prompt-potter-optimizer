from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Literal, NamedTuple

from pydantic import Field

from promptpotter.application.jobs.launcher.admission import admitted
from promptpotter.application.jobs.quota import next_launch_budget
from promptpotter.application.pipeline_resolve import cycle_config
from promptpotter.domain.backend import ServedBackpressure
from promptpotter.domain.campaign import ceiling_meter
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.command_kinds import UNARMED, LookaheadAbreast, LookaheadCells
from promptpotter.domain.cycle_paths import CycleAt, CycleDir, CycleHop
from promptpotter.domain.dashboard_rows import (
    ArmedCaps,
    CapReading,
    RoundHealth,
    RunLimits,
    SampleCoverage,
    ServedRound,
    served_rounds,
)
from promptpotter.domain.origin_panel import overlap_line
from promptpotter.domain.paired_reading import ArmName
from promptpotter.domain.phases import (
    LOOKAHEAD_NO_RUN,
    PRODUCER_STATE_INFO,
    RUN_PHASE_INFO,
    ProducerReading,
    RunAdmission,
    RunPhase,
    RunState,
    StopReading,
)
from promptpotter.domain.records.error import ErrorRecord
from promptpotter.domain.records.run_records import CycleShareRecord, ForkRemainder
from promptpotter.domain.round_health import HEALTH_GRADE_INFO
from promptpotter.domain.run_standing import closed_after_origin
from promptpotter.domain.said import Said, pinned
from promptpotter.domain.sample import sample_tag
from promptpotter.domain.scoring import anchored_criterion_dials
from promptpotter.domain.spend import (
    CAP_AXIS_INFO,
    MONEY_AXES,
    NO_OVERSHOOT,
    Budget,
    CapAxis,
    CeilingMeter,
    MeteredSpend,
    Overshoot,
    SpendCount,
    metered_line,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.llm.spend_book import cycle_count
from promptpotter.infrastructure.projections.live_dashboard import (
    DashboardFacts,
    LiveDashboardState,
    WaitingOn,
    fold_at,
)
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    VerifyLedger,
    scan_ledger_verify,
)
from promptpotter.infrastructure.store.command_log import HeldLookahead, effective_lookahead
from promptpotter.infrastructure.store.layout import CycleLayout, cycle_dir_for
from promptpotter.infrastructure.store.read_model import money_on
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.clock import duration_words, epoch_seconds
from promptpotter.shared.worded import Worded


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
    stop: StopReading | None


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


LookaheadShut = Literal["one_at_a_time", "settled", "followed"]
# Why no deeper press is admitted now, the switch's reasons first.
LookaheadShallow = Literal[LookaheadShut, "no_run", "past_money", "past_most"]
# `SampleLookahead` worded; `{name}` is the field of that name.
LookaheadWord = Literal[
    LookaheadShallow,
    "no_further_cell",
    "more_affordable",
    "holds_every_round",
    "holds_this_round",
    "depth_one",
    "depth_many",
    "depth_max",
    "depth_max_hint",
    "abreast_one",
    "abreast_one_hint",
    "abreast_many",
    "abreast_all",
    "abreast_all_hint",
    "axes",
    "axes_one",
    "axes_all",
    "max_speed",
    "max_speed_hint",
    "flight",
    "scope_every_round",
    "scope_this_round",
]
LOOKAHEAD_WORDS: Worded[LookaheadWord, str] = Worded(
    {
        "one_at_a_time": "This backend runs one sample at a time — a single call has no latency to "
        "overlap.",
        "settled": "This cycle has settled: no walk is left to hold calls ahead for.",
        "followed": "This run follows its enclosing run's Max speed; switch it off there.",
        "no_run": LOOKAHEAD_NO_RUN,
        "past_money": "The spend limits afford {affordable} more call(s) out.",
        "past_most": "The stop rules allow {pick_max} out at most.",
        "flight": "{in_flight} of {most} out · {axes} {scope} · {discards} discarded so far.",
        "scope_every_round": "every round",
        "scope_this_round": "this round",
        "no_further_cell": "holds no further cell",
        "more_affordable": "more affordable",
        "holds_every_round": "Every round's scoring runs with these calls in flight, up to "
        "{pick_max} per candidate, until switched off. A cut discards at most one call. The "
        "measurement is unchanged and the cycle is NOT marked babysat.",
        "holds_this_round": "Runs this round's scoring with these calls in flight, up to "
        "{pick_max} per candidate, then resets to one at a time. A cut discards at most one call. "
        "The measurement is unchanged and the cycle is NOT marked babysat.",
        "depth_one": "One at a time",
        "depth_many": "Hold {cells} in flight per candidate",
        "depth_max": "Max · {ceiling}",
        "depth_max_hint": "Hold as many in flight per candidate as the backend admits: {ceiling} "
        "now, and more if its ceiling rises.",
        "abreast_one": "One after another",
        "abreast_one_hint": "Only the deciding candidate sends ahead; what it sent stays out.",
        "abreast_many": "{abreast} candidates send ahead at once",
        "abreast_all": "All",
        "abreast_all_hint": "Every candidate still walking sends ahead at once.",
        "axes": "depth {held} per candidate · {abreast} candidates send ahead",
        "axes_one": "depth {held} per candidate · one candidate sends ahead",
        "axes_all": "depth {held} per candidate · every candidate sends ahead",
        "max_speed": "Max speed",
        "max_speed_hint": "Both rows at their right end: as many calls per candidate as the "
        "backend admits, every candidate at once. The backend's ceiling and the spend limits "
        "still bound the calls out.",
    }
)


def lookahead_axes(held: int | str, abreast: LookaheadAbreast) -> str:
    """Both axes in one phrase, the readout's and the panel's."""
    word: LookaheadWord = "axes_one" if abreast == 1 else "axes_all" if abreast == "all" else "axes"
    return LOOKAHEAD_WORDS[word].format(held=held, abreast=abreast)


class LookaheadDepth(StrictModel):
    """One depth the look-ahead control offers: that many calls held in flight per candidate."""

    cells: LookaheadCells
    fill: Literal["full", "part"] | None = Field(
        description="`full` where that many calls are out now, `part` where the gauge allows that "
        "many; null past both."
    )
    pickable: bool = Field(
        description="Whether a press of this depth is admitted now: the control applies, the run "
        "admits `set-sample-lookahead` and the next walk can take that many."
    )


class LookaheadAbreastStep(StrictModel):
    """One count of candidates sending at once the look-ahead control offers."""

    abreast: LookaheadAbreast
    pickable: bool = Field(
        description="Whether a press of this count is admitted now: the control applies and the "
        "run admits `set-sample-lookahead`."
    )


class SampleLookahead(StrictModel):
    """The sample look-ahead: the depth per candidate and the candidates at once in force, the
    gauge of calls held ahead, and what its control offers. A replay takes no armed control, so it
    reads one at a time and nothing armed."""

    depth: int = Field(
        description="The depth IN FORCE — how many cells each candidate holds in flight: the "
        "armed request bounded by `ceiling`."
    )
    pressed: LookaheadCells = Field(
        description="The depth the control shows pressed: `max` where the ceiling is armed by "
        "name, else `depth`."
    )
    abreast: LookaheadAbreast = Field(
        description="How many candidates send ahead at once, counted from the one deciding: a "
        "count, or `all`."
    )
    max_speed: bool = Field(
        description="Both axes at their right end, `max` per candidate and `all` candidates: the "
        "Max speed switch, which no press stores apart from them."
    )
    auto: bool = Field(
        description="Whether that press outlives its round — the operator's auto-arm."
    )
    ceiling: int = Field(
        description="The backend's `max_cells_in_flight`; 1 says the control does not apply."
    )
    in_flight: int = Field(
        description="Calls out now on the backend, every run's in this process; 0 once no "
        "producer is appending."
    )
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
    auto_refusal: LookaheadShut | None = Field(
        description="Why the auto-arm switch is shut: `one_at_a_time` where holding calls ahead "
        "does not apply to this backend at all, `settled` once the cycle has, `followed` where "
        "an inner run follows its enclosing run's Max speed. A mode, not a press, so a paused run "
        "takes it for its relaunch; null where offered."
    )
    depths: list[LookaheadDepth] = Field(
        description="Each depth the control draws, 1 to `ceiling`, then `max` where `ceiling` "
        "exceeds 1."
    )
    abreasts: list[LookaheadAbreastStep] = Field(
        description="Each count of candidates at once the control draws, 1 to `ceiling`, then "
        "`all` where `ceiling` exceeds 1."
    )
    shallow: LookaheadShallow | None = Field(
        description="Why no deeper press than `depth` is admitted now (`LOOKAHEAD_WORDS`): the "
        "backend's ceiling, the cycle settled, an enclosing run's Max speed, no run to hold calls "
        "for, money, or the stop rules; null where a deeper one is."
    )
    flight: str = Field(
        description="The look-ahead now in one line: calls out, both axes in force and their "
        "scope, the calls cut so far."
    )

    def lines(self) -> list[str]:
        """The panel's rows as text, for a door that prints them: one wording, `LOOKAHEAD_WORDS`."""
        if self.shallow is None:
            return [self.flight]
        return [self.flight, LOOKAHEAD_WORDS[self.shallow].format_map(self.model_dump())]


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
    unfiled: int | None = Field(
        description="`current_round` while no round file holds it (its arms are its candidates)."
    )


def _round_axis(state: LiveDashboardState, *, live: bool) -> RoundAxis:
    completed = [r.round for r in state.rounds if r.candidates]
    current = state.current_round.round
    in_flight = current if live and not state.round_closed() else None
    return RoundAxis(
        completed=completed,
        live=in_flight,
        position=in_flight if in_flight is not None else completed[-1] if completed else None,
        unfiled=None if any(r.round == current for r in state.rounds) else current,
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
    rows = [] if state.round_closed() else current.candidates
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
    sample_coverage: SampleCoverage
    run_limits: RunLimits = Field(
        description="What binds the cycle (`CampaignStore.run_limits`): its launch's limits, its "
        "standing budget and its campaign's ceiling laid over; a next launch's budget where none runs."
    )
    campaign_id: str
    cycle_id: str
    backpressure: ServedBackpressure | None

    run_phase: RunPhase = Field(
        description="The cycle's phase, derived at the read (`derive_run_state`) — never the "
        "runner's own `declared_phase`, which says `running` forever after a kill."
    )
    producer: ProducerReading = Field(description="The cycle's producer, derived with the phase.")
    admission: RunAdmission = Field(
        description="The verb the run control offers now, its campaign's refusal joined "
        "(`admission.py::admitted`), or the sentence where it offers none."
    )
    skip_refusal: str = Field(
        description="Why `skip-searchpoint` is refused, off the run alone (`RunState`), which no "
        "campaign moves; empty where it is admitted."
    )
    stop: StopReading | None = Field(
        description="Why the cycle reads `paused` or `terminal` — what caused it, the stop it was "
        "left on and what said so (`RunState.stop`); null in every other phase."
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
    spend_metered: MeteredSpend = Field(
        description="The cycle's spend under the campaign's ceiling meter, a manifest fact no "
        "ledger record carries."
    )
    spend_metered_by_round: dict[str, MeteredSpend] = Field(
        description="`spend_metered` per round, in round order."
    )
    metered_line: str = Field(
        description="This cycle's spend in the campaign's meter, capped or not, a replay included "
        "— `domain/spend.py::metered_line`, the CLI's stop line."
    )
    cap_lines: list[str] = Field(
        description="Beside `metered_line`: the rows of `caps` a money cap bounds, its book's "
        "count by part; empty where nothing is capped and on a replay."
    )
    caps: ArmedCaps = Field(
        description="Every cap of `run_limits.budget` read against its own book "
        "(`armed_caps`); on a replay, the caps it stood under and no book read."
    )
    overshot: Overshoot | None = Field(
        description="The cycle's calls billed past their hold (`run_limits.overshoot`), off the "
        "account's charge book; null on a replay, which reads none."
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
    held_call: Said | None = Field(
        description="The call holding the walk and for how long (`held_call`); null until a call "
        "has run past `CALL_HOLDS_AFTER_S`, and where its cell is unknown."
    )
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


def held_call(producer: ProducerReading, waiting: WaitingOn | None) -> Said | None:
    """THE holding predicate: the open call has run long (`call_holds`) AND its cell is known."""
    if not producer.call_holds or producer.open_for_s is None or waiting is None:
        return None
    held = duration_words(producer.open_for_s)
    holder = "the pass" if waiting.label is None else waiting.label
    # The label is the fact that moves, short enough for a phone row; the holder rides `sub`.
    return Said(
        key="held_call",
        tone="attention",
        label=f"held {held}",
        sub=holder,
        detail=f"Waiting {held} on {holder}, sample {sample_tag(waiting.position)}. Calls are taken in "
        "walk order, so this one holds every call behind it.",
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


class _Present(NamedTuple):
    """Only the PRESENT holds these; a replay takes none — a past moment never had them."""

    allotment: Budget | None
    lookahead: HeldLookahead
    verify: VerifyLedger
    # A pass still running on the ledger that no attached run and no paid pass's job scores.
    orphaned: bool
    fork_remainder: ForkRemainder
    caps: ArmedCaps
    overshot: Overshoot
    at: float


def binding_run_limits(
    stores: Stores, hop: CycleHop, config: CampaignConfig | None, run: RunState
) -> RunLimits | None:
    """``None`` on a cycle that has declared no wiring yet."""
    limits = stores.campaigns.run_limits(hop)
    return None if limits is None else _binding(stores, hop, config, run, limits)


def _binding(
    stores: Stores, hop: CycleHop, config: CampaignConfig | None, run: RunState, limits: RunLimits
) -> RunLimits:
    """A next launch keeps the run's optimizer limits under the budget it would be admitted at."""
    if config is not None and not run.producer.appending:
        return limits.model_copy(
            update={"budget": next_launch_budget(config, stores=stores, hop=hop)}
        )
    return limits


def _allotment(stores: Stores, hop: CycleHop, run: RunState) -> Budget | None:
    # The share the book opened over, or the reshare that last moved it; no run, no share.
    if not run.producer.appending:
        return None
    held = stores.campaigns.last(hop, CycleShareRecord)
    return None if held is None else held.allotment


def _caps(
    limits: RunLimits,
    meter: CeilingMeter,
    *,
    asked: Budget,
    used: Mapping[CapAxis, float | None],
    count: SpendCount | None,
) -> ArmedCaps:
    return ArmedCaps.of(
        [
            CapReading.of(
                axis,
                asked=getattr(limits.budget.over(asked), axis),
                armed=getattr(limits.budget, axis),
                used=used.get(axis),
                meter=meter if axis in MONEY_AXES else None,
                count=count if axis in MONEY_AXES else None,
            )
            for axis in CAP_AXIS_INFO
        ]
    )


def armed_caps(
    stores: Stores,
    hop: CycleHop,
    *,
    limits: RunLimits,
    run: RunState,
    meter: CeilingMeter,
    campaign_metered: Budget,
    rounds_closed: int,
) -> ArmedCaps:
    """THE caps as they bind now, each against its own book: the spend book's count, this cycle's
    rounds closed after the origin, the clock of the launch in flight."""
    held = stores.campaigns.holder(hop) if run.producer.appending else None
    job = None if held is None else held.run
    started = None if job is None else epoch_seconds(job.started_at)
    count = cycle_count(
        stores.charges,
        hold_id=None if job is None else job.job_id,
        metered_on=stores.campaigns.ledger(hop),
        meters=meter,
    )
    return _caps(
        limits,
        meter,
        asked=stores.campaigns.read_campaign_ceiling(hop).asked,
        used={
            "rounds": rounds_closed,
            "wall_clock_s": None if started is None else max(0.0, time.time() - started),
        },
        count=SpendCount(
            billed=campaign_metered,
            unreported=count.unreported,
            set_aside=count.set_aside,
            out=count.out,
        ),
    )


def current_caps(stores: Stores, hop: CycleHop) -> ArmedCaps:
    """`armed_caps` read whole off the stores: what `set-limits` answers once it applied."""
    campaign = stores.campaigns.campaign(hop.campaign_id)
    run = stores.run_state(hop)
    config = cycle_config(stores, campaign, hop).config
    # Unwired yet, it stands under what its operator armed, as its first launch will.
    wired = stores.campaigns.run_limits(hop) or RunLimits(budget=stores.campaigns.armed_budget(hop))
    limits = _binding(stores, hop, config, run, wired)
    meter = ceiling_meter(campaign.declaration.purpose)
    return armed_caps(
        stores,
        hop,
        limits=limits,
        run=run,
        meter=meter,
        campaign_metered=stores.campaigns.metered_spend(hop.campaign_id, meter),
        rounds_closed=_remainder(stores, hop, limits).rounds_closed,
    )


def _remainder(stores: Stores, hop: CycleHop, limits: RunLimits | None) -> ForkRemainder:
    return ForkRemainder.of(
        rounds_closed=closed_after_origin(
            list(stores.campaigns.standing_rounds(hop, moment=None).rounds)
        ),
        rounds=None if limits is None else limits.budget.rounds,
    )


def fork_remainder(stores: Stores, hop: CycleHop) -> ForkRemainder:
    config = cycle_config(stores, stores.campaigns.campaign(hop.campaign_id), hop).config
    return _remainder(stores, hop, binding_run_limits(stores, hop, config, stores.run_state(hop)))


def _lookahead(
    state: LiveDashboardState,
    *,
    run: RunState,
    standing: _Present | None,
    in_flight: int,
    allowed: int,
) -> SampleLookahead:
    ceiling = state.max_cells_in_flight
    held = HeldLookahead(UNARMED) if standing is None else standing.lookahead
    depth, asked_abreast = effective_lookahead(held.press, ceiling)
    pressed: LookaheadCells = "max" if ceiling > 1 and held.press.cells == "max" else depth
    abreast: LookaheadAbreast = asked_abreast if ceiling > 1 else 1
    # The most calls both axes together hold out, the ceiling bounding them.
    window = min(ceiling, depth * (ceiling if abreast == "all" else abreast))
    affordable, most = state.lookahead_affordable, state.lookahead_most
    # Both verdicts read the depth and the gauge as just corrected.
    held_by_money = (
        affordable
        if standing is not None
        and affordable is not None
        and in_flight + affordable < min(window, allowed)
        else None
    )
    pick_max = max(1, min(ceiling, most)) if most > 0 else ceiling
    auto = held.press.auto
    auto_refusal: LookaheadShut | None = (
        "one_at_a_time"
        if ceiling <= 1
        else "settled"
        if RUN_PHASE_INFO[run.run_phase].settled
        else "followed"
        if held.followed
        else None
    )
    pressable = ceiling > 1 and run.lookahead_refusal == "" and not held.followed
    shallow: LookaheadShallow | None = (
        auto_refusal
        or ("no_run" if not pressable else None)
        or ("past_money" if held_by_money is not None else None)
        or ("past_most" if depth >= pick_max and pick_max < ceiling else None)
    )
    shown = LOOKAHEAD_WORDS["depth_max"].format(ceiling=ceiling) if pressed == "max" else depth
    offered: list[LookaheadAbreast] = list(range(1, ceiling + 1))
    if ceiling > 1:
        offered.append("all")
    return SampleLookahead(
        depth=depth,
        pressed=pressed,
        abreast=abreast,
        max_speed=pressed == "max" and abreast == "all",
        auto=auto,
        ceiling=ceiling,
        in_flight=in_flight,
        allowed=allowed,
        most=most,
        scoring_now=in_flight > 0 or allowed > 0,
        pick_max=pick_max,
        affordable=held_by_money,
        cell_reserve_usd=state.cell_reserve_usd,
        auto_refusal=auto_refusal,
        depths=[
            LookaheadDepth(
                cells=n,
                fill="full" if n <= in_flight else "part" if n <= allowed else None,
                pickable=pressable and n <= pick_max,
            )
            for n in range(1, ceiling + 1)
        ]
        + ([LookaheadDepth(cells="max", fill=None, pickable=pressable)] if ceiling > 1 else []),
        abreasts=[LookaheadAbreastStep(abreast=n, pickable=pressable) for n in offered],
        shallow=shallow,
        flight=LOOKAHEAD_WORDS["flight"].format(
            in_flight=in_flight,
            most=most,
            axes=lookahead_axes(shown, abreast),
            scope=LOOKAHEAD_WORDS["scope_every_round" if auto else "scope_this_round"],
            discards=state.sample_lookahead_discards,
        ),
    )


def _serve(
    state: LiveDashboardState,
    *,
    hop: CycleHop,
    run: RunState,
    admission: RunAdmission,
    meter: CeilingMeter,
    run_limits: RunLimits,
    standing: _Present | None,
) -> ServedDashboard:
    """*standing* is ``None`` for a replay: the moment it folded, and nothing armed since."""
    banked: dict[str, Any] = {name: getattr(state, name) for name in DashboardFacts.model_fields}
    in_flight, allowed = state.in_flight, state.lookahead_allowed
    # A hold is read against the clock: a past moment has none, and a dead producer holds nothing.
    banked["backpressure"] = None
    if standing is not None:
        if not run.producer.appending:
            # A killed run never publishes its closing zero, so it would go on reporting calls out.
            in_flight = allowed = 0
        if standing.orphaned:
            banked["verification"] = state.orphaned()
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
    axis = _round_axis(state, live=live)
    return ServedDashboard(
        **banked | {"rounds": rounds},
        sample_coverage=SampleCoverage.of(rounds),
        run_limits=run_limits,
        campaign_id=hop.campaign_id,
        cycle_id=hop.cycle_id,
        run_phase=run.run_phase,
        producer=run.producer,
        admission=admission,
        skip_refusal=run.skip_refusal,
        stop=run.stop,
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
        spend_metered=MeteredSpend.of(state.spend, meter),
        spend_metered_by_round={
            key: MeteredSpend.of(spent, meter)
            for key, spent in sorted(state.spend_by_round.items(), key=lambda kv: int(kv[0]))
        },
        metered_line=metered_line(
            meter,
            metered=state.spend.metered(meter),
            cap=Budget() if standing is None else run_limits.budget,
            share=Budget() if standing is None else standing.allotment or Budget(),
        ),
        cap_lines=[]
        if standing is None
        else [
            line
            for cap in standing.caps.caps
            if cap.count is not None and cap.armed is not None
            for line in cap.lines()
        ],
        caps=_caps(run_limits, meter, asked=Budget(), used={}, count=None)
        if standing is None
        else standing.caps,
        overshot=None if standing is None else standing.overshot,
        fork_remainder=None if standing is None else standing.fork_remainder,
        observed=_observed(state, live=live),
        walk=_walk(state, hop, live=live),
        round_axis=axis,
        held_call=held_call(run.producer, state.waiting_on if live else None),
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
    run = stores.run_state(hop)
    campaign = stores.campaigns.campaign(hop.campaign_id)
    meter = ceiling_meter(campaign.declaration.purpose)
    # The read's own join, never the checkout's: the campaign's refusal moves no ledger.
    admission = admitted(stores, campaign, run)

    state = fold_at(CycleAt(cycle=CycleDir(cycle_path), hop=hop, offset=at))
    if state is None:
        return WarmingDashboard(
            campaign_id=hop.campaign_id,
            cycle_id=hop.cycle_id,
            run_phase=run.run_phase,
            producer=run.producer,
            admission=admission,
            stop=run.stop,
        )
    folded = stores.campaigns.run_limits(hop, at=at)
    assert folded is not None, "a folded state stands on its wiring"
    if at is not None:
        return _serve(
            state,
            hop=hop,
            run=run,
            admission=admission,
            meter=meter,
            run_limits=folded,
            standing=None,
        )
    config = cycle_config(stores, campaign, hop).config
    limits = _binding(stores, hop, config, run, folded)
    remainder = _remainder(stores, hop, limits)
    campaign_metered = stores.campaigns.metered_spend(hop.campaign_id, meter)
    return _serve(
        state,
        hop=hop,
        run=run,
        admission=admission,
        meter=meter,
        run_limits=limits,
        standing=_Present(
            allotment=_allotment(stores, hop, run),
            lookahead=stores.commands.book().lookahead(stores.subject(hop)),
            verify=scan_ledger_verify(CycleLayout(cycle_path).ledger),
            orphaned=any(mark.state == "running" for mark in state.verification)
            and stores.campaigns.holder(hop) is None,
            fork_remainder=remainder,
            caps=armed_caps(
                stores,
                hop,
                limits=limits,
                run=run,
                meter=meter,
                campaign_metered=campaign_metered,
                rounds_closed=remainder.rounds_closed,
            ),
            overshot=money_on(stores.charges.path).overshot.get(
                stores.charges.held_on(CycleLayout(cycle_path).ledger), NO_OVERSHOOT
            ),
            at=time.time(),
        ),
    )
