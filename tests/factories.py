from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from types import EllipsisType, SimpleNamespace
from typing import Any, cast

from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizer_manifest import resolve_optimizer
from promptpotter.application.optimizers.potter.knobs import PoBBKnobs
from promptpotter.application.optimizers.potter.records import (
    POTTER_MANIFEST,
    L2L3Memory,
    Ladder,
    PotterRoundState,
)
from promptpotter.domain.bench import (
    BENCH_HEADLINE,
    BandedValue,
    BenchScore,
    LiftCost,
    LineRun,
    OwnLevel,
    bench_status,
)
from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.optimizer_state import OptimizerState
from promptpotter.domain.paired_reading import PairedReading, ReadingState
from promptpotter.domain.phases import StopReason
from promptpotter.domain.pipeline_schema import PipelineNode, PipelineSchema
from promptpotter.domain.results import (
    ArmOutcome,
    CycleResult,
    DegradationHealth,
    OverlapReading,
    RoundResult,
    ScoredCandidate,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import CellSheet, Grade, GradedCell, MeasuredCell
from promptpotter.domain.spend import SpendBucket, SpendCeilings, SpendRollup
from promptpotter.domain.wounds import ValidationFailure
from promptpotter.infrastructure.llm.spend_book import CeilingMeter, SpendBook
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.io import write_json
from promptpotter.infrastructure.store.layout import inner_sandbox_dir, sandbox_owner_path
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.shared.identity import default_identity

# Two labels on purpose: with as many truths as rows, no constant answerer is detectable.
_TRUTH = ["TRUE", "FALSE", "TRUE", "FALSE"]


def pipeline_schema(nodes: Sequence[PipelineNode], **fields: Any) -> PipelineSchema:
    return PipelineSchema(
        declared_nodes=list(nodes), pipelines={"default": [n.name for n in nodes]}, **fields
    )


def measurement(
    sample_id: int,
    fitness: float | None = 1.0,
    *,
    objective: float | None = None,
    **extra: Any,
) -> dict[str, Any]:
    if fitness is None:
        return {"sample_id": sample_id, **extra}
    return {
        "sample_id": sample_id,
        "hit": fitness > 0.5,
        "fitness": fitness,
        "objective": round(fitness * 0.6, 6) if objective is None else objective,
        **extra,
    }


def measurements(
    grades: Sequence[float], sample_ids: Sequence[int] | None = None
) -> list[dict[str, Any]]:
    ids = range(len(grades)) if sample_ids is None else sample_ids
    return [measurement(sid, g) for sid, g in zip(ids, grades, strict=True)]


def sheet(rows: Iterable[dict[str, Any]], scorer_id: str = "test") -> CellSheet:
    """Each row under the grade it already carries: no formula runs."""

    def graded(row: dict[str, Any]) -> GradedCell:
        facts = MeasuredCell.from_wire(row)
        # The grade and ``scored`` by the rule ``Scorer.grade`` applies.
        if facts.errored:
            return GradedCell(
                facts, Grade(0.0, 0.0, None), facts.charged and not facts.verifier_graded
            )
        grade = Grade(row.get("fitness"), row.get("objective"), row.get("unscored"))
        return GradedCell(facts, grade, grade.fitness is not None)

    return CellSheet(scorer_id, tuple(graded(row) for row in rows))


def scored_candidate(
    candidate_id: str = "c0",
    *,
    accuracy: float = 0.5,
    total: int = 4,
    invalid_reason: str | None = None,
    **overrides: Any,
) -> ScoredCandidate:
    failures = (
        [ValidationFailure(axis="prompt_fields", value="", allowed=[], reason=invalid_reason)]
        if invalid_reason
        else []
    )
    outcome = ArmOutcome.INVALID if invalid_reason else ArmOutcome.MEASURED
    return ScoredCandidate(
        **(
            {
                "candidate_id": candidate_id,
                "label": candidate_id,
                "accuracy": accuracy,
                "composite_fitness": accuracy,
                "total": total,
                "deprecated": 0,
                "evaluators": {},
                "mean_fitness_ci_lo": None,
                "mean_fitness_ci_hi": None,
                "outcome": outcome,
                "validation_failures": failures,
            }
            | overrides
        )
    )


def spend_book(
    usd_cap: float | None = None,
    *,
    usd_reserve: float | EllipsisType | None = ...,
    meters: CeilingMeter = "bill",
    **spent: Any,
) -> SpendBook:
    return SpendBook(
        declared=SpendCeilings(usd_cap, None),
        reserved=SpendCeilings(usd_cap if usd_reserve is ... else usd_reserve, None),
        meters=meters,
        **spent,
    )


def workspace(root: Path) -> Stores:
    return build_stores(
        default_identity(), projects_root=root / "projects", benchmarks_root=root / "datasets"
    )


def loop_session(
    stores: Stores, schema: PipelineSchema, samples: list[Sample], *, backend_id: str = ""
) -> Session:
    session = Session(
        store=stores,
        backend_id=backend_id,
        backend_client=SimpleNamespace(  # type: ignore[arg-type]
            max_cells_in_flight=1,
            cancel_stops_billing=True,
            holds_own_sends=True,
            derives_spend_bounds=False,
            measured_unit="sample",
            backpressure=SimpleNamespace(reading=lambda: None),
        ),
        pipeline_schema=schema,
        samples=samples,
        dataset_name=schema.name,
    )
    session.source = RunSource.OPTIMIZATION_LOOP
    return session


SANDBOX_CAMPAIGN = "testds__20260101-000000"


def inner_sandbox(stores: Stores, owner_campaign_id: str, owner_cycle_id: str) -> Path:
    sandbox = inner_sandbox_dir(
        stores.shared_root,
        str(stores.tenant_id),
        CycleHop(campaign_id=owner_campaign_id, cycle_id=owner_cycle_id),
    )
    write_json(
        sandbox_owner_path(sandbox),
        {
            "tenant_id": str(stores.tenant_id),
            "campaign_id": owner_campaign_id,
            "cycle_id": owner_cycle_id,
        },
    )
    inner = CampaignStore(WorkspaceDir(sandbox / "tenant"))
    inner.mint_cycle(CycleHop(campaign_id="innerds__20260101-000000", cycle_id="inner-cycle-0"))
    return sandbox


def pobb_knobs(**bend: Any) -> PoBBKnobs:
    shipped = cast("PoBBKnobs", resolve_optimizer("potter", {}).knobs("pobb"))
    return shipped.model_copy(update=bend)


def degradation_health(
    *, samples: int = 24, degraded_rate: float = 0.0, no_result: int = 0
) -> DegradationHealth:
    return DegradationHealth(
        grade="healthy" if degraded_rate == 0.0 and not no_result else "degraded",
        samples=samples,
        structural_count=0,
        transient_count=0,
        no_result_count=no_result,
        degraded_rate=degraded_rate,
        consecutive_degraded_rounds=0,
        prior_clean_rounds=0,
    )


def optimizer_state(
    memory: L2L3Memory | None = None, *, parse_failure: str | None = None
) -> OptimizerState:
    return OptimizerState(
        manifest=POTTER_MANIFEST,
        prompt_hashes={},
        payload=PotterRoundState(
            memory=memory or L2L3Memory(),
            ladder=Ladder(),
            l1_parse_failure=parse_failure,
        ),
    )


def round_result(
    rnd: int,
    *,
    improved: bool = True,
    degraded_rate: float = 0.0,
    no_result: int = 0,
    samples: int = 24,
    candidates_scored: int = 2,
    parse_failure: str | None = None,
    no_op: int = 0,
    dup: int = 0,
    collapsed: int = 0,
    cut: int = 0,
    **overrides: Any,
) -> RoundResult:
    if parse_failure:
        candidates_scored = 0
    measured = [
        scored_candidate(f"c{i}", total=2 if i < cut else len(_TRUTH))
        for i in range(candidates_scored)
    ]
    rejected = [
        scored_candidate(f"x{i}", invalid_reason=reason)
        for reason, count in (("no_op_variant", no_op), ("duplicate_variant", dup))
        for i in range(count)
    ]
    # Merged rather than splatted after the literals, so ``overrides`` reaches EVERY field.
    base: dict[str, Any] = {
        "round": rnd,
        "label": "C0",
        "accuracy": 0.5,
        "composite_fitness": 0.5,
        "total": 4,
        "improved": improved,
        "elects_on": "ability",
        # No pair read: a test that bends the advance passes the reading that derives it.
        "overlap": OverlapReading.unpaired(
            ReadingState.NOT_HELD if rnd else ReadingState.SAME_INDIVIDUAL,
            rnd,
            bool(overrides.get("improved", improved)),
        ),
        "prompt_fields": {},
        "candidates_scored": candidates_scored,
        "candidate_scores": measured + rejected,
        "all_candidate_results": {
            f"c{i}": sheet(
                {
                    "sample_id": sid,
                    "predicted": "Uncertain" if i < collapsed or i < cut else t,
                    "ground_truth": t,
                }
                for sid, t in enumerate(_TRUTH[:2] if i < cut else _TRUTH)
            )
            for i in range(candidates_scored)
        },
        "health": degradation_health(
            samples=samples, degraded_rate=degraded_rate, no_result=no_result
        ),
        "selected_labels": [],
        "leading_label": None,
        "optimizer_state": optimizer_state(parse_failure=parse_failure),
    }
    return RoundResult(**(base | overrides))


def cycle_result(
    levels: list[float],
    origin: float | None,
    rounds: list[RoundResult],
    *,
    cost: float = 0.03,
    stop_reason: StopReason = StopReason.MAX_ROUNDS,
    unpriced_tokens: int = 0,
    billed: float | None = None,
    **overrides: Any,
) -> CycleResult:
    """L1 rounds only in `rounds`/`levels`; `cost` is INCURRED, `billed` diverges under replay."""
    unheld = PairedReading.unread(ReadingState.NOT_HELD)
    bench = BenchScore.of(
        bench_size=0,
        scorer_id="",
        headline=BENCH_HEADLINE,
        status=bench_status(
            trigger="at_end",
            bench_size=0,
            tolerance=0,
            on_line=True,
            held_by=None,
            origin=None,
            selected=None,
            run=LineRun(selecting=False, ending=stop_reason, selection=None, rounds_closed=0),
        ),
        origin=None,
        selected=None,
        vs_origin=unheld,
        cost=LiftCost.of(unheld, None),
    )
    return CycleResult(
        rounds=rounds,
        n_rounds_after_origin=len(rounds),
        result_accuracy=0.5,
        result_round=len(rounds),
        origin=None
        if origin is None
        else OwnLevel(
            accuracy=BandedValue(value=origin, ci_lo=None, ci_hi=None), composite=None, n=1
        ),
        bench=bench,
        origin_level=origin,
        round_levels=levels,
        result_prompt_fields={},
        stop_reason=stop_reason,
        started_at="2026-01-01T00:00:00Z",
        finished_at="2026-01-01T01:00:00Z",
        spend=SpendRollup(
            total_used_usd=cost if billed is None else billed,
            total_incurred_usd=cost,
            by_kind={"optimizer": SpendBucket(incurred_unpriced_tokens=unpriced_tokens)},
        ),
        **overrides,
    )
