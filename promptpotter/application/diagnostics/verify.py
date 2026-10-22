from __future__ import annotations

import logging
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.difficulty import (
    judging_ruler,
    ruler_clock,
    session_ruler_scope,
)
from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.initialization.loop_start import (
    arm_diagnostic_scoring,
    diagnostic_pass,
    diagnostic_trace,
)
from promptpotter.application.initialization.wiring import bind_cycle_session
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.run_callbacks import RunCallbacks
from promptpotter.application.run_phase_control import RunControl
from promptpotter.application.runner.bench import own_level
from promptpotter.application.scoring.closed_rounds import cycle_instrument, walked_rows
from promptpotter.application.scoring.paired import MemberRows, grade_measurands, read_pair
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.domain.bench import BandedValue
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.paired_reading import (
    ROUND_LIFT_SPEC,
    ArmKey,
    ArmName,
    CellSetName,
    MemberAddress,
)
from promptpotter.domain.phase_views import VerifyEnterView, VerifyGradedView
from promptpotter.domain.phases import CampaignPhase
from promptpotter.domain.results import (
    BankedSearchPointError,
    Concluded,
    VerifyHeldAbsent,
    VerifyPass,
    VerifyReading,
    VerifyStrategy,
    arm_name,
    individual_cells,
)
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.llm.telemetry import active_cycle_ledger
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_ledger_verify,
    scan_ledger_walks,
)
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.shared.errors import DiagnosticError
from promptpotter.shared.measurement_context import MeasurementRole, RoleScope

if TYPE_CHECKING:
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import CellSheet, Scorer, WalkedCell
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)


_VERIFY_LIFT_CAP = 5


def derive_verify_samples(
    *, round_cell_budget: int, rounds_unverified: int, unmeasured: int
) -> int:
    lift = min(max(rounds_unverified, 1), _VERIFY_LIFT_CAP)
    return max(0, min(round_cell_budget * lift, unmeasured))


def rounds_since_verified(verified_rounds: Iterable[int], *, round_num: int) -> int:
    """Over the cycle's OWN passes: a fork inherits its parent's measurements, not its assurance."""
    return round_num - max(verified_rounds, default=0)


@dataclass(frozen=True)
class VerifyOutcome:
    """Both optionals are ``None`` where every sample was already measured."""

    dataset_name: str
    already_measured: int
    verify_pass: VerifyPass | None = None
    reading: VerifyReading | None = None


def pick_fresh(
    unmeasured: Sequence[Sample],
    n: int,
    *,
    strategy: VerifyStrategy,
    rng: random.Random,
    delta: Mapping[str, float],
) -> list[Sample]:
    if strategy == "random":
        return rng.sample(unmeasured, n)
    placed = sorted((s for s in unmeasured if s.key in delta), key=lambda s: (-delta[s.key], s.id))
    unplaced = [s for s in unmeasured if s.key not in delta]
    return [*placed, *rng.sample(unplaced, len(unplaced))][:n]


def _held(
    strategy: VerifyStrategy, n_fresh: int, fresh: BandedValue | None, recorded: BandedValue | None
) -> tuple[bool | None, VerifyHeldAbsent | None]:
    if strategy != "random":
        return None, "hard_picks"
    if n_fresh < 2 or fresh is None or recorded is None or fresh.ci_hi is None:
        return None, "under_two_fresh"
    # The tolerance absorbs the float error of two means taken over different row counts.
    return fresh.ci_hi + 1e-9 >= recorded.value, None


def verify_member(
    hop: CycleHop,
    arm: ArmName,
    sheet: CellSheet,
    *,
    bought: int,
    instrument_id: str,
) -> MemberRows:
    return MemberRows(
        address=MemberAddress(
            path=(hop,),
            individual_id=arm.key,
            arm=arm,
            # A fold over every pass report scope may see, so no one pass names it.
            pass_role=None,
        ),
        sheet=sheet,
        bought=bought,
        cut=False,
        scope=RoleScope.REPORT,
        instrument_id=instrument_id,
        dataset_hash=None,
        cell_set_id=None,
    )


def verify_reading(
    banked: VerifyPass,
    *,
    fresh: CellSheet,
    recorded: CellSheet,
    measured: MemberRows,
    origin: MemberRows,
) -> VerifyReading:
    """Fresh beside recorded, never pooled: a pooled rate moves with how many cells each holds."""
    scorer_id = fresh.scorer_id
    fresh_level, recorded_level = own_level(fresh), own_level(recorded)

    def increment(a: BandedValue | None, b: BandedValue | None) -> float | None:
        return None if a is None or b is None else a.value - b.value

    held, held_absent = _held(
        banked.strategy, fresh_level.n, fresh_level.accuracy, recorded_level.accuracy
    )
    return VerifyReading(
        label=banked.arm.label,
        scorer_id=scorer_id,
        strategy=banked.strategy,
        fresh=fresh_level,
        recorded=recorded_level,
        accuracy_increment=increment(fresh_level.accuracy, recorded_level.accuracy),
        composite_increment=increment(fresh_level.composite, recorded_level.composite),
        vs_origin=read_pair(
            a=origin,
            b=measured,
            cell_set=CellSetName.MEASURED_BY_BOTH,
            cells=None,
            masked=False,
            dataset_hash=None,
            measurands=grade_measurands(scorer_id),
            spec=ROUND_LIFT_SPEC,
            scope=RoleScope.REPORT,
            instrument_id=measured.instrument_id,
        ),
        held=held,
        held_absent=held_absent,
    )


def read_verify(stores: Stores, hop: CycleHop, banked: VerifyPass, scorer: Scorer) -> VerifyReading:
    chain = ledger_chain(CycleDir(stores.campaigns.cycle_dir(hop)), moment=None)
    # A pass is read before it is banked, so it joins the walks whether or not the ledger holds it.
    walks = [*scan_ledger_walks(chain), banked.walk()]

    def rows(cells: list[WalkedCell]) -> CellSheet:
        return walked_rows(stores, cells, scorer)

    def folded(individual_id: str, scope: RoleScope) -> CellSheet:
        return rows(individual_cells(walks, individual_id, scope)).standing()

    fresh = rows(list(banked.walk().cells))
    if not fresh:
        raise DiagnosticError(
            f"the verify pass of {banked.arm.label} holds no answer in the archive."
        )
    standing = stores.campaigns.standing_rounds(hop, moment=None).rounds
    if 0 not in standing:
        raise DiagnosticError(
            f"{hop.campaign_id}/{hop.cycle_id} holds no closed round 0, so {banked.arm.label} has "
            "no origin to be read over."
        )
    instrument_id = cycle_instrument(stores.campaigns.read_bank_partition(hop), standing)
    assert instrument_id is not None, "a cycle that closed round 0 drew its bank"
    c0 = standing[0].close.origin
    origin = ArmName(key=ArmKey(c0.individual.id), round=0, label=c0.label)
    measured = banked.arm.key
    return verify_reading(
        banked,
        fresh=fresh,
        recorded=folded(measured, RoleScope.DECISION),
        measured=verify_member(
            hop,
            banked.arm if origin.key != measured else origin,
            folded(measured, RoleScope.REPORT),
            bought=sum(1 for *_, replayed in banked.cells if not replayed),
            instrument_id=instrument_id,
        ),
        origin=verify_member(
            hop,
            origin,
            folded(origin.key, RoleScope.REPORT),
            bought=0,
            instrument_id=instrument_id,
        ),
    )


def _scored_candidate(stores: Stores, hop: CycleHop, candidate_id: str) -> tuple[int, Concluded]:
    # Newest first: a repair re-measures an arm in place without re-proposing it.
    for round_num, held in reversed(
        stores.campaigns.standing_rounds(hop, moment=None).rounds.items()
    ):
        arm = held.close.arms.get(ArmKey(candidate_id))
        if isinstance(arm, Concluded) and arm.role != MeasurementRole.PARENT:
            return round_num, arm
    raise DiagnosticError(
        f"no round of {hop.campaign_id}/{hop.cycle_id} scored candidate {candidate_id!r}."
    )


async def verify_candidate(
    *,
    stores: Stores,
    hop: CycleHop,
    candidate_id: str,
    samples: int | None,
    strategy: VerifyStrategy,
    seed: int | None,
) -> VerifyOutcome:
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    if campaign is None:
        raise DiagnosticError(f"campaign {hop.campaign_id!r} has no manifest on disk.")
    round_num, entry = _scored_candidate(stores, hop, candidate_id)
    arm = arm_name(ArmKey(candidate_id), entry, round_num)
    label = arm.label

    session, campaign_config = await bind_cycle_session(stores, campaign, hop)
    arm_diagnostic_scoring(session, campaign_config, source=RunSource.VERIFY)

    schema = session.pipeline_schema
    # Before a cell is bought: fresh cells of another searchpoint would read as this one's.
    jsp = entry.searchpoint(
        schema=schema,
        framing=campaign_framing(stores, campaign_config, session.dataset),
        demo=session.scoring.require_partition().demo,
    )
    sp_hash = jsp.sp_hash(schema)
    # Carries the prompt: keyed on config alone, candidates sharing a model read as one.
    predicate: dict[str, dict[str, Any]] = dict(schema.node_configs(jsp.pipeline_params))

    # By sample key under every bank: a re-cut bank re-measures none of the rows it kept.
    prior = stores.archive.measurements_for_config(predicate)
    measured_keys = {m.cell.sample_key for m in prior} & session.bank.slot_of.keys()
    # Search split only: `verify_on_saturation` runs inside the loop, which must decide on no bench row.
    search = session.scoring.require_partition().search
    unmeasured = [s for s in search if s.key not in measured_keys]
    round_cells = select_optimizer(campaign_config.optimization).round_cells(len(search))
    if not unmeasured:
        return VerifyOutcome(
            dataset_name=campaign.dataset_name, already_measured=len(measured_keys)
        )

    ledger_path = CycleLayout(stores.campaigns.cycle_dir(hop)).ledger
    budget = derive_verify_samples(
        round_cell_budget=round_cells,
        rounds_unverified=rounds_since_verified(
            (banked.arm.round for banked, _ in scan_ledger_verify(ledger_path).graded.values()),
            round_num=round_num,
        ),
        unmeasured=len(unmeasured),
    )
    if samples is not None and samples > budget:
        raise DiagnosticError(
            f"--samples {samples} is above this candidate's verify budget of {budget} "
            f"({round_cells} cells per candidate per round, lifted by the "
            f"rounds run since the last verification, capped at {len(unmeasured)} unmeasured). "
            f"Pass {budget} or fewer, or verify again after more rounds."
        )
    # The ruler the campaign's elections read now, less the candidate under verification.
    ruler = judging_ruler(
        stores.ruler,
        session_ruler_scope(session),
        ruler_clock(session),
        {jsp.sp_hash(schema)},
        objective_id=session.scoring.require_scorer().id,
        n_min=campaign_config.optimization.elimination_n_min,
        enable_2pl=campaign_config.optimization.enable_2pl_graduation,
    ).ruler
    picked = pick_fresh(
        unmeasured,
        budget if samples is None else samples,
        strategy=strategy,
        rng=random.Random(seed),
        delta={} if ruler is None else ruler.delta,
    )

    logger.info(
        "verify %s (%s/%s): scoring %d new sample(s); %d already in archive for this config",
        label,
        hop.campaign_id,
        hop.cycle_id,
        len(picked),
        len(measured_keys),
    )
    with diagnostic_trace(stores, hop):
        ledger = active_cycle_ledger()
        assert ledger is not None
        on_phase = RunCallbacks(ledger).on_phase
        on_phase(
            CampaignPhase.VERIFY,
            "enter",
            view=VerifyEnterView(arm=arm, rows=len(picked), strategy=strategy),
        )
        try:
            scored = await diagnostic_pass(
                score_search_point(
                    jsp,
                    picked,
                    session,
                    label=MeasurementRole.VERIFY,
                    measured=None,
                ),
            )
            banked = VerifyPass(
                arm=arm,
                sp_hash=sp_hash,
                cells=scored.cells,
                positions=[s.id for s in picked],
                strategy=strategy,
                seed=seed,
                scorer_id=session.scoring.require_scorer().id,
            )
            reading = read_verify(stores, hop, banked, session.scoring.require_scorer())
            if reading.fresh.accuracy is None:
                raise DiagnosticError(
                    f"the fresh cells of {label!r} produced no scoreable sample — there is "
                    "nothing to read beside the recorded measurement."
                )
            on_phase(
                CampaignPhase.VERIFY,
                "graded",
                view=VerifyGradedView(verify_pass=banked, reading=reading),
            )
        finally:
            on_phase(CampaignPhase.VERIFY, "exit")

    return VerifyOutcome(
        dataset_name=campaign.dataset_name,
        already_measured=len(measured_keys),
        verify_pass=banked,
        reading=reading,
    )


async def verify_on_saturation(
    *,
    stores: Stores,
    hop: CycleHop,
    round_num: int,
    accuracy: float | None,
    winner_id: str | None,
    control: RunControl,
) -> VerifyOutcome | None:
    if accuracy is None or accuracy < 1.0 or not winner_id:
        return None
    # Asked here: the loop consults its own ceiling only at the next round boundary, after this.
    if control.budget_tripped() is not None:
        return None
    ledger_path = CycleLayout(stores.campaigns.cycle_dir(hop)).ledger
    mine = [banked.arm.round for banked, _ in scan_ledger_verify(ledger_path).graded.values()]
    if mine and rounds_since_verified(mine, round_num=round_num) < _VERIFY_LIFT_CAP:
        return None
    try:
        return await verify_candidate(
            stores=stores,
            hop=hop,
            candidate_id=winner_id,
            samples=None,
            strategy="random",
            seed=None,
        )
    except (DiagnosticError, BankedSearchPointError) as exc:
        logger.info("verify skipped for %s: %s", winner_id, exc)
        return None
