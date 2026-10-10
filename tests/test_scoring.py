"""A wrong score or a wrong decision.

Owns `application/scoring/`, `shared/statistics.py`, `optimizers/potter/pobb/` and
`optimizers/potter/escalation/`, the runner's election, `diagnostics/verify.py` and `evidence/`.
The run completes, the dashboard looks fine, and a different candidate should have won.
"""

from __future__ import annotations

import asyncio
import copy
import json
import pickle
import random
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from factories import loop_session, pipeline_schema

from promptpotter.application.bench import node_context
from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.campaign_config import load_campaign_config
from promptpotter.application.campaign_listing import list_campaigns
from promptpotter.application.evidence.head_to_head import HeadToHead
from promptpotter.application.evidence.read import subject_evidence
from promptpotter.application.evidence.subjects import SubjectSpec
from promptpotter.application.optimizer_manifest import bind_optimizer
from promptpotter.application.optimizers.potter.pobb.checks import EliminationGate, PoBBCheck
from promptpotter.application.run_phase_control import RunControl
from promptpotter.application.runner.bench import (
    BenchLine,
    bench_selection,
    read_bench,
    score_on_bench,
)
from promptpotter.application.runner.campaign_result import bank_campaign_result
from promptpotter.application.runner.entry import _build_cycle_result
from promptpotter.application.runner.measurement import _ReferencePairs
from promptpotter.application.runner.round import execute_round
from promptpotter.application.runner.termination import standing_tripped
from promptpotter.application.scoring import query_loop
from promptpotter.application.scoring.classification import DegradationCheck, scoreable_rows
from promptpotter.application.scoring.evaluators import DEFAULT_CELL_FORMULA
from promptpotter.application.scoring.formula import (
    ScoringFormulaError,
    auto_scorer_id,
    compile_scorer,
)
from promptpotter.application.scoring.formula.matchers import (
    _aime_match,
    _gsm8k_match,
    _label_match,
)
from promptpotter.application.scoring.metrics import compute_composite_fitness
from promptpotter.application.scoring.paired import (
    MemberRows,
    absent_pair,
    as_family,
    grade_measurands,
    read_pair,
)
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.application.views.view_models import ViewContext
from promptpotter.domain.bench import (
    BenchPass,
    BenchPasses,
    BenchScore,
    BenchTrigger,
    DatasetSplit,
    LineRun,
    OwnLevel,
    partition_bank,
)
from promptpotter.domain.campaign import Arm, ArmBudget, Campaign, HeadToHeadRecord
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.export import build_prompt_export
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.optimizer_state import RoundPayload
from promptpotter.domain.paired_reading import (
    READING_STATE_INFO,
    ROUND_LIFT_SPEC,
    ArmPointer,
    CellSetName,
    CoverageState,
    EstimatorSpec,
    IntervalMethod,
    Measurand,
    MeasurandKind,
    MeasurandUnit,
    MemberAddress,
    PairedReading,
    ReadingState,
)
from promptpotter.domain.phase_views import BenchGradedView, ViewAnchors
from promptpotter.domain.phases import CampaignPhase, RunPhase, StopReason
from promptpotter.domain.pipeline_schema import NodePromptInfo, PipelineNode, PipelineSchema
from promptpotter.domain.results import (
    ArmOutcome,
    OverlapReading,
    RoundAdvance,
    RoundCells,
    RoundResult,
    RunStanding,
    is_leader_eligible,
    order_floor,
    round_advance,
    rounds_without_advance,
    stalls_left,
)
from promptpotter.domain.ruler import DeltaRuler, anchor_id_of
from promptpotter.domain.run_records import (
    CandidateMintedRecord,
    CandidateScoredRecord,
    PhaseRecord,
    RoundClosedRecord,
    RoundEnteredRecord,
    RoundStandingRecord,
    RunPhaseRecord,
    SampleScoredRecord,
    TokenUsageRecord,
)
from promptpotter.domain.sample import ArchiveEntry, Sample
from promptpotter.domain.scoring import (
    ROW_GRADES,
    CellSheet,
    MeasuredCell,
    PipelineData,
    Scorer,
    WalkedCell,
)
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.spend import SpendCeilings
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.measurement_archive import config_key
from promptpotter.shared.answer_text import extract_gsm8k_number
from promptpotter.shared.measurement_context import NO_ROUND_SLOT, MeasurementRole, RoleScope
from promptpotter.shared.statistics import paired_mean_t
from tests.factories import (
    measurement,
    measurements,
    optimizer_state,
    pobb_knobs,
    round_result,
    scored_candidate,
    sheet,
    spend_book,
)

# 1. Scorer formulas


@pytest.mark.parametrize(
    "fn,args,expected",
    [
        (_aime_match, (r"First: \boxed{10}. Rechecking: \boxed{42}", "42"), 1.0),
        (_aime_match, (r"\boxed{undefined} The answer is 42", "42"), 1.0),
        (_aime_match, ("no numbers", "42"), 0.0),
        (extract_gsm8k_number, ("#### 1,234",), 1234.0),
        (extract_gsm8k_number, ("I calculated 99 but #### 42",), 42.0),
        (extract_gsm8k_number, ("no numbers",), None),
        (_gsm8k_match, ("42.0", "#### 42"), 1.0),
        (_gsm8k_match, ("#### 99", "#### 42"), 0.0),
        (_label_match, ("First try **No**. Corrected: **Yes**", "yes"), 1.0),
        (_label_match, ("plain text answer", "Plain Text Answer"), 1.0),
        (_label_match, ("foo", "bar"), 0.0),
        (_label_match, ("so the answer is **D**", "(D)"), 1.0),
        (_label_match, ("**(b)**", "B"), 1.0),
        (_label_match, ("**C**", "(D)"), 0.0),
    ],
)
def test_matcher_formula(fn, args, expected):
    assert fn(*args) == expected


@pytest.mark.parametrize(
    "formula",
    [
        "().__class__",
        "__import__('os').system('echo pwn')",
        "(lambda: 1)()",
        "[x for x in range(10)][0]",
        "predicted.__class__",
        "label_match(predicted, ground_truth) := 1",
    ],
)
def test_compile_scorer_rejects_attribute_and_unsafe_syntax(formula: str) -> None:
    """Restricted-eval is bypassable; the AST allowlist is the real boundary."""
    with pytest.raises((ValueError, SyntaxError)):
        compile_scorer(formula, verifier_graded=False)


def _single_node_schema() -> PipelineSchema:
    return pipeline_schema(
        name="test",
        nodes=[PipelineNode(name="llm_only", tunes_llm=False)],
    )


def test_a_dial_reads_its_term_against_the_origin_and_never_sums_a_raw_unit() -> None:
    from promptpotter.application.scoring.formula import origin_anchors, parse_dials, realize_dials
    from promptpotter.domain.scoring import anchored_criterion_dials

    def cell(said: str, tokens: int) -> dict[str, Any]:
        return measurement(
            0,
            None,
            query="q",
            predicted=said,
            ground_truth="a",
            error=None,
            pipeline_data={"step_tokens": {"solve": {"input": tokens, "output": 0}}},
        )

    anchors = origin_anchors(sheet([cell("a", 600), cell("b", 784)]))
    assert anchors["tokens"] == pytest.approx(692.0)

    formula = realize_dials(parse_dials("tokens=0.08, latency=0"), anchors)
    scorer = compile_scorer("label_match(predicted, ground_truth)", formula, verifier_graded=False)

    def scored(said: str, tokens: int) -> float:
        graded = scorer.read([cell(said, tokens)])
        return compute_composite_fitness(graded, _single_node_schema()).composite_fitness

    assert scored("a", 692) == pytest.approx(1.0)
    assert scored("a", 6920) == pytest.approx(0.92 + 0.08 * 0.1)
    assert scored("a", 100) == pytest.approx(1.0)

    spelled = realize_dials(parse_dials("tokens=0.08,cached=0.1,errored=0.2"), anchors)
    read_back = anchored_criterion_dials(spelled)
    assert read_back is not None
    assert {name: dial.weight for name, dial in read_back.items()} == {
        "tokens": 0.08,
        "cached": 0.1,
        "errored": 0.2,
    }
    assert read_back["tokens"].anchor == pytest.approx(692.0)
    assert anchored_criterion_dials("0.9 * fitness + 0.1 * (1 - tokens)") is None


def test_a_miss_is_charged_its_cost_and_a_solved_cell_scores_its_composite(monkeypatch) -> None:
    from promptpotter.application.scoring.formula import auto_scorer_id, compiler
    from promptpotter.domain.results import is_floor_pinned

    per_sample = "label_match(predicted, ground_truth)"
    per_cell = "fitness * (0.92 + 0.08 * 692.0 / max(692.0, tokens))"

    def cell(sid: int, predicted: str, tokens: int, **extra: Any) -> dict[str, Any]:
        steps = {"solve": {"input": tokens, "output": 0}}
        return {
            "sample_id": sid,
            "query": "q",
            "predicted": predicted,
            "ground_truth": "a",
            "error": None,
            "pipeline_data": {"step_tokens": steps},
            **extra,
        }

    scorer = compile_scorer(per_sample, per_cell, verifier_graded=False)
    graded = scorer.read(
        [
            cell(0, "a", 692),
            cell(1, "a", 2076),
            cell(2, "b", 692),
            cell(3, "b", 2076),
            cell(4, "b", 20760, error_category="SERVER"),
        ]
    )
    rows = graded.cells
    solved_cheap, solved_costly, miss_cheap, miss_costly, _ = (r.grade.objective for r in rows)
    assert solved_cheap == 1.0, "a solved cell moved off its composite"
    assert solved_costly == pytest.approx(0.92 + 0.08 / 3)
    assert miss_cheap == pytest.approx(compiler.MISS_COST_SHARE)
    assert miss_costly == pytest.approx(compiler.MISS_COST_SHARE * solved_costly)
    scored = compute_composite_fitness(graded, _single_node_schema()).composite_fitness
    assert scored == pytest.approx((solved_cheap + solved_costly + miss_cheap + miss_costly) / 4)
    # The 0% floor is a fact about correctness, which a charged miss no longer reads as zero.
    assert is_floor_pinned(rows[2:4]) and not is_floor_pinned(rows[1:4])

    def objectives(*cells: dict[str, Any]) -> list[float]:
        return [r.grade.objective for r in scorer.read(cells)]

    solved = objectives(*(cell(i, "a", t) for i, t in enumerate((100, 692, 1000, 2076, 10**7))))
    assert solved[0] == solved[1] and solved[1:] == sorted(set(solved[1:]), reverse=True)
    costliest_hit, cheapest_miss = objectives(cell(0, "a", 10**7), cell(1, "b", 100))
    assert costliest_hit > cheapest_miss, "length made a miss outscore a hit"
    long_perfect = objectives(*(cell(i, "a", 10**6) for i in range(4)))
    short_half = objectives(*(cell(i, "ab"[i % 2], 100) for i in range(4)))
    assert sum(long_perfect) > sum(short_half), "length outranked a 2x accuracy gap"

    # A cost term reads the provider's bill, else our rate's price: most report no dollars.
    def costing(**usd: float) -> dict[str, Any]:
        row = cell(0, "a", 692)
        row["pipeline_data"]["step_tokens"]["solve"].update(usd)
        return row

    by_cost = compile_scorer(per_sample, "fitness / (1.0 + cost)", verifier_graded=False)
    at_rate, billed = by_cost.read([costing(rate_priced_usd=1.0), costing(cost_usd=3.0)])
    assert (at_rate.grade.objective, billed.grade.objective) == (0.5, 0.25)
    assert "cost" not in compiler.cell_channels_of(MeasuredCell.from_wire(costing()), None)

    # The share is half the grading function: a ruler must never pool grades across two of them.
    before = auto_scorer_id(per_sample, per_cell, judge_instrument=None)
    # So are the judges whose banked terms a formula reads: one text over two graders is two.
    assert auto_scorer_id(per_sample, per_cell, judge_instrument="a judge") != before
    monkeypatch.setattr(compiler, "MISS_COST_SHARE", 0.3)
    assert auto_scorer_id(per_sample, per_cell, judge_instrument=None) != before
    # Only a DECLARED cell formula charges a miss: the id, never the text, names the grader.
    [defaulted] = compile_scorer(per_sample, None, verifier_graded=False).read([cell(2, "b", 692)])
    assert defaulted.grade.objective == 0.0
    assert auto_scorer_id(per_sample, None, judge_instrument=None) != auto_scorer_id(
        per_sample, DEFAULT_CELL_FORMULA, judge_instrument=None
    )


def _result_min(predicted: str, ground_truth: str) -> dict:
    return {
        "sample_id": 0,
        "query": "q",
        "predicted": predicted,
        "ground_truth": ground_truth,
        "hit": False,
        "fitness": 0.0,
        "error": None,
        "pipeline_data": None,
    }


def test_a_conversation_reaches_the_formula_only_as_projected_scalars() -> None:
    """``turns`` is compacted out of cold rows: a formula walking it would raise on scored cells."""
    from factories import measurement

    from promptpotter.application.scoring.formula import compile_scorer
    from promptpotter.domain.scoring import turn_scalars

    turns = [
        {"index": 1, "source": "agent", "step": "retrieve", "tools": ["bash", "bash"]},
        {"index": 2, "source": "agent", "step": "retrieve", "tools": []},
        {"index": 3, "source": "agent", "step": "answer", "tools": ["bash"]},
    ]
    scalars = turn_scalars(turns)  # type: ignore[arg-type]
    assert scalars == {
        "n_turns": 3.0,
        "n_tool_calls": 3.0,
        "retrieve_turns": 2.0,
        "answer_turns": 1.0,
    }
    assert turn_scalars([]) == {}, "no conversation is absence, never a zeroed count"

    row = measurement(
        sample_id=0, fitness=0.0, pipeline_data={"env_reward": 1.0, "turns": turns, **scalars}
    )
    scorer = compile_scorer(
        "env_reward * (1.0 if n_turns <= 4 else 0.5) * min(1.0, retrieve_turns / 2.0)",
        None,
        verifier_graded=True,
    )
    assert scorer.grade(MeasuredCell.from_wire(row)).grade.fitness == 1.0

    # Refused at COMPILE: a bare Call reaches eval as a NameError, which reads as a missing TERM.
    for formula in ("turns[0]", "turns.index"):
        with pytest.raises(ValueError):
            compile_scorer(formula, None, verifier_graded=True)
    with pytest.raises(ValueError):
        compile_scorer("len(turns)", None, verifier_graded=True)


def test_a_grade_is_a_new_record_and_keeps_nothing_of_the_formula_before_it() -> None:
    from promptpotter.domain.scoring import GRADE_KEYS

    composed = compile_scorer("skill_opened", "fitness * 0.5", verifier_graded=True)
    plain = compile_scorer("env_reward", None, verifier_graded=True)

    def record(**channels: float) -> dict[str, Any]:
        return measurement(
            0, None, query="q", ground_truth="", predicted="p", error=None, pipeline_data=channels
        )

    def facts(**channels: float) -> MeasuredCell:
        return MeasuredCell.from_wire(record(**channels))

    both = facts(skill_opened=1.0, env_reward=0.25)
    as_banked = json.dumps(both.wire(), sort_keys=True)
    first = composed.grade(both)
    assert json.dumps(both.wire(), sort_keys=True) == as_banked
    assert not GRADE_KEYS & both.wire().keys()
    assert (first.grade.fitness, first.grade.objective, first.scored) == (1.0, 0.5, True)

    second = plain.grade(first.facts)
    assert (second.grade.fitness, second.grade.objective) == (0.25, 0.25)
    assert (first.grade.fitness, first.grade.objective) == (1.0, 0.5)

    unread = composed.grade(facts(env_reward=1.0))
    assert (unread.grade.fitness, unread.grade.objective, unread.scored) == (None, None, False)
    assert unread.grade.unscored and not {"fitness", "objective"} & unread.wire().keys()
    recovered = plain.grade(unread.facts)
    assert (recovered.grade.fitness, recovered.scored) == (1.0, True)
    assert recovered.grade.unscored is None and "unscored" not in recovered.wire()

    # An errored row is graded 0.0 without evaluating a formula that would raise on it.
    raising = compile_scorer("1.0 / tokens", None, verifier_graded=True)
    unanswered = MeasuredCell.from_wire(
        {**record(tokens=0.0), "error": "HTTP 502", "error_category": "SERVER"}
    )
    with pytest.raises(ScoringFormulaError):
        raising.grade(facts(tokens=0.0))
    errored = raising.grade(unanswered)
    assert (errored.grade.fitness, errored.grade.objective, errored.scored) == (0.0, 0.0, False)


def test_a_cell_read_off_the_wire_keeps_its_facts_and_drops_any_grade_it_arrived_with() -> None:
    banked = {
        "sample_id": 7,
        "sample_key": "k7",
        "query": "q",
        "ground_truth": "a",
        "predicted": "b",
        "error": "ran past its envelope",
        "error_category": "HALTED",
        "pipeline_data": {
            "terminal_node": "solve",
            "step_tokens": {"solve": {"input": 692, "output": 8, "estimated": False}},
            "step_timings": {"solve": 1.5},
            "total_time": 2.0,
            "env_reward": 0.25,
        },
        "cached": True,
        "ground_truth_rank": 3,
        "n_candidates": 5,
        "answer": "file.a1",
        "retry_of_degraded": True,
        "degraded_obs_count": 2,
    }
    stale = {"fitness": 1.0, "objective": 1.0, "unscored": "an older formula's"}

    facts = MeasuredCell.from_wire({**banked, **stale})
    assert facts.wire() == banked, "a fact was lost, or a grade reached the facts"
    assert MeasuredCell.from_wire(facts.wire()) == facts
    assert (facts.cost_s, facts.elapsed_s, facts.shown_s) == (1.5, 2.0, 1.5)
    plain = compile_scorer("env_reward", None, verifier_graded=True)
    answered = MeasuredCell.from_wire({**banked, **stale, "error": None, "error_category": None})
    graded = plain.grade(answered)
    assert (graded.grade.fitness, graded.grade.unscored) == (0.25, None)
    assert graded.wire() == {**answered.wire(), "fitness": 0.25, "objective": 0.25}
    unranked = MeasuredCell.from_wire({**banked, "ground_truth_rank": None})
    assert unranked.wire()["ground_truth_rank"] is None and unranked.wire()["n_candidates"] == 5


def test_a_wire_record_reads_back_what_it_wrote_and_refuses_a_count_of_another_type() -> None:
    from promptpotter.domain.scoring import Diagnostics, JudgeReading, NodeWarning
    from promptpotter.domain.spend import StepUsage

    usage = StepUsage(
        input=3, output=4, estimated=True, cost_usd=0.5, model="m", reasoning=0, cache_read=0
    )
    warning = NodeWarning(
        step="n", code="c", message="m", kind="transient", details=(1, {"a": 2}), stats={"x": 1}
    )
    cell = MeasuredCell(
        sample_id=1,
        sample_key="k",
        predicted="p",
        cached=True,
        n_candidates=3,
        retry_of_degraded=True,
        rerun_comparison={"hit_change": "MISS->HIT", "rank_change": None, "improved": True},
        pipeline=PipelineData(
            total_time=0.0,
            terminal_node="n",
            step_timings={"n": 1.5},
            step_tokens={"n": usage, "silent": StepUsage()},
            diagnostics=Diagnostics(
                step_statuses={"n": "degraded"}, warnings=(warning, NodeWarning())
            ),
            result_ranking=(),
            turns=({"index": 0},),
            step_phases={},
            judge_readings={"t": JudgeReading(), "u": JudgeReading("label", "why")},
            target_prompt_chars=0,
            observations={"final_ranking": [1], "env_reward": 0.0, "opened": False},
        ),
    )
    for held in (usage, warning, cell.pipeline, cell, MeasuredCell(sample_id=0)):
        wire = json.loads(json.dumps(held.wire()))
        read = type(held).from_wire(wire)
        assert read == held
        assert read.wire() == wire
        assert copy.deepcopy(read) == read
        assert pickle.loads(pickle.dumps(read)) == read

    banked = MeasuredCell.from_wire(json.loads(json.dumps(cell.wire())))
    for closed in (banked, copy.deepcopy(banked), pickle.loads(pickle.dumps(banked))):
        with pytest.raises(TypeError):
            closed.pipeline.step_timings["n"] = 0.0

    for mistyped in (
        {"input": "1200"},
        {"output": 3.9},
        {"attempts": True},
        {"cost_usd": "0.5"},
        {"estimated": "false"},
    ):
        with pytest.raises(TypeError):
            StepUsage.from_wire(mistyped)
    assert StepUsage.from_wire({"input": None}) == StepUsage()


# 2. Composite fitness and coverage


def test_an_unmeasured_term_is_never_scored_as_zero() -> None:
    # A round that measured every sample and failed them all IS a 0.0; an unmeasured one is not.
    from promptpotter.application.scoring.evaluators import (
        compute_accuracy,
        compute_degraded_rate,
        compute_error_rate,
    )

    assert compute_accuracy(results=[]) is None
    assert compute_error_rate(results=[]) is None
    assert compute_degraded_rate(results=[]) is None

    # A CACHED replay stamps `total_time` 0.0, so latency reads `step_timings`; empty is absent.
    def _timed(total: float, steps: dict[str, float]) -> MeasuredCell:
        pipeline_data = {"total_time": total, "step_timings": steps}
        return MeasuredCell.from_wire(_result_min("q", "a") | {"pipeline_data": pipeline_data})

    assert _timed(0.0, {"inner": 600.0}).cost_s == 600.0
    assert _timed(0.0, {}).cost_s is None

    deprecated = _result_min("q", "a") | {"error": "SCHEMA_VALIDATION_FAILED", "fitness": 0.0}
    assert compute_accuracy(results=sheet([deprecated]).cells) == 0.0

    # A provider's fault is the ABSENCE of a verdict: out of the mean, still on the error channel.
    scored = _result_min("q", "a") | {"hit": True, "fitness": 1.0}
    errored = _result_min("ERROR", "a") | {"error": "boom", "error_category": "SERVER"}
    del errored["fitness"], errored["hit"]  # real error rows carry neither
    assert compute_accuracy(results=sheet([scored, errored]).cells) == 1.0
    assert compute_error_rate(results=sheet([scored, errored]).cells) == 0.5

    # An EMPTY round has NO score, never a 0.0 that reads as an arm that failed every cell.
    empty = compute_composite_fitness(sheet([]), _single_node_schema())
    assert empty.composite_fitness is None
    assert empty.accuracy is None
    assert empty.total == 0
    assert order_floor(None) < order_floor(0.0)

    # Every row ERRORED with no label to miss: no rate either (an L4 cell cut throughout is not 0%).
    errored = _result_min("ERROR", "") | {"error": "boom", "error_category": "HALTED"}
    del errored["fitness"], errored["hit"]
    all_errored = compute_composite_fitness(sheet([errored]), _single_node_schema())
    assert all_errored.accuracy is None
    assert all_errored.composite_fitness is None
    assert all_errored.total == 0


def test_a_cell_the_prompt_failed_stays_in_the_denominator_as_a_miss() -> None:
    """Only a provider fault leaves the count: a refusal and a cut cell are the prompt's own."""
    scorer = compile_scorer("label_match(predicted, ground_truth)", verifier_graded=False)

    def row(sid: int, predicted: str, **extra: Any) -> dict[str, Any]:
        cell = {"sample_id": sid, "query": f"q{sid}", "ground_truth": "a", "predicted": predicted}
        return {**cell, "error": None, "pipeline_data": {}, **extra}

    rows = [row(i, "a") for i in range(5)]
    rows += [row(5 + i, "I cannot help with that request.") for i in range(5)]
    rows += [
        row(10, "ERROR", error="ran past its envelope", error_category="HALTED"),
        row(11, "ERROR", error="HTTP 502", error_category="SERVER"),
    ]
    scores = compute_composite_fitness(scorer.read(rows), _single_node_schema())
    assert (scores.total, len(rows) - scores.total) == (11, 1)
    assert scores.accuracy == pytest.approx(5 / 11), "the prompt's own failures left the count"
    # The default formula is plain accuracy, so the decision metric and the headline agree.
    assert scores.composite_fitness == pytest.approx(scores.accuracy)
    # With no label a cut cell has no miss to be, so it carries no verdict either.
    [unlabelled] = scorer.read([{**rows[10], "ground_truth": ""}])
    assert not unlabelled.scored


def _r(score: float) -> dict:
    # ``objective`` pinned equal to fitness deliberately: `factories.measurement` diverges the two.
    return {
        "sample_id": 0,
        "query": "q",
        "predicted": "p",
        "ground_truth": "g",
        "fitness": score,
        "objective": score,
    }


def test_a_measured_cell_the_formula_cannot_grade_is_kept_not_failed() -> None:
    """UNSCORED is a third state: ERRORED would stamp 0.0 and abandon the candidate's whole walk."""
    scorer = compile_scorer("skill_opened", None, verifier_graded=True)

    # The second was banked under another formula: its verdict is one this formula cannot derive.
    carries, lacks = scorer.read(
        [
            {**_r(1.0), "pipeline_data": {"skill_opened": 1.0}},
            {**_r(1.0), "pipeline_data": {"env_reward": 1.0}},
        ]
    )

    assert carries.grade.fitness == 1.0 and carries.grade.objective == 1.0
    assert carries.grade.unscored is None

    # The stamps are ORDERED: a composite naming `fitness` is unevaluable until the first lands.
    composed = compile_scorer(
        "1.0", "fitness * (0.85 + 0.15 * skill_opened)", verifier_graded=True
    ).grade(MeasuredCell.from_wire({**_r(1.0), "pipeline_data": {"skill_opened": 0.0}}))
    assert (composed.grade.fitness, composed.grade.objective) == (1.0, 0.85)

    assert not {"fitness", "objective"} & lacks.wire().keys()
    assert lacks.grade.unscored
    assert not lacks.facts.errored
    assert lacks.facts.error_category is None
    assert scoreable_rows([carries, lacks]) == [carries]

    recovered = compile_scorer("env_reward", None, verifier_graded=True).grade(lacks.facts)
    assert recovered.grade.fitness == 1.0 and recovered.grade.unscored is None

    # A formula that RAISES is a different fact and must still halt loud.
    with pytest.raises(ScoringFormulaError):
        compile_scorer("1.0 / tokens", None, verifier_graded=True).read(
            [{**_r(1.0), "pipeline_data": {"tokens": 0.0}}]
        )


def test_a_judge_never_grades_a_cell_that_has_no_answer() -> None:
    """``predicted`` is the ``NO_RESULT`` sentinel on every cell of a backend emitting no ranking."""
    import asyncio

    from promptpotter.domain.scoring import NO_RESULT
    from promptpotter.judges import call as judge_call
    from promptpotter.judges.grounding import ANSWER_GROUNDING
    from promptpotter.judges.protocol import JudgeSpec, JudgeStage

    calls: list[str] = []

    async def _explode(*_a: Any, **_k: Any) -> tuple[str, str]:
        calls.append("asked a model")
        return "A", ""

    # A cell that RAN and left a trace, so the no-trace arm cannot be what fired.
    row = MeasuredCell(
        sample_id=0,
        predicted=NO_RESULT,
        pipeline=PipelineData(reasoning_trace="searched the docs, found the founding date"),
    )
    spec = JudgeSpec(name="answer_grounding", stages=[JudgeStage(model="m", provider="p")])
    original, judge_call.ask = judge_call.ask, _explode
    try:
        verdict = asyncio.run(ANSWER_GROUNDING.grade(spec, row))
    finally:
        judge_call.ask = original

    assert verdict.score is None, f"a sentinel answer was graded as {verdict.label!r}"
    assert calls == [], f"a cell with no answer was billed a grading: {calls}"


def test_a_merge_never_shrinks_what_was_already_measured() -> None:
    from promptpotter.domain.results import merge_known_outcomes as _merge

    prior = list(sheet(measurements([1.0 if i < 10 else 0.0 for i in range(20)])))
    winner_hits = {10, 12, 15}
    winner = sheet(
        measurements([1.0 if sid in winner_hits else 0.0 for sid in range(10, 18)], range(10, 18))
    )
    merged = _merge(prior, winner)
    by_sid = {r.sample_id: r for r in merged}

    assert set(by_sid.keys()) == set(range(20))
    assert by_sid[10].hit is True
    assert by_sid[11].hit is False
    assert by_sid[19].hit is False
    assert all(by_sid[i].hit is True for i in range(10))
    assert _merge(prior, []) == prior


# 3. Electing a round winner


def _ruler(
    delta: dict[int, float],
    *,
    mu: float = 0.0,
    sigma: float = 2.0,
    se: float | dict[int, float] = 0.5,
) -> DeltaRuler:
    return DeltaRuler(
        delta=dict(delta),
        delta_se=dict(se) if isinstance(se, dict) else dict.fromkeys(delta, se),
        mu_delta=mu,
        sigma_delta=sigma,
        sigma_theta=1.5,
        calibration_model="1PL",
        anchor_id=anchor_id_of(delta, mu, sigma, "1PL"),
    )


def test_round_winner_elects_by_ability_not_subset_accuracy() -> None:
    from promptpotter.application.scoring.selection import elect_round_winner

    # Easy cells {0..19} sit low on the δ ruler, hard {20..39} high; the origin hits only the easy.
    ruler = _ruler({i: (-1.5 if i < 20 else 1.5) for i in range(40)})
    origin = [measurement(i, float(i < 20)) for i in range(40)]
    weak_on_easy = [measurement(i, float(i < 16)) for i in range(20)]
    able_on_hard = [measurement(i, float(i < 34)) for i in range(20, 40)]
    results_by_id = {"weak_on_easy": sheet(weak_on_easy), "able_on_hard": sheet(able_on_hard)}

    assert sum(r["hit"] for r in weak_on_easy) / 20 > sum(r["hit"] for r in able_on_hard) / 20
    winner_id, abilities = elect_round_winner(
        ["weak_on_easy", "able_on_hard"], results_by_id, sheet(origin), 4, ruler, parent_bias=0.0
    )
    assert winner_id == "able_on_hard"
    assert abilities.theta["able_on_hard"] > abilities.theta["weak_on_easy"]


def test_a_thin_arm_cannot_win_on_a_margin_inside_its_own_noise() -> None:
    """`coverage_floor` IS PoBB's `n_min`, so every cut arm clears it and reaches the election."""
    from promptpotter.application.intelligence.rasch import candidate_abilities
    from promptpotter.application.scoring.selection import elect_round_winner
    from promptpotter.shared.statistics import p_exceeds

    ruler = _ruler(dict.fromkeys(range(28), 0.0))
    origin = sheet(measurements([1.0] * 14 + [0.0] * 14))
    deep = sheet(measurements([1.0] * 21 + [0.0] * 7))
    # Cut at n_min: a higher RATE on 1/6 the evidence.
    shallow = sheet(measurements([1.0] * 5 + [0.0]))

    ab = candidate_abilities({"deep": deep, "shallow": shallow}, origin, ruler)
    assert ab.parent is not None
    theta_p, se_p = ab.parent
    p = {c: p_exceeds(ab.theta[c], ab.theta_se[c], theta_p, se_p) for c in ("deep", "shallow")}

    # The thin arm's POINT lift is larger, on nearly twice the SE — so it demonstrated less.
    assert ab.theta["shallow"] > ab.theta["deep"]
    assert ab.theta_se["shallow"] > 1.8 * ab.theta_se["deep"]
    assert p["deep"] > p["shallow"]
    args = (["deep", "shallow"], {"deep": deep, "shallow": shallow}, origin, 6, ruler)
    assert elect_round_winner(*args, parent_bias=0.0)[0] == "deep"

    # Ranking on P must never DISQUALIFY: a lone wide-posterior gain still wins.
    assert (
        elect_round_winner(["shallow"], {"shallow": shallow}, origin, 6, ruler, parent_bias=0.0)[0]
        == "shallow"
    )


def test_the_bar_is_what_the_parent_can_do_not_the_draw_that_crowned_it() -> None:
    """The rank reads the bias-corrected bar; ADMISSION reads the parent's measured θ."""
    import math

    from promptpotter.application.intelligence.rasch import (
        candidate_abilities,
        theta_lift_over_parent,
    )
    from promptpotter.application.scoring.selection import (
        elect_round_winner,
        parent_selection_bias,
    )

    def won(*, se: float | None, electable: int) -> RoundResult:
        return round_result(
            1,
            electable_count=electable,
            selected_labels=["w"],
            candidate_scores=[scored_candidate("w", theta=0.5, theta_se=se)],
        )

    def held() -> RoundResult:
        return round_result(1, prompt_fields={})

    # E[max of two standard normals] is 1/√π: the table is checkable against arithmetic.
    assert parent_selection_bias([won(se=1.0, electable=2)]) == pytest.approx(
        1 / math.sqrt(math.pi), abs=5e-5
    )
    assert parent_selection_bias([won(se=0.25, electable=2)]) == pytest.approx(
        0.25 / math.sqrt(math.pi), abs=2e-5
    )
    # A LONE electable arm has no maximum and no curse; a defaulted `electable_count` clamps there.
    assert parent_selection_bias([won(se=0.9, electable=1)]) == 0.0
    assert parent_selection_bias([round_result(1, selected_labels=["c0"])]) == 0.0
    by_k = [parent_selection_bias([won(se=1.0, electable=k)]) for k in range(1, 7)]
    assert by_k == sorted(by_k) and by_k[0] == 0.0
    assert parent_selection_bias([won(se=1.0, electable=99)]) == pytest.approx(by_k[-1])

    # A HELD round crowned nobody and is walked PAST, never read as "no curse".
    assert parent_selection_bias(
        [won(se=0.40, electable=6), held(), won(se=0.10, electable=2), held()]
    ) == pytest.approx(0.10 / math.sqrt(math.pi), abs=2e-5)
    assert parent_selection_bias([held(), held()]) == 0.0
    assert parent_selection_bias([]) == 0.0
    assert parent_selection_bias([won(se=None, electable=4)]) == 0.0

    # The credit reorders admitted arms; it never admits one.
    ruler = _ruler(dict.fromkeys(range(28), 0.0))
    parent = sheet(measurements([1.0] * 15 + [0.0] * 13))
    trailing = sheet(measurements([1.0] * 13 + [0.0] * 15))
    leading = sheet(measurements([1.0] * 17 + [0.0] * 11))
    tie = sheet(measurements([1.0] * 15 + [0.0] * 13))
    assert theta_lift_over_parent(candidate_abilities({"c": trailing}, parent, ruler), "c") < 0.0

    def elect(arm: CellSheet, bias: float) -> str:
        return elect_round_winner(["c"], {"c": arm}, parent, 6, ruler, parent_bias=bias)[0]

    big = parent_selection_bias([won(se=5.0, electable=6)])
    for bias in (0.0, big):
        assert elect(trailing, bias) == "", "a trailing arm won on the credit"
        assert elect(tie, bias) == "", "a tie won on the credit"
        assert elect(leading, bias) == "c"


def test_leader_eligibility_bars_invalid_measurement_not_stops():
    fatal = scored_candidate(
        "C1.3",
        accuracy=0.8333,
        outcome=ArmOutcome.BROKEN,
        degradation_context={"fatal": True, "dominant_warning": "llm_only:empty_response"},
    )
    # A STOP IS NOT A VERDICT: "more samples will not change the answer" is a budget fact.
    pobb_stopped = scored_candidate(
        "C1.2",
        accuracy=0.40,
        outcome=ArmOutcome.ELIMINATED,
        elimination_context={"p_best": 0.048, "epsilon": 0.05, "gate": "epsilon"},
    )
    leader_locked = scored_candidate(
        "C1.1",
        accuracy=0.55,
        outcome=ArmOutcome.LOCKED_IN,
        elimination_context={"p_best": 0.96, "gate": "lock_in"},
    )
    clean_loser = scored_candidate("C1.4", accuracy=0.45)

    assert not is_leader_eligible(fatal)
    assert is_leader_eligible(pobb_stopped)
    assert is_leader_eligible(leader_locked)
    assert is_leader_eligible(clean_loser)


def _peer_cycle(
    built_stores: Any,
    tmp_path: Path,
    monkeypatch: Any,
    optimizer: str,
    nodes: dict,
    solves: Callable[[str, Sample], bool | None],
    *,
    bench: int = 0,
    origin_composite: float = 0.0,
    **optimization: Any,
) -> Cycle:
    """The backend answers a cell right where ``solves(prompt, sample)`` says, errors on ``None``."""
    bank = [Sample(id=i, query=f"q{i}", ground_truth="a") for i in range(16)]
    schema = pipeline_schema(
        name=f"{optimizer}-e2e",
        nodes=[PipelineNode(name="solve", tunes_llm=False, prompt_info=NodePromptInfo())],
    )
    config = load_campaign_config(
        {
            "dataset_name": f"{optimizer}-e2e",
            "dataset_split": {"bench": bench, "demo": 2},
            "optimization": {
                "optimizer": optimizer,
                "degradation_threshold": 0.0,
                "elimination_n_min": 2,
                "nodes": nodes,
                **optimization,
            },
        }
    )
    session = loop_session(built_stores, schema, bank)
    session.state.ledger = CycleEventLog.open(CycleDir(tmp_path / "cycle"))
    # The campaign's composite REWARDS length, the opposite of CAPO's objective.
    session.scoring.scorer = compile_scorer(
        "label_match(predicted, ground_truth)",
        "fitness * min(1.0, target_prompt_chars / 200.0)",
        verifier_graded=False,
    )
    session.scoring.partition = partition_bank(bank, config.dataset_split)

    async def _measure(sample: Sample, _session: Any, *, pipeline_params: Any) -> MeasuredCell:
        prompt = pipeline_params["solve"]["prompt"]
        solved = solves(prompt, sample)
        row: dict[str, Any] = {
            "sample_id": sample.id,
            "sample_key": sample.key,
            "query": sample.query,
            "ground_truth": sample.ground_truth,
            "predicted": sample.ground_truth if solved else "wrong",
            "error": None,
            "cached": False,
            "pipeline_data": {"total_time": 0.1, "target_prompt_chars": len(prompt)},
        }
        if solved is None:
            row["error_category"] = "SERVER"
        return MeasuredCell.from_wire(row)

    monkeypatch.setattr(query_loop, "measure_sample", _measure)
    origin = OptSearchPoint(
        instruction="Answer.", answer_format="Reply with the letter."
    ).configured(None, schema)
    framing = TaskDecomposition(pipeline_purpose="Answer each query with its letter.")
    search = list(session.scoring.partition.search)
    origin_rows = [
        asyncio.run(_measure(s, session, pipeline_params={"solve": {"prompt": "Answer."}}))
        for s in search[:4]
    ]
    sp = origin.to_job_search_point(
        schema=schema,
        framing=framing,
        demo=session.scoring.require_partition().demo,
    )
    cycle = Cycle.start(
        origin,
        scored_candidate(
            origin.id,
            label="C0",
            accuracy=0.0,
            composite_fitness=origin_composite,
            prompt_fields=origin.prompt_field_dict(),
            resolved_pipeline_params=sp.config_params,
            sp_hash=sp.sp_hash(schema),
        ),
        schema=schema,
        framing=framing,
        origin_results=session.scoring.require_scorer().sheet(origin_rows),
        session=session,
        config=config,
    )
    bind_optimizer(cycle.optimizer)
    return cycle


_noop = lambda *_a, **_k: None  # noqa: E731


def _round_closed(
    round_num: int,
    *,
    improved: bool,
    electable_count: int = 2,
    **reading: Any,
) -> RoundClosedRecord:
    return RoundClosedRecord.of(
        round_result(
            round_num,
            improved=improved,
            electable_count=electable_count,
            all_candidate_results={},
            **reading,
        )
    )


def _line_read(
    round_num: int,
    improved: bool,
    *picks: tuple[str, list[float | None]],
    c0: list[float | None],
    ids: dict[str, str] | None = None,
) -> OverlapReading:
    """A grade of ``None`` is a panel cell that member's backend never answered."""
    bank = [Sample(id=i, query=f"q{i}", ground_truth="t") for i in range(len(c0))]
    hop = CycleHop(campaign_id="camp", cycle_id="cyc")

    def member(label: str, grades: list[float | None]) -> MemberRows:
        rnd = int(label[1:].split(".")[0])
        arm = ArmPointer(round=rnd, label=label, candidate_id=(ids or {}).get(label, label.lower()))
        return _pair_member(
            arm.candidate_id,
            [
                measurement(i, g, **({} if g is not None else {"error_category": "SERVER"}))
                for i, g in enumerate(grades)
            ],
            bank,
            scope=RoleScope.REPORT,
            address=MemberAddress(
                path=(hop,), individual_id=arm.candidate_id, arm=arm, pass_role=None
            ),
        )

    readings = [
        _read_pair(
            member("C0", c0),
            member(label, grades),
            bank,
            scope=RoleScope.REPORT,
            measurands=grade_measurands("s"),
            spec=ROUND_LIFT_SPEC,
        )
        for label, grades in picks
    ]
    return OverlapReading.of(
        round_num, improved, sample_ids=range(len(c0)), lead=readings[-1], earlier=readings[:-1]
    )


# Eight panel cells C0 misses and one it takes: a pick clean on all nine clears zero over it.
_C0_PANEL: list[float | None] = [0.0] * 8 + [1.0]
_CLEAN_PANEL: list[float | None] = [1.0] * 9


_QUIET_CALLBACKS = types.SimpleNamespace(
    view_context=ViewContext(),
    on_phase=_noop,
    announce_candidate=_noop,
    on_candidate_scored=_noop,
    on_race_standing=_noop,
    on_race_catch_up=_noop,
    on_election=_noop,
)


@pytest.mark.parametrize(
    ("cut_round", "budget", "replayed", "parent_cells"), [(1, 17, 0, 2), (2, 5, 12, 4)]
)
def test_a_round_the_budget_cuts_elects_on_the_cells_it_paid_for(
    built_stores,
    tmp_path,
    monkeypatch,
    cut_round: int,
    budget: int,
    replayed: int,
    parent_cells: int,
) -> None:
    """Round 1's parent is the origin: read on the cells run init banked, never bought a cell."""
    minted: list[str] = []

    async def _llm(messages: list[dict], **_kw: Any) -> Any:
        if "Create overall 15 prompts" in messages[0]["content"]:
            text = json.dumps(["Solve the task, A.", "Solve the task, B.", "Solve the task, C."])
        else:
            minted.append(f"Go {len(minted)}.")
            text = f"<prompt>{minted[-1]}</prompt>"
        return types.SimpleNamespace(content=text)

    nodes = {
        "blocks": {"config": {"block_size": 4, "max_blocks": 3}},
        "capo_init": {"config": {"size": 3, "k_max": 0}},
        "mating": {"config": {"offspring": 2}},
        "few_shot": {"config": {"k_max": 0}},
        "population": {"config": {"size": 3}},
    }
    paid: list[int] = []
    book = spend_book(None, usd_reserve=None)

    # Empty while `_peer_cycle` measures the origin, which no round's budget pays for.
    running: list[bool] = []

    def even_cells(_prompt: str, sample: Sample) -> bool:
        if not running:
            return sample.id % 2 == 0
        paid.append(sample.id)
        if session.control.book is book and len(paid) >= budget:
            book.declared = SpendCeilings(0.0, None)
        return sample.id % 2 == 0

    cycle = _peer_cycle(built_stores, tmp_path, monkeypatch, "capo", nodes, even_cells)
    monkeypatch.setattr(node_context, "llm_call", _llm)
    session = cycle.session
    running.append(True)
    # A backend to file under, so the kept population's cells replay and only offspring are bought.
    session.backend_id = "capo-e2e"
    search = list(session.scoring.require_partition().search)
    if cut_round == 1:
        # Run init scores the origin through the gateway — here on half of CAPO's first block.
        asyncio.run(
            score_search_point(
                cycle.tracking.current_sp,
                search[:2],
                session,
                label="origin",
                measured=None,
            )
        )
    else:
        _, cut = asyncio.run(execute_round(cycle, 1, search, _QUIET_CALLBACKS))  # type: ignore[arg-type]
        assert cut is None

    paid.clear()

    session.control = RunControl(book=book)
    closed, cut = asyncio.run(execute_round(cycle, cut_round, search, _QUIET_CALLBACKS))  # type: ignore[arg-type]
    assert cut is StopReason.SPEND_BUDGET
    # The controller routes on this round before anything closes it: health rides it as built.
    assert closed.health is not None and closed.health.samples == len(closed.results)
    rows = closed.all_candidate_results
    assert sum(len(arm) for arm in rows.values()) == replayed + len(paid), "a cell nobody elects on"
    [reference] = closed.reference_results.values()
    assert len(reference) == parent_cells, "the parent is read where the archive holds it"
    [short] = [cs for cs in closed.candidate_scores if len(rows[cs.candidate_id]) == 1]
    assert short.accuracy == 1.0, "the cut arm reads perfect on its one cell"
    carried = [ind.id for ind in closed.optimizer_state.population]
    assert len(carried) == 3 and short.candidate_id not in carried, "elected below the floor"
    full = next(cs for cs in closed.candidate_scores[3:] if cs is not short)
    assert full.candidate_id in carried, "an offspring the budget paid for in full was dropped"


class _ComposedPayload(RoundPayload, manifest="ga-composition"):
    """The payload of a manifest no package ships: it keeps nothing of its own."""


def test_one_round_composes_a_peers_crossover_with_potters_racing_selector(
    built_stores, tmp_path, monkeypatch
) -> None:
    import yaml

    from promptpotter.application import optimizers
    from promptpotter.application.optimizers import nodes as node_contract
    from promptpotter.application.optimizers import paper_templates
    from promptpotter.application.optimizers.capo import prompts
    from promptpotter.config.paths import optimizers_root

    name = "ga-composition"
    capo = yaml.safe_load((optimizers_root() / "capo" / "pipeline.yaml").read_text("utf-8"))
    potter = yaml.safe_load((optimizers_root() / "potter" / "pipeline.yaml").read_text("utf-8"))
    seed = ["capo_init"]
    walk = ["blocks", "mating", "capo_crossover", "pobb", "score", "theta_election"]
    manifest_dir = tmp_path / name
    manifest_dir.mkdir()
    (manifest_dir / "resolved_schemas.json").write_text("{}", "utf-8")
    (manifest_dir / "pipeline.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "version": "v0",
                "available_models": capo["available_models"],
                "nodes": {n: (capo["nodes"] | potter["nodes"])[n] for n in (*seed, *walk)},
                "pipelines": {"default": walk, "initial_population": seed},
                "resolved_prompts": {
                    key: capo["resolved_prompts"][key]
                    for key in ("capo_init/1", "capo_crossover/1")
                },
            }
        ),
        "utf-8",
    )

    class Composed(node_contract.OptimizerRuntime):
        prompt_sources = (paper_templates, prompts)

        def start(self, *_a: Any) -> Any:
            return node_contract.BankedState(_ComposedPayload())

        def arms(self, selected: Any) -> int:
            return 2

    Composed.name, Composed.manifest_dir = name, manifest_dir
    shipped, origins = optimizers._load_runtimes()
    monkeypatch.setattr(
        optimizers, "_load_runtimes", lambda: ({**shipped, name: Composed()}, origins)
    )

    replies = iter(["Merged.", "Other."])

    async def _llm(messages: list[dict], **_kw: Any) -> Any:
        asked = messages[0]["content"]
        if "Create overall 15 prompts" in asked:
            return types.SimpleNamespace(content=json.dumps(["Solve it, A.", "Solve it, B."]))
        assert "Solve it, A." in asked and "Solve it, B." in asked
        return types.SimpleNamespace(content=f"<prompt>{next(replies)}</prompt>")

    nodes = {
        "blocks": {"config": {"block_size": 4, "max_blocks": 3}},
        "capo_init": {"config": {"size": 2, "k_max": 0}},
        "mating": {"config": {"offspring": 2}},
    }
    cycle = _peer_cycle(
        built_stores, tmp_path, monkeypatch, name, nodes, lambda prompt, _s: "Merged." in prompt
    )
    monkeypatch.setattr(node_context, "llm_call", _llm)
    session = cycle.session
    session.backend_id = "composition-e2e"
    assert not cycle.population
    search = list(session.scoring.require_partition().search)
    closed, cut = asyncio.run(execute_round(cycle, 1, search, _QUIET_CALLBACKS))  # type: ignore[arg-type]
    assert cut is None

    minted = [r for _, r in session.state.ledger.iter() if isinstance(r, CandidateMintedRecord)]
    assert {r.candidate_id for r in minted} == {cs.candidate_id for cs in closed.candidate_scores}
    for record in minted:
        assert [(made.node, made.mode) for made in record.lineage.variations] == [
            (f"{name}:mating", "deterministic"),
            (f"{name}:capo_crossover", "llm"),
        ]
        assert len(set(record.lineage.parent_ids)) == 2
    [winner] = closed.selected_labels
    crowned = next(cs for cs in closed.candidate_scores if cs.label == winner)
    assert "Merged." in crowned.prompt_fields["instruction"]
    assert [ind.id for ind in closed.optimizer_state.population] == [crowned.candidate_id], (
        "the individual the next round mutates from is not the population the run carries"
    )


def test_capos_split_nodes_make_the_children_its_one_crossover_made_for_the_same_draws(
    built_stores, tmp_path, monkeypatch
) -> None:
    """Handed ONE stream in walk order, the three nodes make the offspring one node would."""
    from promptpotter.application.optimizers.capo.members import cross_shots, mutate_shots

    offspring, k_max = 4, 2
    initial: list[OptSearchPoint] = []
    asked = 0

    async def _llm(messages: list[dict], **_kw: Any) -> Any:
        nonlocal asked
        if "Create overall 15 prompts" in messages[0]["content"]:
            listed = ["Solve the task, A.", "Solve the task, B.", "Solve the task, C."]
            return types.SimpleNamespace(content=json.dumps(listed))
        if not initial:
            initial.extend(cycle.population)
        asked += 1
        return types.SimpleNamespace(content=f"<prompt>Go {asked}.</prompt>")

    stream = random.Random(7)
    seeded = node_context.NodeContext.rng

    def _rng(self: Any, tag: str = "") -> random.Random:
        if tag.endswith(":request"):
            return seeded(self, tag)
        if self.node in ("mating", "shot_crossover"):
            return stream
        return random.Random(f"{self.node}{tag}")

    nodes = {
        "blocks": {"config": {"block_size": 4, "max_blocks": 3}},
        "capo_init": {"config": {"size": 3, "k_max": k_max}},
        "mating": {"config": {"offspring": offspring}},
        "few_shot": {"config": {"k_max": k_max}},
        "population": {"config": {"size": 3}},
    }
    cycle = _peer_cycle(
        built_stores, tmp_path, monkeypatch, "capo", nodes, lambda _p, s: s.id % 2 == 0
    )
    monkeypatch.setattr(node_context, "llm_call", _llm)
    monkeypatch.setattr(node_context.NodeContext, "rng", _rng)
    session = cycle.session
    session.backend_id = "capo-e2e"
    search = list(session.scoring.require_partition().search)
    closed, cut = asyncio.run(execute_round(cycle, 1, search, _QUIET_CALLBACKS))  # type: ignore[arg-type]
    assert cut is None
    assert any(ind.shot_ids for ind in initial), "no parent carries a shot to recombine"

    one = random.Random(7)
    pairs = [one.sample(initial, 2) for _ in range(offspring)]
    crossed = [cross_shots([a.shot_ids, b.shot_ids], rng=one) for a, b in pairs]
    pool = [s.id for s in session.scoring.require_partition().demo]
    expected = [
        (
            [a.id, b.id],
            mutate_shots(shots, pool, k_max=k_max, rng=random.Random(f"few_shot:{i}")),
        )
        for i, ((a, b), shots) in enumerate(zip(pairs, crossed, strict=True))
    ]
    parents = {
        r.candidate_id: r.lineage.parent_ids
        for _, r in session.state.ledger.iter()
        if isinstance(r, CandidateMintedRecord)
    }
    made = [
        (parents[cs.candidate_id], list(cs.prompt_fields.get("shot_ids", [])))
        for cs in closed.candidate_scores
        if cs.candidate_id not in {ind.id for ind in initial}
    ]
    assert asked == 2 * offspring, "a child was never merged or never rephrased"
    assert sorted(made) == sorted(expected)


def test_the_bench_grades_the_pick_the_optimizer_declared_over_a_higher_composite_round(
    built_stores, tmp_path, monkeypatch
) -> None:

    async def _llm(messages: list[dict], **_kw: Any) -> Any:
        if "Create overall 15 prompts" in messages[0]["content"]:
            return types.SimpleNamespace(content=json.dumps(["Solve it GOOD.", "Solve it OK."]))
        return types.SimpleNamespace(content="<prompt>Solve it BAD.</prompt>")

    nodes = {
        "blocks": {"config": {"block_size": 4, "max_blocks": 2}},
        "capo_init": {"config": {"size": 2, "k_max": 0}},
        "mating": {"config": {"offspring": 1}},
        "few_shot": {"config": {"k_max": 0}},
        "population": {"config": {"size": 2}},
    }
    solves = lambda prompt, s: "GOOD" in prompt or ("OK" in prompt and s.id % 2 == 0)  # noqa: E731
    cycle = _peer_cycle(
        built_stores, tmp_path, monkeypatch, "capo", nodes, solves, bench=4, origin_composite=0.9
    )
    monkeypatch.setattr(node_context, "llm_call", _llm)
    session = cycle.session
    search = list(session.scoring.require_partition().search)
    picked = asyncio.run(execute_round(cycle, 1, search, _QUIET_CALLBACKS))[0]  # type: ignore[arg-type]
    assert (
        picked.selected_labels and picked.composite_fitness < cycle.origin_round.composite_fitness
    )
    from promptpotter.application.runner.termination import target_tripped

    assert picked.accuracy is not None and picked.accuracy > 0.0
    assert target_tripped(cycle, picked.accuracy) is StopReason.TARGET_HIT, (
        "the target stop reads the declared pick, not the round of the composite high-water"
    )

    origin = cycle.origin_round.opt_sp
    assert origin is not None
    # The headline is read off the archive, so the passes file theirs.
    session.backend_id = "capo-e2e"
    origin_pass = asyncio.run(
        score_on_bench(
            session,
            cycle.searchpoint(origin.id),
            individual_id=origin.id,
            subject="origin",
            label="C0",
            round_num=0,
            cb=_QUIET_CALLBACKS,  # type: ignore[arg-type]
        )
    )

    def banked(origin_pass: BenchPass, *held: BenchPass, tolerance: int = 0) -> BenchPasses:
        return BenchPasses(
            tolerance=tolerance,
            origin=origin_pass,
            reserve_usd=0.0,
            reserve_tokens=0,
            selections={taken.round: taken for taken in held},
        )

    def graded(line: BenchPasses) -> BenchPasses:
        return asyncio.run(
            bench_selection(
                session,
                cycle.rounds,
                framing=cycle.framing,
                banked=line,
                cb=_QUIET_CALLBACKS,  # type: ignore[arg-type]
            )
        )

    assert picked.opt_sp is not None
    stands_on = ArmPointer(round=1, label=picked.selected_labels[0], candidate_id=picked.opt_sp.id)

    def read(
        line: BenchPasses | None,
        *,
        trigger: BenchTrigger = "at_end",
        ending: StopReason | None = StopReason.MAX_ROUNDS,
        selecting: bool = False,
        held_by: str | None = None,
        selection: ArmPointer = stands_on,
    ) -> BenchScore:
        return read_bench(
            session.store,
            line,
            session.scoring.require_scorer(),
            line=BenchLine(
                hop=session.hop,
                on_line=held_by is None,
                held_by=held_by,
                trigger=trigger,
                held_out=4,
                instrument_id="capo-e2e",
                run=LineRun(
                    selecting=selecting, ending=ending, selection=selection, rounds_closed=1
                ),
                spend=None,
            ),
        )

    unasked = read(None, trigger="manual")
    assert (unasked.origin, unasked.selected) == (None, None)
    assert (unasked.bench_size, unasked.vs_origin.headline) == (4, None)
    assert (unasked.status.state, unasked.status.can_grade) == (ReadingState.NOT_ASKED, True)
    assert [
        (s.state, s.can_grade)
        for s in (
            read(None, ending=None, selecting=True).status,
            read(None, ending=StopReason.CRASHED).status,
            read(None).status,
            read(None, held_by="cycle_holding_the_line").status,
        )
    ] == [
        (ReadingState.PENDING, False),
        (ReadingState.RUN_FAILED, True),
        (ReadingState.NOT_ASKED, True),
        (ReadingState.HELD_ELSEWHERE, False),
    ]

    passes = graded(banked(origin_pass))
    [taken] = passes.selections.values()
    assert graded(passes) is passes
    left = taken.model_copy(update={"sp_hash": "a pick the line left"})
    resent = graded(banked(origin_pass, left)).selections[1]
    assert all(replayed for *_, replayed in resent.cells)
    assert [c[:3] for c in resent.cells] == [c[:3] for c in taken.cells]
    assert (origin_pass.reads_before, taken.reads_before, resent.reads_before) == (0, 1, 2)
    # A stop inside this pick's pass ends it short and erases no other; only a pause escapes.
    from promptpotter.application.runner import bench as bench_runner
    from promptpotter.domain.phases import StopLoop

    earlier = taken.model_copy(update={"round": 7, "candidate_id": "an earlier pick"})
    sending = bench_runner.score_on_bench

    def _stops_on(reason: StopReason) -> None:
        async def _stopped(*_a: Any, **_k: Any) -> BenchPass:
            raise StopLoop(reason)

        monkeypatch.setattr(bench_runner, "score_on_bench", _stopped)

    _stops_on(StopReason.SPEND_BUDGET)
    short = graded(banked(origin_pass, left, earlier))
    assert short.selections[7] is earlier
    assert short.selections[1].stop is not None
    assert short.selections[1].stop.cause is StopReason.SPEND_BUDGET
    assert (read(short).status.state, read(short).status.subject) == (
        ReadingState.PASS_STOPPED,
        "selected",
    )
    _stops_on(StopReason.PAUSED)
    with pytest.raises(StopLoop):
        graded(banked(origin_pass, left, earlier))
    monkeypatch.setattr(bench_runner, "score_on_bench", sending)
    bench = read(passes)
    assert bench.status.reads_before == taken.reads_before
    unread_row = taken.model_copy(update={"cells": taken.cells[:-1]})
    past = read(banked(origin_pass, unread_row))
    assert (past.status.state, past.selected) == (ReadingState.PAST_TOLERANCE, None)
    assert read(banked(origin_pass, unread_row, tolerance=1)).status.state is ReadingState.READ
    assert bench.selected is not None and bench.origin is not None
    assert (bench.selected.round, bench.selected.sp_hash) == (1, picked.selected_scores[0].sp_hash)
    assert (bench.selected.accuracy.value, bench.origin.accuracy.value) == (1.0, 0.0)
    lift = bench.vs_origin.headline
    assert lift is not None and lift.estimate.value == 1.0, (
        "GOOD solves them all, and the headline is accuracy"
    )
    assert (bench.status.state, bench.status.can_grade) == (ReadingState.READ, False)
    alone = read(
        passes,
        selection=ArmPointer(round=0, label="C0", candidate_id=origin_pass.candidate_id),
    )
    assert (alone.vs_origin.state, alone.vs_origin.headline) == (
        ReadingState.SAME_INDIVIDUAL,
        None,
    )
    # The composite's misses keep a cost share, so its lift is not the accuracy gap.
    for reading in (bench.origin, bench.selected):
        assert reading.composite.ci_lo <= reading.composite.value <= reading.composite.ci_hi
    composite_gap = bench.selected.composite.value - bench.origin.composite.value
    composite_lift = bench.lift("composite")
    assert composite_lift is not None and composite_gap < 1.0
    assert composite_lift.estimate.value == pytest.approx(composite_gap)
    result = _build_cycle_result(
        cycle,
        session,
        stop_reason=StopReason.MAX_ROUNDS,
        cycle_error=None,
        started_at="",
        finished_at="",
        spend=None,
        bench=bench,
        langfuse_trace_id=None,
    )
    assert result.result_round == bench.selected.round, "the result names the round graded"
    # The round's own lift is ACCURACY's over the parent: its bar is the parent's accuracy.
    cells = [Sample(id=i, query=f"q{i}", ground_truth="t") for i in range(4)]
    reading = _read_pair(
        _pair_member("C0", measurements([1.0, 0.0, 1.0, 0.0]), cells),
        _pair_member("C1.1", measurements([1.0, 1.0, 1.0, 0.0]), cells),
        cells,
    )
    won = scored_candidate("C1.1", accuracy=0.75, composite_fitness=0.40, vs_reference=reading)
    m = build_prompt_export(
        round_result(1).model_copy(update={"candidate_scores": [won], "selected_labels": ["C1.1"]}),
        **dict.fromkeys(("tool_version", "campaign_id", "cycle_id", "dataset_name"), ""),
        **dict.fromkeys(("dataset_hash", "finished_at"), ""),
        stop_reason=StopReason.MAX_ROUNDS,
        treatment=None,
        formula=None,
        own=OwnLevel(accuracy=None, composite=None, n=0),
        framing=cycle.framing,
        demo=(),
        bench=bench,
    ).measurement
    assert m.vs_reference is not None and m.vs_reference.headline is not None
    assert (m.vs_reference.headline.estimate.value, m.vs_reference.headline.rate_a) == (0.25, 0.50)

    from promptpotter.shared.errors import ErrorCategory

    async def _refused(sample: Sample, _session: Any, *, pipeline_params: Any) -> MeasuredCell:
        return MeasuredCell(
            sample_id=sample.id,
            sample_key=sample.key,
            query=sample.query,
            ground_truth=sample.ground_truth or "",
            predicted="ERROR",
            error="HTTP 403 — caller config rejected by backend :: model is not allowed",
            error_category=ErrorCategory.CLIENT,
        )

    answering = query_loop.measure_sample
    monkeypatch.setattr(query_loop, "measure_sample", _refused)
    graded_hashes = {bench.selected.sp_hash, bench.origin.sp_hash}
    unread = next(cs for cs in picked.candidate_scores if cs.sp_hash not in graded_hashes)
    refused = asyncio.run(
        score_on_bench(
            session,
            cycle.searchpoint(unread.candidate_id),
            individual_id=unread.candidate_id,
            subject="selected",
            label=unread.label,
            round_num=1,
            cb=_QUIET_CALLBACKS,  # type: ignore[arg-type]
        )
    )
    assert refused.stop is not None
    monkeypatch.setattr(query_loop, "measure_sample", answering)
    cut = read(graded(banked(refused)))
    assert (cut.origin, cut.selected) == (None, bench.selected)
    assert (cut.vs_origin.headline, cut.cost.lift_per_usd) == (None, None)
    assert (cut.status.state, cut.status.subject) == (ReadingState.PASS_STOPPED, "origin")
    assert cut.status.can_grade and "model is not allowed" in cut.status.sentence


def test_a_held_round_leads_on_the_arm_nearest_the_bar_admission_reads() -> None:
    """The lead is named on the read admission uses: the raw θ lift, over the readable arms."""
    from promptpotter.application.scoring.selection import (
        closest_to_bar,
        elect_round_winner,
        readable_lifts,
    )

    ruler = _ruler(dict.fromkeys(range(28), 0.0))
    parent = sheet(measurements([1.0] * 20 + [0.0] * 8))
    arms = {
        "near": sheet(measurements([1.0] * 5 + [0.0] * 3)),  # closest to the bar, on a wide SE
        "deep": sheet(measurements([1.0] * 18 + [0.0] * 10)),  # further off, on the whole panel
        "thin": sheet(measurements([1.0] * 3)),  # the highest θ in the round, under the floor
    }
    args = (list(arms), arms, parent, 6, ruler)
    winner_id, abilities = elect_round_winner(*args, parent_bias=0.3)
    reads = readable_lifts(list(arms), arms, parent, 6, abilities, 0.3)

    assert winner_id == ""
    assert abilities.parent is not None and abilities.theta["thin"] > abilities.parent[0]
    assert set(reads) == {"near", "deep"}
    assert reads["deep"][0] < reads["near"][0] <= 0.0
    assert sum(reads["deep"]) > sum(reads["near"])  # the credit orders them the other way
    assert closest_to_bar(reads) == "near"
    assert closest_to_bar(reads, beside="near") == "deep"


# 4. Elimination — who is cut, and when


def test_pobb_epsilon_ramps_in_and_an_arm_behind_is_cut_to_the_last_cell():
    """The depths come from `fit_theta_given_delta`'s dispersion rule: re-derive if it changes."""
    cfg = pobb_knobs(epsilon=0.30, epsilon_floor=0.15)
    graded = PoBBCheck(cfg, n_min=6, n_samples=28, ruler=None)
    assert graded.epsilon_at(6) == pytest.approx(0.15)
    assert graded.epsilon_at(9) == pytest.approx(0.225)
    assert graded.epsilon_at(12) == pytest.approx(0.30)
    assert [graded.epsilon_at(n) for n in range(12, 29)] == [pytest.approx(0.30)] * 17

    flat = PoBBCheck(
        pobb_knobs(epsilon=0.30, epsilon_floor=0.30), n_min=6, n_samples=28, ruler=None
    )
    assert flat.epsilon_at(6) == pytest.approx(0.30)
    # The manifest sits floor and ε on the same value, so an untouched campaign never grades.
    shipped = PoBBCheck(pobb_knobs(), n_min=6, n_samples=28, ruler=None)
    assert shipped.epsilon_at(shipped.n_min) == pytest.approx(shipped.epsilon)

    def arm_behind_perfect_prior(n: int, misses: int):
        check = PoBBCheck(cfg, n_min=6, n_samples=28, ruler=None)
        check.register_completed(sheet(measurements([1.0] * 28)), candidate_id="winner")
        check.set_current("arm")
        return check.check(sheet(measurements([0.0] * misses + [1.0] * (n - misses))).cells)

    # A single adverse cell caps p_best at 0.25 (`sign_posterior`), so the ramp below it spares it.
    assert arm_behind_perfect_prior(6, 1) is None
    assert arm_behind_perfect_prior(9, 1) is None
    cut = arm_behind_perfect_prior(9, 2)
    assert cut is not None
    # The decision archives the bar that FIRED (the ramped 0.225), never the configured 0.30.
    assert cut.check_result["epsilon"] == pytest.approx(0.225)
    # Two behind is still cut at the floor: the reprieve is for a width, not for a loser.
    assert arm_behind_perfect_prior(6, 2) is not None
    late = arm_behind_perfect_prior(26, 4)
    assert late is not None and late.outcome is ArmOutcome.ELIMINATED
    assert late.check_result["epsilon"] == pytest.approx(0.30)

    unpaired = PoBBCheck(pobb_knobs(epsilon=0.05), n_min=4, n_samples=20, ruler=None)
    unpaired.register_completed(
        sheet(measurements([1.0] * 8, sample_ids=list(range(8)))), candidate_id="lucky_leader"
    )
    unpaired.set_current("challenger")
    challenger = sheet(measurements([0.0] * 5, sample_ids=[9, 12, 13, 14, 8]))
    assert unpaired.check(challenger.cells) is None


def test_the_collapse_gate_reads_the_answer_not_the_labels() -> None:
    """A verifier proves an answer wrong by an unsolved cell; alike and all-solved is NOT collapsed."""
    from promptpotter.shared.errors import ErrorCategory

    def rows(fitness: list[float], **over: Any) -> list[Any]:
        return [
            {"sample_id": i, "ground_truth": "", "predicted": "done", "fitness": f, **over}
            for i, f in enumerate(fitness)
        ]

    def cut(rs: list[Any]) -> Any:
        return PoBBCheck(pobb_knobs(), n_min=6, n_samples=28, ruler=None).check(sheet(rs).cells)

    signal = cut(rows([1.0, 0.0, 0.0, 1.0, 0.0, 0.0]))
    assert signal is not None, "a constant answerer must be cut at n_min with no labels to read"
    assert signal.check_result["gate"] == EliminationGate.COLLAPSED

    assert cut(rows([1.0] * 6)) is None, "one answer that solves every cell is not a collapse"
    assert cut(rows([1.0, 0.0, 0.0, 1.0, 0.0, 0.0], predicted="NO_RESULT")) is None, (
        "an agent that wrote no answer file answered nothing, which is not one answer"
    )
    assert cut(rows([0.0] * 6, error_category=ErrorCategory.PIPELINE)) is None, (
        "a backend that broke on every cell is not a verdict on the idea"
    )

    # A collapse cut returns before the posterior, so it carries no ε field.
    labelled = [
        {
            "sample_id": i,
            "fitness": 1.0 if i % 2 == 0 else 0.0,
            "predicted": "Uncertain",
            "ground_truth": "TRUE" if i % 2 == 0 else "FALSE",
        }
        for i in range(6)
    ]
    signal = cut(labelled)
    assert signal is not None and signal.check_result["gate"] == EliminationGate.COLLAPSED
    assert not {"p_best", "epsilon", "n_priors"} & signal.check_result.keys()


def test_elimination_p_best_discriminates_on_graded_backend() -> None:
    """Concordant cells move θ and say nothing about which arm is better: one must not reach ε."""
    from promptpotter.application.scoring.selection import elimination_p_best
    from promptpotter.domain.ruler import DeltaRuler

    sids = list(range(12))
    ruler = None  # cold ruler — flat δ, the common early-cycle case

    # Graded regime: a binarized `hit` would be all-0 and pin p_best at 0.5.
    strong = [0.66] * 12
    weak = [0.30] * 12
    p_best_strong, _ = elimination_p_best(strong, {"prior": weak}, sids, ruler)
    assert p_best_strong > 0.9, f"graded gate failed to discriminate: {p_best_strong}"
    p_best_weak, _ = elimination_p_best(weak, {"prior": strong}, sids, ruler)
    assert p_best_weak < 0.1
    p_best_tie, _ = elimination_p_best(weak, {"prior": list(weak)}, sids, ruler)
    assert abs(p_best_tie - 0.5) < 1e-9

    epsilon = pobb_knobs().epsilon
    prefix = list(range(6))
    # The probe is also the EASIEST cell in the prefix, which tempts a fit to read it as decisive.
    warm = DeltaRuler(
        delta={0: 2.90, 1: 3.01, 2: 2.66, 3: 1.39, 4: 2.85, 5: 2.95},
        delta_se=dict.fromkeys(prefix, 0.4),
        mu_delta=2.6,
        sigma_delta=0.6,
        sigma_theta=1.0,
        calibration_model="1PL",
        anchor_id="test-anchor",
    )
    candidate = [0.0] * 6  # the low-base-rate arm: nothing solved anywhere in the prefix

    thin_prior = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    p_thin, _ = elimination_p_best(candidate, {"prior": thin_prior}, prefix, warm)
    assert p_thin == pytest.approx(0.25), f"one adverse cell may not reach past 0.25: {p_thin}"
    assert p_thin > epsilon, "a one-cell verdict must not be cuttable (unbounded: .11)"

    wide_prior = [1.0, 0.0, 1.0, 1.0, 0.0, 0.0]
    p_wide, _ = elimination_p_best(candidate, {"prior": wide_prior}, prefix, warm)
    assert p_wide == pytest.approx(0.0625), f"three adverse cells support a cut: {p_wide}"
    assert p_wide < epsilon, "the width is there — ε decides, as it always did"


def test_a_fatal_row_ends_a_candidate_on_one_sighting_and_an_advisory_never_does() -> None:

    def warn(kind: str | None) -> dict:
        warning = {"step": "entity_profiling", "code": "json_validate_failed"}
        if kind is not None:
            warning["kind"] = kind
        return {"pipeline_data": {"diagnostics": {"warnings": [warning]}}}

    check = DegradationCheck(threshold=0.4, min_samples=3)

    sig = check.check(
        sheet([measurement(0, 1.0), {**measurement(1, 1.0), **warn("structural")}]).cells
    )
    assert sig is not None
    assert sig.check_result["fatal"] is True

    # An unstamped warning is advisory: the source stamp is the only structural signal.
    for kind in ("transient", None):
        advisory = sheet({**measurement(i, 1.0), **warn(kind)} for i in range(6))
        assert check.check(advisory.cells) is None

    assert check.check(sheet([measurement(0, 1.0), measurement(1, 0.0)]).cells) is None

    # The rate arm, with the fast path off so the threshold is what is under test.
    rated = DegradationCheck(threshold=0.4, min_samples=3, fatal_fastpath=False)
    fatal_row = {**measurement(0, 0.0), **warn("structural")}
    clean = [measurement(i, 1.0) for i in range(1, 6)]
    assert rated.check(sheet([fatal_row, *clean[:4]]).cells) is None  # 1/5 = 0.2, under the bar
    cut = rated.check(sheet([fatal_row, {**fatal_row, "sample_id": 9}, *clean[:2]]).cells)
    assert cut is not None and cut.check_result["degraded_rate"] == pytest.approx(0.5)


def test_unscoreable_cells_counts_holes_but_not_stops_or_deprecated_rows() -> None:
    """``scored_samples - total`` would count both a stop and a deprecated row; a HOLE is neither."""
    from promptpotter.application.bench.resume_and_fork.repair import repair_cut
    from promptpotter.domain.results import unscoreable_cells
    from promptpotter.domain.results_health import is_deprecated
    from promptpotter.domain.scoring import NO_RESULT
    from promptpotter.shared.errors import ErrorCategory

    def row(sample_id: int, **extra: Any) -> dict[str, Any]:
        return {"sample_id": sample_id, "predicted": "TRUE", **extra}

    holed = [row(i) for i in range(4)] + [
        row(4, predicted="ERROR", error_category="UNKNOWN", error="ran past its deadline"),
        row(5, predicted="ERROR", error_category="UNKNOWN", error="ran past its deadline"),
    ]
    assert unscoreable_cells(sheet(holed)) == 2

    assert unscoreable_cells(sheet(row(i) for i in range(5))) == 0
    assert unscoreable_cells([]) == 0

    # A fatal-classified transient with NO error_category: the retry left nothing extractable.
    deprecated = row(
        22,
        predicted=NO_RESULT,
        pipeline_data={
            "terminal_node": "llm_only",
            "diagnostics": {
                "warnings": [
                    {
                        "step": "llm_only",
                        "code": "content_empty",
                        "message": "finish_reason=error",
                        "kind": "transient",
                    }
                ]
            },
        },
    )
    assert is_deprecated(MeasuredCell.from_wire(deprecated)), (
        "fixture drift — this row must classify as deprecated"
    )
    assert unscoreable_cells(sheet([row(0), row(1), row(2), deprecated])) == 0, (
        "a classifier-deprecated sample was counted as a hole — the gate would halt a "
        "healthy cycle on a transient retry the loop already handles"
    )

    # A cell a declared bound CUT is settled, not a hole: the same bound cuts the next attempt.
    def _rows(category: ErrorCategory) -> list[dict[str, Any]]:
        return [
            measurement(0, 1.0),
            measurement(1, None, error="no verdict", error_category=category),
        ]

    halted = round_result(
        1, candidates_scored=1, all_candidate_results={"c0": sheet(_rows(ErrorCategory.HALTED))}
    )
    assert repair_cut([halted]).rounds == []

    # A cell that ran to its own end can answer differently on a re-measure.
    holed = round_result(
        1,
        candidates_scored=1,
        all_candidate_results={"c0": sheet(_rows(ErrorCategory.UNSCOREABLE))},
    )
    assert repair_cut([holed]).rounds == [1]


# 5. Escalation and stop — live, and folded back on resume


def test_a_theta_stall_verdict_must_clear_its_own_error() -> None:
    from promptpotter.application.optimizers.potter.escalation.rules import NextAction
    from promptpotter.application.optimizers.potter.escalation.state import EscalationFSM
    from promptpotter.application.optimizers.potter.knobs import EscalationLadder

    # (composite, θ, θ_se) per round: θ advances once for real (+0.467), then only by noise.
    live = [
        (0.4853, -0.1296, 0.216),
        (0.6248, 0.3376, 0.202),
        (0.6248, 0.3498, 0.198),
        (0.6248, 0.3498, 0.198),
    ]

    def ladder(*, with_se: bool) -> list[str]:
        fsm, actions = EscalationFSM(), []
        for comp, theta, se in live:
            ask = fsm.ask_l2_escalation(
                current_composite_fitness=comp,
                current_theta=theta,
                current_theta_se=se if with_se else None,
                escalation_ladder=EscalationLadder.FULL,
                l2_patience=2,
                l3_patience=1,
            )
            actions.append(str(ask.next_action))
            if ask.next_action != NextAction.FIRE_L2:
                break
            fsm.record_l2_fired(ask.l2)
        return actions

    assert ladder(with_se=True) == ["fire_l2", "fire_l2", "fire_l2", "fire_l3"]
    assert NextAction.FIRE_L3 not in ladder(with_se=False)

    # The SE bar applies on the θ scale only: a composite-scale verdict has no error term.
    assert EscalationFSM._improved(0.0, 0.0, 0.35, 0.34, 0.20)[0] is False
    assert EscalationFSM._improved(0.0, 0.0, 0.60, 0.34, 0.20)[0] is True
    assert EscalationFSM._improved(0.7, 0.6, None, None, 0.20) == (True, "composite")


def test_the_campaign_ends_only_where_the_objective_is_spent_and_the_round_resolved(
    built_stores, tmp_path, monkeypatch
) -> None:
    cycle = _peer_cycle(built_stores, tmp_path, monkeypatch, "capo", {}, lambda _p, _s: True)
    readings = {
        RoundAdvance.ADVANCED: _line_read(1, True, ("C1.1", _CLEAN_PANEL), c0=_C0_PANEL),
        RoundAdvance.NOT_SEPARATED: _line_read(
            1, True, ("C1.1", [1.0, 0.0] + [0.0] * 6 + [1.0]), c0=_C0_PANEL
        ),
        RoundAdvance.ADVANCED_UNPAIRED: OverlapReading.unpaired(ReadingState.NOT_HELD, 1, True),
        RoundAdvance.UNREAD: OverlapReading.unpaired(ReadingState.PASS_STOPPED, 1, True),
    }
    assert all(reading.advance is advance for advance, reading in readings.items())

    def outcome(objective: float, advance: RoundAdvance) -> StopReason | None:
        cycle.tracking.current_composite_fitness = objective
        cycle.rounds = [
            round_result(0, improved=False),
            round_result(1, improved=True, overlap=readings[advance]),
        ]
        return standing_tripped(cycle, RunStanding.opening(None))

    assert outcome(1.0, RoundAdvance.NOT_SEPARATED) is None
    assert outcome(1.0, RoundAdvance.ADVANCED_UNPAIRED) is None
    assert outcome(1.0, RoundAdvance.UNREAD) is None
    # 100% accuracy under a cost-aware objective: accuracy is spent, the objective is not.
    assert outcome(0.507, RoundAdvance.ADVANCED) is None
    assert outcome(1.0, RoundAdvance.ADVANCED) is StopReason.PERFECT


def test_the_bench_stops_a_peer_on_spent_lives_and_on_convergence(
    built_stores, tmp_path, monkeypatch
) -> None:
    cycle = _peer_cycle(
        built_stores,
        tmp_path,
        monkeypatch,
        "capo",
        {},
        lambda _p, _s: True,
        lives={"start": 2, "cap": 4},
        convergence_patience=3,
    )
    lives = cycle.config.optimization.lives
    assert lives is not None
    origin = round_result(
        0,
        improved=False,
        opt_sp=cycle.opt_sp,
        candidate_scores=[scored_candidate(cycle.opt_sp.id, label="C0")],
        candidates_scored=1,
        all_candidate_results={},
        selected_labels=["C0"],
    )

    def stop_after(*closes: tuple[bool, int]) -> tuple[int | None, StopReason | None]:
        cycle.rounds = [
            origin,
            *(
                round_result(n, improved=improved, electable_count=electable)
                for n, (improved, electable) in enumerate(closes, 1)
            ),
        ]
        standing = RunStanding.after(cycle.rounds, lives=lives.bank, spent=None)
        return standing.stalls_left, standing_tripped(cycle, standing)

    held, promoted, uncompared = (False, 2), (True, 2), (False, 0)
    assert stop_after(held) == (1, None)
    assert stop_after(held, held) == (0, StopReason.LIVES_EXHAUSTED)
    # A round no arm reached the election of is evidence about the proposer and costs no life.
    assert stop_after(held, uncompared) == (1, None)
    # The bank fills to its cap; the convergence clock ends the run with a life unspent.
    assert stop_after(promoted, promoted, promoted, held, held) == (2, None)
    assert stop_after(promoted, promoted, promoted, held, held, held) == (
        1,
        StopReason.CONVERGED,
    )


def test_the_l1_only_arm_can_reach_no_layer_above_it() -> None:
    """Over the WHOLE predicate space: a deferral that never fires in one run is no suppression."""
    from itertools import product

    from promptpotter.application.optimizers.potter.escalation.rules import (
        EscalationInputs,
        NextAction,
        decide_escalation,
    )
    from promptpotter.application.optimizers.potter.knobs import EscalationLadder

    grid = list(
        product(
            [0, 1, 5],  # l1_stall_count
            [0, 3],  # l1_patience
            [None, 0, 2],  # axes_with_positive_yield
            [False, True],  # l1_mandatory_breach
            [False, True],  # l1_zero_candidates
            [False, True],  # evidence_starved
        )
    )

    def actions(ladder: EscalationLadder) -> set[NextAction]:
        return {
            decide_escalation(
                EscalationInputs(
                    l1_stall_count=stall,
                    l1_patience=patience,
                    escalation_ladder=ladder,
                    axes_with_positive_yield=yield_axes,
                    l1_mandatory_breach=mandatory,
                    l1_zero_candidates=zero,
                    evidence_starved=starved,
                )
            ).next_action
            for stall, patience, yield_axes, mandatory, zero, starved in grid
        }

    assert actions(EscalationLadder.L1) == {NextAction.CONTINUE}
    # Not vacuous: the same states fire L2 on the full ladder.
    assert NextAction.FIRE_L2 in actions(EscalationLadder.FULL)


def test_a_heal_fire_spends_no_l3_patience() -> None:
    from promptpotter.application.optimizers.potter.escalation.rules import NextAction
    from promptpotter.application.optimizers.potter.escalation.state import (
        EscalationFSM,
        LadderAsk,
    )
    from promptpotter.application.optimizers.potter.knobs import EscalationLadder

    fsm = EscalationFSM()

    def stretch(theta: float, *, heal_first: bool) -> LadderAsk:
        asks: list[LadderAsk] = []
        while not asks or asks[-1].next_action is NextAction.FIRE_L2:
            ask = fsm.ask_l2_escalation(
                current_composite_fitness=0.5,
                current_theta=theta,
                current_theta_se=0.2,
                escalation_ladder=EscalationLadder.FULL,
                l2_patience=2,
                l3_patience=1,
            )
            asks.append(ask)
            if ask.next_action is NextAction.FIRE_L2:
                fsm.record_l2_fired(ask.l2)
                if heal_first and len(asks) == 1:
                    fsm.record_l3_fired(None)
        return asks[-1]

    # The heal wipes L2's counters; the gate then reached is L3's FIRST patience ask.
    gate = stretch(0.3, heal_first=True)
    assert gate.next_action is NextAction.FIRE_L3
    fsm.record_l3_fired(gate.l3)
    # A later heal at a higher reading must not re-base the comparison.
    gate = stretch(0.6, heal_first=True)
    assert gate.next_action is NextAction.FIRE_L3
    fsm.record_l3_fired(gate.l3)
    # And the patience it did not spend still binds a flat run.
    assert stretch(0.6, heal_first=False).next_action is NextAction.STOP_L3_PATIENCE


def test_the_l1_stall_and_the_lives_bank_read_the_same_rounds() -> None:
    from promptpotter.application.optimizers.potter.escalation.state import EscalationFSM
    from promptpotter.application.optimizers.potter.knobs import EscalationLadder

    # (improved, electable_count): a round where NOTHING reached the election costs no life.
    sequence = [(True, 2), (True, 2), (True, 2), (True, 2), (False, 0), (False, 2), (False, 2)]
    closes = [
        _round_closed(i, improved=improved, electable_count=electable)
        for i, (improved, electable) in enumerate(sequence, start=1)
    ]

    live = EscalationFSM()
    for close in closes:
        live.observe_round(
            advance=close.overlap.advance,
            l1_patience=99,
            escalation_ladder=EscalationLadder.FULL,
        )

    # Three trailing non-improving rounds, one of them uncompared — all three advance the stall.
    assert live.ladder.l1_stall_count == 3
    # A real ledger closes the origin TWICE (again when the ruler warms); neither moves the bank.
    origin = _round_closed(0, improved=False, electable_count=0)
    ledger = [origin, origin, *closes]
    assert [stalls_left(ledger[: 2 + n], (2, 4)) for n in range(8)] == [2, 3, 4, 4, 4, 4, 3, 2]
    drained = [_round_closed(i, improved=False) for i in (8, 9)]
    assert stalls_left([*ledger, *drained], (2, 4)) == 0

    # A round that crowned a winner and resolved NOTHING advances L1 patience.
    unresolved = EscalationFSM()
    tied = [1.0, 0.0] + [0.0] * 6 + [1.0]
    lines = [
        _line_read(1, True, ("C1.1", _CLEAN_PANEL), c0=_C0_PANEL),
        _line_read(2, True, ("C2.1", tied), c0=_C0_PANEL),
        _line_read(3, True, ("C3.1", tied), c0=_C0_PANEL),
    ]
    assert [line.advance for line in lines] == [
        RoundAdvance.ADVANCED,
        RoundAdvance.NOT_SEPARATED,
        RoundAdvance.NOT_SEPARATED,
    ]
    for line in lines:
        unresolved.observe_round(
            advance=line.advance,
            l1_patience=99,
            escalation_ladder=EscalationLadder.FULL,
        )
    assert unresolved.ladder.l1_stall_count == 2


def test_a_reading_that_failed_moves_the_stall_clock_neither_way() -> None:
    from promptpotter.application.optimizers.potter.escalation.state import EscalationFSM
    from promptpotter.application.optimizers.potter.knobs import EscalationLadder

    won = _line_read(1, True, ("C1.1", _CLEAN_PANEL), c0=_C0_PANEL)
    lead = won.lead
    stopped = OverlapReading.of(
        3,
        True,
        sample_ids=won.sample_ids,
        lead=absent_pair(
            state=ReadingState.PASS_STOPPED,
            a=lead.a,
            b=lead.b,
            cell_set=lead.cell_set,
            scope=lead.scope,
            spec=lead.spec,
            instrument_id=lead.instrument_id,
        ),
        earlier=(),
    )
    assert stopped.advance is RoundAdvance.UNREAD

    rounds = [
        round_result(0, improved=False),
        round_result(1, improved=True, overlap=won),
        round_result(2, improved=False),
        round_result(3, improved=True, overlap=stopped),
    ]
    # One held round since the advance; the unread round neither clears it nor adds to it.
    assert [rounds_without_advance(rounds[:n]) for n in (2, 3, 4)] == [0, 1, 1]
    assert rounds_without_advance([*rounds, round_result(4, improved=False)]) == 2

    live = EscalationFSM()
    for rr in rounds[1:]:
        live.observe_round(
            advance=rr.overlap.advance,
            l1_patience=99,
            escalation_ladder=EscalationLadder.FULL,
        )
    assert live.ladder.l1_stall_count == 1

    # One of the pick's nine panel cells errored: read on the eight both scored, and it decides.
    holed = _line_read(1, True, ("C1.1", [*_CLEAN_PANEL[:8], None]), c0=_C0_PANEL)
    assert holed.lead.state is ReadingState.READ
    assert holed.lead.coverage is not None and holed.lead.headline is not None
    assert (holed.lead.coverage.scored, holed.lead.coverage.excluded_faulted) == (8, 1)
    assert holed.lead.headline.estimate.side == "above"
    assert holed.advance is RoundAdvance.ADVANCED

    # Against an earlier pick the holed one is a rate over another exam: it tops nobody.
    earlier = ("C1.1", [1.0] * 4 + [0.0] * 5)
    short = _line_read(2, True, earlier, ("C2.1", [*_CLEAN_PANEL[:8], None]), c0=_C0_PANEL)
    whole = _line_read(2, True, earlier, ("C2.1", _CLEAN_PANEL), c0=_C0_PANEL)
    assert (short.advance, whole.advance) == (RoundAdvance.NOT_SEPARATED, RoundAdvance.ADVANCED)

    # No pair to read stands alone; a pair owed, refused or not landed is no evidence.
    alone, no_evidence = RoundAdvance.ADVANCED_UNPAIRED, RoundAdvance.UNREAD
    promoted_on = {
        ReadingState.READ: RoundAdvance.ADVANCED,
        ReadingState.NOT_ASKED: no_evidence,
        ReadingState.PENDING: no_evidence,
        ReadingState.NOT_HELD: alone,
        ReadingState.HELD_ELSEWHERE: alone,
        ReadingState.NO_SELECTION: alone,
        ReadingState.SAME_INDIVIDUAL: alone,
        ReadingState.PASS_STOPPED: no_evidence,
        ReadingState.PAST_TOLERANCE: no_evidence,
        ReadingState.MEMBER_UNSCOREABLE: no_evidence,
        ReadingState.UNDER_TWO_CELLS: no_evidence,
        ReadingState.RUN_FAILED: no_evidence,
        ReadingState.SCOPE_DIFFERS: no_evidence,
        ReadingState.MEASURAND_DIFFERS: no_evidence,
        ReadingState.DATASET_DIFFERS: no_evidence,
        ReadingState.CELL_SET_DIFFERS: no_evidence,
        ReadingState.INSTRUMENT_DIFFERS: no_evidence,
    }
    assert promoted_on.keys() == set(ReadingState)
    for state, advance in promoted_on.items():
        pair = lead if state is ReadingState.READ else PairedReading.unread(state)
        assert round_advance(1, True, pair, ()) is advance, state
        assert round_advance(1, False, pair, ()) is RoundAdvance.HELD, state
        assert round_advance(0, True, pair, ()) is RoundAdvance.ORIGIN, state


def test_a_landed_fire_moves_the_ladder_and_an_unlanded_one_moves_nothing() -> None:
    from promptpotter.application.optimizers.potter.escalation.state import (
        EscalationFSM,
        LadderAsk,
    )
    from promptpotter.application.optimizers.potter.knobs import EscalationLadder

    def snapshot(f: EscalationFSM) -> tuple[int, int, float | None, int, int, float | None, int]:
        held = f.ladder
        return (
            held.l2.fires,
            held.l2.stall_count,
            held.l2.best_composite_fitness_at_entry,
            held.l3.fires,
            held.l3.stall_count,
            held.l3.best_composite_fitness_at_entry,
            held.l1_stall_count,
        )

    live = EscalationFSM()
    live_trace = []

    def fired() -> None:
        live_trace.append(snapshot(live))

    def ask(fitness: float) -> LadderAsk:
        return live.ask_l2_escalation(
            current_composite_fitness=fitness,
            escalation_ladder=EscalationLadder.FULL,
            l2_patience=3,
            l3_patience=2,
        )

    live.record_l2_fired(ask(0.60).l2)
    fired()
    # A second ask at an unimproved fitness reads a stall, which the fire that lands commits.
    live.record_l2_fired(ask(0.60).l2)
    fired()
    # An unparseable fire adopts nothing — the stall its ask read included.
    unlanded = ask(0.60)
    assert unlanded.l2.stall_count == 2
    # The prompt that fire composed still reported the verdict that asked for it.
    assert live.as_read_by(unlanded).ladder.l2.stall_count == 2
    fired()
    # L3 firing wipes L2's progress. A heal first: it bumps L3, the patience ratchet stays unset.
    live.record_l3_fired(None)
    fired()
    live.record_l3_fired(ask(0.75).l3)
    fired()

    assert live_trace == [
        (1, 0, 0.60, 0, 0, None, 0),
        (2, 1, 0.60, 0, 0, None, 0),
        (2, 1, 0.60, 0, 0, None, 0),
        (0, 0, None, 1, 0, None, 0),
        (0, 0, None, 2, 0, 0.75, 0),
    ]


def test_a_fire_after_the_round_closed_survives_a_pause(tmp_path: Path) -> None:
    """A layer fires AFTER the round it reads has closed, so what it authors is on no close."""
    from types import SimpleNamespace

    from pydantic import ValidationError

    from promptpotter.application.optimizers.potter.dispatch.layout import (
        NODE_LAYOUTS,
        coerce_l1_layout,
    )
    from promptpotter.application.optimizers.potter.escalation.firing import (
        L2,
        L3,
        TransitionResult,
    )
    from promptpotter.application.optimizers.potter.escalation.state import LayerReading
    from promptpotter.application.optimizers.potter.records import L2L3Memory, Ladder
    from promptpotter.application.optimizers.potter.state import PotterState
    from promptpotter.domain.cycle_paths import CycleDir
    from promptpotter.domain.run_records import OptimizerStateRecord
    from promptpotter.infrastructure.ledger import CycleEventLog, ledger_chain
    from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
        scan_standing_rounds,
    )
    from tests.factories import round_result

    cycle_dir = CycleDir(tmp_path)
    ledger = CycleEventLog.open(cycle_dir)
    # No cycle id: the restate below is appended here, as the store appends it.
    live = PotterState(session=SimpleNamespace(state=SimpleNamespace(cycle_id="")))  # type: ignore[arg-type]
    before = round_result(0, optimizer_state=optimizer_state())
    closed = round_result(1, optimizer_state=optimizer_state())
    for rr in (before, closed):
        ledger.append(RoundClosedRecord.of(rr.model_copy(update={"all_candidate_results": {}})))

    def restate() -> None:
        state = closed.optimizer_state.model_copy(update={"payload": live.restated(closed)})
        ledger.append(OptimizerStateRecord(round=1, optimizer_state=state))

    floor = NODE_LAYOUTS["l1_generate"].floor
    moved = coerce_l1_layout({"critique": "persona"}, base=floor)
    assert moved is not None and moved != floor
    handed = live.memory
    steer = {"layout": moved.model_dump(mode="json"), "n_variants": 5}
    live.memory = L2.write(handed, TransitionResult(described="L2", steer=steer))
    # A fire answers the NEXT memory: the one it was handed is what the closed round banked.
    assert handed == L2L3Memory() and live.memory != handed
    live.escalation.record_l2_fired(LayerReading(1, 0.5, None, None))
    restate()
    live.memory = L3.write(
        live.memory, TransitionResult(described="L3", plan="attack the default label")
    )
    live.escalation.record_l3_fired(None)
    restate()
    with pytest.raises(ValidationError):
        closed.improved = True
    with pytest.raises(ValidationError):
        closed.optimizer_state.payload.memory = live.memory  # type: ignore[attr-defined]

    def resumed(last: int) -> PotterState:
        standing = scan_standing_rounds(ledger_chain(cycle_dir)).rounds
        state = PotterState(session=SimpleNamespace())  # type: ignore[arg-type]
        state.replay(round_result(last, optimizer_state=standing[last].close.optimizer_state))
        return state

    # Paused inside round 2: both fires belong to the boundary the resume keeps.
    kept = resumed(1)
    assert (kept.memory, kept.escalation.ladder) == (live.memory, live.escalation.ladder)
    assert kept.escalation.ladder.l3.fires == 1 and kept.memory.plan
    # Rewound to re-run round 1: the fires are discarded with it.
    rewound = resumed(0)
    assert (rewound.memory, rewound.escalation.ladder) == (L2L3Memory(), Ladder())


# 6. Paired readings over shared cells


def test_a_verify_reads_its_fresh_cells_alone_and_pairs_its_lift_over_the_origin() -> None:
    from promptpotter.application.diagnostics.verify import verify_member, verify_reading
    from promptpotter.domain.results import VerifyPass, VerifyStrategy

    def banked(strategy: VerifyStrategy = "random", round_num: int = 3) -> VerifyPass:
        return VerifyPass(
            label="C3.1",
            candidate_id="c",
            round=round_num,
            sp_hash="h",
            cells=[(f"k{sid}", sid, f"r.{sid}", False) for sid in range(100, 110)],
            sample_ids=list(range(100, 110)),
            strategy=strategy,
            seed=1,
            scorer_id="s",
        )

    def keyed(rows: list[dict[str, Any]]) -> CellSheet:
        return sheet(({**row, "sample_key": f"k{row['sample_id']}"} for row in rows), "s")

    recorded = keyed(measurements([1.0] * 20))
    fresh = keyed(measurements([1.0, 0.0] * 5, range(100, 110)))
    # The origin: half the round's cells, none of the fresh ones.
    origin = keyed([*measurements([0.0, 1.0] * 10), *measurements([0.0] * 10, range(100, 110))])
    hop = CycleHop(campaign_id="camp", cycle_id="cyc")
    c0 = ArmPointer(round=0, label="C0", candidate_id="c0")

    def read(pass_: VerifyPass, fresh_rows: CellSheet = fresh) -> Any:
        arm = (
            c0
            if pass_.round == 0
            else ArmPointer(round=pass_.round, label=pass_.label, candidate_id=pass_.candidate_id)
        )
        return verify_reading(
            pass_,
            fresh=fresh_rows,
            recorded=recorded,
            measured=verify_member(
                hop, arm, recorded.merged(fresh_rows), bought=10, instrument_id="bank"
            ),
            origin=verify_member(hop, c0, origin, bought=0, instrument_id="bank"),
        )

    reading = read(banked())
    assert reading.fresh.accuracy is not None and reading.recorded.accuracy is not None
    assert reading.fresh.accuracy.value == pytest.approx(0.5), "the fresh cells, never the pool"
    assert reading.recorded.accuracy.value == pytest.approx(1.0)
    assert reading.accuracy_increment == pytest.approx(-0.5)
    pair = reading.vs_origin
    assert pair.coverage is not None and pair.headline is not None
    assert (reading.fresh.n, reading.recorded.n, pair.coverage.scored) == (10, 20, 30)
    # Paired per cell both scored, never the difference of two rates over different cells.
    assert pair.headline.estimate.value == pytest.approx(0.5)
    assert pair.headline.rate_b - pair.headline.rate_a == pytest.approx(0.5)
    # The recorded level sits above the fresh band, so the round's claim did not survive.
    assert (reading.held, reading.held_absent) == (False, None)
    assert read(banked(), keyed(measurements([1.0] * 10, range(100, 110)))).held is True
    # Hard picks sit below the level whatever the candidate is worth: no verdict from the level.
    hard = read(banked("hard"))
    assert (hard.held, hard.held_absent) == (None, "hard_picks")
    # The origin has nothing to be lifted over, and 0.0 there would read as "no better than C0".
    at_origin = read(banked(round_num=0))
    assert at_origin.vs_origin.state is ReadingState.SAME_INDIVIDUAL
    assert at_origin.vs_origin.headline is None


def test_an_individuals_cells_are_one_set_and_a_decision_never_reads_an_overlap_row() -> None:
    from promptpotter.domain.results import InRunCells
    from promptpotter.shared.measurement_context import RoleScope

    def walk(tag: str, graded: dict[int, float]) -> CellSheet:
        return sheet(
            measurement(sid, g, answer=f"{tag}.{sid}", sample_key=f"k{sid}")
            for sid, g in graded.items()
        )

    c0 = OptSearchPoint(instruction="origin")
    c1 = OptSearchPoint(instruction="edit")
    as_origin = walk("r0", {0: 1.0, 1: 0.0, 2: 1.0})
    as_parent = walk("r1", {2: 0.0, 3: 1.0})
    topped_up = walk("ov", {4: 1.0, 5: 1.0})
    rounds = [
        round_result(
            0,
            candidates_scored=0,
            opt_sp=c0,
            results=as_origin,
            all_candidate_results={c0.id: as_origin},
        ),
        round_result(
            1,
            candidates_scored=0,
            opt_sp=c1,
            results=walk("c1", {2: 1.0, 3: 1.0}),
            all_candidate_results={c1.id: walk("c1", {2: 1.0, 3: 1.0})},
            reference_results={c0.id: as_parent},
            overlap_results={c0.id: topped_up},
        ),
    ]
    measured = InRunCells(rounds)

    def read(scope: RoleScope) -> dict[int, float]:
        return {c.sample_id: c.grade.fitness for c in measured.sheet(c0.id, scope)}

    # ONE set: the later reading of cell 2 stands, and the overlap rows are the origin's own.
    assert read(RoleScope.REPORT) == {0: 1.0, 1: 0.0, 2: 0.0, 3: 1.0, 4: 1.0, 5: 1.0}
    # Rate 4/6 where the report reads it; a decision reads 2/4, never the two bought after it.
    assert read(RoleScope.DECISION) == {0: 1.0, 1: 0.0, 2: 0.0, 3: 1.0}
    assert read(RoleScope.BENCH) == {}
    assert [cell.sample_id for cell in rounds[1].sheet_of(c0.id)] == [2, 3]


def test_a_cell_is_its_samples_content_whatever_query_it_shares_or_slot_it_holds(
    built_stores: Any,
) -> None:
    from promptpotter.domain.results import IndividualWalk, individual_cells
    from promptpotter.infrastructure.ledger import ledger_chain
    from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_walks
    from promptpotter.shared.measurement_context import RoleScope

    stores = built_stores
    asks_one_thing = [
        Sample(id=0, query="is it so?", ground_truth="yes"),
        Sample(id=1, query="is it so?", ground_truth="no"),
    ]

    def cycle(cycle_id: str, slots: dict[int, Sample]) -> list[IndividualWalk[WalkedCell]]:
        hop = CycleHop(campaign_id="keyed", cycle_id=cycle_id)
        stores.campaigns.mint_cycle(hop)
        rows = [
            measurement(slot, None, query=s.query, sample_key=s.key) for slot, s in slots.items()
        ]
        ledger = CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(hop)))
        ledger.append(CandidateMintedRecord(round=0, idx=0, candidate_id="c0", label="C0"))
        for cell in _filed(stores, f"keyed_{cycle_id}", rows):
            ledger.append(
                SampleScoredRecord(
                    round=0,
                    candidate_idx=0,
                    candidate_total=1,
                    individual_id="c0",
                    role=MeasurementRole.ORIGIN,
                    result=_announced(cell),
                )
            )
        return scan_ledger_walks(ledger_chain(CycleDir(stores.campaigns.cycle_dir(hop))))

    first = cycle("cycle_a", dict(enumerate(asks_one_thing)))
    assert len(individual_cells(first, "c0", RoleScope.REPORT)) == 2

    # A second cycle holds the same two samples at other slots: still two cells, the later read.
    second = cycle("cycle_b", {7: asks_one_thing[1], 9: asks_one_thing[0]})
    folded = individual_cells([*first, *second], "c0", RoleScope.REPORT)
    assert {key: slot for key, slot, _, _ in folded} == {
        asks_one_thing[0].key: 9,
        asks_one_thing[1].key: 7,
    }


def test_paired_mean_t_matches_ttest_rel_and_brackets_the_same_evidence_it_tests() -> None:
    """The SE is clipped at ``1/(4n)``: a near-constant difference reads LESS significant."""
    # Spread wide enough that the 1/(4n) floor does not bind, so the two must agree exactly.
    cand = [0.90, 0.10, 0.85, 0.20, 0.75, 0.30, 0.95, 0.05]
    prior = [0.10, 0.85, 0.15, 0.80, 0.20, 0.70, 0.05, 0.90]
    reference = 0.8750918683549795  # scipy 1.17.1 `ttest_rel(cand, prior).pvalue`

    mean_d, lo, hi, p_two, n = paired_mean_t(cand, prior)
    assert n == len(cand)
    assert abs(mean_d - sum(c - p for c, p in zip(cand, prior, strict=True)) / n) < 1e-12
    assert p_two is not None and abs(p_two - reference) < 1e-12

    # One posterior, one verdict: a p above 0.05 and a bracket clearing zero cannot coexist.
    assert lo is not None and hi is not None and lo < mean_d < hi
    assert (p_two < 0.05) == (lo > 0.0 or hi < 0.0)

    p_greater = paired_mean_t(cand, prior, tail="greater")[3]
    assert p_greater is not None and abs(p_greater - reference / 2.0) < 1e-12

    assert paired_mean_t(prior, cand)[3] == p_two

    # The floor binds: a textbook test calls this difference significant, and this one must not.
    tight_cand = [0.5000001 * i for i in range(1, 7)]
    tight_prior = [0.5 * i for i in range(1, 7)]
    tight_p = paired_mean_t(tight_cand, tight_prior)[3]
    assert (
        tight_p is not None and tight_p > 0.00593354451968529
    )  # scipy's `ttest_rel` on the same pair

    assert paired_mean_t([0.5], [0.1])[1:4] == (None, None, None)


def _pair_member(
    individual: str,
    rows: list[dict[str, Any]],
    bank: list[Sample],
    *,
    scorer_id: str = "s",
    **read_under: Any,
) -> MemberRows:
    hop = CycleHop(campaign_id="camp", cycle_id="cyc")
    key_of = {s.id: s.key for s in bank}
    return MemberRows(
        **{
            "address": MemberAddress(
                path=(hop,), individual_id=individual, arm=None, pass_role=None
            ),
            "sheet": sheet(
                ({**row, "sample_key": key_of[row["sample_id"]]} for row in rows), scorer_id
            ),
            "bought": 0,
            "cut": False,
            "scope": RoleScope.DECISION,
            "instrument_id": "i",
            "dataset_hash": "d",
            "cell_set_id": None,
        }
        | read_under
    )


def _read_pair(a: MemberRows, b: MemberRows, panel: list[Sample], **asked: Any) -> PairedReading:
    hit = Measurand(
        kind=MeasurandKind.GRADE, key="hit", scorer_id="s", binary=True, unit=MeasurandUnit.RATE
    )
    return read_pair(
        **{
            "a": a,
            "b": b,
            "cell_set": CellSetName.ORIGIN_PANEL,
            "cells": [s.key for s in panel],
            "masked": False,
            "dataset_hash": "d",
            "measurands": [hit],
            "spec": EstimatorSpec(
                interval_method=IntervalMethod.STUDENT_T, alpha=0.05, null_value=0.0
            ),
            "scope": RoleScope.DECISION,
            "instrument_id": "i",
        }
        | asked
    )


def test_a_pair_is_one_estimate_over_the_cells_both_members_scored() -> None:
    # Samples 0 and 1 are one query under two truths: two cells, wherever a query would key one.
    bank = [
        Sample(id=i, query="same" if i < 2 else f"q{i}", ground_truth=f"t{i}") for i in range(7)
    ]
    a = _pair_member(
        "a",
        [
            *measurements([1.0, 0.0, 0.0, 1.0, 1.0]),
            measurement(5, None, error_category="SERVER"),
            measurement(6, 1.0),
        ],
        bank,
    )
    b = _pair_member(
        "b",
        [*measurements([1.0, 1.0, 1.0, 0.0, 1.0, 1.0]), measurement(6, None, unscored=True)],
        bank,
    )
    composite = Measurand(
        kind=MeasurandKind.GRADE,
        key="objective",
        scorer_id="s",
        binary=False,
        unit=MeasurandUnit.SCORE,
    )

    reading = _read_pair(a, b, bank)
    lift, coverage = reading.headline, reading.coverage
    assert lift is not None and coverage is not None and reading.cell_set is not None
    assert reading.cell_set.size == 7
    assert (coverage.shared, coverage.scored) == (7, 5)
    assert (coverage.excluded_faulted, coverage.excluded_unscored) == (1, 1)
    assert coverage.state is CoverageState.PARTIAL

    mean, lo, hi, p, _ = paired_mean_t([1.0, 1.0, 1.0, 0.0, 1.0], [1.0, 0.0, 0.0, 1.0, 1.0])
    assert (lift.rate_a, lift.rate_b) == (pytest.approx(0.6), pytest.approx(0.8))
    assert lift.rate_b - lift.rate_a == pytest.approx(lift.estimate.value) == pytest.approx(mean)
    estimate = lift.estimate
    assert (estimate.ci_lo, estimate.ci_hi, estimate.p_value) == pytest.approx((lo, hi, p))
    assert estimate.side == "spans"
    # Three cells differ, so no exact test on this pair can read below 2/2**3.
    assert estimate.p_floor == 0.25
    # The floor gates the claim: Student-t's interval clears zero, yet four differing cells cap p.
    sparse = [Sample(id=i, query=f"w{i}", ground_truth="t") for i in range(34)]
    gained = _read_pair(
        _pair_member("a", measurements([0.0] * 34), sparse),
        _pair_member("b", measurements([1.0] * 4 + [0.0] * 30), sparse),
        sparse,
    ).headline
    assert gained is not None and gained.estimate.ci_lo > 0.0
    assert (gained.estimate.side, gained.estimate.p_value) == ("spans", 0.125)
    assert lift.flips is not None
    assert (lift.flips.gained, lift.flips.lost, lift.flips.unchanged) == (2, 1, 2)

    (beside,) = _read_pair(a, b, bank, measurands=[lift.measurand, composite]).beside
    assert beside.flips is None
    assert beside.rate_b - beside.rate_a == pytest.approx(beside.estimate.value)
    assert beside.estimate.value == pytest.approx(0.6 * lift.estimate.value)

    # One table's pairs share one Holm correction, and a pair nobody read is no test.
    clear = _read_pair(
        _pair_member("a", measurements([0.0] * 6), bank),
        _pair_member("b", measurements([1.0] * 6), bank),
        bank,
    )
    assert clear.headline is not None
    p_wide, p_clear = estimate.p_value, clear.headline.estimate.p_value
    assert p_wide is not None and p_clear is not None and 2 * p_clear < p_wide
    spanning, unread, tight = as_family(
        [reading, PairedReading.unread(ReadingState.PENDING), clear], "table"
    )
    assert unread.headline is None
    assert spanning.headline is not None and spanning.headline.family is not None
    assert tight.headline is not None and tight.headline.family is not None
    assert (spanning.headline.family.n_tests, tight.headline.family.n_tests) == (2, 2)
    assert tight.headline.family.p_adjusted == pytest.approx(2 * p_clear)
    assert spanning.headline.family.p_adjusted == pytest.approx(p_wide)


def test_a_cell_measured_twice_is_one_standing_row_to_every_reader() -> None:
    from promptpotter.application.intelligence.rasch import responses_of
    from promptpotter.application.scoring.metrics import fold_cells
    from promptpotter.application.scoring.selection import distinct_valid_cells, level_band

    bank = [Sample(id=i, query=f"q{i}", ground_truth="t") for i in range(4)]

    def unanswered(sid: int) -> dict[str, Any]:
        return measurement(sid, None, error="HTTP 502", error_category="SERVER")

    walked = [
        *(measurement(0, 0.0), measurement(0, 1.0)),  # scored twice: the later stands
        *(measurement(1, 1.0), unanswered(1)),  # the scored row stands over a later fault
        *(unanswered(2), measurement(2, 0.0)),  # and over an earlier one
        measurement(3, 1.0),
    ]
    standing = {0: 1.0, 1: 1.0, 2: 0.0, 3: 1.0}
    worth = {sid: measurement(sid, grade)["objective"] for sid, grade in standing.items()}
    twice = _pair_member("twice", walked, bank)
    cells = twice.sheet

    assert cells.column(ROW_GRADES["fitness"]) == standing
    assert cells.column(ROW_GRADES["objective"]) == worth
    assert responses_of(cells) == worth
    assert distinct_valid_cells(cells) == 4
    fold = fold_cells(cells)
    assert (fold.total, fold.accuracy) == (4, 0.75)
    assert fold.composite_fitness == pytest.approx(sum(worth.values()) / 4)
    assert level_band(cells, ROW_GRADES["fitness"])[0] == fold.accuracy
    assert {key: row.grade.fitness for key, row in twice.by_key().items()} == {
        bank[sid].key: grade for sid, grade in standing.items()
    }

    # Paired against an individual that walked each cell once at those grades, nothing moved.
    once = _pair_member("once", measurements(list(standing.values())), bank)
    reading = _read_pair(once, twice, bank)
    lift, coverage = reading.headline, reading.coverage
    assert lift is not None and coverage is not None and lift.flips is not None
    assert (coverage.scored, coverage.excluded_faulted) == (4, 0)
    assert (lift.rate_a, lift.rate_b, lift.estimate.value) == (0.75, 0.75, 0.0)
    assert (lift.flips.gained, lift.flips.lost, lift.flips.unchanged) == (0, 0, 4)

    ungraded = _pair_member(
        "ungraded", [*walked[:-1], measurement(3, None, unscored="no latency recorded")], bank
    )
    assert ungraded.sheet.column(ROW_GRADES["fitness"]) == {0: 1.0, 1: 1.0, 2: 0.0}
    assert level_band(ungraded.sheet, ROW_GRADES["fitness"])[0] == pytest.approx(2 / 3)
    short = _read_pair(once, ungraded, bank).coverage
    assert short is not None and short.scored == 3
    with pytest.raises(ValueError, match="ungraded cell"):
        ROW_GRADES["fitness"].read(ungraded.sheet.cells[-1])


def _reference_reading(
    reference: list[float | None], arm: list[float | None], *, outcome: ArmOutcome
) -> PairedReading:
    """A grade of ``None`` is a cell that member never sat."""
    bank = [Sample(id=i, query=f"q{i}", ground_truth="t") for i in range(len(reference))]

    def rows(grades: list[float | None]) -> CellSheet:
        return sheet(
            (
                measurement(s.id, g, sample_key=s.key)
                for s, g in zip(bank, grades, strict=True)
                if g is not None
            ),
            "s",
        )

    scorer = Scorer(id="s", per_cell=None, fitness=lambda _row: 0.0, objective=None)
    session = types.SimpleNamespace(
        hop=CycleHop(campaign_id="camp", cycle_id="cyc"),
        instrument_id="i",
        scoring=types.SimpleNamespace(require_scorer=lambda: scorer),
        samples=bank,
    )
    cycle = types.SimpleNamespace(session=session, opt_sp=OptSearchPoint(), rounds=[])
    scored = scored_candidate("C1.1", outcome=outcome)
    reference_rows = rows(reference)
    pairs = _ReferencePairs(
        types.SimpleNamespace(cycle=cycle, round_num=1),  # type: ignore[arg-type]
        [scored],
        bar_cut=False,
        declared=[cell.key for cell in reference_rows],
    )
    return pairs.read(scored, rows(arm), cycle.opt_sp.id, reference_rows)


_WHOLE_PANEL: list[float | None] = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0]


@pytest.mark.parametrize(
    ("reference", "arm", "outcome", "state", "scored", "size", "rates", "level"),
    [
        # The reference sat four of the six cells: 5/6 over the arm's own walk, 3/4 on those four.
        (
            [1.0, 0.0, 1.0, 0.0, None, None],
            [1.0, 1.0, 1.0, 0.0, 1.0, 1.0],
            ArmOutcome.MEASURED,
            CoverageState.COMPLETE,
            4,
            4,
            (0.5, 0.75),
            0.5,
        ),
        # An eliminated arm's stop read its answers to pick the cells, so no level is served.
        (
            _WHOLE_PANEL,
            [None, 1.0, None, 1.0, None, 1.0],
            ArmOutcome.ELIMINATED,
            CoverageState.SELECTED_BY_STOP,
            3,
            6,
            (0.0, 1.0),
            None,
        ),
        # A walk that ran its panel and lost a cell is short without having been chosen.
        (
            _WHOLE_PANEL,
            [1.0, 1.0, 1.0, 0.0, 1.0, None],
            ArmOutcome.MEASURED,
            CoverageState.PARTIAL,
            5,
            6,
            (0.6, 0.8),
            None,
        ),
    ],
)
def test_an_arm_is_read_on_the_cells_its_reference_was_read_on(
    reference: list[float | None],
    arm: list[float | None],
    outcome: ArmOutcome,
    state: CoverageState,
    scored: int,
    size: int,
    rates: tuple[float, float],
    level: float | None,
) -> None:
    reading = _reference_reading(reference, arm, outcome=outcome)
    lift, coverage, cells = reading.headline, reading.coverage, reading.cell_set
    assert lift is not None and coverage is not None and cells is not None
    assert (cells.name, cells.size) == (CellSetName.REFERENCE_CELLS, size)
    assert (coverage.state, coverage.scored) == (state, scored)
    assert (lift.rate_a, lift.rate_b) == rates
    assert reading.reference_level("fitness") == level
    assert (reading.on_whole_set is None) == (state is not CoverageState.COMPLETE)
    assert reading.beside[0].measurand.key == "objective"


def test_a_pair_that_cannot_be_read_says_which_way_instead_of_reading_zero() -> None:
    bank = [Sample(id=i, query=f"q{i}", ground_truth="t") for i in range(6)]
    panel = bank[:4]
    a = _pair_member("a", measurements([1.0, 0.0, 1.0, 0.0]), bank)

    def b(rows: list[dict[str, Any]] | None = None, **read_under: Any) -> MemberRows:
        return _pair_member(
            "b", measurements([1.0, 1.0, 1.0, 0.0]) if rows is None else rows, bank, **read_under
        )

    def state(member: MemberRows, **asked: Any) -> ReadingState:
        reading = _read_pair(a, member, panel, **asked)
        assert (reading.headline is None) == (reading.state is not ReadingState.READ)
        return reading.state

    assert state(b()) is ReadingState.READ
    assert state(b(scope=RoleScope.REPORT)) is ReadingState.SCOPE_DIFFERS
    assert state(b(instrument_id="other")) is ReadingState.INSTRUMENT_DIFFERS
    assert state(b(dataset_hash="other")) is ReadingState.DATASET_DIFFERS
    assert state(b(scorer_id="other")) is ReadingState.MEASURAND_DIFFERS
    assert state(b(cell_set_id="another-split")) is ReadingState.CELL_SET_DIFFERS
    assert state(b(address=a.address)) is ReadingState.SAME_INDIVIDUAL
    assert state(b(), cells=[]) is ReadingState.NOT_HELD
    errored = [measurement(i, None, error_category="SERVER") for i in range(4)]
    assert state(b(errored)) is ReadingState.MEMBER_UNSCOREABLE
    assert state(b(measurements([1.0], [0]))) is ReadingState.UNDER_TWO_CELLS
    # Two members that measured nothing in common hold no set to be empty.
    apart = b(measurements([1.0, 1.0], [4, 5]))
    assert state(apart, cells=None, cell_set=CellSetName.MEASURED_BY_BOTH) is (
        ReadingState.UNDER_TWO_CELLS
    )

    read = _read_pair(a, b(), panel)

    def said(as_state: ReadingState) -> PairedReading:
        return absent_pair(
            state=as_state,
            a=read.a,
            b=read.b,
            cell_set=read.cell_set,
            scope=read.scope,
            spec=read.spec,
            instrument_id=read.instrument_id,
        )

    assert said(ReadingState.PASS_STOPPED).headline is None
    with pytest.raises(ValueError):
        said(ReadingState.READ)


def test_a_level_is_bracketed_on_the_student_t_its_paired_lift_is_tested_on() -> None:
    """On the normal quantile an eight-cell band is a fifth too narrow."""
    from statistics import mean, stdev

    from promptpotter.application.scoring.selection import level_band

    def band(grades: list[float]) -> tuple[float | None, float | None]:
        return level_band(sheet(measurements(grades)), ROW_GRADES["fitness"])[1:]

    grades = [0.90, 0.10, 0.85, 0.20, 0.75, 0.30, 0.95, 0.05]
    lo, hi = band(grades)
    half = 2.364624251592785 * stdev(grades) / len(grades) ** 0.5  # scipy `t.ppf(0.975, 7)`
    assert lo == pytest.approx(mean(grades) - half, abs=1e-9)
    assert hi == pytest.approx(mean(grades) + half, abs=1e-9)

    # Four cells at 0.0 have no spread, yet PoBB's 1/(4n) floor holds the band open.
    floor_lo, floor_hi = band([0.0] * 4)
    assert floor_lo == 0.0
    assert floor_hi == pytest.approx(3.182446305284263 / 16, abs=1e-9)  # `t.ppf(0.975, 3)`

    assert band([1.0]) == (None, None)


def test_a_run_stands_on_its_selection_read_against_the_origin_it_was_promoted_from() -> None:
    """Promotions and separations are counted apart: a promotion is a point estimate."""
    origin, first_pick, second_pick, third_pick = (
        OptSearchPoint(instruction=text) for text in ("origin", "one", "three", "five")
    )
    ids = {"C0": origin.id, "C1.1": first_pick.id, "C3.2": second_pick.id, "C5.1": third_pick.id}

    def closed(rnd: int, on: OptSearchPoint, *arms: str, selected: str = "", **reading: Any) -> Any:
        return round_result(
            rnd,
            improved=bool(selected) and rnd > 0,
            opt_sp=on,
            candidate_scores=[
                scored_candidate(ids.get(arm, arm.lower()), label=arm) for arm in arms
            ],
            candidates_scored=len(arms),
            all_candidate_results={},
            selected_labels=[selected] if selected else [],
            **reading,
        )

    def stands(rounds: list[Any]) -> RunStanding:
        return RunStanding.after(rounds, lives=None, spent=None)

    # C0 is re-read between the lines: the panel cell it took in round 1 it misses by round 3.
    tied = [1.0, 0.0] + [0.0] * 6 + [1.0]
    first = _line_read(1, True, ("C1.1", tied), c0=_C0_PANEL, ids=ids)
    newest = _line_read(3, True, ("C1.1", tied), ("C3.2", _CLEAN_PANEL), c0=[0.0] * 9, ids=ids)
    reread = _line_read(4, False, ("C3.2", _CLEAN_PANEL), c0=_C0_PANEL, ids=ids)
    assert (first.advance, newest.advance) == (RoundAdvance.NOT_SEPARATED, RoundAdvance.ADVANCED)
    rounds = [
        closed(0, origin, "C0", selected="C0"),
        closed(1, first_pick, "C1.1", "C1.2", selected="C1.1", overlap=first),
        closed(2, first_pick, "C2.1"),  # held: nobody promoted, no line re-read
        closed(3, second_pick, "C3.1", "C3.2", selected="C3.2", overlap=newest),
    ]

    at_origin = stands(rounds[:1])
    assert (at_origin.selection.label, at_origin.parent) == ("C0", None)
    assert at_origin.vs_origin == PairedReading.unread(ReadingState.SAME_INDIVIDUAL)
    held = stands(rounds[:3])
    assert held.selection.label == "C1.1", "a held round keeps the selection before it"
    assert held.vs_origin == first.lead

    standing = stands(rounds)
    assert standing.selection.model_dump() == {
        "round": 3,
        "label": "C3.2",
        "candidate_id": second_pick.id,
    }
    assert standing.parent.label == "C1.1"
    assert (standing.rounds_closed, standing.improved, standing.advanced) == (3, 2, 1)
    # C0 on the reading its selection sat beside: rates differenced must have sat one exam.
    pair = standing.vs_origin
    assert (pair.coverage.scored, pair.headline.rate_a, pair.headline.rate_b) == (9, 0.0, 1.0)

    # A later round that holds re-reads the line, and the standing follows the NEWEST reading.
    later = stands([*rounds, closed(4, second_pick, "C4.1", overlap=reread)])
    assert later.selection == standing.selection
    assert later.vs_origin.headline.rate_a == pytest.approx(1 / 9)

    # A pick nobody has read against C0 yet says so — never the pick before it under its name.
    stopped = OverlapReading.unpaired(ReadingState.PASS_STOPPED, 5, True)
    unread = stands([*rounds, closed(5, third_pick, "C5.1", selected="C5.1", overlap=stopped)])
    assert (unread.selection.label, unread.parent) == ("C5.1", standing.selection)
    assert (unread.vs_origin.state, unread.vs_origin.headline) == (ReadingState.PASS_STOPPED, None)
    # The one stall count, off the same rounds: round 3 advanced, and an unread pick is no stall.
    assert (standing.rounds_without_advance, unread.rounds_without_advance) == (0, 0)
    assert later.rounds_without_advance == 1


def test_a_head_to_head_pairs_two_optimizers_only_on_one_bench_under_one_grader(
    built_stores,
) -> None:
    stores = built_stores
    bank = [Sample(id=i, query=f"q{i}", ground_truth="a") for i in range(12)]
    formula = "env_reward"

    def campaign(
        cid: str,
        n: int,
        *,
        seed: int,
        selected: float,
        origin: float = 0.4,
        scoring: str = formula,
        at: int | None = None,
        incurred: float = 0.2,
        loop: float = 0.05,
        wall: int = 100,
        rebased: bool = False,
        arm: Arm | None = None,
        proposer_model: str | None = None,
        graded: bool = True,
        steered: bool = False,
    ) -> Any:
        split = DatasetSplit(bench=6, seed=seed)
        partition = partition_bank(bank, split)
        root = CycleHop(campaign_id=cid, cycle_id=f"cycle_{cid}")
        stores.campaigns.create_campaign(
            Campaign(
                campaign_id=cid,
                dataset_name="ds",
                created_at=f"2026-09-26T00:00:{n:02d}Z",
                root_cycle_id=root.cycle_id,
                root_content_hash="origin",
                arm=arm,
                config={
                    "optimization": {
                        "optimizer": cid.split("_")[0],
                        "degradation_threshold": 0.0,
                        "nodes": {}
                        if proposer_model is None
                        else {"l1_generate": {"config": {"model": proposer_model}}},
                    },
                    "dataset_split": split.model_dump(),
                    "scoring": scoring,
                },
            )
        )
        hop = CycleHop(campaign_id=cid, cycle_id=f"{root.cycle_id}_fork_r1") if rebased else root
        panel = [
            measurement(s.id, None, query=s.query, pipeline_data={"env_reward": 0.5})
            for s in partition.search[:4]
        ]
        walked = _filed(stores, f"panel_{cid}", panel, sp_hash="origin")
        # A rebase fork replays its own origin and redraws the one partition.
        ledgers: dict[CycleHop, CycleEventLog] = {}
        for cycle in {root, hop}:
            stores.campaigns.mint_cycle(cycle)
            stores.campaigns.write_bank_partition(cycle, partition.record(dataset_hash="bank"))
            origin_walk = CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(cycle)))
            ledgers[cycle] = origin_walk
            origin_walk.append(CandidateMintedRecord(round=0, idx=0, candidate_id="c0", label="C0"))
            for cell in walked:
                origin_walk.append(
                    SampleScoredRecord(
                        round=0,
                        candidate_idx=0,
                        candidate_total=1,
                        individual_id="c0",
                        role=MeasurementRole.ORIGIN,
                        result=_announced(cell),
                    )
                )
        if rebased:
            stores.campaigns.mark_superseded(root, hop.cycle_id)
        if graded:
            unread = READING_STATE_INFO[ReadingState.NO_SELECTION].sentence
            standing = RunStanding.opening(None).model_copy(
                update={
                    "selection": ArmPointer(round=1, label="C1.1", candidate_id=f"{cid}:selected"),
                    "rounds_closed": 1,
                    # The line is the standing's own wording, and a read back refuses another.
                    "selection_line": f"C1.1 (round 1) — {unread}",
                }
            )
            pick = scored_candidate(f"{cid}:selected", label="C1.1")
            ledgers[hop].append(
                CandidateMintedRecord(round=1, idx=0, candidate_id=pick.candidate_id, label="C1.1")
            )
            ledgers[hop].append(
                CandidateScoredRecord(round=1, candidate_idx=0, candidate_total=1, scores=pick)
            )
            closed = RoundClosedRecord.of(
                round_result(
                    1, candidate_scores=[pick], selected_labels=["C1.1"], all_candidate_results={}
                )
            )
            ledgers[hop].append(
                closed.model_copy(update={"cells": RoundCells(arms={pick.candidate_id: walked})})
            )
            ledgers[hop].append(
                RoundStandingRecord(round=1, run_standing=standing, anchors=ViewAnchors())
            )
        if steered:
            stores.campaigns.record_intervention(hop, kind="skip")
        passes: dict[str, BenchPass] = {}
        for role, level in (("origin", origin), ("selected", selected))[: 1 + graded]:
            # The origin is ONE individual every campaign sends; each pick is its own.
            graded = role if role == "origin" else f"{cid}:{role}"
            rows = [
                measurement(
                    s.id,
                    None,
                    query=s.query,
                    pipeline_data={"env_reward": level + 0.1 * (s.id % 2)},
                )
                for s in partition.bench
            ]
            passes[role] = BenchPass(
                round=int(role == "selected"),
                candidate_id=graded,
                sp_hash=role,
                cells=_filed(
                    stores, f"bench_{graded}_{seed}_{level}", rows, role="bench", sp_hash=graded
                ),
                sample_keys=[f"key:{s.query}" for s in partition.bench],
                stop=None,
                scorer_id=scoring,
                reads_before=int(role == "selected") * n,
            )
        hour = n if at is None else at
        started = f"2026-09-26T{hour:02d}:00:00Z"
        # The optimizer billed on the root; a rebase fork replayed its cells and graded the pick.
        calls = [("optimizer", loop, False), ("backend", incurred - loop - 0.05, True)]
        for kind, usd, cached in [*calls, ("bench", 0.05, True)]:
            ledgers[root if kind == "optimizer" else hop].append(
                TokenUsageRecord(
                    kind=kind,
                    node=kind,
                    input_tokens=10,
                    output_tokens=5,
                    cost_usd=usd,
                    cached=cached,
                    timestamp=started,
                )
            )
        for role, taken in passes.items():
            ledgers[hop].append(
                PhaseRecord(
                    phase=CampaignPhase.BENCH,
                    event="graded",
                    view=BenchGradedView(
                        subject=role,  # type: ignore[arg-type]
                        bench_pass=taken,
                        tolerance=0,
                        reserve_usd=0.05 if role == "origin" else None,
                        reserve_tokens=0 if role == "origin" else None,
                        reading=None,
                        state=ReadingState.READ,
                        label="C0" if role == "origin" else "C1.1",
                    ),
                )
            )
        # A cycle the line has moved past banks nothing — its result is the successor's.
        for holder in (hop, root) if rebased else (hop,):
            bank_campaign_result(
                stores,
                holder,
                started_at=started,
                finished_at=f"2026-09-26T{hour:02d}:{wall // 60:02d}:{wall % 60:02d}Z",
                optimizer_phases=frozenset(),
            )
        banked = stores.campaigns.load_result(cid)
        assert banked is not None and banked.cycle_id == hop.cycle_id
        return SubjectSpec("campaign", cid)

    potter = campaign("potter_a", 1, seed=0, selected=0.5, rebased=True)
    capo = campaign("capo_b", 2, seed=0, selected=0.7, incurred=0.3, loop=0.15, wall=50)
    read = subject_evidence(stores, [potter, capo])
    assert read.scorer_id == auto_scorer_id(formula, None, judge_instrument=None)
    h2h = read.head_to_head
    assert h2h is not None and h2h.verdict is True

    lines = {
        c.campaign_id: c.line
        for c in list_campaigns(stores, inside=(), dataset=None, lifecycle="all").campaigns
    }
    rebased, held = lines["potter_a"], lines["capo_b"]
    assert rebased is not None and held is not None
    assert rebased.holder.cycle_id.endswith("_fork_r1") and rebased.rounds_closed == 1
    assert rebased.standing is not None and rebased.standing.selection is not None
    assert held.holder == stores.campaigns.load_campaign("capo_b").root_hop  # type: ignore[union-attr]

    def ends(table: HeadToHead) -> list[tuple[str, ...]]:
        return [
            tuple(m.address.path[-1].campaign_id for m in (p.reading.a, p.reading.b) if m)
            for p in table.pairs
            if p.reading.headline is not None and p.reading.headline.family is not None
        ]

    def guards(table: HeadToHead) -> list[str]:
        return [r.guard.state.value for r in table.rows]

    (pair,) = h2h.pairs
    assert pair.guard.state.value == "uncontrolled"
    shift, coverage = pair.reading.headline, pair.reading.coverage
    assert shift is not None and coverage is not None
    assert (ends(h2h), coverage.scored) == ([("potter_a", "capo_b")], 6)
    assert shift.estimate.value == pytest.approx(0.2) and shift.estimate.side == "above"
    # Priced on INCURRED spend: capo replayed cells potter paid for, so its bill prices arrival.
    assert h2h.ratio_reference == "potter_a"
    potter_row, row = h2h.rows
    assert potter_row.spend is not None and potter_row.spend.total_incurred_usd == 0.2
    assert (row.incurred_usd_ratio, row.optimizer_incurred_usd_ratio, row.worked_ratio) == (
        pytest.approx(1.5),
        pytest.approx(3.0),
        pytest.approx(0.5),
    )
    # The lift is priced on the SEARCH alone: the bench's own pass is the instrument grading it.
    assert row.bench.cost.lift_per_usd == pytest.approx(0.3 / 0.25)
    assert [r.concurrent_with for r in h2h.rows] == [[], []]
    assert [r.bench.status.reads_before for r in h2h.rows] == [1, 2]
    # A campaign ticked in the webapp arrives as a COURSE and stands for the campaign ONCE.
    course = SubjectSpec("course", "potter_a", "cycle_potter_a_fork_r1")
    for asked, subjects in (([course, capo], [course]), ([course, potter, capo], [potter])):
        h2h = subject_evidence(stores, asked).head_to_head
        assert h2h is not None and len(h2h.pairs) == 1
        assert sorted(r.subject for r in h2h.rows) == sorted(s.key for s in (*subjects, capo))

    # Another seed drew other held-out rows: a pair across the two sets is tested as nothing.
    gepa = campaign("gepa_c", 3, seed=1, selected=0.9)
    h2h = subject_evidence(stores, [potter, capo, gepa]).head_to_head
    assert h2h is not None and h2h.verdict is False
    assert {"bench_rows", "split"} <= set(h2h.guard.differs_on)
    assert ends(h2h) == [("potter_a", "capo_b")]
    assert guards(h2h) == ["uncontrolled", "uncontrolled", "differs"]

    # The shared origin reads 0.2 apart under one formula: pairs still READ, the guard differs.
    drifted = campaign("capo_d", 4, seed=0, selected=0.7, origin=0.6)
    h2h = subject_evidence(stores, [potter, capo, drifted]).head_to_head
    assert h2h is not None and h2h.guard.differs_on == ["origin_reading"]
    assert ends(h2h) == [("potter_a", "capo_b")]
    assert guards(h2h) == ["uncontrolled", "uncontrolled", "differs"]
    assert [(p.reading.state.value, p.guard.state.value) for p in h2h.pairs] == [
        ("read", "uncontrolled"),
        ("read", "differs"),
        ("read", "differs"),
    ]

    # Another formula shares no scorer with these: the read refuses rather than serve a column.
    halved = campaign("capo_h", 8, seed=0, selected=0.7, scoring="0.5 * env_reward")
    with pytest.raises(ValueError):
        subject_evidence(stores, [potter, capo, halved])

    # Its run overlapped potter's: a shared cache split the bill by arrival.
    overlapping = campaign("capo_f", 6, seed=0, selected=0.7, at=1)
    h2h = subject_evidence(stores, [potter, capo, overlapping]).head_to_head
    assert h2h is not None and h2h.verdict is True and len(h2h.pairs) == 3
    assert [r.concurrent_with for r in h2h.rows] == [["capo_f"], [], ["potter_a"]]

    # One optimizer on another model: a gap to it is the model's as much as the method's.
    swapped = campaign("potter_g", 7, seed=0, selected=0.7, proposer_model="other/model")
    h2h = subject_evidence(stores, [potter, capo, swapped]).head_to_head
    assert h2h is not None and h2h.verdict is False
    assert h2h.guard.differs_on == ["optimizer_models"]
    assert ends(h2h) == [("potter_a", "capo_b")]

    # An operator steered one line: its pairs are still read, outside the correction.
    babysat = campaign("capo_s", 9, seed=0, selected=0.7, steered=True)
    h2h = subject_evidence(stores, [potter, capo, babysat]).head_to_head
    assert h2h is not None and h2h.guard.differs_on == ["human_intervened"]
    assert [(p.reading.state.value, p.guard.state.value) for p in h2h.pairs] == [
        ("read", "uncontrolled"),
        ("read", "differs"),
        ("read", "differs"),
    ]
    assert ends(h2h) == [("potter_a", "capo_b")]

    # Naming two records must not demote the arms, which read their own record.
    shared = read.head_to_head
    assert shared is not None and shared.rows[0].bench_set is not None
    for h2h_id in ("real4", "real5"):
        stores.campaigns.declare_head_to_head(
            HeadToHeadRecord(
                head_to_head_id=h2h_id,
                created_at="",
                bench_set=shared.rows[0].bench_set,
                budget=ArmBudget(
                    usd=2.0, max_rounds=3, determinism=None, lives=None, convergence_patience=None
                ),
            )
        )
    arms = [
        campaign(
            f"{key}_{h2h_id}",
            n,
            seed=0,
            selected=0.6,
            arm=Arm(head_to_head_id=h2h_id, arm_key=key, treatment_digest=key),
        )
        for n, (key, h2h_id) in enumerate(
            [("potter", "real4"), ("capo", "real4"), ("gepa", "real4"), ("levi", "real5")],
            start=10,
        )
    ]
    h2h = subject_evidence(stores, arms).head_to_head
    assert h2h is not None and h2h.guard.head_to_head_id == "real4"
    assert guards(h2h) == ["controlled", "controlled", "controlled", "uncontrolled"]

    # A third arm of that record still ungraded withholds the verdict.
    assert subject_evidence(stores, arms[:2]).head_to_head.verdict is True  # type: ignore[union-attr]
    waiting = campaign(
        "capo_waiting",
        14,
        seed=0,
        selected=0.6,
        arm=Arm(head_to_head_id="real4", arm_key="waiting", treatment_digest="waiting"),
        graded=False,
    )
    h2h = subject_evidence(stores, [*arms[:2], waiting]).head_to_head
    assert h2h is not None and h2h.verdict is None and h2h.guard.differs_on == []
    assert h2h.verdict_absent is ReadingState.PENDING

    # Without its ending, an arm refused at run init reads exactly like one still running.
    assert [r.status.mark == "failed" for r in h2h.rows] == [False, False, False]
    refused = CycleHop(campaign_id="capo_waiting", cycle_id="cycle_capo_waiting")
    CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(refused))).append(
        RunPhaseRecord(run_phase=RunPhase.TERMINAL, stop_reason=StopReason.INPUT_REFUSED)
    )
    h2h = subject_evidence(stores, [*arms[:2], waiting]).head_to_head
    assert h2h is not None
    assert [r.status.mark == "failed" for r in h2h.rows] == [False, False, True]

    # A cap moved on ONE arm moves that arm alone, off its declared budget.
    stores.campaigns.write_run_limits(
        CycleHop(campaign_id="capo_real4", cycle_id="cycle_capo_real4"),
        SpendCeilings(1.0, None),
        rounds=None,
        pause_at_round=None,
        reserve=SpendCeilings(),
    )
    h2h = subject_evidence(stores, arms).head_to_head
    assert h2h is not None
    assert guards(h2h) == ["controlled", "differs", "controlled", "uncontrolled"]


def test_an_arm_still_scoring_reads_only_its_own_cells_and_says_it_is_partial(
    built_stores: Any,
) -> None:
    stores = built_stores
    hop = CycleHop(campaign_id="potter_live", cycle_id="cycle_live")
    stores.campaigns.create_campaign(
        Campaign(
            campaign_id=hop.campaign_id,
            dataset_name="ds",
            created_at="2026-09-26T00:00:00Z",
            root_cycle_id=hop.cycle_id,
            root_content_hash="origin",
            config={
                "optimization": {"optimizer": "potter", "degradation_threshold": 0.0},
                "scoring": "env_reward",
            },
        )
    )
    stores.campaigns.mint_cycle(hop)
    ledger = CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(hop)))

    def walk(round_num: int, idx: int, individual: str, rewards: dict[int, float]) -> None:
        rows = [
            measurement(
                sample_id,
                None,
                query=f"q{sample_id}",
                pipeline_data={"env_reward": reward, "target_prompt_chars": 100},
            )
            for sample_id, reward in rewards.items()
        ]
        for cell in _filed(stores, individual, rows):
            ledger.append(
                SampleScoredRecord(
                    round=round_num,
                    candidate_idx=idx,
                    candidate_total=1,
                    individual_id=individual,
                    role=MeasurementRole.PANEL,
                    sample_total=4,
                    result=_announced(cell),
                )
            )

    def report(round_num: int, candidate_id: str) -> None:
        scores = scored_candidate(candidate_id)
        ledger.append(
            CandidateScoredRecord(
                round=round_num, candidate_idx=0, candidate_total=1, scores=scores
            )
        )

    def read(candidate_id: str) -> Any:
        subjects = [
            SubjectSpec("candidate", hop.campaign_id, hop.cycle_id, cid)
            for cid in ("c0", candidate_id)
        ]
        return subject_evidence(stores, subjects)

    def unread(candidate_id: str) -> list[str]:
        return [f"candidate:{hop.campaign_id}/{hop.cycle_id}/{candidate_id}"]

    ledger.append(CandidateMintedRecord(round=0, idx=0, candidate_id="c0", label="C0"))
    walk(0, 0, "run_c0", {0: 1.0, 1: 0.0, 2: 0.0, 3: 0.0})
    report(0, "c0")
    ledger.append(RoundEnteredRecord(round=1))
    # The individual first minted into the slot scored a cell, then a re-proposal replaced it.
    ledger.append(CandidateMintedRecord(round=1, idx=0, candidate_id="replaced", label="C1.1"))
    walk(1, 0, "run_replaced", {3: 1.0})
    ledger.append(CandidateMintedRecord(round=1, idx=0, candidate_id="arm", label="C1.1"))
    walk(1, 0, "run_live", {0: 1.0, 1: 1.0, 2: 0.0})
    walk(1, NO_ROUND_SLOT, "run_parent", {3: 1.0})  # the parent re-measured on this round's cells

    evidence = read("arm")
    origin, live = evidence.subjects
    assert (origin.status, origin.n_cells) == ("measured", 4)
    assert (live.status, live.n_cells, live.expected_samples) == ("minted", 3, 4)
    assert (live.round, live.label) == (1, "C1.1")
    assert sorted(live.values) == ["q0", "q1", "q2"]
    # A channel the ledger's own narrowed copy of a cell never carried.
    assert live.cell_means["target_prompt_chars"] == 100
    # One cycle, so the pair runs round 0 → round 1 though the arm's id sorts ahead of the origin's.
    pair = evidence.metric.pairwise[0]
    flips = pair.hit.headline.flips
    assert (flips.gained, flips.lost, flips.unchanged) == (1, 0, 2)
    assert (pair.gained, pair.lost) == ([1], [])
    assert read("replaced").unread_subjects == unread("replaced")

    # Its report lands and no round file does: the same cells, now a whole walk.
    report(1, "arm")
    closed = read("arm").subjects[1]
    assert (closed.status, closed.values) == ("measured", live.values)

    # A rewind enters the round again: what it walked before is displaced.
    ledger.append(RoundEnteredRecord(round=1))
    assert read("arm").unread_subjects == unread("arm")


def _filed(
    stores: Any, individual: str, rows: list[Any], *, role: str = "panel", sp_hash: str = ""
) -> list[WalkedCell]:
    """A cell is keyed by the query it asks unless the row names its own key."""
    entry = ArchiveEntry(
        config_key=config_key([("", {"individual": individual})]),
        prompt_fields_id=sp_hash or individual,
        rendered_prompt_hash="",
        node_configs=[],
        pipeline_params={},
        dataset_name="ds",
    )
    keyed = [{"sample_key": f"key:{row['query']}", **row} for row in rows]
    answers = stores.archive.file_answers(
        entry,
        [(MeasuredCell.from_wire(row), "A") for row in keyed],
        role=role,
        source="optimization_loop",
        created_at="",
    )
    return [
        (row["sample_key"], row["sample_id"], answer, False)
        for row, answer in zip(keyed, answers, strict=True)
    ]


def _announced(cell: WalkedCell) -> dict[str, Any]:
    key, sample_id, answer, _ = cell
    return {"sample_key": key, "sample_id": sample_id, "answer": answer}


def _banked_origin(
    stores: Any, campaign_id: str, minute: int, rewards: dict[int, float | None]
) -> CycleHop:
    """``None`` is a cell the formula cannot grade; sample ``i`` asks query ``q{i % 10}``."""
    hop = CycleHop(campaign_id=campaign_id, cycle_id=f"cycle_{campaign_id}")
    stores.campaigns.create_campaign(
        Campaign(
            campaign_id=campaign_id,
            dataset_name="ds",
            created_at=f"2026-09-26T00:{minute:02d}:00Z",
            root_cycle_id=hop.cycle_id,
            root_content_hash="origin",
            config={
                "optimization": {"optimizer": "potter", "degradation_threshold": 0.0},
                "scoring": "env_reward",
            },
        )
    )
    stores.campaigns.mint_cycle(hop)
    _walk_arm(stores, hop, 0, "c0", "C0", rewards)
    return hop


def _walk_arm(
    stores: Any,
    hop: CycleHop,
    round_num: int,
    candidate_id: str,
    label: str,
    rewards: dict[int, float | None],
) -> None:
    rows = [
        measurement(
            sample_id,
            None,
            query=f"q{sample_id % 10}",
            pipeline_data={} if reward is None else {"env_reward": reward},
        )
        for sample_id, reward in rewards.items()
    ]
    ledger = CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(hop)))
    ledger.append(
        CandidateMintedRecord(round=round_num, idx=0, candidate_id=candidate_id, label=label)
    )
    for cell in _filed(stores, f"{hop.campaign_id}_{candidate_id}", rows):
        ledger.append(
            SampleScoredRecord(
                round=round_num,
                candidate_idx=0,
                candidate_total=1,
                individual_id=candidate_id,
                role=MeasurementRole.PANEL,
                result=_announced(cell),
            )
        )


def test_a_pairs_flips_pair_on_the_cell_and_count_only_what_both_subjects_graded(
    built_stores: Any,
) -> None:
    stores = built_stores
    # The newer campaign numbers the same six queries 10..15.
    _banked_origin(stores, "edit_b", 2, {10: 1.0, 11: 1.0, 12: 0.5, 13: 0.0, 14: 1.0, 15: 1.0})
    _banked_origin(stores, "base_a", 1, {0: 1.0, 1: 0.0, 2: 1.0, 3: 0.5, 4: None, 6: 1.0})

    read = subject_evidence(
        stores, [SubjectSpec("campaign", "edit_b"), SubjectSpec("campaign", "base_a")]
    )
    (pair,) = read.metric.pairwise
    assert (pair.subject_a, pair.subject_b) == ("campaign:base_a", "campaign:edit_b")
    # q0 both hit, q1 gained, q2 part-credited away, q3 both missed; q4-q6 share no verdict.
    hit, flips = pair.hit.coverage, pair.hit.headline.flips
    assert (hit.scored, flips.gained, flips.lost, flips.unchanged) == (4, 1, 1, 2)
    assert (pair.gained, pair.lost) == ([11], [12])
    assert pair.reading.coverage.scored == 4 and pair.reading.headline.flips is None


def test_a_sample_is_never_hit_only_where_every_graded_cell_of_it_scored_nothing(
    built_stores: Any,
) -> None:
    from promptpotter.application.scoring.measurement_log import measurement_log

    stores = built_stores
    stores.tenant_datasets.save_benchmark_rows(
        "ds", [Sample(id=i, query=f"q{i}", ground_truth="a") for i in range(7)]
    )
    hop = _banked_origin(stores, "graded", 1, {0: 1.0, 1: 0.0, 2: 0.0, 3: 0.5, 4: None, 5: None})
    _walk_arm(stores, hop, 1, "c1", "C1.1", {0: 1.0, 1: 0.0, 2: 1.0, 3: 0.5, 4: 1.0})

    def log(round_num: int | None = None) -> Any:
        return measurement_log(
            stores,
            "ds",
            scope="cycle",
            campaign_id=hop.campaign_id,
            cycle_id=hop.cycle_id,
            descend=(),
            limit=10,
            max_unmeasured=None,
            order="info_gain",
            candidate_id=None,
            round=round_num,
            status=None,
        )

    whole = log()
    assert {s.sample_id: s.hit_spread for s in whole.samples} == {
        0: "always",
        1: "never",
        2: "partly",  # one arm missed it, the next hit it
        3: "partly",  # half credit from both: no hit, and not nothing
        4: "always",  # its one graded cell; the ungraded one is no miss
        5: "unmeasured",  # answered, never graded
        6: "unmeasured",
    }
    assert (whole.never_hit, whole.partly_hit, whole.always_hit) == (1, 2, 2)
    assert (whole.total_measurements, whole.total_hits) == (9, 4)

    # One round's rows: the bucket is over the cells kept, so sample 2 is that round's miss.
    origin = log(0)
    assert {s.sample_id: s.hit_spread for s in origin.samples}[2] == "never"
    assert (origin.never_hit, origin.partly_hit, origin.always_hit) == (2, 1, 1)


def test_decision_bank_pairs_every_decision_and_holds_the_parent_where_nothing_measured() -> None:
    from promptpotter.application.diagnostics.decision_bank import read_arm, read_bank

    bank = [Sample(id=i, query=f"q{i}", ground_truth="a") for i in range(4)]
    proposals = iter(range(100))

    def rows(*fitness: float) -> MemberRows:
        return _pair_member(f"proposal{next(proposals)}", measurements(fitness), bank)

    parent = rows(0.4, 0.4, 0.4, 0.4)
    base = [
        read_arm(
            parent,
            [rows(0.5, 0.7, 0.6, 0.6), rows(0.3, 0.3, 0.5, 0.5)],
            proposed=3,
            collapses={"duplicate_variant": 1},
        ),
        read_arm(parent, [rows(0.4, 0.6, 0.5, 0.5)], proposed=1, collapses={}),
        read_arm(parent, [rows(0.4, 0.4, 0.6, 0.6)], proposed=1, collapses={}),
    ]
    other = [
        read_arm(parent, [rows(0.8, 1.0, 0.9, 0.9)], proposed=1, collapses={}),
        read_arm(parent, [], proposed=2, collapses={"no_op_variant": 2}),
        read_arm(parent, [rows(0.2, 0.2, 0.3, 0.1)], proposed=1, collapses={}),
    ]
    assert (base[0].rejected, base[0].best_lift, base[0].mean_lift) == (
        1,
        pytest.approx(0.2),
        pytest.approx(0.1),
    )
    assert (other[1].best_lift, other[1].mean_lift) == (0.0, None)
    assert (other[2].best_lift, other[2].mean_lift) == (0.0, pytest.approx(-0.2))
    # One shared cell has no spread to read a lift off: the proposal is unread, never a zero.
    assert read_arm(parent, [rows(0.9)], proposed=1, collapses={}).lifts == ()

    reading = read_bank(list(zip(base, other, strict=True)))
    assert reading["best_lift"]["n"] == 3
    assert reading["best_lift"]["mean"] == pytest.approx((0.3 - 0.1 - 0.1) / 3)
    assert reading["mean_lift"]["n"] == 2, "the arm that measured nothing has no mean to pair"
    assert reading["mean_lift"]["mean"] == pytest.approx((0.4 - 0.3) / 2)
    assert (reading["proposed"], reading["rejected"]) == ([5, 4], [1, 2])


def test_a_lens_rank_shift_is_counted_once_over_the_arms_still_on_the_line() -> None:
    from promptpotter.domain.results import ArmElection, ArmPanel, ArmReading
    from promptpotter.infrastructure.store.lineage_queries import (
        ArmNode,
        LensShift,
        rank_moves,
    )

    def arm(label: str, composite: float, lens: float | None, *, retired: bool = False) -> ArmNode:
        pointer = ArmPointer(round=1, label=label, candidate_id=label.lower())
        return ArmNode(
            id=pointer.candidate_id,
            row=0,
            label=label,
            reading=ArmReading.of(
                pointer,
                scored_candidate(pointer.candidate_id, label=label, composite_fitness=composite),
                cut=False,
                election=ArmElection(held=False, selected=False, leading=False, crown=None),
                changes_description="",
                ability=None,
                vs_reference=None,
            ),
            lens_value=lens,
            superseded_by="cycle_branch" if retired else None,
        )

    # Composite ranks A D B C; the lens ranks D B A C, and D is a retired tail.
    moved, shift = rank_moves(
        [
            arm("A", 0.9, 0.2),
            arm("B", 0.5, 0.8),
            arm("C", 0.1, 0.1),
            arm("D", 0.7, 0.9, retired=True),
        ]
    )
    assert {k.label: k.lens_rank_move for k in moved} == {
        "A": "down",
        "B": "up",
        "C": "unchanged",
        "D": "up",
    }
    assert shift == LensShift(
        top_composite="A", top_lens=None, top_changed=True, moved_up=1, moved_down=1, unchanged=1
    )
    unread, none = rank_moves([arm("A", 0.9, None), arm("B", 0.5, None)])
    assert none is None and [k.lens_rank_move for k in unread] == [None, None]

    # Absent where the walk holds no cell: a zero there would draw an arm as fully measured.
    assert ArmPanel.of(8, 10, 2, cut=False).cached_share == pytest.approx(0.25)
    assert ArmPanel.of(0, 10, 0, cut=False).cached_share is None
    assert ArmPanel.of(None, None, None, cut=False).cached_share is None
