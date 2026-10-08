"""A wrong score or a wrong decision.

Owns `application/scoring/`, `shared/statistics.py`, `optimizers/potter/pobb/` and
`optimizers/potter/escalation/`, the runner's election, `diagnostics/verify.py` and `evidence/`.
The run completes, the dashboard looks fine, and a different candidate should have won.
"""

from __future__ import annotations

import asyncio
import json
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from factories import pipeline_schema

from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.campaign_config import load_campaign_config
from promptpotter.application.evidence.read import subject_evidence
from promptpotter.application.evidence.subjects import SubjectSpec
from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizer_manifest import bind_optimizer
from promptpotter.application.optimizers import paper_templates
from promptpotter.application.optimizers.potter.pobb.checks import EliminationGate, PoBBCheck
from promptpotter.application.run_phase_control import RunControl
from promptpotter.application.runner.bench import bench_selection, headline, score_on_bench
from promptpotter.application.runner.campaign_result import bank_campaign_result
from promptpotter.application.runner.entry import _build_cycle_result
from promptpotter.application.runner.round import execute_round
from promptpotter.application.scoring import query_loop
from promptpotter.application.scoring.classification import DegradationCheck, scoreable_rows
from promptpotter.application.scoring.evaluators import DEFAULT_CELL_FORMULA
from promptpotter.application.scoring.formula import (
    ScoringFormulaError,
    auto_scorer_id,
    compile_scorer,
    rescore_results,
)
from promptpotter.application.scoring.formula.matchers import (
    _aime_match,
    _gsm8k_match,
    _label_match,
)
from promptpotter.application.scoring.metrics import compute_composite_fitness, matched_parent_stats
from promptpotter.application.scoring.search_point_scorer import (
    merge_with_unprocessed_priors,
    score_search_point,
)
from promptpotter.domain.bench import BenchPass, BenchPasses, DatasetSplit, partition_bank
from promptpotter.domain.campaign import Arm, ArmBudget, Campaign, HeadToHeadRecord
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.export import build_prompt_export
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import StopOutcome, StopReason
from promptpotter.domain.pipeline_schema import NodePromptInfo, PipelineNode, PipelineSchema
from promptpotter.domain.results import (
    ArmOutcome,
    RoundResult,
    ScoredCandidate,
    is_leader_eligible,
    order_floor,
)
from promptpotter.domain.ruler import DeltaRuler, anchor_id_of
from promptpotter.domain.run_records import TokenUsageRecord
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import is_graded, is_unscored
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.spend import BudgetChange
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.spend_book import SpendBook
from promptpotter.infrastructure.store.archive_queries import record_measurement_run
from promptpotter.shared import extract_gsm8k_number
from promptpotter.shared.errors import error_category, is_error_result
from promptpotter.shared.statistics import paired_reading
from tests.factories import (
    measurement,
    measurements,
    optimizer_state,
    pobb_knobs,
    round_result,
    scored_candidate,
)

# 1. Scorer formulas


@pytest.mark.parametrize(
    "fn,args,expected",
    [
        # _aime_match: last boxed wins / bad boxed → fallback / no numbers.
        (_aime_match, (r"First: \boxed{10}. Rechecking: \boxed{42}", "42"), 1.0),
        (_aime_match, (r"\boxed{undefined} The answer is 42", "42"), 1.0),
        (_aime_match, ("no numbers", "42"), 0.0),
        # extract_gsm8k_number: comma-stripped / #### preferred / none.
        (extract_gsm8k_number, ("#### 1,234",), 1234.0),
        (extract_gsm8k_number, ("I calculated 99 but #### 42",), 42.0),
        (extract_gsm8k_number, ("no numbers",), None),
        # _gsm8k_match: cross-format numeric equivalence / mismatch.
        (_gsm8k_match, ("42.0", "#### 42"), 1.0),
        (_gsm8k_match, ("#### 99", "#### 42"), 0.0),
        # _label_match: last bold wins (case-insensitive) / no-marker / mismatch / an option
        # letter against its parenthesised truth, either way round / a wrong option.
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
    """Minimal schema with one generic node and no role assignments."""
    return pipeline_schema(
        name="test",
        nodes=[PipelineNode(name="llm_only", tunes_llm=False)],
    )


def test_a_dial_reads_its_term_against_the_origin_and_never_sums_a_raw_unit() -> None:
    """A dial on an unbounded term — tokens, seconds, dollars — has to be read against a level.
    Summed raw, `0.9 * fitness + 0.1 * (1 - tokens)`, the term swamps correctness, the clamp floors
    every candidate at 0.000 and the election falls to its tie-break. Silent: every bar renders.

    Also pins the inverse the dashboard reads the dials back through: a criterion that does not
    round-trip opens the form on weights the run was never scored under."""
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

    anchors = origin_anchors([cell("a", 600), cell("b", 784)])
    assert anchors["tokens"] == pytest.approx(692.0)

    formula = realize_dials(parse_dials("tokens=0.08, latency=0"), anchors)
    scorer = compile_scorer("label_match(predicted, ground_truth)", formula, verifier_graded=False)

    def scored(said: str, tokens: int) -> float:
        rows = rescore_results([cell(said, tokens)], scorer)
        return compute_composite_fitness(rows, _single_node_schema())["composite_fitness"]

    # At the origin's own length the dial charges nothing; ten times it costs the dial's share of
    # nine tenths, and a hit is never floored. Shorter than the origin earns no bonus.
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
    # A flat sum is not this shape, so it is served as a formula and never as dials.
    assert anchored_criterion_dials("0.9 * fitness + 0.1 * (1 - tokens)") is None


def test_a_miss_is_charged_its_cost_and_a_solved_cell_scores_its_composite(monkeypatch) -> None:
    """A ``per_cell`` composite scales correctness by a cost factor, so on its own every miss
    scores 0.0 whatever it spent. A miss keeps ``MISS_COST_SHARE`` of what the same cell would
    score solved; a solved cell scores exactly its composite; an errored row stays out.

    Silent harm: θ reads a runaway miss level with a cheap one, so neither the election nor PoBB
    ever charges an arm for the tokens it burns on the cells it fails. The shipped length charge
    is bounded, so length never outranks accuracy: no miss beats a hit, a long 100% arm beats a
    short 50% one."""
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
    rows = rescore_results(
        [
            cell(0, "a", 692),
            cell(1, "a", 2076),
            cell(2, "b", 692),
            cell(3, "b", 2076),
            cell(4, "b", 20760, error_category="SERVER"),
        ],
        scorer,
    )
    solved_cheap, solved_costly, miss_cheap, miss_costly, _ = (r["objective"] for r in rows)
    assert solved_cheap == 1.0, "a solved cell moved off its composite"
    assert solved_costly == pytest.approx(0.92 + 0.08 / 3)
    assert miss_cheap == pytest.approx(compiler.MISS_COST_SHARE)
    assert miss_costly == pytest.approx(compiler.MISS_COST_SHARE * solved_costly)
    scored = compute_composite_fitness(rows, _single_node_schema())["composite_fitness"]
    assert scored == pytest.approx((solved_cheap + solved_costly + miss_cheap + miss_costly) / 4)
    # The 0% floor is a fact about correctness, which a charged miss no longer reads as zero.
    assert is_floor_pinned(rows[2:4]) and not is_floor_pinned(rows[1:4])

    def objectives(*cells: dict[str, Any]) -> list[float]:
        return [r["objective"] for r in rescore_results(list(cells), scorer)]

    solved = objectives(*(cell(i, "a", t) for i, t in enumerate((100, 692, 1000, 2076, 10**7))))
    assert solved[0] == solved[1] and solved[1:] == sorted(set(solved[1:]), reverse=True)
    costliest_hit, cheapest_miss = objectives(cell(0, "a", 10**7), cell(1, "b", 100))
    assert costliest_hit > cheapest_miss, "length made a miss outscore a hit"
    long_perfect = objectives(*(cell(i, "a", 10**6) for i in range(4)))
    short_half = objectives(*(cell(i, "ab"[i % 2], 100) for i in range(4)))
    assert sum(long_perfect) > sum(short_half), "length outranked a 2x accuracy gap"

    # The share is half the grading function: a ruler must never pool grades across two of them.
    before = auto_scorer_id(per_sample, per_cell, judge_instrument=None)
    # So are the judges whose banked terms a formula reads: one text over two graders is two.
    assert auto_scorer_id(per_sample, per_cell, judge_instrument="a judge") != before
    monkeypatch.setattr(compiler, "MISS_COST_SHARE", 0.3)
    assert auto_scorer_id(per_sample, per_cell, judge_instrument=None) != before
    # A defaulted cell formula and the same text declared stamp one `scorer_cell_formula`, yet only
    # the declared one charges a miss — so the id, never the resolved text, names the grader.
    (defaulted,) = rescore_results(
        [cell(2, "b", 692)], compile_scorer(per_sample, None, verifier_graded=False)
    )
    assert defaulted["objective"] == 0.0
    assert auto_scorer_id(per_sample, None, judge_instrument=None) != auto_scorer_id(
        per_sample, DEFAULT_CELL_FORMULA, judge_instrument=None
    )


def _result_min(predicted: str, ground_truth: str) -> dict:
    return {
        "query": "q",
        "predicted": predicted,
        "ground_truth": ground_truth,
        "hit": False,
        "fitness": 0.0,
        "error": None,
        "pipeline_data": None,
    }


def test_a_conversation_reaches_the_formula_only_as_projected_scalars() -> None:
    """The turn channel must stay unreachable from a formula; its projection must not be.

    ``turns`` is compacted out of cold rows, so a formula that walked it would raise on cells it
    had already scored."""
    from factories import measurement

    from promptpotter.application.scoring.formula import compile_scorer, rescore_results
    from promptpotter.application.scoring.formula.compiler import CELL_INTRINSIC_NAMES
    from promptpotter.domain.scoring import TURN_SCALAR_KEYS, turn_scalars

    assert not (TURN_SCALAR_KEYS & CELL_INTRINSIC_NAMES), (
        "a projected term colliding with an intrinsic is dropped by cell_namespace's splat, "
        "silently, leaving a key no formula can reach"
    )

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

    # The point of the whole projection: a formula can NAME these. Scored off a row shaped the way
    # `measure_sample` banks one.
    row = measurement(
        sample_id=0, fitness=0.0, pipeline_data={"env_reward": 1.0, "turns": turns, **scalars}
    )
    scorer = compile_scorer(
        "env_reward * (1.0 if n_turns <= 4 else 0.5) * min(1.0, retrieve_turns / 2.0)",
        None,
        verifier_graded=True,
    )
    rescore_results([row], scorer)
    assert row["fitness"] == 1.0

    # All three are refused at COMPILE, before a cell is bought — indexing and attribute access on
    # node kind, `len` on its call target. `len` matters most: a bare Call reaches eval as a
    # NameError, which the classifier reads as a missing TERM, so a mistyped function becomes a
    # campaign that grades nothing and reports measuring fine. Pinned so adding `len` to
    # SAFE_BUILTINS is caught.
    for formula in ("turns[0]", "turns.index"):
        with pytest.raises(ValueError, match="disallowed syntax"):
            compile_scorer(formula, None, verifier_graded=True)
    with pytest.raises(ValueError, match="not a scoring helper"):
        compile_scorer("len(turns)", None, verifier_graded=True)


# 2. Composite fitness and coverage


def test_an_unmeasured_term_is_never_scored_as_zero() -> None:
    # SILENT wrong-score. Every empty-collection aggregate in the evaluator registry used to
    # return a PERFECT value — no rows meant "no errors" (0.0), "instant" (1.0), "maximally
    # compact" (1.0). The registry now omits the key, so a reading shows the absence instead of a
    # number nobody computed. The distinction matters: a round that measured every sample and
    # failed them all IS a 0.0; a round that measured nothing is not.
    from promptpotter.application.scoring.evaluators import (
        compute_accuracy,
        compute_degraded_rate,
        compute_error_rate,
    )
    from promptpotter.domain.scoring import recorded_cost_s

    assert compute_accuracy(results=[]) is None
    assert compute_error_rate(results=[]) is None
    assert compute_degraded_rate(results=[]) is None

    # Latency stopped being an evaluator and became the `latency` CHANNEL
    # (`domain/scoring.py::recorded_cost_s`) — one reading, per row, so a mask and a `per_cell`
    # formula name the same number. It kept both properties, which is why they are pinned here
    # and not left to the deleted evaluator's grave: a CACHED replay stamps `total_time` 0.0 while
    # the work it replays took minutes, so reading that field priced a whole round at "instant"
    # and elected the arm that had doubled the clock. It reads `step_timings`, which survives the
    # stamp. An EMPTY timing map is a row that recorded no time at all — absent, never a 0.0
    # that would read as a free cell and divide into any budget the formula sets.
    def _timed(total: float, steps: dict[str, float]) -> dict[str, object]:
        pipeline_data = {"total_time": total, "step_timings": steps}
        return _result_min("q", "a") | {"pipeline_data": pipeline_data}

    assert recorded_cost_s(_timed(0.0, {"inner": 600.0})) == 600.0
    assert recorded_cost_s(_timed(0.0, {})) is None

    # All samples measured, all fatally deprecated → a verdict of 0.0, not an absence.
    deprecated = _result_min("q", "a") | {"error": "SCHEMA_VALIDATION_FAILED", "fitness": 0.0}
    assert compute_accuracy(results=[deprecated]) == 0.0

    # A provider's fault is the ABSENCE of a verdict — excluded from the mean, never a silent
    # 0.0 dragging a real score down...
    scored = _result_min("q", "a") | {"hit": True, "fitness": 1.0}
    errored = _result_min("ERROR", "a") | {"error": "boom", "error_category": "SERVER"}
    del errored["fitness"], errored["hit"]  # real error rows carry neither
    assert compute_accuracy(results=[scored, errored]) == 1.0
    # ...while it still surfaces on the error channel, counted over ALL rows.
    assert compute_error_rate(results=[scored, errored]) == 0.5

    # The GATEWAY over an EMPTY round is defined, not a crash: NO score with ``total`` 0 — never a
    # 0.0, which an ordering, a lift or a stall counter reads as an arm that failed every cell.
    empty = compute_composite_fitness([], _single_node_schema())
    assert empty["composite_fitness"] is None
    assert empty["accuracy"] is None
    assert empty["total"] == 0
    assert order_floor(None) < order_floor(0.0)

    # The same split, one step in: a candidate whose every row ERRORED with no label to miss has
    # no rate either. It is the arm that matters, because it is reachable — an L4 cell is a whole
    # inner campaign, and one cut throughout must not read as having driven the inner loop to 0%.
    errored = _result_min("ERROR", "") | {"error": "boom", "error_category": "HALTED"}
    del errored["fitness"], errored["hit"]
    all_errored = compute_composite_fitness([errored], _single_node_schema())
    assert all_errored["accuracy"] is None
    assert all_errored["composite_fitness"] is None
    assert all_errored["total"] == 0


def test_a_cell_the_prompt_failed_stays_in_the_denominator_as_a_miss() -> None:
    """A reading's population is the one SENT. A refusal is flagged for the stop rule and a cut
    cell errors, but both are what the prompt produced: dropping them pays a prompt accuracy for
    refusing exactly the cells it could not answer — five hits beside five refusals read 100%.
    Only a provider fault leaves the count. Silent: every number renders."""
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
    scores = compute_composite_fitness(rescore_results(rows, scorer), _single_node_schema())
    assert (scores["total"], len(rows) - scores["total"]) == (11, 1)
    assert scores["accuracy"] == pytest.approx(5 / 11), "the prompt's own failures left the count"
    # The default formula is plain accuracy, so the decision metric and the headline agree.
    assert scores["composite_fitness"] == pytest.approx(scores["accuracy"])
    # With no label a cut cell has no miss to be, so it carries no verdict either.
    assert not is_graded({**rows[10], "ground_truth": ""})


def _eval_result(
    *,
    hit: bool = True,
    score: float = 1.0,
    total_time: float = 100.0,
    error: str | None = None,
    final_ranking: list | None = None,
    candidate_ranking: list | None = None,
    step_timings: dict | None = None,
    diagnostics: dict | None = None,
    ground_truth: str = "gt",
    predicted: str = "gt",
) -> dict:
    pd: dict = {"total_time": total_time}
    if final_ranking is not None:
        pd["final_ranking"] = final_ranking
    if candidate_ranking is not None:
        pd["candidate_ranking"] = candidate_ranking
    if step_timings is not None:
        pd["step_timings"] = step_timings
    if diagnostics is not None:
        pd["diagnostics"] = diagnostics
    return {
        "query": "q",
        "predicted": predicted,
        "ground_truth": ground_truth,
        "hit": hit,
        "fitness": score,
        # Both halves, as ``rescore_results`` stamps them. Equality is PINNED here, not inherited:
        # `factories.measurement` diverges the two on purpose, and these rows mean them equal.
        "objective": score,
        "error": error,
        "pipeline_data": pd,
    }


def test_matched_parent_stats_refuses_a_prefix_it_cannot_measure():
    """A wrong number carried forward with no error — this file's own bar.

    ``build_round_order`` stratifies the round on the PARENT's grades: every 4th slot is
    a cell it passed, the rest are cells it missed. So origin's rate on a truncated
    candidate's prefix is ``⌊n/4⌋/n`` — set by where PoBB stopped, not by the data — and
    both halves of the comparison are conditioned on the outcome that chose the subset, so
    a candidate of identical ability outscores origin there by regression to the mean.
    Measured over the 32 truncated rows banked on disk, that prediction held exactly 28
    times; the 19 candidates cut at six samples every one reported 0.1667.

    Nothing raises when it is wrong: the value renders into the scoreboard, the
    ``mutation_memory`` panel L1 reasons from, and the L4 narrative's top-arm pick.
    """
    # Origin scored 20 samples: 10 hits (samples 0-9 hit, 10-19 miss).
    origin_results = [
        {**_eval_result(hit=i < 10, score=1.0 if i < 10 else 0.0), "sample_id": i}
        for i in range(20)
    ]
    # Candidate stopped after 8 of the *hardest* samples (origin's misses, ids 10..17).
    # Origin reads 0/8 there — but it reads 0/8 for ANY candidate cut at that depth, which
    # is what makes it unusable rather than merely harsh.
    truncated = [
        {**_eval_result(hit=i < 13, score=1.0 if i < 13 else 0.0), "sample_id": i}
        for i in range(10, 18)
    ]
    assert matched_parent_stats(origin_results, truncated) is None
    # Covered the whole panel → a real comparison, on the origin's own measured set.
    full = matched_parent_stats(origin_results, origin_results)
    assert full is not None
    assert full["total"] == 20
    assert full["accuracy"] == pytest.approx(0.5)
    # DISJOINT (per_round_resubset can hand a candidate samples the origin never measured):
    # no shared basis at all, so no comparison. This previously fell back to origin's full
    # rate, publishing a floor measured on cells the candidate never ran.
    disjoint_candidate = [
        {**_eval_result(hit=True, score=1.0), "sample_id": i} for i in range(100, 108)
    ]
    assert matched_parent_stats(origin_results, disjoint_candidate) is None


def _r(score: float) -> dict:
    # ``objective`` is what θ is fit on. Pinned equal here deliberately — `factories.measurement`
    # diverges the two, and these rows are about the rescore, not about the composite.
    return {
        "query": "q",
        "predicted": "p",
        "ground_truth": "g",
        "fitness": score,
        "objective": score,
    }


def test_a_measured_cell_the_formula_cannot_grade_is_kept_not_failed() -> None:
    """A cell the active formula cannot grade is UNSCORED — a third state, keeping the measurement.

    The harm is silent and costs paid measurement twice over. A judge term is absent per CELL — one
    grading fails past its retry while the cell beside it grades fine. Marked ERRORED, that row is
    stamped ``fitness = 0.0``, which reads as an arm answering wrong rather than as a formula saying
    nothing, and it trips ``query_loop.py::Walk._abort_reason`` on ``ErrorCategory.PIPELINE``, which
    abandons the candidate's ENTIRE remaining walk. Nothing raises either way.

    The replay half is the unrecoverable one: the cached path rescores every archived row on its way
    back in, so a row arrives carrying the verdict of whatever formula was active when it was
    banked. Left in place, one campaign's grade is served as another's; raising instead kills
    resume, fork and ``ab`` on an archive that is fine on disk.
    """
    scorer = compile_scorer("skill_opened", None, verifier_graded=True)

    carries = {**_r(1.0), "pipeline_data": {"skill_opened": 1.0}}
    # Banked under an OLDER formula, so it arrives holding a verdict this one cannot re-derive.
    lacks = {**_r(1.0), "pipeline_data": {"env_reward": 1.0}}
    rescore_results([carries, lacks], scorer)

    assert carries["fitness"] == 1.0 and carries["objective"] == 1.0
    assert not is_unscored(carries)

    # The two stamps are ORDERED, not merely both written: `objective_namespace` binds `fitness`
    # off the row, so a composite naming it — which every shipped `per_cell` does — is unevaluable
    # until the first stamp has landed. Computing the pair before assigning either read as a
    # missing term and marked all four shipped datasets UNSCORED on every cell.
    composed = {**_r(1.0), "pipeline_data": {"skill_opened": 0.0}}
    rescore_results(
        [composed],
        compile_scorer("1.0", "fitness * (0.85 + 0.15 * skill_opened)", verifier_graded=True),
    )
    assert (composed["fitness"], composed["objective"]) == (1.0, 0.85)

    # The stale verdict is GONE rather than left to be read as this formula's.
    assert "fitness" not in lacks and "objective" not in lacks
    assert is_unscored(lacks)
    # Not an error, which is the whole reason the walk survives it.
    assert not is_error_result(lacks)
    assert error_category(lacks) is None
    # And it carries no verdict into any denominator.
    assert scoreable_rows([carries, lacks]) == [carries]  # type: ignore[arg-type]

    # Idempotent in BOTH directions: the same row re-graded under a formula that can read it loses
    # the mark, or a recovered cell would stay unscored forever.
    rescore_results([lacks], compile_scorer("env_reward", None, verifier_graded=True))
    assert lacks["fitness"] == 1.0 and not is_unscored(lacks)

    # A formula that RAISES is a different fact and must still halt loud — every cell fails it, so
    # swallowing it would grade a whole campaign against a broken formula.
    with pytest.raises(ScoringFormulaError):
        rescore_results(
            [{**_r(1.0), "pipeline_data": {"tokens": 0.0}}],
            compile_scorer("1.0 / tokens", None, verifier_graded=True),
        )


def test_a_judge_never_grades_a_cell_that_has_no_answer() -> None:
    """A cell with no answer must cost nothing and bank nothing.

    ``predicted`` is the ``NO_RESULT`` sentinel on every cell of a backend that emits no ranking —
    which is the whole of ``harbor``, and any episodic backend after it. A judge reading it raw
    renders ``Answer: NO_RESULT`` into its rubric, bills a model call, and banks whatever category
    comes back as a graded observation of an answer that does not exist. Both halves are the harm:
    a fabricated reading enters the composite the election is decided on, and it is paid for.

    Absence is the only honest verdict here, and it must be reached BEFORE the prompt is rendered,
    so the assertion is a score AND a spend."""
    import asyncio

    from factories import measurement

    from promptpotter.config.settings import NO_RESULT
    from promptpotter.judges import call as judge_call
    from promptpotter.judges.grounding import ANSWER_GROUNDING
    from promptpotter.judges.protocol import JudgeSpec, JudgeStage

    calls: list[str] = []

    async def _explode(*_a: Any, **_k: Any) -> tuple[str, str]:
        calls.append("asked a model")
        return "A", ""

    # A cell that RAN and left a trace — so nothing but the missing answer can explain the
    # absence, and the no-trace arm cannot be what fired.
    row = measurement(
        sample_id=0,
        fitness=0.0,
        predicted=NO_RESULT,
        pipeline_data={"reasoning_trace": "searched the docs, found the founding date"},
    )
    spec = JudgeSpec(name="answer_grounding", stages=[JudgeStage(model="m", provider="p")])
    original, judge_call.ask = judge_call.ask, _explode
    try:
        verdict = asyncio.run(ANSWER_GROUNDING.grade(spec, row))
    finally:
        judge_call.ask = original

    assert verdict.score is None, f"a sentinel answer was graded as {verdict.label!r}"
    assert calls == [], f"a cell with no answer was billed a grading: {calls}"


def _prior(sample_id: int, predicted: str = "p", gt: str = "g") -> dict:
    """A cached measurement. ``sample_id`` IS the cell's identity — the merge keys on it;
    ``query`` rides along as the human-readable label."""
    return {
        "sample_id": sample_id,
        "query": f"q{sample_id}",
        "predicted": predicted,
        "ground_truth": gt,
        "error": None,
        "pipeline_data": {"total_time": 1.5},
    }


def test_a_merge_never_shrinks_what_was_already_measured() -> None:
    """A partial walk merged with the prior tail yields back every cell the archive already
    covered: without it a Ctrl+C records the run as having measured only what the walk reached,
    and its derived fields (provenance, item_count) are computed off that short set.

    The known-outcome pool holds the same way under a subset-measured winner. It seeds PoBB and
    resume's election floor, so shrinking it loses measurement.
    """
    from promptpotter.domain.results import merge_known_outcomes as _merge

    dataset_sample_ids = set(range(20))
    prior_tail = {i: _prior(i) for i in dataset_sample_ids}
    # A partial run: 6 cache hits + 1 fresh measurement.
    walked = merge_with_unprocessed_priors([_prior(i) for i in range(7)], prior_tail)
    assert len(walked) == 20
    assert {r["sample_id"] for r in walked} == dataset_sample_ids

    prior = [{"sample_id": i, "fitness": 1.0 if i < 10 else 0.0, "hit": i < 10} for i in range(20)]
    winner_hits = {10, 12, 15}
    winner = [
        {"sample_id": sid, "fitness": 1.0 if sid in winner_hits else 0.0, "hit": sid in winner_hits}
        for sid in range(10, 18)
    ]
    merged = _merge(prior, winner)
    by_sid = {r["sample_id"]: r for r in merged}

    assert set(by_sid.keys()) == set(range(20))
    assert by_sid[10]["hit"] is True
    assert by_sid[11]["hit"] is False
    assert by_sid[19]["hit"] is False
    assert all(by_sid[i]["hit"] is True for i in range(10))
    assert _merge(prior, []) == prior


# 3. Electing a round winner


def _ruler(
    delta: dict[int, float],
    *,
    mu: float = 0.0,
    sigma: float = 2.0,
    se: float | dict[int, float] = 0.5,
) -> DeltaRuler:
    """A locked ruler over a bare δ map — the shape most numeric tests care about.

    ``se`` per cell rather than flat is what the acquisition tests bend: the whole question
    there is which of two cells at the SAME difficulty a round buys, and a flat map cannot ask
    it."""
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
    """Per-round-resubset drift guard. Two candidates measured on DIFFERENT subsets:
    the high-accuracy one only saw easy samples; the lower-accuracy one cleared HARD
    samples the origin always misses. Raw subset accuracy would crown the easy candidate;
    difficulty-adjusted ability (θ) — the gating metric — must crown the abler one.

    Silent harm: under per-round resubset the wrong winner is promoted with no error —
    the run completes, the dashboard looks fine, the lineage decays toward whoever drew
    the gentlest samples. The election ranks θ on the cycle's fixed δ ruler, so it does not.
    """
    from promptpotter.application.scoring.selection import elect_round_winner

    # Fixed δ ruler: easy {0..19} low difficulty, hard {20..39} high — the bank the election reads.
    ruler = _ruler({i: (-1.5 if i < 20 else 1.5) for i in range(40)})
    # Origin spans all 40: easy {0..19} hit, hard {20..39} missed.
    origin = [measurement(i, float(i < 20)) for i in range(40)]
    # Easy candidate: 16/20 on easy samples the origin also hits → accuracy 0.80, modest lift.
    weak_on_easy = [measurement(i, float(i < 16)) for i in range(20)]
    # Able candidate: 14/20 on HARD samples the origin always misses → accuracy 0.70, bigger lift.
    able_on_hard = [measurement(i, float(i < 34)) for i in range(20, 40)]
    results_by_id = {"weak_on_easy": weak_on_easy, "able_on_hard": able_on_hard}

    # Raw subset accuracy crowns the easy candidate (0.80 > 0.70)...
    assert sum(r["hit"] for r in weak_on_easy) / 20 > sum(r["hit"] for r in able_on_hard) / 20
    # ...but the θ-gated election crowns the abler one — it cleared HARD items (high δ), stronger
    # evidence of ability than more wins on easy items (low δ) everyone already passes.
    winner_id, abilities = elect_round_winner(
        ["weak_on_easy", "able_on_hard"], results_by_id, origin, 4, ruler, parent_bias=0.0
    )
    assert winner_id == "able_on_hard"
    # The fit rides out so the caller stamps θ from the same election fit (no second fit):
    # the abler candidate's θ outranks the easy one's despite the lower raw accuracy.
    assert abilities.theta["able_on_hard"] > abilities.theta["weak_on_easy"]


def test_a_thin_arm_cannot_win_on_a_margin_inside_its_own_noise() -> None:
    """`coverage_floor` IS PoBB's `n_min`, so every cut arm clears it and reaches the election.
    Ranked on a bare point estimate, a thin arm out-points a full panel on a margin smaller than
    its own SE.

    Silent harm: a winner is crowned, every number renders, and the lineage descends from a margin
    the round could not measure."""
    from promptpotter.application.intelligence.exploration import (
        PARENT_ABILITY_ID,
        candidate_abilities,
    )
    from promptpotter.application.scoring.selection import elect_round_winner
    from promptpotter.shared.statistics import p_exceeds

    ruler = _ruler(dict.fromkeys(range(28), 0.0))
    origin = measurements([1.0] * 14 + [0.0] * 14)
    deep = measurements([1.0] * 21 + [0.0] * 7)  # full panel, a well-measured gain
    shallow = measurements([1.0] * 5 + [0.0])  # cut at n_min, higher RATE on 1/6 the evidence

    ab = candidate_abilities({"deep": deep, "shallow": shallow}, origin, ruler)
    theta_p, se_p = ab.theta[PARENT_ABILITY_ID], ab.theta_se[PARENT_ABILITY_ID]
    p = {c: p_exceeds(ab.theta[c], ab.theta_se[c], theta_p, se_p) for c in ("deep", "shallow")}

    # The thin arm's POINT lift is larger, on nearly twice the SE — so it demonstrated less.
    assert ab.theta["shallow"] > ab.theta["deep"]
    assert ab.theta_se["shallow"] > 1.8 * ab.theta_se["deep"]
    assert p["deep"] > p["shallow"]
    args = (["deep", "shallow"], {"deep": deep, "shallow": shallow}, origin, 6, ruler)
    assert elect_round_winner(*args, parent_bias=0.0)[0] == "deep"

    # Ranking on P must never DISQUALIFY — that is what separates it from the `- theta_se` shrink
    # it replaced, which turned a wide-posterior gain negative and crowned nobody.
    assert (
        elect_round_winner(["shallow"], {"shallow": shallow}, origin, 6, ruler, parent_bias=0.0)[0]
        == "shallow"
    )


def test_the_bar_is_what_the_parent_can_do_not_the_draw_that_crowned_it() -> None:
    """A winner is the MAXIMUM over its round's electable arms, so its θ carries that round's
    largest noise draw and not just its ability. Nothing washes it out — ``rescore_parent``
    replays the winner's own cached rows, so the same inflated estimate is re-fit bit-for-bit
    every round after. ``parent_selection_bias`` subtracts E[max of k standard normals] × the
    winner's OWN SE; the rank reads the corrected bar. Admission does not: an arm must beat
    the parent's measured θ on its own, so the credit can reorder admitted arms but never crown
    a tie or a trailing arm.
    """
    import math

    from promptpotter.application.intelligence.exploration import (
        candidate_abilities,
        theta_lift_over_parent,
    )
    from promptpotter.application.scoring.selection import (
        elect_round_winner,
        parent_selection_bias,
    )

    def won(*, se: float | None, electable: int) -> RoundResult:
        """A round that selected ``w`` over ``electable`` arms."""
        return round_result(
            1,
            electable_count=electable,
            selected_labels=["w"],
            candidate_scores=[scored_candidate("w", theta=0.5, theta_se=se)],
        )

    def held() -> RoundResult:
        return round_result(1, prompt_fields={})

    # ---- the term itself -------------------------------------------------------------
    # k=2 has a closed form — E[max of two standard normals] is 1/√π — so the table's entries
    # are checkable against arithmetic rather than against themselves.
    assert parent_selection_bias([won(se=1.0, electable=2)]) == pytest.approx(
        1 / math.sqrt(math.pi), abs=5e-5
    )
    # Linear in the winner's own SE: a sharply-measured winner carries almost no curse.
    assert parent_selection_bias([won(se=0.25, electable=2)]) == pytest.approx(
        0.25 / math.sqrt(math.pi), abs=2e-5
    )
    # A LONE electable arm was selected against nothing, so there is no maximum and no curse —
    # and the default ``electable_count`` of 0 clamps into that same safe end, never inventing
    # a correction for a round that never recorded how many arms it had.
    assert parent_selection_bias([won(se=0.9, electable=1)]) == 0.0
    assert parent_selection_bias([round_result(1, selected_labels=["c0"])]) == 0.0
    # Monotone in k, and CLAMPED past the table's end rather than extrapolated or IndexError-ing
    # on a round this loop does not produce.
    by_k = [parent_selection_bias([won(se=1.0, electable=k)]) for k in range(1, 7)]
    assert by_k == sorted(by_k) and by_k[0] == 0.0
    assert parent_selection_bias([won(se=1.0, electable=99)]) == pytest.approx(by_k[-1])

    # The STANDING parent is what the next round must beat, so the term is the one that crowned
    # it — the most recent round with a winner. A HELD round crowned nobody and must be walked
    # PAST, not read as "no curse": reading the newest round unconditionally zeroes the term for
    # every round after the first hold, which is most of a long cycle.
    assert parent_selection_bias(
        [won(se=0.40, electable=6), held(), won(se=0.10, electable=2), held()]
    ) == pytest.approx(0.10 / math.sqrt(math.pi), abs=2e-5)
    # No crowned round at all, and a winner with no θ fit (a cold ruler stamps none): 0.0, the
    # safe end again — an absent SE may not be defaulted into a correction nobody measured.
    assert parent_selection_bias([held(), held()]) == 0.0
    assert parent_selection_bias([]) == 0.0
    assert parent_selection_bias([won(se=None, electable=4)]) == 0.0

    # ---- and what it may NOT do to an election ----------------------------------------
    # The credit reorders admitted arms; it never admits one. Live rounds crowned an arm at
    # exactly the parent's θ ("+0.000 over the parent + 0.323 parent selection bias") and arms
    # BELOW it (-0.061, -0.072), each won on the credit alone.
    ruler = _ruler(dict.fromkeys(range(28), 0.0))
    parent = measurements([1.0] * 15 + [0.0] * 13)
    trailing = measurements([1.0] * 13 + [0.0] * 15)  # two cells behind on the same panel
    leading = measurements([1.0] * 17 + [0.0] * 11)
    tie = measurements([1.0] * 15 + [0.0] * 13)
    assert theta_lift_over_parent(candidate_abilities({"c": trailing}, parent, ruler), "c") < 0.0

    def elect(arm: list[Any], bias: float) -> str:
        return elect_round_winner(["c"], {"c": arm}, parent, 6, ruler, parent_bias=bias)[0]

    big = parent_selection_bias([won(se=5.0, electable=6)])
    for bias in (0.0, big):
        assert elect(trailing, bias) == "", "a trailing arm won on the credit"
        assert elect(tie, bias) == "", "a tie won on the credit"
        assert elect(leading, bias) == "c"


def _cs(
    *,
    candidate_id: str,
    accuracy: float,
    outcome: ArmOutcome = ArmOutcome.MEASURED,
    degradation_context: dict | None = None,
    elimination_context: dict | None = None,
) -> ScoredCandidate:
    return ScoredCandidate(
        run_id=None,
        candidate_id=candidate_id,
        label=candidate_id,
        changes_description="",
        accuracy=accuracy,
        composite_fitness=accuracy,
        total=20,
        evaluators={},
        outcome=outcome,
        degradation_context=degradation_context or {},
        elimination_context=elimination_context or {},
    )


def test_leader_eligibility_bars_invalid_measurement_not_stops():
    """Winner-selection eligibility: fatal degradation disqualifies; a true PoBB *loss*
    (p_best < epsilon, lead not locked) disqualifies — the eliminator's own verdict that the
    candidate isn't the best; a LEADER_LOCKED stop stays eligible; a clean loser stays eligible.
    """
    fatal = _cs(
        candidate_id="C1.3",
        accuracy=0.8333,
        outcome=ArmOutcome.BROKEN,
        degradation_context={"fatal": True, "dominant_warning": "llm_only:empty_response"},
    )
    # A STOP IS NOT A VERDICT. A PoBB-stopped candidate stays electable: the stop said
    # "more samples will not change the answer", which is a budget fact, not a ranking one.
    # Reading it as a loss cost a real round — a candidate cut at 19/28 carried a genuine
    # +0.099 theta lift over origin and was the best thing measured, but its stop recorded
    # `p_best: 0.0` (a placeholder the futility gate never computed) and eligibility read
    # that as "PoBB says it lost". Every candidate in that round stopped the same way, so
    # the round crowned nobody. SILENT: `improved=False`, no winner, no reason recorded, and
    # the loop reports a flat cycle rather than a discarded improvement.
    pobb_stopped = _cs(
        candidate_id="C1.2",
        accuracy=0.40,
        outcome=ArmOutcome.ELIMINATED,
        elimination_context={"p_best": 0.048, "epsilon": 0.05, "gate": "epsilon"},
    )
    leader_locked = _cs(
        candidate_id="C1.1",
        accuracy=0.55,
        outcome=ArmOutcome.LOCKED_IN,
        elimination_context={"p_best": 0.96, "gate": "lock_in"},
    )
    clean_loser = _cs(candidate_id="C1.4", accuracy=0.45)

    # Only INVALID measurement disqualifies; ranking is the election's job alone.
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
    """A cycle of a peer ``optimizer`` over a sixteen-row bank, whose backend answers a cell right
    where ``solves(prompt, sample)`` says, and errors on it where that says ``None``."""
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
    session = Session(
        store=built_stores,
        backend_id="",
        backend_client=types.SimpleNamespace(  # type: ignore[arg-type]
            max_cells_in_flight=1,
            cancel_stops_billing=True,
            holds_own_sends=True,
            derives_spend_bounds=False,
            backpressure=types.SimpleNamespace(reading=lambda: None),
        ),
        pipeline_schema=schema,
        samples=bank,
        dataset_name=f"{optimizer}-e2e",
    )
    session.source = RunSource.OPTIMIZATION_LOOP
    session.state.ledger = CycleEventLog.open(CycleDir(tmp_path / "cycle"))
    # The campaign's composite REWARDS length, the opposite of CAPO's objective: CAPO's population
    # must still be kept by its own reading, while LEVI's archive ranks on this formula itself.
    session.scoring.scorer = compile_scorer(
        "label_match(predicted, ground_truth)",
        "fitness * min(1.0, target_prompt_chars / 200.0)",
        verifier_graded=False,
    )
    session.scoring.partition = partition_bank(bank, config.dataset_split)

    async def _measure(sample: Sample, _session: Any, *, pipeline_params: Any) -> dict:
        prompt = pipeline_params["solve"]["prompt"]
        solved = solves(prompt, sample)
        row = {
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
        return rescore_results([row], session.scoring.scorer)[0]

    monkeypatch.setattr(query_loop, "measure_sample", _measure)
    origin = OptSearchPoint(instruction="Answer.", answer_format="Reply with the letter.")
    framing = TaskDecomposition(pipeline_purpose="Answer each query with its letter.")
    search = list(session.scoring.partition.search)
    origin_rows = [
        asyncio.run(_measure(s, session, pipeline_params={"solve": {"prompt": "Answer."}}))
        for s in search[:4]
    ]
    sp = origin.to_job_search_point(
        base_pipeline_params=None,
        schema=schema,
        framing=framing,
        demo=session.scoring.require_partition().demo,
    )
    cycle = Cycle.start(
        origin,
        scored_candidate(
            origin.lineage.id,
            label="C0",
            accuracy=0.0,
            composite_fitness=origin_composite,
            prompt_fields=origin.prompt_field_dict(),
            resolved_pipeline_params=sp.config_params,
            sp_hash=sp.sp_hash(schema),
        ),
        schema=schema,
        framing=framing,
        origin_results=origin_rows,
        session=session,
        config=config,
    )
    bind_optimizer(cycle.optimizer)
    return cycle


_noop = lambda *_a, **_k: None  # noqa: E731


_QUIET_CALLBACKS = types.SimpleNamespace(
    on_phase=_noop,
    announce_candidate=_noop,
    on_sample_scored=_noop,
    on_sample_started=_noop,
    on_candidate_scored=_noop,
    on_race_standing=_noop,
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
    """A spend ceiling reached mid-race ends the walks and the round still elects: the population
    is kept among the arms that reached the coverage floor. The arm cut at one cell reads perfect
    on it, so electing it would carry a prompt nobody measured. Round 1's parent is the origin, no
    racing arm: it is read on the cells run init banked, and a cell bought for it would unwind the
    round. Silent if wrong: the round closes with a population either way."""
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
        "capo_crossover": {"config": {"crossovers": 2}},
        "few_shot": {"config": {"k_max": 0}},
        "population": {"config": {"size": 3}},
    }
    paid: list[int] = []

    def even_cells(_prompt: str, sample: Sample) -> bool:
        paid.append(sample.id)
        return sample.id % 2 == 0

    cycle = _peer_cycle(built_stores, tmp_path, monkeypatch, "capo", nodes, even_cells)
    monkeypatch.setattr(paper_templates, "llm_call", _llm)
    session = cycle.session
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
                on_sample_scored=None,
                on_sample_starting=None,
            )
        )
    else:
        first, cut = asyncio.run(execute_round(cycle, 1, search, _QUIET_CALLBACKS))  # type: ignore[arg-type]
        assert cut is None
        cycle.absorb_round(first)

    paid.clear()

    # The shipping control over a book whose ceiling is reached once `budget` cells are paid.
    session.control = RunControl(
        book=SpendBook(
            usd_cap=lambda: 0.0 if len(paid) >= budget else None,
            tokens_cap=lambda: None,
            usd_reserve=lambda: None,
            tokens_reserve=lambda: None,
            meters="bill",
        )
    )
    closed, cut = asyncio.run(execute_round(cycle, cut_round, search, _QUIET_CALLBACKS))  # type: ignore[arg-type]
    assert cut is StopReason.SPEND_BUDGET
    # The controller routes on this round before anything closes it, so the verdict rides the
    # round as built: an ungraded one starves no node and escalation never hears of it.
    assert closed.health is not None and closed.health.samples == len(closed.results)
    rows = closed.all_candidate_results
    assert sum(len(arm) for arm in rows.values()) == replayed + len(paid), "a cell nobody elects on"
    [reference] = closed.reference_results.values()
    assert len(reference) == parent_cells, "the parent is read where the archive holds it"
    [short] = [cs for cs in closed.candidate_scores if len(rows[cs.candidate_id]) == 1]
    assert short.accuracy == 1.0, "the cut arm reads perfect on its one cell"
    carried = [ind.lineage.id for ind in closed.optimizer_state.payload.population]
    assert len(carried) == 3 and short.candidate_id not in carried, "elected below the floor"
    full = next(cs for cs in closed.candidate_scores[3:] if cs is not short)
    assert full.candidate_id in carried, "an offspring the budget paid for in full was dropped"


def test_the_bench_grades_the_pick_the_optimizer_declared_over_a_higher_composite_round(
    built_stores, tmp_path, monkeypatch
) -> None:
    """The bench grades the optimizer's LAST selection. C0's composite, read on its own rows, tops
    the round CAPO selected in, read on others; a bench that picks by that cross-round comparison
    grades the origin as the selection and serves a zero lift. Silent: every number renders."""

    async def _llm(messages: list[dict], **_kw: Any) -> Any:
        if "Create overall 15 prompts" in messages[0]["content"]:
            return types.SimpleNamespace(content=json.dumps(["Solve it GOOD.", "Solve it OK."]))
        return types.SimpleNamespace(content="<prompt>Solve it BAD.</prompt>")

    nodes = {
        "blocks": {"config": {"block_size": 4, "max_blocks": 2}},
        "capo_crossover": {"config": {"crossovers": 1}},
        "few_shot": {"config": {"k_max": 0}},
        "population": {"config": {"size": 2}},
    }
    solves = lambda prompt, s: "GOOD" in prompt or ("OK" in prompt and s.id % 2 == 0)  # noqa: E731
    cycle = _peer_cycle(
        built_stores, tmp_path, monkeypatch, "capo", nodes, solves, bench=4, origin_composite=0.9
    )
    monkeypatch.setattr(paper_templates, "llm_call", _llm)
    session = cycle.session
    search = list(session.scoring.require_partition().search)
    picked = cycle.absorb_round(asyncio.run(execute_round(cycle, 1, search, _QUIET_CALLBACKS))[0])  # type: ignore[arg-type]
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
            cycle.searchpoint(origin.lineage.id),
            subject="origin",
            label="C0",
            round_num=0,
            cb=_QUIET_CALLBACKS,  # type: ignore[arg-type]
        )
    )

    def banked(origin_pass: BenchPass) -> BenchPasses:
        return BenchPasses(
            tolerance=0, origin=origin_pass, reserve_usd=0.0, reserve_tokens=0, selected=None
        )

    passes = asyncio.run(
        bench_selection(cycle, session, banked=banked(origin_pass), cb=_QUIET_CALLBACKS)  # type: ignore[arg-type]
    )
    bench = headline(_QUIET_CALLBACKS, session, passes)  # type: ignore[arg-type]
    assert (bench.selected.round, bench.selected.sp_hash) == (1, picked.selected_scores[0].sp_hash)
    assert (bench.selected.accuracy.value, bench.origin.accuracy.value) == (1.0, 0.0)
    assert bench.headline_lift.value == 1.0, "GOOD solves them all, and the headline is accuracy"
    # Each column's point, band and lift read ONE per-row series. The composite's misses keep a
    # cost share, so its lift is not the accuracy gap, and a band folded off the other column's
    # series misses its own point.
    for reading in (bench.origin, bench.selected):
        assert reading.composite.ci_lo <= reading.composite.value <= reading.composite.ci_hi
    composite_gap = bench.selected.composite.value - bench.origin.composite.value
    assert bench.lift.composite.value == pytest.approx(composite_gap) and composite_gap < 1.0
    result = _build_cycle_result(
        cycle,
        None,
        session,
        stop_reason=StopReason.MAX_ROUNDS,
        cycle_error=None,
        started_at="",
        finished_at="",
        spend=None,
        bench=bench,
    )
    assert result.result_round == bench.selected.round, "the result names the round graded"
    # The export carries that composite headline beside the round's own reading, whose lift is
    # ACCURACY's over the parent: the bar it pairs with is the parent's accuracy, not its composite.
    won = scored_candidate(
        "C1.1",
        accuracy=0.75,
        composite_fitness=0.40,
        reference_accuracy=0.50,
        reference_composite=0.60,
        reference_lift=0.25,
        reference_lift_ci_lo=0.05,
        reference_lift_ci_hi=0.45,
    )
    m = build_prompt_export(
        round_result(1).model_copy(update={"candidate_scores": [won], "selected_labels": ["C1.1"]}),
        **dict.fromkeys(("tool_version", "campaign_id", "cycle_id", "dataset_name"), ""),
        **dict.fromkeys(("dataset_hash", "finished_at"), ""),
        stop_reason=StopReason.MAX_ROUNDS,
        treatment=None,
        formula=None,
        origin_accuracy=None,
        origin_composite_fitness=None,
        framing=cycle.framing,
        demo=(),
        bench=bench,
    ).measurement
    assert (m.reference_lift, m.reference_accuracy) == (0.25, 0.50)

    # A backend refusing the first bench row aborts the pass. Its one errored row is no 0.0 over
    # one row: the pass yields no reading, and the headline says why in its place.
    from promptpotter.shared.errors import ErrorCategory

    async def _refused(sample: Sample, _session: Any, *, pipeline_params: Any) -> dict:
        row = {
            "sample_id": sample.id,
            "sample_key": sample.key,
            "query": sample.query,
            "ground_truth": sample.ground_truth,
            "predicted": "ERROR",
            "error": "HTTP 403 — caller config rejected by backend :: model is not allowed",
            "error_category": ErrorCategory.CLIENT,
            "cached": False,
            "pipeline_data": {},
        }
        return rescore_results([row], _session.scoring.require_scorer())[0]

    answering = query_loop.measure_sample
    monkeypatch.setattr(query_loop, "measure_sample", _refused)
    read = {bench.selected.sp_hash, bench.origin.sp_hash}
    unread = next(cs for cs in picked.candidate_scores if cs.sp_hash not in read)
    refused = asyncio.run(
        score_on_bench(
            session,
            cycle.searchpoint(unread.candidate_id),
            subject="selected",
            label=unread.label,
            round_num=1,
            cb=_QUIET_CALLBACKS,  # type: ignore[arg-type]
        )
    )
    assert refused.stopped is not None
    monkeypatch.setattr(query_loop, "measure_sample", answering)
    cut = headline(
        _QUIET_CALLBACKS,  # type: ignore[arg-type]
        session,
        asyncio.run(
            bench_selection(cycle, session, banked=banked(refused), cb=_QUIET_CALLBACKS)  # type: ignore[arg-type]
        ),
    )
    assert (cut.origin, cut.selected) == (None, bench.selected)
    assert (cut.lift.accuracy, cut.lift.composite) == (None, None)
    assert cut.missing_reason is not None and "model is not allowed" in cut.missing_reason


# 4. Elimination — who is cut, and when


def test_pobb_epsilon_ramps_in_and_an_arm_behind_is_cut_to_the_last_cell():
    """The ε bar is ``epsilon_floor`` at ``n_min``, ramps to ``epsilon`` over the next ``n_min``
    cells and holds it to the panel's end; equal floor and ε — the manifest's — leaves it flat.
    Past the ramp nothing reprieves an arm: one clearly behind two cells from the end is cut.

    Silent harm: a bar that sinks, or a guard that stops cutting, near the end of the panel keeps
    measuring an arm its cells already ruled out, and PoBB then cuts only in a narrow early band.
    The depths below come from the dispersion rule (`fit_theta_given_delta`) and from
    `elimination_p_best` refusing a one-cell verdict; re-derive them if either changes."""
    cfg = pobb_knobs(epsilon=0.30, epsilon_floor=0.15)
    graded = PoBBCheck(cfg, n_min=6, n_samples=28, ruler=None)
    assert graded.epsilon_at(6) == pytest.approx(0.15)
    assert graded.epsilon_at(9) == pytest.approx(0.225)
    assert graded.epsilon_at(12) == pytest.approx(0.30)
    # Clamped, never extrapolated, and never lowered again before the last cell.
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
        check.register_completed(measurements([1.0] * 28), candidate_id="winner")
        check.set_current("arm")
        return check.check(measurements([0.0] * misses + [1.0] * (n - misses)))

    # A single adverse cell caps p_best at 0.25 (`sign_posterior`), so the ramp below it spares it.
    assert arm_behind_perfect_prior(6, 1) is None
    assert arm_behind_perfect_prior(9, 1) is None
    cut = arm_behind_perfect_prior(9, 2)
    assert cut is not None
    # The bar that FIRED is what the decision archives — a reader must see the ramped 0.225 at
    # n=9, never the configured 0.30, or the record cannot explain its own cut.
    assert cut.check_result["epsilon"] == pytest.approx(0.225)
    # Two behind is still cut at the floor: the reprieve is for a width, not for a loser.
    assert arm_behind_perfect_prior(6, 2) is not None
    late = arm_behind_perfect_prior(26, 4)
    assert late is not None and late.outcome is ArmOutcome.ELIMINATED
    assert late.check_result["epsilon"] == pytest.approx(0.30)

    # The lucky-prefix trap: a leader 8/8 on the easy prefix must not eliminate an arm measured
    # on hard cells the leader never answered. A prior that does not cover the arm's cells is
    # excluded from the pairing, so nothing is cut on a comparison nobody made.
    unpaired = PoBBCheck(pobb_knobs(epsilon=0.05), n_min=4, n_samples=20, ruler=None)
    unpaired.register_completed(
        measurements([1.0] * 8, sample_ids=list(range(8))), candidate_id="lucky_leader"
    )
    unpaired.set_current("challenger")
    assert unpaired.check(measurements([0.0] * 5, sample_ids=[9, 12, 13, 14, 8])) is None


def test_the_collapse_gate_reads_the_answer_not_the_labels() -> None:
    """A verifier-graded round carries no ground truths, and the gate that cuts a constant answerer
    at `n_min` keyed on a truth SET — so on every benchmark the preprint runs it was permanently
    False, and an arm that had stopped answering measured its whole budget before `l1_score`
    dropped it anyway.

    Labels only ever PROVED that one answer to every cell must be wrong. A verifier proves the same
    thing by leaving a cell unsolved — which is why an arm answering alike and solving every cell
    is NOT collapsed: that one is degenerate and correct, and cutting it would cost the round its
    best arm.

    Silent harm: nothing errors in either direction. The cut never fires, the θ posterior spends
    the arm's full budget establishing what six cells had shown, and the L1 panel that renders a
    COLLAPSED cut as a verdict on the idea (rather than as a stopped measurement) has none to
    render, so the idea comes back next round."""
    from promptpotter.shared.errors import ErrorCategory

    def rows(fitness: list[float], **over: Any) -> list[Any]:
        return [
            {"sample_id": i, "ground_truth": "", "predicted": "done", "fitness": f, **over}
            for i, f in enumerate(fitness)
        ]

    def cut(rs: list[Any]) -> Any:
        return PoBBCheck(pobb_knobs(), n_min=6, n_samples=28, ruler=None).check(rs)

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

    # With labels the proof is the truth set itself: one answer against two truths. And a
    # collapse cut returns before the posterior, so it carries no ε field — read through them it
    # would render four numbers nobody measured.
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
    """The PoBB ε-gate must read GRADED responses, not binarized hits.

    Silent harm: on a graded backend (L4 outer, reciprocal-rank) every ``hit`` is
    False, so a binarized gate fits identical all-0 θ for every arm and pins
    ``p_best = 0.5`` forever — elimination never discriminates, with no error.
    Graded inputs must separate a plainly-better candidate.

    And the ε bar is absolute, so the posterior must not spend confidence the pairs never bought:
    concordant cells move θ but say nothing about which arm is better. Where one discordant cell
    reads as decisive, every arm of every round is cut at an identical p_best. The bar must not be
    reachable on one cell, and must stay reachable on three.
    """
    from promptpotter.application.scoring.selection import elimination_p_best
    from promptpotter.domain.ruler import DeltaRuler

    sids = list(range(12))
    ruler = None  # cold ruler — flat δ, the common early-cycle case

    # Graded regime: candidate consistently outscores the prior; hit would be all-0.
    strong = [0.66] * 12
    weak = [0.30] * 12
    p_best_strong, _ = elimination_p_best(strong, {"prior": weak}, sids, ruler)
    assert p_best_strong > 0.9, f"graded gate failed to discriminate: {p_best_strong}"
    p_best_weak, _ = elimination_p_best(weak, {"prior": strong}, sids, ruler)
    assert p_best_weak < 0.1
    # Identical grades ⇒ genuinely undecided.
    p_best_tie, _ = elimination_p_best(weak, {"prior": list(weak)}, sids, ruler)
    assert abs(p_best_tie - 0.5) < 1e-9

    epsilon = pobb_knobs().epsilon
    prefix = list(range(6))
    # `sealqa-longseal-12`'s own geometry: the probe the ordering lands at slot 4 is also the
    # EASIEST cell in the prefix, which is what let the fit read one cell as decisive.
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

    thin_prior = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0]  # that probe, and nothing else
    p_thin, _ = elimination_p_best(candidate, {"prior": thin_prior}, prefix, warm)
    assert p_thin == pytest.approx(0.25), f"one adverse cell may not reach past 0.25: {p_thin}"
    assert p_thin > epsilon, "a one-cell verdict must not be cuttable (unbounded: .11)"

    wide_prior = [1.0, 0.0, 1.0, 1.0, 0.0, 0.0]  # a prior that genuinely outscored it
    p_wide, _ = elimination_p_best(candidate, {"prior": wide_prior}, prefix, warm)
    assert p_wide == pytest.approx(0.0625), f"three adverse cells support a cut: {p_wide}"
    assert p_wide < epsilon, "the width is there — ε decides, as it always did"


def test_a_fatal_row_ends_a_candidate_on_one_sighting_and_an_advisory_never_does() -> None:
    """``DegradationCheck`` is what stops paying for an arm the backend has broken, and both
    directions are silent. Fast-cutting on an ADVISORY warning eliminates an arm that is scoring
    fine — `web_search:low_document_count` fires whenever fewer than max_sites docs are gathered,
    so on a slow index it would cut every candidate in the round and the loop would report a
    winner nobody could beat. Not cutting on a FATAL one keeps buying cells from a node that
    cannot answer, and every one of them lands in the panel as a measured miss.

    The rate arm is the same fact one step slower: only genuinely-deprecated rows count toward the
    threshold, so an arm at 2/6 advisory survives while 3/6 fatal does not."""

    def warn(kind: str | None) -> dict:
        warning = {"step": "entity_profiling", "code": "json_validate_failed"}
        if kind is not None:
            warning["kind"] = kind
        return {"pipeline_data": {"diagnostics": {"warnings": [warning]}}}

    check = DegradationCheck(threshold=0.4, min_samples=3)

    # ONE fatal sighting ends it, before `min_samples` is even reached.
    sig = check.check([measurement(0, 1.0), {**measurement(1, 1.0), **warn("structural")}])
    assert sig is not None
    assert sig.check_result["fatal"] is True

    # An advisory sighting is not an elimination at ANY depth — and neither is a warning the
    # backend never stamped: the source stamp is the only structural signal, so an unstamped
    # one under-counts rather than over-eliminating.
    for kind in ("transient", None):
        assert check.check([{**measurement(i, 1.0), **warn(kind)} for i in range(6)]) is None

    # Below `min_samples` a non-fatal round decides nothing rather than deciding on two rows.
    assert check.check([measurement(0, 1.0), measurement(1, 0.0)]) is None

    # The rate arm, with the fast path off so the threshold is what is under test.
    rated = DegradationCheck(threshold=0.4, min_samples=3, fatal_fastpath=False)
    fatal_row = {**measurement(0, 0.0), **warn("structural")}
    clean = [measurement(i, 1.0) for i in range(1, 6)]
    assert rated.check([fatal_row, *clean[:4]]) is None  # 1/5 = 0.2, under the bar
    cut = rated.check([fatal_row, {**fatal_row, "sample_id": 9}, *clean[:2]])
    assert cut is not None and cut.check_result["degraded_rate"] == pytest.approx(0.5)


def test_unscoreable_cells_counts_holes_but_not_stops_or_deprecated_rows() -> None:
    """A HOLE is a cell that was attempted and returned nothing — not a stop, not a retry.

    The silent direction is the false NEGATIVE. If this stops recognising an errored row,
    the panel gate never fires and rounds resume being elected on incomplete comparisons —
    which is the original defect, and it ran a whole campaign without anyone noticing: two
    of C1.1's six cells returned no measurement, it was ranked against a rival measured on
    a different five, and it won.

    The two near-misses are pinned in the other direction because the obvious arithmetic
    (``scored_samples - total``) counts both, and both occur on real runs:

    * a PoBB-eliminated candidate simply stopped early — those cells were never attempted;
    * a *deprecated* row is a sample the classifier marked fatal — ``content_empty`` where
      the retry beside it never produced an answer either. It carries no ``error_category``
      and is already excluded from ``total``, so the arithmetic form would halt an otherwise
      healthy cycle; through the L4 recursion a halted inner cycle is itself unscoreable, so
      one such sample would take the whole outer run down.

    The row that motivated this guard was NOT of that kind: it carried ``content_empty`` and
    answered on the retry, and only reached here because ``classify_result`` read an
    attempt-level advisory as a verdict on the result. That is fixed at the predicate now
    (``domain/results_health.py``), so a recovered retry is an ordinary scored row and never needs
    this protection — which stays, for samples that really did come back empty.
    """
    from promptpotter.application.bench.resume_and_fork.repair import repair_cut
    from promptpotter.config.settings import NO_RESULT
    from promptpotter.domain.results import unscoreable_cells
    from promptpotter.domain.results_health import is_deprecated
    from promptpotter.shared.errors import ErrorCategory

    def row(sample_id: int, **extra: Any) -> dict[str, Any]:
        return {"sample_id": sample_id, "predicted": "TRUE", **extra}

    # The real C1.1 shape: six attempted cells, two returned no measurement.
    holed = [row(i) for i in range(4)] + [
        row(4, predicted="ERROR", error_category="UNKNOWN", error="ran past its deadline"),
        row(5, predicted="ERROR", error_category="UNKNOWN", error="ran past its deadline"),
    ]
    assert unscoreable_cells(holed) == 2

    # The real C1.2 shape: PoBB cut it at five cells. Complete, not holed.
    assert unscoreable_cells([row(i) for i in range(5)]) == 0
    assert unscoreable_cells([]) == 0

    # The real inner C2.2 shape: a fatal-classified transient with NO error_category. The
    # retry left nothing extractable, which is what separates it from the recovered row in
    # ``test_content_empty_on_a_result_that_answered_is_not_an_empty_response``.
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
    assert is_deprecated(deprecated), "fixture drift — this row must classify as deprecated"
    assert unscoreable_cells([row(0), row(1), row(2), deprecated]) == 0, (
        "a classifier-deprecated sample was counted as a hole — the gate would halt a "
        "healthy cycle on a transient retry the loop already handles"
    )

    # A cell a declared bound CUT is settled, not incomplete — the same declaration cuts the next
    # attempt at the same place. Counted as a hole a resume can plug, every resume branches the
    # cycle, re-buys the cell at full price and lands the identical row, without bound.
    def _rows(category: ErrorCategory) -> list[dict[str, Any]]:
        return [
            measurement(0, 1.0),
            measurement(1, None, error="no verdict", error_category=category),
        ]

    halted = round_result(
        1, candidates_scored=1, all_candidate_results={"c0": _rows(ErrorCategory.HALTED)}
    )
    assert repair_cut([halted]).rounds == []

    # The other unscoreable arm is unchanged: the cell ran to its own end, so a re-measure can
    # answer differently and the round genuinely does not re-derive until it does.
    holed = round_result(
        1, candidates_scored=1, all_candidate_results={"c0": _rows(ErrorCategory.UNSCOREABLE)}
    )
    assert repair_cut([holed]).rounds == [1]


# 5. Escalation and stop — live, and folded back on resume


def test_a_theta_stall_verdict_must_clear_its_own_error() -> None:
    """The escalation ladder advances on "did the cycle improve", and a θ rise inside its own
    standard error is not an improvement. A bare ``>`` counts one, resets the stall counter, and
    holds the ladder at L2 forever — with no error anywhere: every round completes, L2 fires, and
    L3 simply never arrives.

    Measured on `justlogic-d234__082126`, whose numbers this replays. Round 3's θ rose +0.012 on
    se 0.198 — six hundredths of one standard error — which reset ``_l2_stall_count`` to zero and
    cost exactly one round. L3 would then have fired at round 5, but a round's escalation runs
    AFTER it closes and round 5 closed on `max_rounds`, so L3 never fired at all; rounds 3-5 spent
    49% of the run's budget re-testing candidates against a panel that had stopped moving.

    Silent harm: nothing distinguishes "L2 keeps firing because it is working" from "L2 keeps
    firing because noise keeps clearing its stall counter"."""
    from promptpotter.application.optimizers.potter.escalation.state import (
        EscalationFSM,
        NextAction,
    )
    from promptpotter.application.optimizers.potter.knobs import EscalationLadder

    # (composite, θ, θ_se) per round, from the live run: composite frozen from round 2 on, θ
    # advancing once for real (+0.467) and then only by noise (+0.012, then flat).
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

    # The real +0.467 move at round 2 still counts — the bar rejects noise, not signal.
    assert ladder(with_se=True) == ["fire_l2", "fire_l2", "fire_l2", "fire_l3"]
    # Without it the noise move buys another L2 round and L3 is pushed out of the run.
    assert NextAction.FIRE_L3 not in ladder(with_se=False)

    # The bar is the reading's own SE, applied on the θ scale only: a composite-scale verdict has
    # no error term to clear and must be untouched by it.
    assert EscalationFSM._improved(0.0, 0.0, 0.35, 0.34, 0.20)[0] is False
    assert EscalationFSM._improved(0.0, 0.0, 0.60, 0.34, 0.20)[0] is True
    assert EscalationFSM._improved(0.7, 0.6, None, None, 0.20) == (True, "composite")


def test_the_campaign_ends_only_where_the_objective_is_spent_and_the_round_resolved() -> None:
    """The one stop the loop fires with no human in the way, so what it reads has to be worth
    ending a campaign on. It read `accuracy >= 1.0` — a bystander field. A round is ELECTED on the
    composite, and where that composite prices tokens, 100% correct at 3x the tokens is not a
    ceiling: there is still somewhere to go, and stopping there is the loop refusing the objective
    it was given. It is also a mean over whatever the acquisition bought, so it can read 1.00 on a
    round whose own arms cannot be told apart — `swiss-invoices-eval__b1b4f5` round 9, 1.00 over 20
    cells at p=0.33, `separable: false`, ended at 27% of budget, `index.json` then naming round 8
    its best. Silent by construction: `perfect_score` is a SUCCESS outcome and every number
    renders."""
    from promptpotter.application.optimizers.potter.escalation.state import (
        EscalationFSM,
        NextAction,
    )
    from promptpotter.application.optimizers.potter.knobs import EscalationLadder

    def outcome(objective: float, separable: bool | None) -> NextAction:
        return (
            EscalationFSM()
            .observe_round(
                improved=True,
                compared=True,
                separable=separable,
                current_objective=objective,
                l1_patience=3,
                escalation_ladder=EscalationLadder.FULL,
            )
            .next_action
        )

    # Round 9 as it ran: at the ceiling, and resolved nothing.
    assert outcome(1.0, False) == NextAction.CONTINUE
    # Unreadable is not "read and told nothing apart", and neither ends a campaign.
    assert outcome(1.0, None) == NextAction.CONTINUE
    # The same round scored under a cost-aware objective (A = its own C0 median tokens): 100%
    # accuracy, 1,943 tokens, .507. Accuracy has nothing left to win and the objective has plenty.
    assert outcome(0.507, True) == NextAction.CONTINUE
    # Spent on every declared term, on a round that resolved — the one shape worth stopping for.
    assert outcome(1.0, True) == NextAction.STOP_PERFECT


def test_the_l1_only_arm_can_reach_no_layer_above_it() -> None:
    """The ablation switch, and why it is a switch rather than a large ``l1_patience``: a deferral
    that never fires *in this run* is not a suppression, and the arm it produces is only as clean
    as the round budget that happened to bound it. The L1 / L1+L2 / full comparison is the
    sharpest result the preprint carries, so an arm that escalates once measured a different
    thing under the arm's name.

    Silent by construction: every arm completes, every round file renders, and an L2 fire that
    should not have happened reads exactly like one that should. Unrecoverable because the number
    is what gets published — a re-run does not un-report it.

    Proved over the WHOLE predicate space rather than a sample, because the harm is one rule
    nobody thought about."""
    from itertools import product

    from promptpotter.application.optimizers.potter.escalation.rules import (
        EscalationInputs,
        decide_escalation,
    )
    from promptpotter.application.optimizers.potter.escalation.state import NextAction
    from promptpotter.application.optimizers.potter.knobs import EscalationLadder

    grid = list(
        product(
            [None, 0.5, 1.0],  # current_objective
            [0, 1, 5],  # l1_stall_count
            [0, 3],  # l1_patience
            [None, True, False],  # separable
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
                    current_objective=objective,
                    l1_stall_count=stall,
                    l1_patience=patience,
                    escalation_ladder=ladder,
                    separable=separable,
                    axes_with_positive_yield=yield_axes,
                    l1_mandatory_breach=mandatory,
                    l1_zero_candidates=zero,
                    evidence_starved=starved,
                )
            ).next_action
            for objective, stall, patience, separable, yield_axes, mandatory, zero, starved in grid
        }

    # The arm's whole claim. `escalate_l2` has one caller and it is gated on FIRE_L2, so no
    # `l2_context` / `l3_plan` prompt is composable from any state in this space. A stall is
    # simply another L1 round; the objective ceiling still ends a resolved run.
    assert actions(EscalationLadder.L1) == {NextAction.CONTINUE, NextAction.STOP_PERFECT}
    # Not vacuous: the same states fire L2 on the full ladder, so this passes because the rule
    # preempts and not because the grid missed every firing shape.
    assert NextAction.FIRE_L2 in actions(EscalationLadder.FULL)


def test_a_heal_fire_spends_no_l3_patience() -> None:
    """A refused `l1_layout` edit fires L3 to heal it, and that fire set the reading L3's patience
    compares against. Under `l3_patience: 1` the first patience-driven L3 gate then found no
    advance over a reading taken moments into the run and stopped the cycle `converged`, origin
    still selected. Silent: `converged` is a success outcome."""
    from promptpotter.application.optimizers.potter.escalation.state import (
        EscalationFSM,
        LadderAsk,
        NextAction,
    )
    from promptpotter.application.optimizers.potter.knobs import EscalationLadder

    fsm = EscalationFSM()

    def stretch(theta: float, *, heal_first: bool) -> LadderAsk:
        """Asks at one flat reading until something other than L2 answers."""
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

    # The heal wipes L2's counters, so L2 takes its grace ask again; the gate it then reaches is
    # L3's FIRST patience ask, with nothing to compare against.
    gate = stretch(0.3, heal_first=True)
    assert gate.next_action is NextAction.FIRE_L3
    fsm.record_l3_fired(gate.l3)
    # A later heal at a higher reading must not re-base the comparison either: the advance since
    # the patience fire is still there to be seen.
    gate = stretch(0.6, heal_first=True)
    assert gate.next_action is NextAction.FIRE_L3
    fsm.record_l3_fired(gate.l3)
    # And the patience it did not spend still binds a flat run.
    assert stretch(0.6, heal_first=False).next_action is NextAction.STOP_L3_PATIENCE


def test_lives_resume_fold_matches_live_observe() -> None:
    """Resume-integrity: the banked-lives ("hearts") count rebuilt from the ledger's
    ``improved`` sequence (``EscalationFSM.fold``) must equal the live in-run count
    (``observe_round``). A mismatch is silent — a resumed run would grant a different
    round budget than the un-interrupted run, quietly changing how long it optimizes."""
    from promptpotter.application.optimizers.potter.escalation.state import (
        EscalationFSM,
        NextAction,
    )
    from promptpotter.application.optimizers.potter.knobs import EscalationLadder, LivesConfig
    from promptpotter.domain.phases import StopReason
    from promptpotter.domain.run_records import PhaseRecord

    cfg = LivesConfig(start=2, cap=4)
    # (improved, electable_count) — the streak saturates at cap, then a round where NOTHING
    # reached the election (every proposal rejected before it was scored) must cost no life,
    # then real stalls drain. Both halves must replay identically: an uncompared round banked
    # as a stall on one side and skipped on the other silently hands the resumed run a
    # different round budget, which is the whole harm this test exists for.
    sequence = [(True, 2), (True, 2), (True, 2), (True, 2), (False, 0), (False, 2), (False, 2)]

    live = EscalationFSM()
    live_trace: list[int | None] = []
    last_event = None
    for improved, electable in sequence:
        last_event = live.observe_round(
            improved=improved,
            compared=electable > 0,
            separable=None,
            current_objective=0.5,
            l1_patience=99,
            escalation_ladder=EscalationLadder.FULL,
            lives=cfg,
        )
        live_trace.append(live.lives)

    replay = EscalationFSM()
    replay_trace: list[int | None] = []
    # Round 0 leads, TWICE — the shape a real ledger has. The origin closes once at its own
    # `emit_origin_round` and again when the ruler warms at round 1 (`round.py::close_round`),
    # because its θ cannot be fit before a second arm exists. The live side banks neither: the
    # origin reaches `close_round` without going through `post_round`, so `observe_round` never
    # sees it. Folding them advanced the stall counter by two per resume and escalated to L2 early.
    for _ in range(2):
        replay.fold(
            PhaseRecord(
                phase="round",
                event="complete",
                round=0,
                payload={"improved": False, "electable_count": 0, "separable": None},
            ),
            lives=cfg,
        )
    assert (replay.lives, replay.l1_stall_count) == (None, 0), "round 0 banks nothing"

    for i, (improved, electable) in enumerate(sequence, start=1):
        replay.fold(
            PhaseRecord(
                phase="round",
                event="complete",
                round=i,
                payload={"improved": improved, "electable_count": electable, "separable": None},
            ),
            lives=cfg,
        )
        replay_trace.append(replay.lives)

    assert replay_trace == live_trace == [3, 4, 4, 4, 4, 3, 2]
    # The counter the two round-0 records used to inflate. Live: three trailing non-improving
    # rounds, one of them uncompared — all three advance the stall.
    assert replay.l1_stall_count == live.l1_stall_count == 3
    # And exhausting the bank on the resumed FSM stops with the same reason the live loop uses.
    replay.observe_round(
        improved=False,
        compared=True,
        separable=None,
        current_objective=0.5,
        l1_patience=99,
        escalation_ladder=EscalationLadder.FULL,
        lives=cfg,
    )
    exhaust = replay.observe_round(
        improved=False,
        compared=True,
        separable=None,
        current_objective=0.5,
        l1_patience=99,
        escalation_ladder=EscalationLadder.FULL,
        lives=cfg,
    )
    assert replay.lives == 0
    assert exhaust.next_action is NextAction.STOP_LIVES
    assert exhaust.stop_reason is StopReason.LIVES_EXHAUSTED
    assert last_event is not None  # streak never stopped mid-sequence

    # A round that crowned a winner and resolved NOTHING advances L1 patience, live and on replay
    # alike. The round sets ``improved`` so every surface reads it as a win, and the escalation
    # it should have triggered never happens — the loop re-asks a question the panel could not
    # answer. (improved, separable): a resolved win, then two that told no arm from the parent.
    unresolved_live, unresolved_replay = EscalationFSM(), EscalationFSM()
    for i, (improved, separable) in enumerate([(True, True), (True, False), (True, False)], 1):
        unresolved_live.observe_round(
            improved=improved,
            compared=True,
            separable=separable,
            current_objective=0.5,
            l1_patience=99,
            escalation_ladder=EscalationLadder.FULL,
        )
        unresolved_replay.fold(
            PhaseRecord(
                phase="round",
                event="complete",
                round=i,
                payload={"improved": improved, "electable_count": 2, "separable": separable},
            ),
            lives=None,
        )
    assert unresolved_replay.l1_stall_count == unresolved_live.l1_stall_count == 2


def test_a_rewound_round_leaves_the_escalation_state(tmp_path) -> None:
    """A rewind deletes round files and leaves the ledger whole, so the rounds it discarded are
    still on it. Folded, they hand the re-run a stall count and a lives bank from rounds that no
    longer exist: the cycle escalates or stops on a round it never ran, every number rendering."""
    from promptpotter.application.optimizers.potter.escalation.state import EscalationFSM
    from promptpotter.domain.run_records import PhaseRecord
    from promptpotter.infrastructure.ledger import CycleEventLog

    def close(round_num: int, improved: bool) -> PhaseRecord:
        return PhaseRecord(
            phase="round",
            event="complete",
            round=round_num,
            payload={"improved": improved, "electable_count": 2, "separable": None},
        )

    ledger = CycleEventLog(tmp_path / "ledger.jsonl")
    for round_num, improved in [(1, True), (2, True), (3, False), (4, False), (5, False)]:
        ledger.append(close(round_num, improved))

    # `resume --from 2`: rounds 3..5 are discarded before any of them is re-run.
    rewound = EscalationFSM.from_ledger(ledger, lives=None, before_round=3)
    assert rewound.l1_stall_count == 0

    # The re-run closes round 3 with a win and stalls once; a plain resume at round 5 then sees
    # both epochs on one ledger and must fold the second alone.
    ledger.append(close(3, True))
    ledger.append(close(4, False))
    assert EscalationFSM.from_ledger(ledger, lives=None, before_round=5).l1_stall_count == 1


def test_l2_l3_escalation_state_survives_resume() -> None:
    """Resume-integrity: L2/L3 counters rebuilt from the ledger must equal the live in-run ones.

    Builds the records with the firing seam's own exit views, off the live FSM, so this pins
    reader-against-writer rather than reader-against-itself: a fold keyed on the NODE names, which
    no PhaseRecord carries, or reading the in-memory-only ``payload["data"]``, rebuilds L2/L3 as
    never-fired and hands the resumed run a fresh escalation budget. A fire whose output never
    parsed adopts nothing and folds as nothing, so the ask before it must have moved nothing
    either: counters committed at the ask split the live run from every resume of it.
    """
    from types import SimpleNamespace

    from promptpotter.application.optimizers.potter.escalation.firing import L2, L3
    from promptpotter.application.optimizers.potter.escalation.state import (
        EscalationFSM,
        LadderAsk,
        PotterPhase,
    )
    from promptpotter.application.optimizers.potter.knobs import EscalationLadder
    from promptpotter.application.optimizers.potter.state import PotterState
    from promptpotter.application.views.view_models import OptimizerStepExitView
    from promptpotter.domain.run_records import PhaseRecord

    def snapshot(f: EscalationFSM) -> tuple[int, int, float | None, int, int, float | None, int]:
        return (
            f.l2_round,
            f.l2_stall_count,
            f.l2_best_composite_fitness_at_entry,
            f.l3_round,
            f.l3_stall_count,
            f.l3_best_composite_fitness_at_entry,
            f.l1_stall_count,
        )

    live = EscalationFSM()
    live_trace = []
    # What each exit record carries on disk: the view the seam composes off the live state.
    banked: list[tuple[PotterPhase, OptimizerStepExitView]] = []
    fired_state = PotterState(escalation=live)
    output = SimpleNamespace(
        l1_layout=None,
        axis_targeted="",
        plan="",
        described="",
    )

    def fired(layer) -> None:
        live_trace.append(snapshot(live))
        banked.append((layer.phase, layer.exit_view(fired_state, output)))

    def ask(fitness: float) -> LadderAsk:
        return live.ask_l2_escalation(
            current_composite_fitness=fitness,
            escalation_ladder=EscalationLadder.FULL,
            l2_patience=3,
            l3_patience=2,
        )

    live.record_l2_fired(ask(0.60).l2)
    fired(L2)
    # A second ask at an unimproved fitness reads a stall, which the fire that lands commits.
    live.record_l2_fired(ask(0.60).l2)
    fired(L2)
    # An unparseable fire closes its bracket and adopts nothing — the stall its ask read
    # included, or the live counters run ahead of the only record a resume can fold.
    unlanded = ask(0.60)
    assert unlanded.l2.stall_count == 2
    # The prompt that fire composed still reported the verdict that asked for it.
    assert live.as_read_by(unlanded).l2_stall_count == 2
    live_trace.append(snapshot(live))
    discarded = OptimizerStepExitView(headline="", details=(), audit=None, state=None)
    banked.append((PotterPhase.REFINE_STRATEGY, discarded))
    # L3 firing wipes L2's progress — a new plan invalidates it. Checked BEFORE the wipe above,
    # or the L2 half of this test would assert zeros and pass against the bug it exists for.
    # A heal first: it bumps L3 and leaves the patience ratchet unset, which must fold as unset.
    live.record_l3_fired(None)
    fired(L3)
    live.record_l3_fired(ask(0.75).l3)
    fired(L3)

    replay = EscalationFSM()
    replay_trace = []
    for phase, view in banked:
        # Round-trip through Pydantic exactly as a resume does: `fold` must read the
        # dict `ledger.iter()` hands back, not only the live dataclass.
        on_disk = PhaseRecord.model_validate_json(
            PhaseRecord(phase=phase, event="exit", payload={"view": view}).model_dump_json()
        )
        replay.fold(on_disk, lives=None)
        replay_trace.append(snapshot(replay))

    assert replay_trace == live_trace
    # Pinned literally too: an arm that never matches leaves every one of these at 0/None.
    assert replay_trace == [
        (1, 0, 0.60, 0, 0, None, 0),
        (2, 1, 0.60, 0, 0, None, 0),
        (2, 1, 0.60, 0, 0, None, 0),
        (0, 0, None, 1, 0, None, 0),
        (0, 0, None, 2, 0, 0.75, 0),
    ]


def test_a_fire_after_the_round_closed_survives_a_pause(tmp_path: Path) -> None:
    """A layer fires AFTER the round it reads has been written, so what it authors is on no round
    document until the next round closes. Paused in between, the cycle resumed with the ladder's
    counters restored off the ledger and the layout, overrides and plan those fires wrote gone —
    the next round ran under the framing of the one before. Silent: both layouts are valid."""
    from types import SimpleNamespace

    from promptpotter.application.optimizer_manifest import resolve_optimizer
    from promptpotter.application.optimizers.potter.dispatch.layout import (
        coerce_l1_layout,
        default_l1_layout,
    )
    from promptpotter.application.optimizers.potter.escalation.firing import L2, L3
    from promptpotter.application.optimizers.potter.escalation.state import LayerReading
    from promptpotter.application.optimizers.potter.records import L2L3Memory
    from promptpotter.application.optimizers.potter.state import PotterState
    from promptpotter.domain.run_records import PhaseRecord
    from promptpotter.infrastructure.ledger import CycleEventLog
    from tests.factories import round_result

    selected = resolve_optimizer("potter", {})
    live = PotterState()
    closed = round_result(1, optimizer_state=optimizer_state(live.memory.model_copy(deep=True)))
    ledger = CycleEventLog(tmp_path / "ledger.jsonl")

    def fire(layer: Any) -> None:
        output = SimpleNamespace(l1_layout=None, axis_targeted="", plan="", described="")
        view = layer.exit_view(live, output)
        ledger.append(PhaseRecord(phase=layer.phase, event="exit", round=1, payload={"view": view}))

    moved = coerce_l1_layout({"critique": "persona"}, base=live.memory.l1_layout)
    assert moved is not None and moved != live.memory.l1_layout
    live.memory.l1_layout = moved
    live.memory.l1_overrides = {"n_variants": 5}
    live.escalation.record_l2_fired(LayerReading(0, 0.5, None, None))
    fire(L2)
    live.memory.plan = "attack the default label"
    live.escalation.record_l3_fired(None)
    fire(L3)

    def resumed(before_round: int) -> PotterState:
        state = PotterState()
        state.replay(closed)
        state.resume(ledger, selected, before_round=before_round)
        return state

    # Paused inside round 2: both fires belong to the boundary the resume keeps.
    assert resumed(2).memory == live.memory
    # Rewound to re-run round 1: the fires are discarded with it.
    assert resumed(1).memory == L2L3Memory(l1_layout=default_l1_layout())


# 6. Paired readings over shared cells


def test_a_verify_reads_its_fresh_cells_alone_and_pairs_its_lift_over_the_origin() -> None:
    """A verify buys a candidate cells it never met, and the reading POOLED them with the round's
    own — so the verdict moved with how many cells each side held. Ten fresh cells at 50% under
    twenty recorded at 100% pool to 83%: a candidate whose every other unseen cell missed, served
    as a small dip. Silent, since a pooled rate is a plausible number, and it is the one a winner
    is kept or dropped on."""
    from promptpotter.application.diagnostics.verify import verify_reading
    from promptpotter.domain.results import VerifyPass, VerifyStrategy

    def banked(strategy: VerifyStrategy = "random", round_num: int = 3) -> VerifyPass:
        return VerifyPass(
            label="C3.1",
            candidate_id="c",
            round=round_num,
            sp_hash="h",
            run_id="r",
            sample_ids=list(range(100, 110)),
            strategy=strategy,
            seed=1,
            scorer_id="s",
        )

    recorded = measurements([1.0] * 20)
    fresh = measurements([1.0, 0.0] * 5, range(100, 110))
    # The origin: half the round's cells, none of the fresh ones.
    origin = [*measurements([0.0, 1.0] * 10), *measurements([0.0] * 10, range(100, 110))]

    def read(pass_: VerifyPass, fresh_rows: list[dict[str, Any]] = fresh) -> Any:
        return verify_reading(
            pass_, scorer_id="s", fresh=fresh_rows, recorded=recorded, origin=origin
        )

    reading = read(banked())
    assert reading.fresh.accuracy is not None and reading.recorded.accuracy is not None
    assert reading.fresh.accuracy.value == pytest.approx(0.5), "the fresh cells, never the pool"
    assert reading.recorded.accuracy.value == pytest.approx(1.0)
    assert reading.accuracy_increment == pytest.approx(-0.5)
    assert (reading.n_fresh, reading.n_recorded, reading.n_shared) == (10, 20, 30)
    # Paired per cell both scored: +1 on the ten round cells the origin missed, 0 on the ten it
    # hit, +1 on the five fresh hits — never the difference of two rates over different cells.
    assert reading.lift.accuracy is not None
    assert reading.lift.accuracy.value == pytest.approx(0.5)
    # The recorded level sits above the fresh band, so the round's claim did not survive.
    assert reading.held is False
    assert read(banked(), measurements([1.0] * 10, range(100, 110))).held is True
    # Hard picks sit below the level whatever the candidate is worth: no verdict from the level.
    assert read(banked("hard")).held is None
    # The origin has nothing to be lifted over, and 0.0 there would read as "no better than C0".
    at_origin = read(banked(round_num=0))
    assert at_origin.lift.accuracy is None and at_origin.n_shared == 0


def test_paired_reading_matches_ttest_rel_and_brackets_the_same_evidence_it_tests() -> None:
    """Checked against an INDEPENDENT oracle, so a sidedness flip or a df off-by-one goes red
    rather than agreeing with itself. The interval and the p come from ONE posterior, so the
    silent failure this pins is the two disagreeing about zero — a bracket excluding it beside a
    p that does not, or the reverse. The floor case pins the documented deviation instead of
    hiding it: ``_normal_posterior`` clips the SE at ``1/(4n)``, so a near-constant difference
    reads far LESS significant here than a textbook paired t-test."""
    # Spread wide enough that the 1/(4n) floor does not bind, so the two must agree exactly.
    cand = [0.90, 0.10, 0.85, 0.20, 0.75, 0.30, 0.95, 0.05]
    prior = [0.10, 0.85, 0.15, 0.80, 0.20, 0.70, 0.05, 0.90]
    reference = 0.8750918683549795  # scipy 1.17.1 `ttest_rel(cand, prior).pvalue`

    mean_d, lo, hi, p_two, n = paired_reading(cand, prior)
    assert n == len(cand)
    assert abs(mean_d - sum(c - p for c, p in zip(cand, prior, strict=True)) / n) < 1e-12
    assert p_two is not None and abs(p_two - reference) < 1e-12

    # One posterior, one verdict: a p above 0.05 and a bracket clearing zero cannot coexist.
    assert lo is not None and hi is not None and lo < mean_d < hi
    assert (p_two < 0.05) == (lo > 0.0 or hi < 0.0)

    p_greater = paired_reading(cand, prior, tail="greater")[3]
    assert p_greater is not None and abs(p_greater - reference / 2.0) < 1e-12

    # Reading a pair in either order must give one number — the whole point of the two-sided test.
    assert paired_reading(prior, cand)[3] == p_two

    # The floor binds: a difference this tight is "significant" to a textbook test and must not be
    # to this one.
    tight_cand = [0.5000001 * i for i in range(1, 7)]
    tight_prior = [0.5 * i for i in range(1, 7)]
    tight_p = paired_reading(tight_cand, tight_prior)[3]
    assert (
        tight_p is not None and tight_p > 0.00593354451968529
    )  # scipy's `ttest_rel` on the same pair

    # One pair tests nothing and brackets nothing — absent, not a p of 1.0 nor a zero-width bar.
    assert paired_reading([0.5], [0.1])[1:4] == (None, None, None)


def test_a_level_is_bracketed_on_the_student_t_its_paired_lift_is_tested_on() -> None:
    """The band drawn beside a candidate's fitness, checked against an INDEPENDENT quantile. On the
    normal one an eight-cell band is a fifth too narrow, and a level reads clear of a bar its own
    paired lift does not clear. Silent: a narrower band is a plausible band, and `held` is read
    off it."""
    from statistics import mean, stdev

    from promptpotter.application.scoring.selection import mean_fitness_ci

    grades = [0.90, 0.10, 0.85, 0.20, 0.75, 0.30, 0.95, 0.05]
    lo, hi = mean_fitness_ci(measurements(grades), grade="fitness")  # type: ignore[arg-type]
    half = 2.364624251592785 * stdev(grades) / len(grades) ** 0.5  # scipy `t.ppf(0.975, 7)`
    assert lo == pytest.approx(mean(grades) - half, abs=1e-9)
    assert hi == pytest.approx(mean(grades) + half, abs=1e-9)

    # Four cells at 0.0 have no spread, and the band is still not a point: PoBB's 1/(4n) floor
    # holds it open, on three degrees of freedom, clipped to what a fitness can be.
    floor_lo, floor_hi = mean_fitness_ci(measurements([0.0] * 4), grade="fitness")  # type: ignore[arg-type]
    assert floor_lo == 0.0
    assert floor_hi == pytest.approx(3.182446305284263 / 16, abs=1e-9)  # `t.ppf(0.975, 3)`

    # One cell brackets nothing — absent, never a zero-width bar.
    assert mean_fitness_ci(measurements([1.0]), grade="fitness") == (None, None)  # type: ignore[arg-type]


def test_a_head_to_head_pairs_two_optimizers_only_on_one_bench_under_one_grader(
    built_stores,
) -> None:
    """The held-out bench IS an optimizer head-to-head's comparability guard, and ONE grader reads
    every arm's banked passes. A campaign whose rows another seed drew sat a different exam; one
    whose shared origin reads apart on its own rows under that grader met another backend; one kept
    under its own formula is another function's number. A paired difference across any of them
    still prints an interval and names a winning optimizer. Silent: every number renders.

    Each arm is read off its campaign's result, so an arm whose line a rebase handed to a fork is
    graded and priced as ONE line; read off the root it retired, it had no headline and half a
    bill."""
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
        panel = [measurement(s.id, 0.5, query=s.query) for s in partition.search[:4]]
        # A rebase fork lifts its parent's origin round and redraws the one partition.
        for cycle in {root, hop}:
            stores.campaigns.create(cycle, {})
            stores.campaigns.save_round_file(
                cycle, round_result(0, candidates_scored=1, all_candidate_results={"c0": panel})
            )
            stores.campaigns.write_bank_partition(cycle, partition)
        if rebased:
            stores.campaigns.mark_superseded(root, hop.cycle_id)
        passes: dict[str, BenchPass | None] = {"selected": None}
        for role, level in (("origin", origin), ("selected", selected))[: 1 + graded]:
            # The origin is ONE individual every campaign sends, filed under one content-addressed
            # run while the backend reads it alike; each pick is its own.
            graded = role if role == "origin" else f"{cid}:{role}"
            run_id = f"bench_{graded}_{seed}_{level}"
            # Facts only, as the archive banks them: the read grades each under one formula.
            rows = [
                measurement(
                    s.id,
                    None,
                    query=s.query,
                    pipeline_data={"env_reward": level + 0.1 * (s.id % 2)},
                )
                for s in partition.bench
            ]
            header = {"run_id": run_id, "prompt_fields_id": graded, "item_count": len(rows)}
            header |= {"name": "bench", "dataset_name": "ds"}
            record_measurement_run(
                stores, run_id, {**header, "content_hash": run_id, "created_at": ""}, rows
            )
            passes[role] = BenchPass(
                round=int(role == "selected"),
                sp_hash=role,
                run_id=run_id,
                sample_ids=[s.id for s in partition.bench],
                stopped=None,
                scorer_id=scoring,
            )
        hour = n if at is None else at
        started = f"2026-09-26T{hour:02d}:00:00Z"
        # The optimizer billed on the root; the fork a rebase handed the line to replayed its
        # cells and graded the pick, which the chain's cost counts once, on whichever cycle paid.
        calls = [("optimizer", loop, False), ("backend", incurred - loop - 0.05, True)]
        for kind, usd, cached in [*calls, ("bench", 0.05, True)]:
            ledger = root if kind == "optimizer" else hop
            CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(ledger))).append(
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
        # A cycle the line has moved past banks nothing — its result is the successor's.
        for holder in (hop, root) if rebased else (hop,):
            bank_campaign_result(
                stores,
                holder,
                started_at=started,
                finished_at=f"2026-09-26T{hour:02d}:{wall // 60:02d}:{wall % 60:02d}Z",
                optimizer_phases=frozenset(),
                bench=BenchPasses(tolerance=0, reserve_usd=0.05, reserve_tokens=0, **passes),
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
    (pair,) = h2h.pairs
    assert (pair.campaign_a, pair.campaign_b, pair.n_rows) == ("potter_a", "capo_b", 6)
    assert pair.shift == pytest.approx(0.2) and pair.ci_lo is not None and pair.ci_lo > 0.0
    # Priced against the oldest run on INCURRED spend: capo replayed cells potter paid for, so its
    # bill, half of what its campaign consumed, prices arriving second rather than its optimizer.
    assert h2h.ratio_reference == "potter_a"
    potter_row, row = h2h.rows
    assert potter_row.spend is not None and potter_row.spend.total_incurred_usd == 0.2
    assert (row.incurred_usd_ratio, row.optimizer_incurred_usd_ratio, row.worked_ratio) == (
        pytest.approx(1.5),
        pytest.approx(3.0),
        pytest.approx(0.5),
    )
    # The lift is priced on the SEARCH alone: the bench's own pass is the instrument grading it.
    assert row.lift_per_incurred_usd == pytest.approx(0.3 / 0.25)
    assert [r.concurrent_with for r in h2h.rows] == [[], []]
    # Every individual graded on the shared rows spent them — the one origin and two picks.
    assert [r.bench_reads for r in h2h.rows] == [3, 3]
    # A campaign ticked in the webapp arrives as a COURSE on its line and stands for the campaign
    # ONCE: a second subject of it would pair the campaign with itself.
    course = SubjectSpec("course", "potter_a", "cycle_potter_a_fork_r1")
    for asked, subjects in (([course, capo], [course]), ([course, potter, capo], [potter])):
        h2h = subject_evidence(stores, asked).head_to_head
        assert h2h is not None and len(h2h.pairs) == 1
        assert sorted(r.subject for r in h2h.rows) == sorted(s.key for s in (*subjects, capo))

    # Another seed drew other held-out rows: its headline is listed and never paired.
    gepa = campaign("gepa_c", 3, seed=1, selected=0.9)
    h2h = subject_evidence(stores, [potter, capo, gepa]).head_to_head
    assert h2h is not None and h2h.verdict is False
    assert {"bench_rows", "split"} <= set(h2h.differs_on)
    assert [(p.campaign_a, p.campaign_b) for p in h2h.pairs] == [("potter_a", "capo_b")]
    assert [r.comparable for r in h2h.rows] == [True, True, False]

    # One bench set, but the shared origin reads 0.2 apart on its own rows under the one formula:
    # the backend or a judge moved between the two runs.
    drifted = campaign("capo_d", 4, seed=0, selected=0.7, origin=0.6)
    h2h = subject_evidence(stores, [potter, capo, drifted]).head_to_head
    assert h2h is not None and h2h.differs_on == ["origin_reading"]
    assert [(p.campaign_a, p.campaign_b) for p in h2h.pairs] == [("potter_a", "capo_b")]
    assert [r.comparable for r in h2h.rows] == [True, True, False]

    # A campaign run under another formula shares no scorer with these: grading it under theirs
    # reads a number its own run never would, so the read refuses rather than serve one column.
    halved = campaign("capo_h", 8, seed=0, selected=0.7, scoring="0.5 * env_reward")
    with pytest.raises(ValueError, match="share none"):
        subject_evidence(stores, [potter, capo, halved])

    # Its run overlapped potter's: a shared cache split the bill by arrival.
    overlapping = campaign("capo_f", 6, seed=0, selected=0.7, at=1)
    h2h = subject_evidence(stores, [potter, capo, overlapping]).head_to_head
    assert h2h is not None and h2h.verdict is True and len(h2h.pairs) == 3
    assert [r.concurrent_with for r in h2h.rows] == [["capo_f"], [], ["potter_a"]]

    # One optimizer on another model: a gap to it is the model's as much as the method's, so it
    # is listed and never paired.
    swapped = campaign("potter_g", 7, seed=0, selected=0.7, proposer_model="other/model")
    h2h = subject_evidence(stores, [potter, capo, swapped]).head_to_head
    assert h2h is not None and h2h.verdict is False
    assert h2h.differs_on == ["optimizer_models"]
    assert [(p.campaign_a, p.campaign_b) for p in h2h.pairs] == [("potter_a", "capo_b")]

    # Three arms of one declared record beside an arm of another: only the foreign row is not
    # controlled. Naming two records must not demote the arms, which read their own record — the
    # one copy of the budget, which no arm's snapshot carries.
    shared = subject_evidence(stores, [potter, capo]).head_to_head
    assert shared is not None and shared.rows[0].bench_set is not None
    for h2h_id in ("real4", "real5"):
        stores.campaigns.declare_head_to_head(
            HeadToHeadRecord(
                head_to_head_id=h2h_id,
                created_at="",
                instrument=shared.rows[0].bench_set,
                budget=ArmBudget(usd=2.0, max_rounds=3, determinism=None),
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
    assert h2h is not None and h2h.head_to_head_id == "real4"
    assert [r.controlled for r in h2h.rows] == [True, True, True, False]

    # Two graded arms read as one quantity; a third arm of that record still ungraded withholds
    # the verdict, since the graded rows are not yet the comparison that was declared.
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
    assert h2h is not None and h2h.verdict is None and h2h.differs_on == []

    # An ungraded arm is waiting only while its cycle has not ended: refused at run init it holds
    # no bench and never will, and without the ending it reads exactly like one still running.
    assert [r.outcome for r in h2h.rows] == [None, None, None]
    stores.campaigns.mark_finished(
        CycleHop(campaign_id="capo_waiting", cycle_id="cycle_capo_waiting"),
        stop_reason=StopReason.INPUT_REFUSED,
        finished_at="2026-09-26T15:00:00Z",
    )
    h2h = subject_evidence(stores, [*arms[:2], waiting]).head_to_head
    assert h2h is not None
    assert [r.outcome for r in h2h.rows] == [None, None, StopOutcome.FAILED]

    # A cap moved on ONE arm moves that arm alone, and the read serves it off its declared budget
    # rather than counting it equal to the arms it no longer matches.
    stores.campaigns.write_run_limits(
        CycleHop(campaign_id="capo_real4", cycle_id="cycle_capo_real4"),
        BudgetChange(1.0, None),
        rounds=None,
        reserve=BudgetChange(None, None),
    )
    h2h = subject_evidence(stores, arms).head_to_head
    assert h2h is not None
    assert [r.controlled for r in h2h.rows] == [True, False, True, False]
