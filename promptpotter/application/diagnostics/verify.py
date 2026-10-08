"""Re-score one campaign candidate — C0 included — on search cells it has never met. Not a cycle
or a fork: the pass is banked on the cycle's own ledger as facts (``VerifyPass``) and
``read_verify`` is the one reading of them. Its SPEND joins that ledger in the ``diagnostic``
bucket — inside every ceiling, banked apart."""

from __future__ import annotations

import logging
import random
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.initialization.loop_start import (
    arm_diagnostic_scoring,
    diagnostic_pass,
    diagnostic_trace,
)
from promptpotter.application.initialization.wiring import bind_cycle_session
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.run_observers import RunCallbacks
from promptpotter.application.run_phase_control import RunControl
from promptpotter.application.runner.bench import level_columns, paired_lift
from promptpotter.application.scoring.classification import scoreable_rows
from promptpotter.application.scoring.formula import rescore_results
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.application.scoring.selection import paired_fitness
from promptpotter.domain.bench import BandedValue
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.phases import CampaignPhase, emit_phase
from promptpotter.domain.results import (
    BankedSearchPointError,
    ScoredCandidate,
    VerifyPass,
    VerifyReading,
    VerifyStrategy,
)
from promptpotter.infrastructure.llm.telemetry import active_cycle_ledger
from promptpotter.infrastructure.store import archive_queries
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_verify
from promptpotter.infrastructure.store.layout import ROUND_GLOB, CycleLayout, round_number
from promptpotter.shared.errors import ConflictError

if TYPE_CHECKING:
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import CellScorer, QueryMeasurement
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)

__all__ = [
    "VerifyError",
    "derive_verify_samples",
    "pick_fresh",
    "read_verify",
    "rounds_since_verified",
    "verify_candidate",
    "verify_on_saturation",
    "verify_reading",
]


# A verify is a TEMPORARY LIFT of the per-candidate round budget, and the lift is how long the
# cycle has gone unchecked. The base keeps the bill on the scale the operator already set; the cap
# is what makes firing one automatically safe, so a 200-round campaign asks for five rounds' worth
# of cells and not 200. Five, because past a handful of rounds' cells the binding constraint stops
# being the noise and starts being the wallet.
_VERIFY_LIFT_CAP = 5


def derive_verify_samples(
    *, round_cell_budget: int, rounds_unverified: int, unmeasured: int
) -> int:
    """How many NEW cells one verify buys. See ``_VERIFY_LIFT_CAP`` above for the model."""
    lift = min(max(rounds_unverified, 1), _VERIFY_LIFT_CAP)
    return max(0, min(round_cell_budget * lift, unmeasured))


def rounds_since_verified(verified_rounds: Iterable[int], *, round_num: int) -> int:
    """Rounds this CYCLE has run since any of its candidates was last verified; never verified
    reads as ``round_num``. *verified_rounds* are the cycle's OWN passes, because a fork inherits
    its parent's measurements but not its assurance — the branch is a different search from the
    point it left."""
    return round_num - max(verified_rounds, default=0)


class VerifyError(ConflictError):
    """A resolved-state failure: campaign, round or candidate missing on disk. The CLI shell maps it to a clean exit — this
    module never raises ``SystemExit`` itself."""


@dataclass(frozen=True)
class VerifyOutcome:
    """Both ``None`` on the "every sample already measured" path. The verdict line is formatted by
    the CLI shell from this outcome, never here."""

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
    delta: Mapping[int, float],
) -> list[Sample]:
    """The cells a pass buys. ``hard`` takes the highest-δ cells the cycle's ruler carries, since
    a verdict is weakest where the cells are hardest; a cell the ruler never placed has no
    difficulty to rank on, so those follow in a seeded order."""
    if strategy == "random":
        return rng.sample(unmeasured, n)
    placed = sorted((s for s in unmeasured if s.id in delta), key=lambda s: (-delta[s.id], s.id))
    unplaced = [s for s in unmeasured if s.id not in delta]
    return [*placed, *rng.sample(unplaced, len(unplaced))][:n]


def _held(
    strategy: VerifyStrategy, n_fresh: int, fresh: BandedValue | None, recorded: BandedValue | None
) -> bool | None:
    if strategy != "random" or n_fresh < 2 or fresh is None or recorded is None:
        return None
    if fresh.ci_hi is None:
        return None
    # The tolerance absorbs the float error of two means taken over different row counts.
    return fresh.ci_hi + 1e-9 >= recorded.value


def verify_reading(
    banked: VerifyPass,
    *,
    scorer_id: str,
    fresh: list[QueryMeasurement],
    recorded: list[QueryMeasurement],
    origin: list[QueryMeasurement],
) -> VerifyReading:
    """*banked* read off rows already graded under ``scorer_id``: the fresh cells ALONE beside the
    round's own, never pooled — a pooled rate moves with how many cells each side holds — and the
    lift over the origin paired on every cell both scored."""
    fresh_level, recorded_level = level_columns(fresh), level_columns(recorded)
    n_fresh = len(scoreable_rows(fresh))

    def increment(a: BandedValue | None, b: BandedValue | None) -> float | None:
        return None if a is None or b is None else a.value - b.value

    mine = [*recorded, *fresh]
    shared, _ = paired_fitness(scoreable_rows(mine), scoreable_rows(origin), grade="fitness")
    is_origin = banked.round == 0
    return VerifyReading(
        label=banked.label,
        scorer_id=scorer_id,
        strategy=banked.strategy,
        n_fresh=n_fresh,
        fresh=fresh_level,
        n_recorded=len(scoreable_rows(recorded)),
        recorded=recorded_level,
        accuracy_increment=increment(fresh_level.accuracy, recorded_level.accuracy),
        composite_increment=increment(fresh_level.composite, recorded_level.composite),
        n_shared=0 if is_origin else len(shared),
        lift=paired_lift([] if is_origin else mine, origin),
        held=_held(banked.strategy, n_fresh, fresh_level.accuracy, recorded_level.accuracy),
    )


def _round_rows(
    stores: Stores, hop: CycleHop, round_num: int, candidate_id: str | None
) -> list[Any]:
    """One arm's rows off its round document; ``None`` names round 0's origin."""
    doc = stores.campaigns.load_round_file(hop, round_num)
    if doc is None:
        return []
    arm = doc.origin.candidate_id if candidate_id is None else candidate_id
    return [{**r} for r in doc.all_candidate_results[arm]]


def read_verify(
    stores: Stores, hop: CycleHop, banked: VerifyPass, scorer: CellScorer, *, scorer_id: str
) -> VerifyReading:
    """The reading, derived from the pass's archived rows and the round documents under *scorer*
    — for the run that graded it and any later reader alike, so a copy is only ever a cache."""
    run = archive_queries.load_run(stores, banked.run_id)
    if run is None:
        raise VerifyError(f"verify run {banked.run_id} of {banked.label} is not in the archive.")
    sent = set(banked.sample_ids)

    def graded(rows: list[Any]) -> list[QueryMeasurement]:
        return cast("list[QueryMeasurement]", rescore_results(rows, scorer))

    return verify_reading(
        banked,
        scorer_id=scorer_id,
        fresh=graded([{**r} for r in run["measurements"] if r.get("sample_id") in sent]),
        recorded=graded(_round_rows(stores, hop, banked.round, banked.candidate_id)),
        origin=graded(_round_rows(stores, hop, 0, None)),
    )


def _scored_candidate(
    stores: Stores, hop: CycleHop, candidate_id: str
) -> tuple[int, ScoredCandidate]:
    """The candidate's own score report, off the LAST round document carrying it — a repair
    re-measures a candidate in place without re-minting it, so the newest one stands. The round
    documents are durable; the proposal cache under ``.runtime/`` is not, and is never read."""
    rounds = CycleLayout(stores.campaigns.cycle_dir(hop)).rounds
    numbers = sorted(n for p in rounds.glob(ROUND_GLOB) if (n := round_number(p)) is not None)
    for round_num in reversed(numbers):
        doc = stores.campaigns.load_round_file(hop, round_num)
        if doc is None:
            continue
        for entry in doc.candidate_scores:
            if entry.candidate_id == candidate_id:
                return round_num, entry
    raise VerifyError(
        f"no round document of {hop.campaign_id}/{hop.cycle_id} scored candidate {candidate_id!r}."
    )


async def verify_candidate(
    *,
    stores: Stores,
    hop: CycleHop,
    candidate_id: str,
    samples: int | None,
    strategy: VerifyStrategy,
    seed: int | None,
    log: Callable[[str], None] | None = None,
) -> VerifyOutcome:
    """Re-score one candidate on *samples* UNMEASURED search cells. Raises :class:`VerifyError`
    when it cannot be resolved off disk."""

    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    if campaign is None:
        raise VerifyError(f"campaign {hop.campaign_id!r} has no manifest on disk.")
    round_num, entry = _scored_candidate(stores, hop, candidate_id)
    label = entry.label

    session, campaign_config = await bind_cycle_session(stores, campaign, hop)
    log_fn = log or (lambda *_a, **_k: None)
    arm_diagnostic_scoring(session, campaign_config, source=RunSource.VERIFY, log=log_fn)

    schema = session.pipeline_schema
    # Before a cell is bought: fresh cells of another searchpoint would read as this one's.
    jsp = entry.searchpoint(
        schema=schema,
        framing=campaign_framing(stores, campaign_config, session.dataset_name),
        demo=session.scoring.require_partition().demo,
    )
    sp_hash = jsp.sp_hash(schema)
    # Off the searchpoint itself, so it carries the rendered prompt: keyed on the round's config
    # alone, every candidate sharing a model reads as one and their cells as already measured.
    predicate: dict[str, dict[str, Any]] = dict(schema.node_configs(jsp.pipeline_params))

    # Find samples this exact searchpoint has not yet been measured on.
    prior = archive_queries.measurements_for_config(
        stores,
        predicate=predicate,
        dataset_name=campaign.dataset_name,
    )
    measured_ids = {m.sample_id for m in prior}
    # The search pool alone: `verify_on_saturation` runs inside the loop, so a bench row it
    # measured would be a bench row the loop decided on.
    search = session.scoring.require_partition().search
    unmeasured = [s for s in search if s.id not in measured_ids]
    round_cells = select_optimizer(campaign_config.optimization).round_cells(len(search))
    if not unmeasured:
        return VerifyOutcome(dataset_name=campaign.dataset_name, already_measured=len(measured_ids))

    ledger_path = CycleLayout(stores.campaigns.cycle_dir(hop)).ledger
    budget = derive_verify_samples(
        round_cell_budget=round_cells,
        rounds_unverified=rounds_since_verified(
            (banked.round for banked, _ in scan_ledger_verify(ledger_path).graded.values()),
            round_num=round_num,
        ),
        unmeasured=len(unmeasured),
    )
    if samples is not None and samples > budget:
        raise VerifyError(
            f"--samples {samples} is above this candidate's verify budget of {budget} "
            f"({round_cells} cells per candidate per round, lifted by the "
            f"rounds run since the last verification, capped at {len(unmeasured)} unmeasured). "
            f"Pass {budget} or fewer, or verify again after more rounds."
        )
    ruler = stores.campaigns.read_ruler(hop, dataset_name=campaign.dataset_name)
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
        len(measured_ids),
    )
    with diagnostic_trace(stores, hop):
        ledger = active_cycle_ledger()
        assert ledger is not None
        on_phase = RunCallbacks(ledger).on_phase
        emit_phase(
            on_phase,
            CampaignPhase.VERIFY,
            "enter",
            label=label,
            candidate_round=round_num,
            rows=len(picked),
            strategy=strategy,
        )
        try:
            scored = await diagnostic_pass(
                VerifyError,
                score_search_point(
                    jsp,
                    picked,
                    session,
                    label="verify",
                    measured=None,
                    on_sample_scored=lambda *_a, **_k: None,
                    on_sample_starting=lambda *_a, **_k: None,
                ),
            )
            banked = VerifyPass(
                label=label,
                candidate_id=candidate_id,
                round=round_num,
                sp_hash=sp_hash,
                run_id=scored.run_id,
                sample_ids=[s.id for s in picked],
                strategy=strategy,
                seed=seed,
                scorer_id=session.scoring.scorer_id,
            )
            reading = read_verify(
                stores,
                hop,
                banked,
                session.scoring.require_scorer(),
                scorer_id=session.scoring.scorer_id,
            )
            if reading.fresh.accuracy is None:
                # `level_columns` reads None over no scoreable row, and a 0.0 here would read as
                # "the fresh cells collapsed" — the exact verdict `verify` reports.
                raise VerifyError(
                    f"the fresh cells of {label!r} produced no scoreable sample — there is "
                    "nothing to read beside the recorded measurement."
                )
            emit_phase(
                on_phase, CampaignPhase.VERIFY, "graded", verify_pass=banked, reading=reading
            )
        finally:
            emit_phase(on_phase, CampaignPhase.VERIFY, "exit")

    return VerifyOutcome(
        dataset_name=campaign.dataset_name,
        already_measured=len(measured_ids),
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
    log: Callable[[str], None] | None = None,
) -> VerifyOutcome | None:
    """A round that reads 100% gets checked, automatically, on cells it has never seen — the one
    question a round's own panel cannot answer, and until now one only an operator at a terminal
    could ask.

    ``derive_verify_samples`` bounds the cells; the ``_VERIFY_LIFT_CAP`` gate below bounds the
    CADENCE, so a cycle sitting at 100% for twenty rounds pays for four checks and not twenty. The
    gate reads passes off the LEDGER, which is also what makes a resumed round decline to repeat its
    own earlier check. Never fatal: a ``VerifyError`` means the candidate would not resolve off
    disk, which is a reason to say nothing, not to end a healthy run.

    ``control`` holds the SAME ceiling the round loop halts on, and is required rather than optional
    because this is the loop spending, not an operator: a discretionary check that could start on an
    exhausted budget would make ``max_usd`` mean whatever the checks happened to cost. The loop's own
    ceiling is consulted at the next round boundary, which is AFTER this runs.
    """
    if accuracy is None or accuracy < 1.0 or not winner_id:
        return None
    if control.budget_tripped() is not None:
        return None
    ledger_path = CycleLayout(stores.campaigns.cycle_dir(hop)).ledger
    mine = [banked.round for banked, _ in scan_ledger_verify(ledger_path).graded.values()]
    if mine and rounds_since_verified(mine, round_num=round_num) < _VERIFY_LIFT_CAP:
        return None
    say = log or (lambda *_a, **_k: None)
    try:
        return await verify_candidate(
            stores=stores,
            hop=hop,
            candidate_id=winner_id,
            samples=None,
            strategy="random",
            seed=None,
            log=log,
        )
    except (VerifyError, BankedSearchPointError) as exc:
        say(f"verify skipped for {winner_id}: {exc}")
        return None
