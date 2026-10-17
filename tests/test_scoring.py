"""A wrong score or a wrong decision.

Owns `application/scoring/`, `shared/statistics.py`, `optimizers/potter/pobb/` and
`optimizers/potter/escalation/`, the runner's election, `diagnostics/verify.py` and `evidence/`.
The run completes, the dashboard looks fine, and a different candidate should have won.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import math
import types
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from itertools import product
from pathlib import Path
from statistics import mean, stdev
from typing import Any

import pytest
import yaml
from factories import (
    loop_session,
    measurement,
    measurements,
    optimizer_state,
    pipeline_schema,
    pobb_knobs,
    round_result,
    scored_candidate,
    sheet,
    spend_book,
    walking_cycle,
    workspace,
)

from promptpotter.application import optimizers
from promptpotter.application.bench import node_context
from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.bench.resume_and_fork.repair import repair_cut
from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.diagnostics.verify import verify_member, verify_reading
from promptpotter.application.evidence.read import subject_evidence
from promptpotter.application.evidence.subjects import SubjectSpec
from promptpotter.application.intelligence.rasch import (
    candidate_abilities,
    responses_of,
    theta_lift_over_parent,
)
from promptpotter.application.optimizer_manifest import bind_optimizer
from promptpotter.application.optimizers import nodes as node_contract
from promptpotter.application.optimizers import paper_templates
from promptpotter.application.optimizers.capo import prompts as capo_prompts
from promptpotter.application.optimizers.potter.dispatch.layout import (
    NODE_LAYOUTS,
    coerce_l1_layout,
)
from promptpotter.application.optimizers.potter.escalation.firing import L2, L3, TransitionResult
from promptpotter.application.optimizers.potter.escalation.rules import (
    EscalationInputs,
    NextAction,
    decide_escalation,
)
from promptpotter.application.optimizers.potter.escalation.state import (
    EscalationFSM,
    LadderAsk,
    LayerReading,
)
from promptpotter.application.optimizers.potter.knobs import EscalationLadder
from promptpotter.application.optimizers.potter.pobb.checks import EliminationGate, PoBBCheck
from promptpotter.application.optimizers.potter.records import L2L3Memory, Ladder
from promptpotter.application.optimizers.potter.state import PotterState
from promptpotter.application.run_phase_control import RunControl
from promptpotter.application.runner import bench as bench_runner
from promptpotter.application.runner import operator_hold
from promptpotter.application.runner.bench import (
    BenchLine,
    bench_selection,
    bench_series,
    read_bench,
    score_on_bench,
)
from promptpotter.application.runner.campaign_result import bank_campaign_result
from promptpotter.application.runner.entry import _build_cycle_result
from promptpotter.application.runner.measurement import _ReferencePairs
from promptpotter.application.runner.round import execute_round
from promptpotter.application.runner.termination import standing_tripped, target_tripped
from promptpotter.application.scoring import query_loop
from promptpotter.application.scoring.classification import DegradationCheck, scoreable_rows
from promptpotter.application.scoring.evaluators import (
    DEFAULT_CELL_FORMULA,
    compute_accuracy,
    compute_degraded_rate,
    compute_error_rate,
)
from promptpotter.application.scoring.formula import (
    ScoringFormulaError,
    auto_scorer_id,
    compile_scorer,
    compiler,
    origin_anchors,
    parse_dials,
    realize_dials,
)
from promptpotter.application.scoring.formula.matchers import (
    _aime_match,
    _blocksworld_plan_match,
    _em_match,
    _gsm8k_match,
    _label_match,
    _math_match,
    _meeting_plan_match,
)
from promptpotter.application.scoring.metrics import compute_composite_fitness, fold_cells
from promptpotter.application.scoring.paired import (
    MemberRows,
    absent_pair,
    as_family,
    grade_measurands,
    read_pair,
)
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.application.scoring.selection import (
    closest_to_bar,
    distinct_valid_cells,
    elect_round_winner,
    elimination_p_best,
    level_band,
    parent_selection_bias,
    readable_lifts,
)
from promptpotter.application.views.view_models import ViewContext
from promptpotter.config.paths import optimizers_root
from promptpotter.domain.bench import (
    BenchPass,
    BenchPasses,
    DatasetSplit,
    LineRun,
    PassStop,
    partition_bank,
)
from promptpotter.domain.campaign import Arm, ArmBudget, Campaign, HeadToHeadRecord
from promptpotter.domain.command_kinds import SubmitArmsPayload, SubmittedArm
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.optimizer_state import RoundPayload
from promptpotter.domain.paired_reading import (
    READING_STATE_INFO,
    ROUND_LIFT_SPEC,
    ArmPointer,
    CellSetName,
    CoverageState,
    EstimatorSpec,
    Measurand,
    MeasurandKind,
    MeasurandUnit,
    MemberAddress,
    PairedReading,
    ReadingState,
)
from promptpotter.domain.phase_views import BenchGradedView, ViewAnchors
from promptpotter.domain.phases import CampaignPhase, StopLoop, StopReason, WalkEnd
from promptpotter.domain.pipeline_schema import NodePromptInfo, PipelineNode
from promptpotter.domain.results import (
    ArmOutcome,
    IndividualWalk,
    InRunCells,
    OverlapReading,
    RoundAdvance,
    RoundCells,
    RoundResult,
    RunStanding,
    VerifyPass,
    VerifyStrategy,
    individual_cells,
    is_floor_pinned,
    is_leader_eligible,
    merge_known_outcomes,
    order_floor,
    round_advance,
    rounds_without_advance,
    stalls_left,
    unscoreable_cells,
)
from promptpotter.domain.results_health import is_deprecated
from promptpotter.domain.ruler import DeltaRuler, anchor_id_of
from promptpotter.domain.run_records import (
    CandidateMintedRecord,
    CandidateScoredRecord,
    OptimizerStateRecord,
    PhaseRecord,
    RoundClosedRecord,
    RoundStandingRecord,
    SampleScoredRecord,
    TokenUsageRecord,
)
from promptpotter.domain.sample import ArchiveEntry, Sample
from promptpotter.domain.scoring import (
    NO_RESULT,
    ROW_GRADES,
    CellSheet,
    Diagnostics,
    GradedCell,
    JudgeReading,
    MeasuredCell,
    NodeWarning,
    PipelineData,
    Scorer,
    WalkedCell,
    anchored_criterion_dials,
    turn_scalars,
)
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.spend import SpendCeilings, StepUsage
from promptpotter.infrastructure.ledger import CycleEventLog, ledger_chain
from promptpotter.infrastructure.llm.telemetry import (
    at_round,
    reset_cycle_ledger,
    set_cycle_ledger,
)
from promptpotter.infrastructure.producer_lock import release_cycle
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_ledger_walks,
    scan_standing_rounds,
)
from promptpotter.infrastructure.store.measurement_archive import config_key
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.judges import call as judge_call
from promptpotter.judges.grounding import ANSWER_GROUNDING
from promptpotter.judges.protocol import JudgeSpec, JudgeStage
from promptpotter.shared.answer_text import extract_gsm8k_number
from promptpotter.shared.errors import ConflictError, ErrorCategory
from promptpotter.shared.measurement_context import MeasurementRole, RoleScope
from promptpotter.shared.statistics import p_exceeds, paired_mean_t

_H2H_FORMULA = "env_reward"


def _keyed(cell: GradedCell, sample_key: str) -> GradedCell:
    return cell.filed(dataclasses.replace(cell.facts, sample_key=sample_key))


def _pair_member(
    individual: str,
    rows: list[GradedCell],
    bank: list[Sample],
    *,
    scorer_id: str = "s",
    address: MemberAddress | None = None,
    scope: RoleScope = RoleScope.DECISION,
    instrument_id: str = "i",
    dataset_hash: str | None = "d",
    cell_set_id: str | None = None,
) -> MemberRows:
    key_of = {s.id: s.key for s in bank}
    return MemberRows(
        address=MemberAddress(
            path=(CycleHop(campaign_id="camp", cycle_id="cyc"),),
            individual_id=individual,
            arm=None,
            pass_role=None,
        )
        if address is None
        else address,
        sheet=sheet((_keyed(row, key_of[row.sample_id]) for row in rows), scorer_id),
        bought=0,
        cut=False,
        scope=scope,
        instrument_id=instrument_id,
        dataset_hash=dataset_hash,
        cell_set_id=cell_set_id,
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
            "spec": EstimatorSpec(alpha=0.05, null_value=0.0),
            "scope": RoleScope.DECISION,
            "instrument_id": "i",
        }
        | asked
    )


def _filed(
    stores: Any, individual: str, rows: list[GradedCell], *, role: str = "panel", sp_hash: str = ""
) -> list[WalkedCell]:
    entry = ArchiveEntry(
        config_key=config_key([("", {"individual": individual})]),
        prompt_fields_id=sp_hash or individual,
        rendered_prompt_hash="",
        node_configs=[],
        pipeline_params={},
        dataset_name="ds",
    )
    keyed = [row.facts for row in rows]
    answers = stores.archive.file_answers(
        entry,
        [(facts, "A") for facts in keyed],
        role=role,
        source="optimization_loop",
        created_at="",
    )
    return [
        (facts.sample_key, facts.sample_id, answer, False)
        for facts, answer in zip(keyed, answers, strict=True)
    ]


def _announced(cell: WalkedCell) -> dict[str, Any]:
    key, sample_id, answer, _ = cell
    return {"sample_key": key, "sample_id": sample_id, "answer": answer}


# 1. Scorer formulas

_LLM_ONLY = pipeline_schema(name="test", nodes=[PipelineNode(name="llm_only", tunes_llm=False)])
_BLOCKSWORLD_GOLD = (
    '{"below":{"red":"blue","yellow":"orange","blue":"table","orange":"table"},'
    '"goal":[["orange","red"]]}'
)
_MEETING_GOLD = (
    '{"constraints":[["Marina District","9:00AM"],'
    '["Stephanie","Mission District","10:30AM to 1:30PM",120]],'
    '"dist_matrix":{"Marina District":{"Mission District":20},'
    '"Mission District":{"Marina District":19}},'
    '"golden_plan":["You start at Marina District at 9:00AM.",'
    '"You travel to Mission District in 20 minutes and arrive at 9:20AM.",'
    '"You wait until 10:30AM.",'
    '"You meet Stephanie for 120 minutes from 10:30AM to 12:30PM."]}'
)


def test_a_scorer_grades_each_cell_from_its_facts_under_the_formula_it_compiled(
    monkeypatch,
) -> None:
    # Each matcher grades an answer against its gold.
    for match, args, expected in (
        (
            _blocksworld_plan_match,
            (
                "Clear orange first.\n[PLAN]\nunstack the yellow block from on top of the orange "
                "block\nput down the yellow block\npick up the orange block\nstack the orange block "
                "on top of the red block\n[PLAN END]",
                _BLOCKSWORLD_GOLD,
            ),
            1.0,
        ),
        # The final layout reads right, but the hand still held yellow when orange was picked up.
        (
            _blocksworld_plan_match,
            (
                "[PLAN]\nunstack the yellow block from on top of the orange block\npick up the "
                "orange block\nstack the orange block on top of the red block\n[PLAN END]",
                _BLOCKSWORLD_GOLD,
            ),
            0.0,
        ),
        (
            _meeting_plan_match,
            (
                "Go early and wait. SOLUTION: You start at Marina District at 9:00AM. You travel to "
                "Mission District in 20 minutes and arrive at 9:20AM. You wait until 10:30AM. You "
                "meet Stephanie for 120 minutes from 10:30AM to 12:30PM.",
                _MEETING_GOLD,
            ),
            1.0,
        ),
        # No wait: the replay reaches Stephanie at 9:20AM, before her window opens.
        (
            _meeting_plan_match,
            (
                "SOLUTION: You start at Marina District at 9:00AM. You travel to Mission District "
                "in 20 minutes and arrive at 9:20AM. You meet Stephanie for 120 minutes from 9:20AM "
                "to 11:20AM.",
                _MEETING_GOLD,
            ),
            0.0,
        ),
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
        # Bolds nothing: the last non-empty line answers, the reasoning above it does not.
        (
            _label_match,
            ("The story is about a merger, a business matter.\n\nBusiness\n", "Business"),
            1.0,
        ),
        (_label_match, ("The story is about a merger.\n\nBusiness\n\nWorld", "Business"), 0.0),
        # SQuAD's normalizer, articles and all, but a number keeps its own point and sign.
        (_em_match, ("so it is **the Beatles**", "Beatles"), 1.0),
        (_em_match, ("**A.**", "a"), 1.0),
        (_em_match, ("", "a"), 0.0),
        (_em_match, ("**the**", "a"), 1.0),
        (_em_match, ("**13**", "1.3"), 0.0),
        (_em_match, ("**.5**", "-.5"), 0.0),
        (_em_match, ("**-5 points.**", "-5 points"), 1.0),
        # The last box wins, braces nest, and the Hendrycks string rules equate spellings.
        (_math_match, (r"\boxed{\dfrac{1}{2}} so \boxed{\left( 1, 2 \right)}", "(1,2)"), 1.0),
        (_math_match, (r"\boxed{\dfrac{3}{4}}", r"\frac34"), 1.0),
        (_math_match, (r"\boxed{10\text{ cm}}", "10"), 1.0),
        (_math_match, ("**x = 5**", "5"), 1.0),
        (_math_match, (r"\boxed{0.75}", r"\frac{3}{4}"), 0.0),
    ):
        assert match(*args) == expected, f"{match.__name__}{args!r} must grade {expected}"

    # The AST allowlist is the boundary: a restricted `eval()` namespace is bypassable.
    for formula in (
        "().__class__",
        "__import__('os').system('echo pwn')",
        "(lambda: 1)()",
        "[x for x in range(10)][0]",
        "predicted.__class__",
        "label_match(predicted, ground_truth) := 1",
    ):
        with pytest.raises((ValueError, SyntaxError)):
            compile_scorer(formula, verifier_graded=False)
    # `turns` is compacted out of cold rows, so a formula reaching it is refused at COMPILE: a
    # bare Call reaches `eval()` as a NameError, which reads as a missing TERM.
    for formula in ("turns[0]", "turns.index", "len(turns)"):
        with pytest.raises(ValueError):
            compile_scorer(formula, None, verifier_graded=True)

    # A cell read off the wire keeps its facts and drops any grade it arrived with.
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
    assert MeasuredCell.from_wire(facts.wire()) == facts, "a cell reads back as the cell it wrote"
    assert (facts.cost_s, facts.elapsed_s, facts.shown_s) == (1.5, 2.0, 1.5), (
        "latency reads the step timings, the elapsed the total"
    )
    unranked = MeasuredCell.from_wire({**banked, "ground_truth_rank": None}).wire()
    assert unranked["ground_truth_rank"] is None and unranked["n_candidates"] == 5, (
        "an absent rank stays absent beside the facts that are present"
    )
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
        assert read == held and read.wire() == wire, f"a {type(held).__name__} lost a field"

    # A grade is a new record and keeps nothing of the formula before it.
    plain = compile_scorer("env_reward", None, verifier_graded=True)
    answered = MeasuredCell.from_wire({**banked, **stale, "error": None, "error_category": None})
    graded = plain.grade(answered)
    assert (graded.grade.fitness, graded.grade.unscored) == (0.25, None), "the stale grade stood"
    assert graded.wire() == {**answered.wire(), "fitness": 0.25, "objective": 0.25}, (
        "a graded cell is its facts plus this formula's grade"
    )
    composed = compile_scorer("skill_opened", "fitness * 0.5", verifier_graded=True)

    def channels(error: str | None = None, **reads: float) -> MeasuredCell:
        fault = {"error_category": "SERVER"} if error else {}
        cell = {"query": "q", "ground_truth": "", "predicted": "p", "error": error}
        return measurement(0, None, pipeline_data=reads, **cell, **fault).facts

    both = channels(skill_opened=1.0, env_reward=0.25)
    as_banked = json.dumps(both.wire(), sort_keys=True)
    first = composed.grade(both)
    assert json.dumps(both.wire(), sort_keys=True) == as_banked, "grading wrote into the facts"
    assert (first.grade.fitness, first.grade.objective, first.scored) == (1.0, 0.5, True)
    second = plain.grade(first.facts)
    assert (second.grade.fitness, second.grade.objective) == (0.25, 0.25), (
        "a regrade read the formula before it"
    )
    assert (first.grade.fitness, first.grade.objective) == (1.0, 0.5), (
        "a regrade moved the record it was taken from"
    )
    ordered = compile_scorer(
        "1.0", "fitness * (0.85 + 0.15 * skill_opened)", verifier_graded=True
    ).grade(channels(skill_opened=0.0))
    assert (ordered.grade.fitness, ordered.grade.objective) == (1.0, 0.85), (
        "the stamps are ORDERED: a composite naming `fitness` reads the one just landed"
    )

    # UNSCORED is a third state: ERRORED would stamp 0.0 and abandon the candidate's whole walk.
    unread = composed.grade(channels(env_reward=1.0))
    assert (unread.grade.fitness, unread.grade.objective, unread.scored) == (None, None, False)
    assert unread.grade.unscored and not {"fitness", "objective"} & unread.wire().keys(), (
        "a cell the formula cannot grade carries no grade at all"
    )
    assert not unread.facts.errored and unread.facts.error_category is None, (
        "a cell the formula cannot grade is no fault of the backend"
    )
    assert scoreable_rows([first, unread]) == [first], "an unscored cell entered the mean"
    recovered = plain.grade(unread.facts)
    assert (recovered.grade.fitness, recovered.scored) == (1.0, True), "a regrade recovers it"
    assert recovered.grade.unscored is None and "unscored" not in recovered.wire()
    # A formula that RAISES is a different fact and halts loud; an errored row never evaluates it.
    raising = compile_scorer("1.0 / tokens", None, verifier_graded=True)
    with pytest.raises(ScoringFormulaError):
        raising.grade(channels(tokens=0.0))
    errored = raising.grade(channels("HTTP 502", tokens=0.0))
    assert (errored.grade.fitness, errored.grade.objective, errored.scored) == (0.0, 0.0, False), (
        "an errored row is graded 0.0 without evaluating a formula that would raise on it"
    )
    # Arithmetic is on floats, so a tenant's formula cannot buy unbounded CPU or memory.
    for formula in ("'x' * 10**10", "abs('x')", "label_match(predicted, 'x' * 10**10)"):
        with pytest.raises(ValueError):
            compile_scorer(formula, verifier_graded=False)
    for formula in ("9**9**9", "pow(9, pow(9, 9))", "predicted * 10**10", "-predicted"):
        with pytest.raises(ScoringFormulaError):
            compile_scorer(formula, verifier_graded=False).grade(channels())
    halved = compile_scorer("label_match(predicted, 'p') * 2**-1", verifier_graded=False)
    assert halved.grade(channels()).grade.fitness == 0.5, (
        "a helper's string argument or float arithmetic was refused"
    )

    # A dial reads its term against the origin and never sums a raw unit.
    def solve(
        sid: int, said: str, tokens: int, usd: dict | None = None, **wire: Any
    ) -> MeasuredCell:
        steps = {"solve": {"input": tokens, "output": 0, **(usd or {})}}
        cell = {"query": "q", "predicted": said, "ground_truth": "a", "error": None}
        return measurement(sid, None, pipeline_data={"step_tokens": steps}, **cell, **wire).facts

    def composite(scorer: Scorer, *cells: MeasuredCell) -> float | None:
        return compute_composite_fitness(scorer.sheet(cells), _LLM_ONLY).composite_fitness

    anchors = origin_anchors(
        sheet(
            GradedCell.from_wire(solve(0, said, t).wire()) for said, t in (("a", 600), ("b", 784))
        )
    )
    assert anchors["tokens"] == pytest.approx(692.0), "the anchor is the origin's mean"
    per_sample = "label_match(predicted, ground_truth)"
    dialled = compile_scorer(
        per_sample,
        realize_dials(parse_dials("tokens=0.08, latency=0"), anchors),
        verifier_graded=False,
    )
    for tokens, expected in ((692, 1.0), (6920, 0.92 + 0.08 * 0.1), (100, 1.0)):
        assert composite(dialled, solve(0, "a", tokens)) == pytest.approx(expected), (
            f"a hit at {tokens} tokens reads its tokens against the origin's"
        )
    read_back = anchored_criterion_dials(
        realize_dials(parse_dials("tokens=0.08,cached=0.1,errored=0.2"), anchors)
    )
    assert read_back is not None and read_back["tokens"].anchor == pytest.approx(692.0)
    assert {name: dial.weight for name, dial in read_back.items()} == {
        "tokens": 0.08,
        "cached": 0.1,
        "errored": 0.2,
    }, "a dial reads back at the weight it was spelled"
    assert anchored_criterion_dials("0.9 * fitness + 0.1 * (1 - tokens)") is None, (
        "a hand-written formula is no dial"
    )

    # A miss is charged its cost, and a solved cell scores its composite.
    per_cell = "fitness * (0.92 + 0.08 * 692.0 / max(692.0, tokens))"
    charged = compile_scorer(per_sample, per_cell, verifier_graded=False)
    walked = charged.sheet(
        [
            solve(0, "a", 692),
            solve(1, "a", 2076),
            solve(2, "b", 692),
            solve(3, "b", 2076),
            solve(4, "b", 20760, error_category="SERVER"),
        ]
    )
    rows = walked.cells
    solved_cheap, solved_costly, miss_cheap, miss_costly, _ = (r.grade.objective for r in rows)
    assert solved_cheap == 1.0, "a solved cell moved off its composite"
    assert solved_costly == pytest.approx(0.92 + 0.08 / 3)
    assert miss_cheap == pytest.approx(compiler.MISS_COST_SHARE), "a miss is charged its share"
    assert miss_costly == pytest.approx(compiler.MISS_COST_SHARE * solved_costly)
    assert compute_composite_fitness(walked, _LLM_ONLY).composite_fitness == pytest.approx(
        (solved_cheap + solved_costly + miss_cheap + miss_costly) / 4
    ), "the faulted cell left the mean"
    assert is_floor_pinned(rows[2:4]) and not is_floor_pinned(rows[1:4]), (
        "the 0% floor is a fact about correctness, never about a charged miss's objective"
    )

    def objectives(*cells: MeasuredCell) -> list[float]:
        return [r.grade.objective for r in charged.sheet(cells)]

    solved = objectives(*(solve(i, "a", t) for i, t in enumerate((100, 692, 1000, 2076, 10**7))))
    assert solved[0] == solved[1] and solved[1:] == sorted(set(solved[1:]), reverse=True)
    costliest_hit, cheapest_miss = objectives(solve(0, "a", 10**7), solve(1, "b", 100))
    assert costliest_hit > cheapest_miss, "length made a miss outscore a hit"
    long_perfect = objectives(*(solve(i, "a", 10**6) for i in range(4)))
    short_half = objectives(*(solve(i, "ab"[i % 2], 100) for i in range(4)))
    assert sum(long_perfect) > sum(short_half), "length outranked a 2x accuracy gap"
    by_cost = compile_scorer(per_sample, "fitness / (1.0 + cost)", verifier_graded=False)
    at_rate, billed = by_cost.sheet(
        [solve(0, "a", 692, {"rate_priced_usd": 1.0}), solve(0, "a", 692, {"cost_usd": 3.0})]
    )
    assert (at_rate.grade.objective, billed.grade.objective) == (0.5, 0.25), (
        "a cost term reads the provider's bill, else our rate's price"
    )
    assert "cost" not in compiler.cell_channels_of(solve(0, "a", 692), None), (
        "most cells report no dollars, and an unpriced cell has no cost term"
    )

    # A conversation reaches the formula only as projected scalars.
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
    talked = measurement(0, 0.0, pipeline_data={"env_reward": 1.0, "turns": turns, **scalars})
    assert (
        compile_scorer(
            "env_reward * (1.0 if n_turns <= 4 else 0.5) * min(1.0, retrieve_turns / 2.0)",
            None,
            verifier_graded=True,
        )
        .grade(talked.facts)
        .grade.fitness
        == 1.0
    ), "a formula reads the projected scalars"

    # The id names the grading function: a ruler must never pool grades across two of them.
    defaulted = compile_scorer(per_sample, None, verifier_graded=False).grade(solve(2, "b", 692))
    assert defaulted.grade.objective == 0.0, "only a DECLARED cell formula charges a miss"
    assert auto_scorer_id(per_sample, None, judge_instrument=None) != auto_scorer_id(
        per_sample, DEFAULT_CELL_FORMULA, judge_instrument=None
    ), "the id, never the text, names the grader"
    before = auto_scorer_id(per_sample, per_cell, judge_instrument=None)
    assert auto_scorer_id(per_sample, per_cell, judge_instrument="a judge") != before, (
        "one text over two graders is two"
    )
    monkeypatch.setattr(compiler, "MISS_COST_SHARE", 0.3)
    assert auto_scorer_id(per_sample, per_cell, judge_instrument=None) != before, (
        "the miss share is half the grading function"
    )


# 2. Composite fitness and coverage


def test_a_round_scores_the_cells_its_prompt_answered_and_never_an_absence_as_zero(
    monkeypatch,
) -> None:
    # A round that measured every sample and failed them all IS a 0.0; an unmeasured one is not.
    for rate in (compute_accuracy, compute_error_rate, compute_degraded_rate):
        assert rate(results=[]) is None, f"{rate.__name__} read an empty round as 0.0"
    empty = compute_composite_fitness(sheet([]), _LLM_ONLY)
    assert (empty.composite_fitness, empty.accuracy, empty.total) == (None, None, 0), (
        "an EMPTY round has NO score, never a 0.0 that reads as an arm that failed every cell"
    )
    assert order_floor(None) < order_floor(0.0), "an unscored arm ranks below a failed one"
    # A CACHED replay stamps `total_time` 0.0, so latency reads `step_timings`; empty is absent.
    for steps, cost_s in (({"inner": 600.0}, 600.0), ({}, None)):
        timed = measurement(0, None, pipeline_data={"total_time": 0.0, "step_timings": steps})
        assert timed.facts.cost_s == cost_s, f"latency over {steps} must read {cost_s}"

    def asked(sid: int, predicted: str, fitness: float | None = None, **wire: Any) -> GradedCell:
        cell = {"query": f"q{sid}", "ground_truth": "a", "predicted": predicted, "error": None}
        return measurement(sid, fitness, **(cell | wire))

    deprecated = asked(0, "q", 0.0, error="SCHEMA_VALIDATION_FAILED")
    assert compute_accuracy(results=[deprecated]) == 0.0, "a deprecated row is a measured miss"
    # A provider's fault is the ABSENCE of a verdict: out of the mean, still on the error channel.
    faulted = [asked(0, "a", 1.0), asked(0, "ERROR", error="boom", error_category="SERVER")]
    assert compute_accuracy(results=faulted) == 1.0, "a provider fault entered the mean"
    assert compute_error_rate(results=faulted) == 0.5, "a provider fault left the error channel"
    # Every row ERRORED with no label to miss: no rate either (an L4 cell cut throughout is not 0%).
    unlabelled = asked(0, "ERROR", ground_truth="", error="boom", error_category="HALTED")
    all_errored = compute_composite_fitness(sheet([unlabelled]), _LLM_ONLY)
    assert (all_errored.accuracy, all_errored.composite_fitness, all_errored.total) == (
        None,
        None,
        0,
    ), "a round of faults has no score"

    # Only a provider fault leaves the count: a refusal and a cut cell are the prompt's own.
    labelled = compile_scorer("label_match(predicted, ground_truth)", verifier_graded=False)
    cut = {"error": "ran past its envelope", "error_category": "HALTED"}
    walked = [asked(i, "a") for i in range(5)]
    walked += [asked(5 + i, "I cannot help with that request.") for i in range(5)]
    walked += [
        asked(10, "ERROR", **cut),
        asked(11, "ERROR", error="HTTP 502", error_category="SERVER"),
    ]
    scores = compute_composite_fitness(labelled.sheet([c.facts for c in walked]), _LLM_ONLY)
    assert (scores.total, len(walked) - scores.total) == (11, 1), "only the provider fault left"
    assert scores.accuracy == pytest.approx(5 / 11), "the prompt's own failures left the count"
    assert scores.composite_fitness == pytest.approx(scores.accuracy), (
        "the default formula is plain accuracy, so the decision metric and the headline agree"
    )
    assert not labelled.grade(asked(10, "ERROR", ground_truth="", **cut).facts).scored, (
        "with no label a cut cell has no miss to be, so it carries no verdict either"
    )

    # A judge never grades a cell whose `predicted` is the NO_RESULT sentinel.
    billed: list[str] = []

    async def _ask(*_a: Any, **_k: Any) -> tuple[str, str]:
        billed.append("asked a model")
        return "A", ""

    monkeypatch.setattr(judge_call, "ask", _ask)
    # A cell that RAN and left a trace, so the no-trace arm cannot be what fired.
    traced = MeasuredCell(
        sample_id=0,
        predicted=NO_RESULT,
        pipeline=PipelineData(reasoning_trace="searched the docs, found the founding date"),
    )
    spec = JudgeSpec(name="answer_grounding", stages=[JudgeStage(model="m", provider="p")])
    verdict = asyncio.run(ANSWER_GROUNDING.grade(spec, traced))
    assert verdict.score is None, f"a sentinel answer was graded as {verdict.label!r}"
    assert billed == [], f"a cell with no answer was billed a grading: {billed}"

    # A merge never shrinks what was already measured.
    prior = list(sheet(measurements([1.0 if i < 10 else 0.0 for i in range(20)])))
    winner = sheet(
        measurements([1.0 if sid in {10, 12, 15} else 0.0 for sid in range(10, 18)], range(10, 18))
    )
    by_sid = {r.sample_id: r for r in merge_known_outcomes(prior, winner)}
    assert set(by_sid) == set(range(20)), "a merge dropped a measured cell"
    assert [by_sid[i].hit for i in (10, 11, 19)] == [True, False, False], "the later read stands"
    assert all(by_sid[i].hit is True for i in range(10)), "a merge rewrote an untouched cell"
    assert merge_known_outcomes(prior, []) == prior, "an empty merge moved something"


# 3. Electing a round winner


def _ruler(
    delta: dict[int, float],
    *,
    mu: float = 0.0,
    sigma: float = 2.0,
    se: float | dict[int, float] = 0.5,
) -> DeltaRuler:
    """Keyed by slot here, by the slot's `measurement` key on the ruler."""
    keyed = {str(sid): d for sid, d in delta.items()}
    return DeltaRuler(
        delta=keyed,
        delta_se=(
            {str(sid): s for sid, s in se.items()}
            if isinstance(se, dict)
            else dict.fromkeys(keyed, se)
        ),
        mu_delta=mu,
        sigma_delta=sigma,
        sigma_theta=1.5,
        calibration_model="1PL",
        anchor_id=anchor_id_of(keyed, mu, sigma, "1PL"),
    )


@contextmanager
def _peer_cycle(
    stores: Stores,
    ledger_dir: Path,
    monkeypatch: Any,
    optimizer: str,
    nodes: dict,
    solves: Callable[[str, Sample], bool | None],
    *,
    bench: int = 0,
    origin_composite: float = 0.0,
    **optimization: Any,
) -> Iterator[Cycle]:
    """Bound as a run seam binds it; the backend answers right where ``solves`` says, errs on None."""
    ledger = CycleEventLog.open(CycleDir(ledger_dir))
    bound = set_cycle_ledger(ledger)
    with at_round(1):
        bank = [Sample(id=i, query=f"q{i}", ground_truth="a") for i in range(16)]
        schema = pipeline_schema(
            name=f"{optimizer}-e2e",
            nodes=[PipelineNode(name="solve", tunes_llm=False, prompt_info=NodePromptInfo())],
        )
        config = CampaignConfig.model_validate(
            {
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
        session = loop_session(stores, schema, bank)
        session.state.ledger = ledger
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
        origin_rows = [
            asyncio.run(_measure(s, session, pipeline_params={"solve": {"prompt": "Answer."}}))
            for s in list(session.scoring.partition.search)[:4]
        ]
        sp = origin.to_job_search_point(
            schema=schema, framing=framing, demo=session.scoring.require_partition().demo
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
        try:
            yield cycle
        finally:
            reset_cycle_ledger(bound)


_noop = lambda *_a, **_k: None  # noqa: E731
_QUIET_CALLBACKS = types.SimpleNamespace(
    view_context=ViewContext(),
    on_phase=_noop,
    announce_candidate=_noop,
    on_candidate_scored=_noop,
    on_race_standing=_noop,
    on_election=_noop,
)


class _ComposedPayload(RoundPayload, manifest="ga-composition"):
    """The payload of a manifest no package ships: it keeps nothing of its own."""


def _bench_pass(
    stores: Stores, individual: str, reward: float, *, cells: int = 4, stop: PassStop | None = None
) -> BenchPass:
    held_out = [Sample(id=i, query=f"b{i}", ground_truth="a") for i in range(4)]
    rows = [
        measurement(
            s.id,
            None,
            query=s.query,
            sample_key=f"key:{s.query}",
            pipeline_data={"env_reward": reward},
        )
        for s in held_out[:cells]
    ]
    return BenchPass(
        round=int(individual != "origin"),
        candidate_id=individual,
        sp_hash=individual,
        cells=_filed(stores, f"bench_{individual}", rows, role="bench", sp_hash=individual),
        sample_keys=[f"key:{s.query}" for s in held_out],
        stop=stop,
        scorer_id=auto_scorer_id(_H2H_FORMULA, None, judge_instrument=None),
        reads_before=0,
    )


def test_a_round_elects_on_ability_and_the_bench_grades_the_pick_it_declared(
    tmp_path, monkeypatch
) -> None:
    # The election reads θ on one δ ruler: easy cells {0..19} sit low, hard {20..39} high.
    ruler = _ruler({i: (-1.5 if i < 20 else 1.5) for i in range(40)})
    weak_on_easy = [measurement(i, float(i < 16)) for i in range(20)]
    able_on_hard = [measurement(i, float(i < 34)) for i in range(20, 40)]
    by_id = {"weak_on_easy": sheet(weak_on_easy), "able_on_hard": sheet(able_on_hard)}
    assert sum(r.hit for r in weak_on_easy) > sum(r.hit for r in able_on_hard)
    winner_id, abilities = elect_round_winner(
        list(by_id),
        by_id,
        sheet(measurement(i, float(i < 20)) for i in range(40)),
        4,
        ruler,
        parent_bias=0.0,
    )
    assert winner_id == "able_on_hard", "a round winner elects by ability, not subset accuracy"
    assert abilities.theta["able_on_hard"] > abilities.theta["weak_on_easy"]

    # A thin arm cannot win on a margin inside its own noise; `coverage_floor` IS PoBB's `n_min`.
    flat = _ruler(dict.fromkeys(range(28), 0.0))
    half = sheet(measurements([1.0] * 14 + [0.0] * 14))
    deep = sheet(measurements([1.0] * 21 + [0.0] * 7))
    shallow = sheet(measurements([1.0] * 5 + [0.0]))
    ab = candidate_abilities({"deep": deep, "shallow": shallow}, half, flat)
    assert ab.parent is not None
    theta_p, se_p = ab.parent
    p = {c: p_exceeds(ab.theta[c], ab.theta_se[c], theta_p, se_p) for c in ("deep", "shallow")}
    assert ab.theta["shallow"] > ab.theta["deep"], "the thin arm's POINT lift is the larger"
    assert ab.theta_se["shallow"] > 1.8 * ab.theta_se["deep"], "on nearly twice the SE"
    assert p["deep"] > p["shallow"], "so it demonstrated less"
    thin_round = (["deep", "shallow"], {"deep": deep, "shallow": shallow}, half, 6, flat)
    assert elect_round_winner(*thin_round, parent_bias=0.0)[0] == "deep", "a thin arm won"
    assert (
        elect_round_winner(["shallow"], {"shallow": shallow}, half, 6, flat, parent_bias=0.0)[0]
        == "shallow"
    ), "ranking on P must never DISQUALIFY: a lone wide-posterior gain still wins"

    # The bar is what the parent can do, not the draw that crowned it.
    def won(*, se: float | None, electable: int) -> RoundResult:
        return round_result(
            1,
            electable_count=electable,
            selected_labels=["w"],
            candidate_scores=[scored_candidate("w", theta=0.5, theta_se=se)],
        )

    unelected = round_result(1, prompt_fields={})
    root_pi = 1 / math.sqrt(math.pi)
    for se, abs_tol in ((1.0, 5e-5), (0.25, 2e-5)):
        assert parent_selection_bias([won(se=se, electable=2)]) == pytest.approx(
            se * root_pi, abs=abs_tol
        ), "E[max of two standard normals] is 1/√π"
    assert parent_selection_bias([won(se=0.9, electable=1)]) == 0.0, "a LONE arm has no curse"
    assert parent_selection_bias([round_result(1, selected_labels=["c0"])]) == 0.0, (
        "a defaulted `electable_count` clamps to one arm"
    )
    assert parent_selection_bias([won(se=1.0, electable=99)]) == pytest.approx(
        parent_selection_bias([won(se=1.0, electable=6)])
    ), "the widest entry stands for any wider round"
    assert parent_selection_bias(
        [won(se=0.40, electable=6), unelected, won(se=0.10, electable=2), unelected]
    ) == pytest.approx(0.10 * root_pi, abs=2e-5), "a HELD round crowned nobody and is walked PAST"
    for rounds in ([unelected, unelected], [], [won(se=None, electable=4)]):
        assert parent_selection_bias(rounds) == 0.0, "nothing crowned on a read SE: no curse"
    parent = sheet(measurements([1.0] * 15 + [0.0] * 13))
    trailing = sheet(measurements([1.0] * 13 + [0.0] * 15))
    leading = sheet(measurements([1.0] * 17 + [0.0] * 11))
    tie = sheet(measurements([1.0] * 15 + [0.0] * 13))
    assert theta_lift_over_parent(candidate_abilities({"c": trailing}, parent, flat), "c") < 0.0
    for bias in (0.0, parent_selection_bias([won(se=5.0, electable=6)])):
        for arm, elected in ((trailing, ""), (tie, ""), (leading, "c")):
            assert (
                elect_round_winner(["c"], {"c": arm}, parent, 6, flat, parent_bias=bias)[0]
                == elected
            ), f"the credit {bias:.2f} reorders admitted arms; it never admits one"

    # A held round leads on the arm nearest the bar admission reads, over the readable arms.
    twenty = sheet(measurements([1.0] * 20 + [0.0] * 8))
    arms = {
        "near": sheet(measurements([1.0] * 5 + [0.0] * 3)),  # closest to the bar, on a wide SE
        "deep": sheet(measurements([1.0] * 18 + [0.0] * 10)),  # further off, on the whole panel
        "thin": sheet(measurements([1.0] * 3)),  # the highest θ in the round, under the floor
    }
    held_id, held_abilities = elect_round_winner(list(arms), arms, twenty, 6, flat, parent_bias=0.3)
    reads = readable_lifts(list(arms), arms, twenty, 6, held_abilities, 0.3)
    assert held_id == "", "no arm cleared the bar"
    assert held_abilities.parent is not None
    assert held_abilities.theta["thin"] > held_abilities.parent[0], "the floor, not θ, bars thin"
    assert set(reads) == {"near", "deep"}, "an arm under the floor is not readable"
    assert reads["deep"][0] < reads["near"][0] <= 0.0
    assert sum(reads["deep"]) > sum(reads["near"]), "the credit orders them the other way"
    assert closest_to_bar(reads) == "near", "the lead is named on the raw θ lift"
    assert closest_to_bar(reads, beside="near") == "deep"

    # A STOP IS NOT A VERDICT: only an invalid measurement bars an arm from leading.
    for arm, eligible in (
        (
            scored_candidate(
                "C1.3",
                accuracy=0.8333,
                outcome=ArmOutcome.BROKEN,
                degradation_context={"fatal": True, "dominant_warning": "llm_only:empty_response"},
            ),
            False,
        ),
        (
            scored_candidate(
                "C1.2",
                accuracy=0.40,
                outcome=ArmOutcome.ELIMINATED,
                elimination_context={"p_best": 0.048, "epsilon": 0.05, "gate": "epsilon"},
            ),
            True,
        ),
        (
            scored_candidate(
                "C1.1",
                accuracy=0.55,
                outcome=ArmOutcome.LOCKED_IN,
                elimination_context={"p_best": 0.96, "gate": "lock_in"},
            ),
            True,
        ),
        (scored_candidate("C1.4", accuracy=0.45), True),
    ):
        assert is_leader_eligible(arm) is eligible, f"{arm.outcome} decides leading wrongly"

    # A round the budget cuts elects only past the panel gate and stands only once closed.
    # Round 1 races the seeds with its offspring; its parent is the origin, never bought a cell.
    capo_nodes = {
        "blocks": {"config": {"block_size": 4, "max_blocks": 3}},
        "capo_init": {"config": {"size": 3, "k_max": 0}},
        "mating": {"config": {"offspring": 2}},
        "few_shot": {"config": {"k_max": 0}},
        "population": {"config": {"size": 3}},
    }

    def cut_by_budget(
        cut_round: int, budget: int, replayed: int, parent_cells: int, panel_gate: str
    ) -> None:
        case = f"round {cut_round} cut under panel_gate={panel_gate}"
        root = tmp_path / f"cut-{cut_round}-{panel_gate}"
        minted: list[str] = []

        async def _capo_llm(messages: list[dict], **_kw: Any) -> Any:
            if "Create overall 15 prompts" in messages[0]["content"]:
                text = json.dumps(
                    ["Solve the task, A.", "Solve the task, B.", "Solve the task, C."]
                )
            else:
                minted.append(f"Go {len(minted)}.")
                text = f"<prompt>{minted[-1]}</prompt>"
            return types.SimpleNamespace(content=text)

        paid: list[int] = []
        book = spend_book(None, usd_reserve=None)
        # Empty while the origin is measured, which no round's budget pays for.
        running: list[bool] = []

        def even_cells(_prompt: str, sample: Sample) -> bool:
            if running:
                paid.append(sample.id)
                if cycle.session.control.book is book and len(paid) >= budget:
                    book.declared = SpendCeilings(0.0, None)
            return sample.id % 2 == 0

        with _peer_cycle(
            workspace(root),
            root / "cycle",
            monkeypatch,
            "capo",
            capo_nodes,
            even_cells,
            panel_gate=panel_gate,
        ) as cycle:
            monkeypatch.setattr(node_context, "llm_call", _capo_llm)
            session = cycle.session
            running.append(True)
            # A backend to file under, so the kept population replays and only offspring are bought.
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
                _, uncut = asyncio.run(execute_round(cycle, 1, search, _QUIET_CALLBACKS))
                assert uncut is None, f"{case}: round 1 ran inside its budget"
            paid.clear()
            session.control = RunControl(book=book)
            cutting = execute_round(cycle, cut_round, search, _QUIET_CALLBACKS)
            if panel_gate == "strict":
                with pytest.raises(StopLoop) as halted:
                    asyncio.run(cutting)
                assert halted.value.reason is StopReason.SPEND_BUDGET, (
                    f"{case}: the bound that cut it names the stop"
                )
                assert cycle.rounds[-1].round == cut_round - 1, (
                    f"{case}: an arm was crowned on a holed panel"
                )
                return
            closed, cut = asyncio.run(cutting)
            assert cut is StopReason.SPEND_BUDGET, f"{case}: the round names its cut"
            rows = closed.all_candidate_results
            assert sum(len(arm) for arm in rows.values()) == replayed + len(paid), (
                f"{case}: a cell nobody elects on"
            )
            [reference] = closed.reference_results.values()
            assert len(reference) == parent_cells, (
                f"{case}: the parent is read where the archive holds it"
            )
            [short] = [cs for cs in closed.candidate_scores if len(rows[cs.candidate_id]) == 1]
            assert short.accuracy == 1.0, f"{case}: the cut arm reads perfect on its one cell"
            carried = [ind.id for ind in closed.optimizer_state.population]
            assert len(carried) == 3 and short.candidate_id not in carried, (
                f"{case}: elected below the floor"
            )
            full = next(cs for cs in closed.candidate_scores[3:] if cs is not short)
            assert full.candidate_id in carried, (
                f"{case}: an offspring the budget paid for in full was dropped"
            )
            assert cycle.rounds[-1].round == cut_round
            cycle.unwind_unclosed()
            assert [rr.round for rr in cycle.rounds] == [0] and cycle.selection.round == 0, (
                f"{case}: a stop between this election and its close leaves no result"
            )

    for cut_round, budget, replayed, parent_cells in ((1, 17, 0, 2), (2, 5, 12, 4)):
        for panel_gate in ("strict", "off"):
            cut_by_budget(cut_round, budget, replayed, parent_cells, panel_gate)

    # One round composes a peer's crossover with potter's racing selector.
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
                "initial_population_walks": "before_default",
                "resolved_prompts": {
                    key: capo["resolved_prompts"][key]
                    for key in ("capo_init/1", "capo_crossover/1")
                },
            }
        ),
        "utf-8",
    )

    class Composed(node_contract.OptimizerRuntime):
        prompt_sources = (paper_templates, capo_prompts)

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

    async def _crossover_llm(messages: list[dict], **_kw: Any) -> Any:
        asked = messages[0]["content"]
        if "Create overall 15 prompts" in asked:
            return types.SimpleNamespace(content=json.dumps(["Solve it, A.", "Solve it, B."]))
        assert "Solve it, A." in asked and "Solve it, B." in asked, "a crossover saw one parent"
        return types.SimpleNamespace(content=f"<prompt>{next(replies)}</prompt>")

    composed_nodes = {
        "blocks": {"config": {"block_size": 4, "max_blocks": 3}},
        "capo_init": {"config": {"size": 2, "k_max": 0}},
        "mating": {"config": {"offspring": 2}},
    }
    root = tmp_path / "composition"
    with _peer_cycle(
        workspace(root),
        root / "cycle",
        monkeypatch,
        name,
        composed_nodes,
        lambda prompt, _s: "Merged." in prompt,
    ) as cycle:
        monkeypatch.setattr(node_context, "llm_call", _crossover_llm)
        session = cycle.session
        session.backend_id = "composition-e2e"
        assert not cycle.population
        search = list(session.scoring.require_partition().search)
        closed, cut = asyncio.run(execute_round(cycle, 1, search, _QUIET_CALLBACKS))
    assert cut is None
    minted = [r for _, r in session.state.ledger.iter() if isinstance(r, CandidateMintedRecord)]
    assert {r.candidate_id for r in minted} == {cs.candidate_id for cs in closed.candidate_scores}
    for record in minted:
        assert [(made.node, made.mode) for made in record.lineage.variations] == [
            (f"{name}:mating", "deterministic"),
            (f"{name}:capo_crossover", "llm"),
        ], "an arm's lineage names the peer's mating and crossover"
        assert len(set(record.lineage.parent_ids)) == 2, "a crossover child has two parents"
    [winner] = closed.selected_labels
    crowned = next(cs for cs in closed.candidate_scores if cs.label == winner)
    assert "Merged." in crowned.prompt_fields["instruction"], "potter's selector elected it"
    assert [ind.id for ind in closed.optimizer_state.population] == [crowned.candidate_id], (
        "the individual the next round mutates from is not the population the run carries"
    )

    # An authored round measures the operator's arm and asks no proposer.
    authored = workspace(tmp_path / "authored")
    hop, cycle_dir = walking_cycle(authored)
    measured: list[str] = []

    def operator_solves(prompt: str, _sample: Sample) -> bool:
        measured.append(prompt)
        return "Operator" in prompt

    async def _no_proposer(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("an authored round asked a generator for an arm")

    def submit(key: str) -> Any:
        payload = SubmitArmsPayload(
            campaign_id=hop.campaign_id,
            cycle_id=hop.cycle_id,
            arms=[
                SubmittedArm(
                    prompt_fields={"instruction": "Operator wrote this."},
                    changes_description="the operator's own wording",
                )
            ],
        )
        return CommandDispatcher(authored).dispatch_cycle_command(
            CommandCall(payload, key), expected_version=None
        )

    try:
        with _peer_cycle(
            authored, cycle_dir, monkeypatch, "potter", {}, operator_solves, arm_gate="author"
        ) as cycle:
            monkeypatch.setattr(node_context, "llm_call", _no_proposer)
            monkeypatch.setattr(operator_hold, "_POLL_S", 0.0)
            session = cycle.session
            session.campaign_id, session.state.cycle_id = hop.campaign_id, hop.cycle_id
            session.control = RunControl(cycle_dir=cycle_dir)
            search = list(session.scoring.require_partition().search)
            measured.clear()

            async def at_the_hold() -> RoundResult:
                with pytest.raises(ConflictError):
                    await submit("arms-before-the-hold")

                async def answer() -> None:
                    while derive_run_state(cycle_dir).gate != "arms":
                        await asyncio.sleep(0)
                    await submit("arms-at-the-hold")

                (held_round, held_cut), _ = await asyncio.gather(
                    execute_round(cycle, 1, search, _QUIET_CALLBACKS), answer()
                )
                assert held_cut is None
                return held_round

            closed = asyncio.run(at_the_hold())
    finally:
        release_cycle(cycle_dir)
    [arm] = closed.candidate_scores
    assert arm.prompt_fields["instruction"] == "Operator wrote this.", "the operator's arm ran"
    assert any("Operator wrote this." in prompt for prompt in measured), "it was measured"
    [record] = [r for _, r in session.state.ledger.iter() if isinstance(r, CandidateMintedRecord)]
    assert [made.mode for made in record.lineage.variations] == ["operator"]

    # The bench grades the pick the optimizer declared, over a higher-composite round.
    async def _bench_llm(messages: list[dict], **_kw: Any) -> Any:
        if "Create overall 15 prompts" in messages[0]["content"]:
            return types.SimpleNamespace(content=json.dumps(["Solve it GOOD.", "Solve it OK."]))
        return types.SimpleNamespace(content="<prompt>Solve it BAD.</prompt>")

    bench_nodes = {
        "blocks": {"config": {"block_size": 4, "max_blocks": 2}},
        "capo_init": {"config": {"size": 2, "k_max": 0}},
        "mating": {"config": {"offspring": 1}},
        "few_shot": {"config": {"k_max": 0}},
        "population": {"config": {"size": 2}},
    }
    root = tmp_path / "bench"
    with _peer_cycle(
        workspace(root),
        root / "cycle",
        monkeypatch,
        "capo",
        bench_nodes,
        lambda prompt, s: "GOOD" in prompt or ("OK" in prompt and s.id % 2 == 0),
        bench=4,
        origin_composite=0.9,
    ) as cycle:
        monkeypatch.setattr(node_context, "llm_call", _bench_llm)
        session = cycle.session
        search = list(session.scoring.require_partition().search)
        picked = asyncio.run(execute_round(cycle, 1, search, _QUIET_CALLBACKS))[0]
        assert picked.composite_fitness < cycle.origin_round.composite_fitness
        assert picked.opt_sp is not None and picked.accuracy is not None
        [crowned] = picked.selected_scores
        [bar] = picked.reference_results.values()
        cold = candidate_abilities(
            {crowned.candidate_id: picked.all_candidate_results[crowned.candidate_id]}, bar, None
        )
        assert (crowned.theta, crowned.theta_se) == (
            cold.theta[crowned.candidate_id],
            cold.theta_se[crowned.candidate_id],
        ), "a cold ruler still reads a θ, and an arm carries the one an election takes of its rows"
        assert target_tripped(cycle, picked.accuracy) is StopReason.TARGET_HIT
        origin = cycle.origin_round.opt_sp
        assert origin is not None
        session.backend_id = "capo-e2e"
        origin_pass = asyncio.run(
            score_on_bench(
                session,
                cycle.searchpoint(origin.id),
                individual_id=origin.id,
                subject="origin",
                label="C0",
                round_num=0,
                cb=_QUIET_CALLBACKS,
            )
        )

        def graded(*held: BenchPass) -> BenchPasses:
            banked = BenchPasses(
                tolerance=0,
                origin=origin_pass,
                reserve_usd=0.0,
                reserve_tokens=0,
                selections={taken.candidate_id: taken for taken in held},
            )
            return asyncio.run(
                bench_selection(
                    session, cycle.rounds, framing=cycle.framing, banked=banked, cb=_QUIET_CALLBACKS
                )
            )

        passes = graded()
        [taken] = passes.selections.values()
        assert (taken.candidate_id, taken.round, taken.stop) == (picked.opt_sp.id, 1, None), (
            "the bench grades the pick the optimizer declared"
        )
        assert graded(taken).selections[taken.candidate_id] is taken, "a held pass was sent again"
        left = taken.model_copy(update={"sp_hash": "a pick the line left"})
        resent = graded(left).selections[taken.candidate_id]
        assert [c[:3] for c in resent.cells] == [c[:3] for c in taken.cells]
        assert all(replayed for *_, replayed in resent.cells), "a resent pass bought a cell again"
        earlier = taken.model_copy(update={"round": 7, "candidate_id": "an earlier pick"})

        async def _stopped(reason: StopReason) -> BenchPass:
            raise StopLoop(reason)

        with monkeypatch.context() as patched:
            patched.setattr(
                bench_runner, "score_on_bench", lambda *_a, **_k: _stopped(StopReason.SPEND_BUDGET)
            )
            short = graded(left, earlier)
            stop = short.selections[taken.candidate_id].stop
            assert stop is not None and stop.cause is StopReason.SPEND_BUDGET, (
                "a spent budget stops the pass and names the bound"
            )
            assert short.selections[earlier.candidate_id] is earlier, "a held pass was dropped"
            patched.setattr(
                bench_runner, "score_on_bench", lambda *_a, **_k: _stopped(StopReason.PAUSED)
            )
            with pytest.raises(StopLoop):
                graded(left, earlier)
        bench = read_bench(
            session.store,
            passes,
            session.scoring.require_scorer(),
            line=BenchLine(
                hop=session.hop,
                on_line=True,
                held_by=None,
                trigger="at_end",
                held_out=4,
                instrument_id="capo-e2e",
                run=LineRun(
                    selecting=False,
                    ending=StopReason.MAX_ROUNDS,
                    selection=ArmPointer(
                        round=1, label=picked.selected_labels[0], candidate_id=taken.candidate_id
                    ),
                    rounds_closed=1,
                ),
                spend=None,
            ),
        )
        assert bench.selected is not None and bench.origin is not None
        assert (bench.selected.accuracy.value, bench.origin.accuracy.value) == (1.0, 0.0)
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
        assert result.result_round == bench.selected.round == 1, "the result names the round graded"

    # A bench line reads the pick it holds, or says why it reads nothing.
    line_stores = workspace(tmp_path / "line")
    pick = OptSearchPoint(instruction="the declared pick")
    spent = PassStop(cause=StopReason.SPEND_BUDGET, warning=None)
    refused = PassStop(cause=WalkEnd.CLIENT_ERROR, warning="HTTP 403 :: model is not allowed")
    line_scorer = compile_scorer(_H2H_FORMULA, verifier_graded=False)
    for banked, run, state, can_grade, lift, series, stopped in (
        (None, {"trigger": "manual"}, ReadingState.NOT_ASKED, True, None, (None, []), None),
        (
            None,
            {"selecting": True, "ending": None},
            ReadingState.PENDING,
            False,
            None,
            (None, []),
            None,
        ),
        (
            None,
            {"ending": StopReason.CRASHED},
            ReadingState.RUN_FAILED,
            True,
            None,
            (None, []),
            None,
        ),
        (None, {}, ReadingState.NOT_ASKED, True, None, (None, []), None),
        (
            None,
            {"held_by": "cyc_beside"},
            ReadingState.HELD_ELSEWHERE,
            False,
            None,
            (None, []),
            None,
        ),
        ({}, {}, ReadingState.READ, False, 1.0, (0.0, [1.0]), None),
        ({"pick": "origin"}, {}, ReadingState.READ, False, None, (0.0, [None]), None),
        ({"pick": "displaced"}, {}, ReadingState.NOT_ASKED, True, None, (0.0, [None]), None),
        ({"cells": 3}, {}, ReadingState.PAST_TOLERANCE, True, None, (0.0, [None]), None),
        ({"cells": 3, "tolerance": 1}, {}, ReadingState.READ, False, 1.0, (0.0, [1.0]), None),
        ({"stop": spent}, {}, ReadingState.PASS_STOPPED, True, None, (0.0, [None]), "selected"),
        (
            {"origin_stop": refused},
            {},
            ReadingState.PASS_STOPPED,
            True,
            None,
            (None, [1.0]),
            "origin",
        ),
    ):
        case = f"banked={banked} run={run}"
        passes = None
        if banked is not None:
            origin_stop = banked.get("origin_stop")
            line_origin = _bench_pass(
                line_stores, "origin", 0.0, cells=4 - 4 * bool(origin_stop), stop=origin_stop
            )
            held_pick = banked.get("pick", pick.id)
            held = (
                []
                if held_pick == "origin"
                else [
                    _bench_pass(
                        line_stores,
                        held_pick,
                        1.0,
                        cells=banked.get("cells", 4),
                        stop=banked.get("stop"),
                    )
                ]
            )
            passes = BenchPasses(
                tolerance=banked.get("tolerance", 0),
                origin=line_origin,
                reserve_usd=0.0,
                reserve_tokens=0,
                selections={taken.candidate_id: taken for taken in held},
            )
        selection = "origin" if banked and banked.get("pick") == "origin" else pick.id
        line = BenchLine(
            hop=CycleHop(campaign_id="camp", cycle_id="cyc"),
            on_line="held_by" not in run,
            held_by=run.get("held_by"),
            trigger=run.get("trigger", "at_end"),
            held_out=4,
            instrument_id="i",
            run=LineRun(
                selecting=run.get("selecting", False),
                ending=run.get("ending", StopReason.MAX_ROUNDS),
                selection=ArmPointer(round=1, label="C1.1", candidate_id=selection),
                rounds_closed=1,
            ),
            spend=None,
        )
        line_bench = read_bench(line_stores, passes, line_scorer, line=line)
        assert (line_bench.status.state, line_bench.status.can_grade) == (state, can_grade), case
        headline = line_bench.vs_origin.headline
        assert (None if headline is None else headline.estimate.value) == lift, case
        assert line_bench.status.subject == stopped or not stopped, f"{case}: names who stopped"
        if stopped == "origin":
            assert "model is not allowed" in line_bench.status.sentence, f"{case}: says why"
        on_line = round_result(1, selected_labels=["C1.1"], opt_sp=pick, all_candidate_results={})
        assert bench_series(line_stores, passes, line_scorer, rounds=[on_line]) == series, case


# 4. Elimination — who is cut, and when


def test_an_arm_is_cut_only_on_evidence_that_it_trails() -> None:
    # ε ramps in from its floor; the depths come from `fit_theta_given_delta`'s dispersion rule.
    cfg = pobb_knobs(epsilon=0.30, epsilon_floor=0.15)
    graded = PoBBCheck(cfg, n_min=6, n_samples=28, ruler=None)
    for n, epsilon in ((6, 0.15), (9, 0.225), *((n, 0.30) for n in range(12, 29))):
        assert graded.epsilon_at(n) == pytest.approx(epsilon), f"ε at {n} cells"
    flat = PoBBCheck(
        pobb_knobs(epsilon=0.30, epsilon_floor=0.30), n_min=6, n_samples=28, ruler=None
    )
    assert flat.epsilon_at(6) == pytest.approx(0.30), "a floor at ε does not ramp"
    shipped = PoBBCheck(pobb_knobs(), n_min=6, n_samples=28, ruler=None)
    assert shipped.epsilon_at(shipped.n_min) == pytest.approx(shipped.epsilon), (
        "the manifest sits floor and ε on the same value, so an untouched campaign never grades"
    )

    def behind_perfect_prior(n: int, misses: int) -> Any:
        check = PoBBCheck(cfg, n_min=6, n_samples=28, ruler=None)
        check.register_completed(sheet(measurements([1.0] * 28)), candidate_id="winner")
        check.set_current("arm")
        return check.check(sheet(measurements([0.0] * misses + [1.0] * (n - misses))).cells)

    # A single adverse cell caps p_best at 0.25 (`sign_posterior`), so the ramp below it spares it.
    assert behind_perfect_prior(6, 1) is None and behind_perfect_prior(9, 1) is None, (
        "one adverse cell was cut"
    )
    cut = behind_perfect_prior(9, 2)
    assert cut is not None and cut.check_result["epsilon"] == pytest.approx(0.225), (
        "the decision archives the bar that FIRED, never the configured ε"
    )
    assert behind_perfect_prior(6, 2) is not None, (
        "two behind is still cut at the floor: the reprieve is for a width, not for a loser"
    )
    late = behind_perfect_prior(26, 4)
    assert late is not None and late.outcome is ArmOutcome.ELIMINATED
    assert late.check_result["epsilon"] == pytest.approx(0.30)
    unpaired = PoBBCheck(pobb_knobs(epsilon=0.05), n_min=4, n_samples=20, ruler=None)
    unpaired.register_completed(
        sheet(measurements([1.0] * 8, sample_ids=list(range(8)))), candidate_id="lucky_leader"
    )
    unpaired.set_current("challenger")
    challenger = sheet(measurements([0.0] * 5, sample_ids=[9, 12, 13, 14, 8]))
    assert unpaired.check(challenger.cells) is None, "an arm was cut on cells its rival never sat"

    # The collapse gate reads the answer, not the labels: a verifier proves an answer wrong by an
    # unsolved cell, and alike-and-all-solved is NOT collapsed.
    def answered(fitness: list[float], **over: Any) -> list[GradedCell]:
        return [
            GradedCell.from_wire(
                {
                    "sample_id": i,
                    "sample_key": str(i),
                    "ground_truth": "",
                    "predicted": "done",
                    "fitness": f,
                    **over,
                }
            )
            for i, f in enumerate(fitness)
        ]

    def collapse(rows: list[GradedCell]) -> Any:
        return PoBBCheck(pobb_knobs(), n_min=6, n_samples=28, ruler=None).check(rows)

    signal = collapse(answered([1.0, 0.0, 0.0, 1.0, 0.0, 0.0]))
    assert signal is not None and signal.check_result["gate"] == EliminationGate.COLLAPSED, (
        "a constant answerer must be cut at n_min with no labels to read"
    )
    assert collapse(answered([1.0] * 6)) is None, (
        "one answer that solves every cell is not a collapse"
    )
    assert collapse(answered([1.0, 0.0, 0.0, 1.0, 0.0, 0.0], predicted="NO_RESULT")) is None, (
        "an agent that wrote no answer file answered nothing, which is not one answer"
    )
    assert collapse(answered([0.0] * 6, error_category=ErrorCategory.PIPELINE)) is None, (
        "a backend that broke on every cell is not a verdict on the idea"
    )
    labelled = [
        GradedCell.from_wire(
            {
                "sample_id": i,
                "fitness": 1.0 if i % 2 == 0 else 0.0,
                "predicted": "Uncertain",
                "ground_truth": "TRUE" if i % 2 == 0 else "FALSE",
            }
        )
        for i in range(6)
    ]
    signal = collapse(labelled)
    assert signal is not None and signal.check_result["gate"] == EliminationGate.COLLAPSED
    assert not {"p_best", "epsilon", "n_priors"} & signal.check_result.keys(), (
        "a collapse cut returns before the posterior, so it carries no ε field"
    )

    # On a graded backend p_best discriminates; concordant cells move θ and must not reach ε.
    sids = [str(s) for s in range(12)]
    strong, weak = [0.66] * 12, [0.30] * 12
    p_strong, _ = elimination_p_best(strong, {"prior": weak}, sids, None)
    assert p_strong > 0.9, f"graded gate failed to discriminate: {p_strong}"
    assert elimination_p_best(weak, {"prior": strong}, sids, None)[0] < 0.1
    p_tie, _ = elimination_p_best(weak, {"prior": list(weak)}, sids, None)
    assert abs(p_tie - 0.5) < 1e-9, "a tie reads as a coin"
    epsilon = pobb_knobs().epsilon
    prefix = [str(s) for s in range(6)]
    # The probe is also the EASIEST cell in the prefix, which tempts a fit to read it as decisive.
    warm = DeltaRuler(
        delta={"0": 2.90, "1": 3.01, "2": 2.66, "3": 1.39, "4": 2.85, "5": 2.95},
        delta_se=dict.fromkeys(prefix, 0.4),
        mu_delta=2.6,
        sigma_delta=0.6,
        sigma_theta=1.0,
        calibration_model="1PL",
        anchor_id="test-anchor",
    )
    nothing_solved = [0.0] * 6
    p_thin, _ = elimination_p_best(
        nothing_solved, {"prior": [0.0, 0.0, 0.0, 1.0, 0.0, 0.0]}, prefix, warm
    )
    assert p_thin == pytest.approx(0.25), f"one adverse cell may not reach past 0.25: {p_thin}"
    assert p_thin > epsilon, "a one-cell verdict must not be cuttable"
    p_wide, _ = elimination_p_best(
        nothing_solved, {"prior": [1.0, 0.0, 1.0, 1.0, 0.0, 0.0]}, prefix, warm
    )
    assert p_wide == pytest.approx(0.0625), f"three adverse cells support a cut: {p_wide}"
    assert p_wide < epsilon, "the width is there — ε decides"

    # A fatal row ends a candidate on one sighting, and an advisory never does.
    def warned(kind: str | None) -> dict:
        warning = {"step": "entity_profiling", "code": "json_validate_failed"}
        if kind is not None:
            warning["kind"] = kind
        return {"pipeline_data": {"diagnostics": {"warnings": [warning]}}}

    degradation = DegradationCheck(threshold=0.4, min_samples=3)
    sig = degradation.check([measurement(0, 1.0), measurement(1, 1.0, **warned("structural"))])
    assert sig is not None and sig.check_result["fatal"] is True, (
        "a structural row is fatal at once"
    )
    for kind in ("transient", None):
        advisory = [measurement(i, 1.0, **warned(kind)) for i in range(6)]
        assert degradation.check(advisory) is None, f"a {kind} warning is advisory, never a cut"
    assert degradation.check(sheet([measurement(0, 1.0), measurement(1, 0.0)]).cells) is None
    # The rate arm, with the fast path off so the threshold is what is under test.
    rated = DegradationCheck(threshold=0.4, min_samples=3, fatal_fastpath=False)
    fatal = [measurement(sid, 0.0, **warned("structural")) for sid in (0, 9)]
    clean = [measurement(i, 1.0) for i in range(1, 6)]
    assert rated.check([fatal[0], *clean[:4]]) is None, "1/5 = 0.2, under the bar"
    rate_cut = rated.check([*fatal, *clean[:2]])
    assert rate_cut is not None and rate_cut.check_result["degraded_rate"] == pytest.approx(0.5)

    # A hole is neither a stop nor a deprecated row: `scored_samples - total` would count both.
    def hole_row(sample_id: int, predicted: str = "TRUE", **extra: Any) -> GradedCell:
        return measurement(sample_id, None, predicted=predicted, **extra)

    timed_out = {
        "predicted": "ERROR",
        "error_category": "UNKNOWN",
        "error": "ran past its deadline",
    }
    holed = [hole_row(i) for i in range(4)] + [hole_row(4, **timed_out), hole_row(5, **timed_out)]
    assert unscoreable_cells(sheet(holed)) == 2, "two holes"
    assert unscoreable_cells(sheet(hole_row(i) for i in range(5))) == 0
    assert unscoreable_cells([]) == 0
    # A fatal-classified transient with NO error_category: the retry left nothing extractable.
    deprecated = hole_row(
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
    assert is_deprecated(deprecated.facts), "fixture drift — this row must classify as deprecated"
    assert unscoreable_cells(sheet([hole_row(0), hole_row(1), hole_row(2), deprecated])) == 0, (
        "a classifier-deprecated sample was counted as a hole — the gate would halt a "
        "healthy cycle on a transient retry the loop already handles"
    )

    def bounded(category: ErrorCategory) -> RoundResult:
        rows = [
            measurement(0, 1.0),
            measurement(1, None, error="no verdict", error_category=category),
        ]
        return round_result(1, candidates_scored=1, all_candidate_results={"c0": sheet(rows)})

    assert repair_cut([bounded(ErrorCategory.HALTED)]).rounds == [], (
        "a cell a declared bound CUT is settled: the same bound cuts the next attempt"
    )
    assert repair_cut([bounded(ErrorCategory.UNSCOREABLE)]).rounds == [1], (
        "a cell that ran to its own end can answer differently on a re-measure"
    )


# 5. Escalation and stop — live, and folded back on resume


def _line_read(
    round_num: int,
    improved: bool,
    *picks: tuple[str, list[float | None]],
    c0: list[float | None],
    ids: dict[str, str] | None = None,
) -> OverlapReading:
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
_TIED_PANEL: list[float | None] = [1.0, 0.0] + [0.0] * 6 + [1.0]


def test_the_ladder_climbs_and_the_run_stops_only_on_evidence_it_resolved(
    tmp_path, monkeypatch
) -> None:
    # A θ-stall verdict must clear its own error: θ advances once for real (+0.467), then by noise.
    stall_rounds = [
        (0.4853, -0.1296, 0.216),
        (0.6248, 0.3376, 0.202),
        (0.6248, 0.3498, 0.198),
        (0.6248, 0.3498, 0.198),
    ]

    def climb(*, with_se: bool) -> list[str]:
        fsm, actions = EscalationFSM(), []
        for comp, theta, se in stall_rounds:
            asked = fsm.ask_l2_escalation(
                current_composite_fitness=comp,
                current_theta=theta,
                current_theta_se=se if with_se else None,
                escalation_ladder=EscalationLadder.FULL,
                l2_patience=2,
                l3_patience=1,
            )
            actions.append(str(asked.next_action))
            if asked.next_action != NextAction.FIRE_L2:
                break
            fsm.record_l2_fired(asked.l2)
        return actions

    assert climb(with_se=True) == ["fire_l2", "fire_l2", "fire_l2", "fire_l3"], (
        "a θ step inside its own SE is a stall"
    )
    assert NextAction.FIRE_L3 not in climb(with_se=False)
    # The SE bar applies on the θ scale only: a composite-scale verdict has no error term.
    assert EscalationFSM._improved(0.0, 0.0, 0.35, 0.34, 0.20)[0] is False
    assert EscalationFSM._improved(0.0, 0.0, 0.60, 0.34, 0.20)[0] is True
    assert EscalationFSM._improved(0.7, 0.6, None, None, 0.20) == (True, "composite")

    # The L1-only arm can reach no layer above it, over the WHOLE predicate space: a deferral that
    # never fires in one run is no suppression.
    grid = list(
        product([0, 1, 5], [0, 3], [None, 0, 2], [False, True], [False, True], [False, True])
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

    assert actions(EscalationLadder.L1) == {NextAction.CONTINUE}, "the L1 arm escalated"
    assert NextAction.FIRE_L2 in actions(EscalationLadder.FULL), (
        "not vacuous: the full ladder fires"
    )

    # A heal fire spends no L3 patience.
    healed = EscalationFSM()

    def stretch(theta: float, *, heal_first: bool) -> LadderAsk:
        asks: list[LadderAsk] = []
        while not asks or asks[-1].next_action is NextAction.FIRE_L2:
            asked = healed.ask_l2_escalation(
                current_composite_fitness=0.5,
                current_theta=theta,
                current_theta_se=0.2,
                escalation_ladder=EscalationLadder.FULL,
                l2_patience=2,
                l3_patience=1,
            )
            asks.append(asked)
            if asked.next_action is NextAction.FIRE_L2:
                healed.record_l2_fired(asked.l2)
                if heal_first and len(asks) == 1:
                    healed.record_l3_fired(None)
        return asks[-1]

    gate = stretch(0.3, heal_first=True)
    assert gate.next_action is NextAction.FIRE_L3, (
        "the heal wiped L2; the gate reached is L3's FIRST"
    )
    healed.record_l3_fired(gate.l3)
    gate = stretch(0.6, heal_first=True)
    assert gate.next_action is NextAction.FIRE_L3, "a later heal must not re-base the comparison"
    healed.record_l3_fired(gate.l3)
    assert stretch(0.6, heal_first=False).next_action is NextAction.STOP_L3_PATIENCE, (
        "the patience a heal did not spend still binds a flat run"
    )

    # A landed fire moves the ladder, and an unlanded one moves nothing.
    landed = EscalationFSM()
    trace: list[tuple[int, int, float | None, int, int, float | None, int]] = []

    def snapshot() -> None:
        held = landed.ladder
        trace.append(
            (
                held.l2.fires,
                held.l2.stall_count,
                held.l2.best_composite_fitness_at_entry,
                held.l3.fires,
                held.l3.stall_count,
                held.l3.best_composite_fitness_at_entry,
                held.l1_stall_count,
            )
        )

    def ask_at(fitness: float) -> LadderAsk:
        return landed.ask_l2_escalation(
            current_composite_fitness=fitness,
            escalation_ladder=EscalationLadder.FULL,
            l2_patience=3,
            l3_patience=2,
        )

    landed.record_l2_fired(ask_at(0.60).l2)
    snapshot()
    landed.record_l2_fired(ask_at(0.60).l2)  # an unimproved ask reads a stall the landing commits
    snapshot()
    unlanded = ask_at(0.60)
    assert unlanded.l2.stall_count == 2, "the ask read the stall"
    assert landed.as_read_by(unlanded).ladder.l2.stall_count == 2, (
        "the prompt that fire composed still reported the verdict that asked for it"
    )
    snapshot()
    landed.record_l3_fired(None)  # a heal: it bumps L3, the patience ratchet stays unset
    snapshot()
    landed.record_l3_fired(ask_at(0.75).l3)
    snapshot()
    assert trace == [
        (1, 0, 0.60, 0, 0, None, 0),
        (2, 1, 0.60, 0, 0, None, 0),
        (2, 1, 0.60, 0, 0, None, 0),
        (0, 0, None, 1, 0, None, 0),
        (0, 0, None, 2, 0, 0.75, 0),
    ], "an unparseable fire adopted something, or L3 firing kept L2's progress"

    # The L1 stall and the lives bank read the same rounds.
    def close(rnd: int, *, improved: bool, electable: int = 2) -> RoundClosedRecord:
        return RoundClosedRecord.of(
            round_result(
                rnd, improved=improved, electable_count=electable, all_candidate_results={}
            )
        )

    sequence = [(True, 2), (True, 2), (True, 2), (True, 2), (False, 0), (False, 2), (False, 2)]
    closes = [close(i, improved=up, electable=n) for i, (up, n) in enumerate(sequence, start=1)]
    stalled = EscalationFSM()
    for closed in closes:
        stalled.observe_round(
            advance=closed.overlap.advance, l1_patience=99, escalation_ladder=EscalationLadder.FULL
        )
    assert stalled.ladder.l1_stall_count == 3, (
        "three trailing non-improving rounds, one uncompared — all three advance the stall"
    )
    origin_close = close(0, improved=False, electable=0)
    ledger = [origin_close, origin_close, *closes]
    assert [stalls_left(ledger[: 2 + n], (2, 4)) for n in range(8)] == [2, 3, 4, 4, 4, 4, 3, 2], (
        "the origin closes TWICE and moves no bank; a round nothing reached costs no life"
    )
    drained = [close(i, improved=False) for i in (8, 9)]
    assert stalls_left([*ledger, *drained], (2, 4)) == 0
    unresolved = EscalationFSM()
    lines = [
        _line_read(1, True, ("C1.1", _CLEAN_PANEL), c0=_C0_PANEL),
        _line_read(2, True, ("C2.1", _TIED_PANEL), c0=_C0_PANEL),
        _line_read(3, True, ("C3.1", _TIED_PANEL), c0=_C0_PANEL),
    ]
    assert [line.advance for line in lines] == [
        RoundAdvance.ADVANCED,
        RoundAdvance.NOT_SEPARATED,
        RoundAdvance.NOT_SEPARATED,
    ]
    for line in lines:
        unresolved.observe_round(
            advance=line.advance, l1_patience=99, escalation_ladder=EscalationLadder.FULL
        )
    assert unresolved.ladder.l1_stall_count == 2, (
        "a round that crowned a winner and resolved NOTHING advances L1 patience"
    )

    # A reading that failed moves the stall clock neither way.
    won = lines[0]
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
    assert [rounds_without_advance(rounds[:n]) for n in (2, 3, 4)] == [0, 1, 1], (
        "the unread round neither clears the held round nor adds to it"
    )
    assert rounds_without_advance([*rounds, round_result(4, improved=False)]) == 2
    clock = EscalationFSM()
    for rr in rounds[1:]:
        clock.observe_round(
            advance=rr.overlap.advance, l1_patience=99, escalation_ladder=EscalationLadder.FULL
        )
    assert clock.ladder.l1_stall_count == 1, "the live stall reads what the standing reads"
    holed = _line_read(1, True, ("C1.1", [*_CLEAN_PANEL[:8], None]), c0=_C0_PANEL)
    assert holed.lead.coverage is not None and holed.lead.headline is not None
    assert (holed.lead.coverage.scored, holed.lead.coverage.excluded_faulted) == (8, 1)
    assert (
        holed.lead.headline.estimate.side == "above" and holed.advance is RoundAdvance.ADVANCED
    ), "one of nine panel cells errored: read on the eight both scored, and it decides"
    earlier = ("C1.1", [1.0] * 4 + [0.0] * 5)
    short = _line_read(2, True, earlier, ("C2.1", [*_CLEAN_PANEL[:8], None]), c0=_C0_PANEL)
    whole = _line_read(2, True, earlier, ("C2.1", _CLEAN_PANEL), c0=_C0_PANEL)
    assert (short.advance, whole.advance) == (RoundAdvance.NOT_SEPARATED, RoundAdvance.ADVANCED), (
        "against an earlier pick the holed one is a rate over another exam: it tops nobody"
    )
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
    assert promoted_on.keys() == set(ReadingState), "a reading state with no decided advance"
    for state, advance in promoted_on.items():
        pair = lead if state is ReadingState.READ else PairedReading.unread(state)
        assert round_advance(1, True, pair, ()) is advance, state
        assert round_advance(1, False, pair, ()) is RoundAdvance.HELD, state
        assert round_advance(0, True, pair, ()) is RoundAdvance.ORIGIN, state

    # The bench stops a peer on spent lives and on convergence.
    root = tmp_path / "peer"
    with _peer_cycle(
        workspace(root),
        root / "cycle",
        monkeypatch,
        "capo",
        {},
        lambda _p, _s: True,
        lives={"start": 2, "cap": 4},
        convergence_patience=3,
    ) as cycle:
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
        assert stop_after(held, uncompared) == (1, None), (
            "a round no arm reached the election of is evidence about the proposer, no life"
        )
        assert stop_after(promoted, promoted, promoted, held, held) == (2, None), (
            "the bank fills to its cap"
        )
        assert stop_after(promoted, promoted, promoted, held, held, held) == (
            1,
            StopReason.CONVERGED,
        ), "the convergence clock ends the run with a life unspent"

        # The campaign ends only where the objective is spent and the round resolved.
        readings = {
            RoundAdvance.ADVANCED: _line_read(1, True, ("C1.1", _CLEAN_PANEL), c0=_C0_PANEL),
            RoundAdvance.NOT_SEPARATED: _line_read(1, True, ("C1.1", _TIED_PANEL), c0=_C0_PANEL),
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

        for advance in (
            RoundAdvance.NOT_SEPARATED,
            RoundAdvance.ADVANCED_UNPAIRED,
            RoundAdvance.UNREAD,
        ):
            assert outcome(1.0, advance) is None, f"a perfect objective on {advance} stopped"
        assert outcome(0.507, RoundAdvance.ADVANCED) is None, (
            "100% accuracy under a cost-aware objective: accuracy is spent, the objective is not"
        )
        assert outcome(1.0, RoundAdvance.ADVANCED) is StopReason.PERFECT


def test_a_fire_after_the_round_closed_is_kept_on_resume_and_rewound_with_its_round(
    tmp_path,
) -> None:
    # A layer fires AFTER the round it reads has closed, so what it authors is on no close.
    cycle_dir = CycleDir(tmp_path)
    ledger = CycleEventLog.open(cycle_dir)
    # No cycle id: the restate below is appended here, as the store appends it.
    live = PotterState(session=types.SimpleNamespace(state=types.SimpleNamespace(cycle_id="")))  # type: ignore[arg-type]
    closed = round_result(1, optimizer_state=optimizer_state())
    for rr in (round_result(0, optimizer_state=optimizer_state()), closed):
        ledger.append(RoundClosedRecord.of(rr.model_copy(update={"all_candidate_results": {}})))

    def restate() -> None:
        state = closed.optimizer_state.model_copy(update={"payload": live.restated(closed)})
        ledger.append(OptimizerStateRecord(round=1, optimizer_state=state))

    floor = NODE_LAYOUTS["l1_generate"].floor
    moved = coerce_l1_layout({"persona": ["critique"]}, base=floor)
    assert moved is not None and moved != floor
    handed = live.memory
    steer = {"layout": moved.model_dump(mode="json"), "n_variants": 5}
    live.memory = L2.write(handed, TransitionResult(described="L2", steer=steer))
    assert handed == L2L3Memory() and live.memory != handed, (
        "a fire answers the NEXT memory: the one it was handed is what the closed round banked"
    )
    live.escalation.record_l2_fired(LayerReading(1, 0.5, None, None))
    restate()
    live.memory = L3.write(
        live.memory, TransitionResult(described="L3", plan="attack the default label")
    )
    live.escalation.record_l3_fired(None)
    restate()

    def resumed(last: int) -> PotterState:
        standing = scan_standing_rounds(ledger_chain(cycle_dir, moment=None)).rounds
        state = PotterState(session=types.SimpleNamespace())  # type: ignore[arg-type]
        state.replay(round_result(last, optimizer_state=standing[last].close.optimizer_state))
        return state

    kept = resumed(1)
    assert (kept.memory, kept.escalation.ladder) == (live.memory, live.escalation.ladder), (
        "paused inside round 2: both fires belong to the boundary the resume keeps"
    )
    assert kept.escalation.ladder.l3.fires == 1 and kept.memory.plan
    rewound = resumed(0)
    assert (rewound.memory, rewound.escalation.ladder) == (L2L3Memory(), Ladder()), (
        "rewound to re-run round 1: the fires are discarded with it"
    )


# 6. Paired readings over shared cells


def _reference_reading(
    reference: list[float | None], arm: list[float | None], *, outcome: ArmOutcome
) -> PairedReading:
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


_H2H_BANK = [Sample(id=i, query=f"q{i}", ground_truth="a") for i in range(12)]


def _h2h_campaign(
    stores: Stores,
    cid: str,
    n: int,
    *,
    selected: float,
    seed: int = 0,
    origin: float = 0.4,
    scoring: str = _H2H_FORMULA,
    at: int | None = None,
    incurred: float = 0.2,
    loop: float = 0.05,
    wall: int = 100,
    rebased: bool = False,
    arm: Arm | None = None,
    proposer_model: str | None = None,
    graded: bool = True,
    steered: bool = False,
) -> SubjectSpec:
    if stores.campaigns.load_campaign(cid) is not None:
        return SubjectSpec("campaign", cid)
    split = DatasetSplit(bench=6, seed=seed)
    partition = partition_bank(_H2H_BANK, split)
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
        measurement(
            s.id,
            None,
            query=s.query,
            sample_key=f"key:{s.query}",
            pipeline_data={"env_reward": 0.5},
        )
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
        individual = role if role == "origin" else f"{cid}:{role}"
        rows = [
            measurement(
                s.id,
                None,
                query=s.query,
                sample_key=f"key:{s.query}",
                pipeline_data={"env_reward": level + 0.1 * (s.id % 2)},
            )
            for s in partition.bench
        ]
        passes[role] = BenchPass(
            round=int(role == "selected"),
            candidate_id=individual,
            sp_hash=role,
            cells=_filed(
                stores, f"bench_{individual}_{seed}_{level}", rows, role="bench", sp_hash=individual
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


def test_a_pair_is_one_estimate_over_the_cells_both_members_scored_or_says_why_it_is_none(
    built_stores, tmp_path
) -> None:
    # The paired t matches `ttest_rel`, and brackets the same evidence it tests.
    cand = [0.90, 0.10, 0.85, 0.20, 0.75, 0.30, 0.95, 0.05]
    prior = [0.10, 0.85, 0.15, 0.80, 0.20, 0.70, 0.05, 0.90]
    reference = 0.8750918683549795  # scipy 1.17.1 `ttest_rel(cand, prior).pvalue`
    mean_d, lo, hi, p_two, n = paired_mean_t(cand, prior)
    assert n == len(cand)
    assert abs(mean_d - sum(c - p for c, p in zip(cand, prior, strict=True)) / n) < 1e-12
    assert p_two is not None and abs(p_two - reference) < 1e-12, (
        "spread wide enough that the 1/(4n) floor does not bind, so the two must agree exactly"
    )
    assert lo is not None and hi is not None and lo < mean_d < hi
    assert (p_two < 0.05) == (lo > 0.0 or hi < 0.0), (
        "one posterior, one verdict: a p above 0.05 and a bracket clearing zero cannot coexist"
    )
    p_greater = paired_mean_t(cand, prior, tail="greater")[3]
    assert p_greater is not None and abs(p_greater - reference / 2.0) < 1e-12
    assert paired_mean_t(prior, cand)[3] == p_two, "a two-sided p is symmetric"
    tight_p = paired_mean_t([0.5000001 * i for i in range(1, 7)], [0.5 * i for i in range(1, 7)])[3]
    assert tight_p is not None and tight_p > 0.05, (
        "the SE is clipped at 1/(4n): a near-constant difference scipy calls significant "
        "(p = 0.006) is not"
    )
    assert paired_mean_t([0.5], [0.1])[1:4] == (None, None, None), "one cell brackets nothing"

    # A level is bracketed on the Student-t its paired lift is tested on.
    def band(grades: list[float]) -> tuple[float | None, float | None]:
        return level_band(sheet(measurements(grades)), ROW_GRADES["fitness"])[1:]

    band_lo, band_hi = band(cand)
    half = 2.364624251592785 * stdev(cand) / len(cand) ** 0.5  # scipy `t.ppf(0.975, 7)`
    assert (band_lo, band_hi) == (
        pytest.approx(mean(cand) - half, abs=1e-9),
        pytest.approx(mean(cand) + half, abs=1e-9),
    ), "on the normal quantile an eight-cell band is a fifth too narrow"
    # scipy `t.ppf(0.975, 3)` over the SE floor 1/(4n) at n = 4.
    assert band([0.0] * 4) == (0.0, pytest.approx(3.182446305284263 / (4 * 4), abs=1e-9)), (
        "four cells at 0.0 have no spread, yet PoBB's 1/(4n) floor holds the band open"
    )
    assert band([1.0]) == (None, None)

    # A pair is ONE estimate over the cells both scored; samples 0 and 1 are one query under two
    # truths: two cells, wherever a query would key one.
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
        [*measurements([1.0, 1.0, 1.0, 0.0, 1.0, 1.0]), measurement(6, None, unscored="no term")],
        bank,
    )
    reading = _read_pair(a, b, bank)
    lift, coverage = reading.headline, reading.coverage
    assert lift is not None and coverage is not None and reading.cell_set is not None
    assert reading.cell_set.size == 7
    assert (coverage.shared, coverage.scored) == (7, 5), "the pair reads the cells both scored"
    assert (coverage.excluded_faulted, coverage.excluded_unscored) == (1, 1)
    assert coverage.state is CoverageState.PARTIAL
    t_mean, t_lo, t_hi, t_p, _ = paired_mean_t([1.0, 1.0, 1.0, 0.0, 1.0], [1.0, 0.0, 0.0, 1.0, 1.0])
    assert (lift.rate_a, lift.rate_b) == (pytest.approx(0.6), pytest.approx(0.8))
    estimate = lift.estimate
    assert estimate.value == pytest.approx(t_mean), "the lift is the paired mean"
    assert (estimate.ci_lo, estimate.ci_hi, estimate.p_value) == pytest.approx((t_lo, t_hi, t_p))
    assert estimate.side == "spans"
    assert estimate.p_floor == 0.25, "three cells differ, so no exact test can read below 2/2**3"
    assert lift.flips is not None
    assert (lift.flips.gained, lift.flips.lost, lift.flips.unchanged) == (2, 1, 2)
    sparse = [Sample(id=i, query=f"w{i}", ground_truth="t") for i in range(34)]
    gained = _read_pair(
        _pair_member("a", measurements([0.0] * 34), sparse),
        _pair_member("b", measurements([1.0] * 4 + [0.0] * 30), sparse),
        sparse,
    ).headline
    assert gained is not None and gained.estimate.ci_lo > 0.0
    assert (gained.estimate.side, gained.estimate.p_value) == ("spans", 0.125), (
        "the floor gates the claim: Student-t's interval clears zero, yet four differing cells cap p"
    )
    composite = Measurand(
        kind=MeasurandKind.GRADE,
        key="objective",
        scorer_id="s",
        binary=False,
        unit=MeasurandUnit.SCORE,
    )
    (beside,) = _read_pair(a, b, bank, measurands=[lift.measurand, composite]).beside
    assert beside.flips is None
    assert beside.estimate.value == pytest.approx(0.6 * estimate.value), (
        "a measurand beside the headline is read over the SAME scored cells"
    )
    clear = _read_pair(
        _pair_member("a", measurements([0.0] * 6), bank),
        _pair_member("b", measurements([1.0] * 6), bank),
        bank,
    )
    assert clear.headline is not None
    p_wide, p_clear = estimate.p_value, clear.headline.estimate.p_value
    assert 2 * p_clear < p_wide
    spanning, unheld, tight = as_family(
        [reading, PairedReading.unread(ReadingState.PENDING), clear], "table"
    )
    assert unheld.headline is None, "a pair nobody read is no test"
    assert spanning.headline is not None and spanning.headline.family is not None
    assert tight.headline is not None and tight.headline.family is not None
    assert (spanning.headline.family.n_tests, tight.headline.family.n_tests) == (2, 2), (
        "one table's pairs share one Holm correction"
    )
    assert tight.headline.family.p_adjusted == pytest.approx(2 * p_clear)
    assert spanning.headline.family.p_adjusted == pytest.approx(p_wide)

    # A cell measured twice is one standing row to every reader.
    four = [Sample(id=i, query=f"q{i}", ground_truth="t") for i in range(4)]

    def unanswered(sid: int) -> GradedCell:
        return measurement(sid, None, error="HTTP 502", error_category="SERVER")

    walked = [
        *(measurement(0, 0.0), measurement(0, 1.0)),  # scored twice: the later stands
        *(measurement(1, 1.0), unanswered(1)),  # the scored row stands over a later fault
        *(unanswered(2), measurement(2, 0.0)),  # and over an earlier one
        measurement(3, 1.0),
    ]
    by_slot = {0: 1.0, 1: 1.0, 2: 0.0, 3: 1.0}
    standing = {four[sid].key: grade for sid, grade in by_slot.items()}
    worth = {four[sid].key: measurement(sid, g).grade.objective for sid, g in by_slot.items()}
    twice = _pair_member("twice", walked, four)
    cells = twice.sheet
    assert cells.column(ROW_GRADES["fitness"]) == standing, "a second reading is a second row"
    assert cells.column(ROW_GRADES["objective"]) == worth
    assert responses_of(cells) == worth, "the ruler read another row than the column"
    assert distinct_valid_cells(cells) == 4
    fold = fold_cells(cells)
    assert (fold.total, fold.accuracy) == (4, 0.75)
    assert fold.composite_fitness == pytest.approx(sum(worth.values()) / 4)
    assert level_band(cells, ROW_GRADES["fitness"])[0] == fold.accuracy
    assert {key: row.grade.fitness for key, row in twice.by_key().items()} == standing
    once = _pair_member("once", measurements(list(by_slot.values())), four)
    unmoved = _read_pair(once, twice, four)
    lift, coverage = unmoved.headline, unmoved.coverage
    assert lift is not None and coverage is not None and lift.flips is not None
    assert (coverage.scored, coverage.excluded_faulted) == (4, 0)
    assert (lift.rate_a, lift.rate_b, lift.estimate.value) == (0.75, 0.75, 0.0), (
        "paired against an individual that walked each cell once at those grades, nothing moved"
    )
    assert (lift.flips.gained, lift.flips.lost, lift.flips.unchanged) == (0, 0, 4)
    ungraded = _pair_member(
        "ungraded", [*walked[:-1], measurement(3, None, unscored="no latency recorded")], four
    )
    assert ungraded.sheet.column(ROW_GRADES["fitness"]) == {
        four[sid].key: grade for sid, grade in by_slot.items() if sid < 3
    }
    assert level_band(ungraded.sheet, ROW_GRADES["fitness"])[0] == pytest.approx(2 / 3)
    short = _read_pair(once, ungraded, four).coverage
    assert short is not None and short.scored == 3, "an unscored cell left the pair"
    with pytest.raises(ValueError, match="ungraded cell"):
        ROW_GRADES["fitness"].read(ungraded.sheet.cells[-1])

    # A pair that cannot be read says which way instead of reading zero.
    six = [Sample(id=i, query=f"q{i}", ground_truth="t") for i in range(6)]
    panel = six[:4]
    left = _pair_member("a", measurements([1.0, 0.0, 1.0, 0.0]), six)

    def right(rows: list[GradedCell] | None = None, **read_under: Any) -> MemberRows:
        return _pair_member(
            "b", measurements([1.0, 1.0, 1.0, 0.0]) if rows is None else rows, six, **read_under
        )

    for member, asked, state in (
        (right(), {}, ReadingState.READ),
        (right(scope=RoleScope.REPORT), {}, ReadingState.SCOPE_DIFFERS),
        (right(instrument_id="other"), {}, ReadingState.INSTRUMENT_DIFFERS),
        (right(dataset_hash="other"), {}, ReadingState.DATASET_DIFFERS),
        (right(scorer_id="other"), {}, ReadingState.MEASURAND_DIFFERS),
        (right(cell_set_id="another-split"), {}, ReadingState.CELL_SET_DIFFERS),
        (right(address=left.address), {}, ReadingState.SAME_INDIVIDUAL),
        (right(), {"cells": []}, ReadingState.NOT_HELD),
        (
            right([measurement(i, None, error_category="SERVER") for i in range(4)]),
            {},
            ReadingState.MEMBER_UNSCOREABLE,
        ),
        (right(measurements([1.0], [0])), {}, ReadingState.UNDER_TWO_CELLS),
        # Two members that measured nothing in common hold no set to be empty.
        (
            right(measurements([1.0, 1.0], [4, 5])),
            {"cells": None, "cell_set": CellSetName.MEASURED_BY_BOTH},
            ReadingState.UNDER_TWO_CELLS,
        ),
    ):
        assert _read_pair(left, member, panel, **asked).state is state, f"{state} misread"
    read = _read_pair(left, right(), panel)

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

    # An arm is read on the cells its reference was read on.
    whole_panel: list[float | None] = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0]
    for ref_grades, arm_grades, outcome, state, scored, size, rates, level in (
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
            whole_panel,
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
            whole_panel,
            [1.0, 1.0, 1.0, 0.0, 1.0, None],
            ArmOutcome.MEASURED,
            CoverageState.PARTIAL,
            5,
            6,
            (0.6, 0.8),
            None,
        ),
    ):
        on_reference = _reference_reading(ref_grades, arm_grades, outcome=outcome)
        lift, coverage, set_read = (
            on_reference.headline,
            on_reference.coverage,
            on_reference.cell_set,
        )
        assert lift is not None and coverage is not None and set_read is not None
        assert (set_read.name, set_read.size) == (CellSetName.REFERENCE_CELLS, size), state
        assert (coverage.state, coverage.scored) == (state, scored), state
        assert (lift.rate_a, lift.rate_b) == rates, f"{state}: rates over the shared cells"
        assert on_reference.reference_level("fitness") == level, (
            f"{state}: a reference level is served only on its whole cell set"
        )
        assert on_reference.beside[0].measurand.key == "objective"

    # A verify reads its fresh cells alone and pairs its lift over the origin.
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

    def keyed(rows: list[GradedCell]) -> CellSheet:
        return sheet((_keyed(row, f"k{row.sample_id}") for row in rows), "s")

    recorded = keyed(measurements([1.0] * 20))
    fresh = keyed(measurements([1.0, 0.0] * 5, range(100, 110)))
    # The origin: half the round's cells, none of the fresh ones.
    c0_rows = keyed([*measurements([0.0, 1.0] * 10), *measurements([0.0] * 10, range(100, 110))])
    hop = CycleHop(campaign_id="camp", cycle_id="cyc")
    c0 = ArmPointer(round=0, label="C0", candidate_id="c0")

    def verified(pass_: VerifyPass, fresh_rows: CellSheet = fresh) -> Any:
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
            origin=verify_member(hop, c0, c0_rows, bought=0, instrument_id="bank"),
        )

    verify = verified(banked())
    assert verify.fresh.accuracy is not None and verify.recorded.accuracy is not None
    assert verify.fresh.accuracy.value == pytest.approx(0.5), "the fresh cells, never the pool"
    assert verify.recorded.accuracy.value == pytest.approx(1.0)
    assert verify.accuracy_increment == pytest.approx(-0.5)
    pair = verify.vs_origin
    assert pair.coverage is not None and pair.headline is not None
    assert (verify.fresh.n, verify.recorded.n, pair.coverage.scored) == (10, 20, 30)
    assert pair.headline.estimate.value == pytest.approx(0.5), (
        "paired per cell both scored, never the difference of two rates over different cells"
    )
    assert (verify.held, verify.held_absent) == (False, None), (
        "the recorded level sits above the fresh band, so the round's claim did not survive"
    )
    assert verified(banked(), keyed(measurements([1.0] * 10, range(100, 110)))).held is True
    hard = verified(banked("hard"))
    assert (hard.held, hard.held_absent) == (None, "hard_picks"), (
        "hard picks sit below the level whatever the candidate is worth: no verdict from the level"
    )
    at_origin = verified(banked(round_num=0))
    assert at_origin.vs_origin.state is ReadingState.SAME_INDIVIDUAL, (
        "the origin has nothing to be lifted over, and 0.0 there would read as no better than C0"
    )
    assert at_origin.vs_origin.headline is None

    # An individual's cells are one set, and a decision never reads an overlap row.
    def walk(tag: str, graded: dict[int, float]) -> CellSheet:
        return sheet(
            measurement(sid, g, answer=f"{tag}.{sid}", sample_key=f"k{sid}")
            for sid, g in graded.items()
        )

    origin_sp, edit_sp = OptSearchPoint(instruction="origin"), OptSearchPoint(instruction="edit")
    as_origin = walk("r0", {0: 1.0, 1: 0.0, 2: 1.0})
    rounds = [
        round_result(
            0,
            candidates_scored=0,
            opt_sp=origin_sp,
            results=as_origin,
            all_candidate_results={origin_sp.id: as_origin},
        ),
        round_result(
            1,
            candidates_scored=0,
            opt_sp=edit_sp,
            results=walk("c1", {2: 1.0, 3: 1.0}),
            all_candidate_results={edit_sp.id: walk("c1", {2: 1.0, 3: 1.0})},
            reference_results={origin_sp.id: walk("r1", {2: 0.0, 3: 1.0})},
            overlap_results={origin_sp.id: walk("ov", {4: 1.0, 5: 1.0})},
        ),
    ]
    in_run = InRunCells(rounds)

    def origin_read(scope: RoleScope) -> dict[int, float]:
        return {c.sample_id: c.grade.fitness for c in in_run.sheet(origin_sp.id, scope)}

    assert origin_read(RoleScope.REPORT) == {0: 1.0, 1: 0.0, 2: 0.0, 3: 1.0, 4: 1.0, 5: 1.0}, (
        "ONE set: the later reading of cell 2 stands, and the overlap rows are the origin's own"
    )
    assert origin_read(RoleScope.DECISION) == {0: 1.0, 1: 0.0, 2: 0.0, 3: 1.0}, (
        "a decision reads 2/4, never the two bought after it"
    )
    assert origin_read(RoleScope.BENCH) == {}
    assert [cell.sample_id for cell in rounds[1].sheet_of(origin_sp.id)] == [2, 3]

    # A cell is its sample's content, whatever query it shares or slot it holds.
    asks_one_thing = [
        Sample(id=0, query="is it so?", ground_truth="yes"),
        Sample(id=1, query="is it so?", ground_truth="no"),
    ]

    def filed_cycle(cycle_id: str, slots: dict[int, Sample]) -> list[IndividualWalk[WalkedCell]]:
        cycle_hop = CycleHop(campaign_id="keyed", cycle_id=cycle_id)
        built_stores.campaigns.mint_cycle(cycle_hop)
        rows = [
            measurement(slot, None, query=s.query, sample_key=s.key) for slot, s in slots.items()
        ]
        cycle_ledger = CycleEventLog.open(CycleDir(built_stores.campaigns.cycle_dir(cycle_hop)))
        cycle_ledger.append(CandidateMintedRecord(round=0, idx=0, candidate_id="c0", label="C0"))
        for cell in _filed(built_stores, f"keyed_{cycle_id}", rows):
            cycle_ledger.append(
                SampleScoredRecord(
                    round=0,
                    candidate_idx=0,
                    candidate_total=1,
                    individual_id="c0",
                    role=MeasurementRole.ORIGIN,
                    result=_announced(cell),
                )
            )
        return scan_ledger_walks(
            ledger_chain(CycleDir(built_stores.campaigns.cycle_dir(cycle_hop)), moment=None)
        )

    first = filed_cycle("cycle_a", dict(enumerate(asks_one_thing)))
    assert len(individual_cells(first, "c0", RoleScope.REPORT)) == 2, "one query, two cells"
    second = filed_cycle("cycle_b", {7: asks_one_thing[1], 9: asks_one_thing[0]})
    folded = individual_cells([*first, *second], "c0", RoleScope.REPORT)
    assert {key: slot for key, slot, _, _ in folded} == {
        asks_one_thing[0].key: 9,
        asks_one_thing[1].key: 7,
    }, "the same two samples at other slots are still two cells, at the later read"

    # A run stands on its selection, read against the origin it was promoted from.
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

    def stands(run: list[Any]) -> RunStanding:
        return RunStanding.after(run, lives=None, spent=None)

    # C0 is re-read between the lines: the panel cell it took in round 1 it misses by round 3.
    first_line = _line_read(1, True, ("C1.1", _TIED_PANEL), c0=_C0_PANEL, ids=ids)
    newest = _line_read(
        3, True, ("C1.1", _TIED_PANEL), ("C3.2", _CLEAN_PANEL), c0=[0.0] * 9, ids=ids
    )
    reread = _line_read(4, False, ("C3.2", _CLEAN_PANEL), c0=_C0_PANEL, ids=ids)
    assert (first_line.advance, newest.advance) == (
        RoundAdvance.NOT_SEPARATED,
        RoundAdvance.ADVANCED,
    )
    run = [
        closed(0, origin, "C0", selected="C0"),
        closed(1, first_pick, "C1.1", "C1.2", selected="C1.1", overlap=first_line),
        closed(2, first_pick, "C2.1"),  # held: nobody promoted, no line re-read
        closed(3, second_pick, "C3.1", "C3.2", selected="C3.2", overlap=newest),
    ]
    at_c0 = stands(run[:1])
    assert (at_c0.selection.label, at_c0.parent) == ("C0", None)
    assert at_c0.vs_origin == PairedReading.unread(ReadingState.SAME_INDIVIDUAL)
    held = stands(run[:3])
    assert held.selection.label == "C1.1", "a held round keeps the selection before it"
    assert held.vs_origin == first_line.lead
    standing_now = stands(run)
    assert standing_now.selection.model_dump() == {
        "round": 3,
        "label": "C3.2",
        "candidate_id": second_pick.id,
    }
    assert standing_now.parent.label == "C1.1"
    assert (standing_now.rounds_closed, standing_now.improved, standing_now.advanced) == (
        3,
        2,
        1,
    ), "promotions and separations are counted apart: a promotion is a point estimate"
    vs_c0 = standing_now.vs_origin
    assert (vs_c0.coverage.scored, vs_c0.headline.rate_a, vs_c0.headline.rate_b) == (9, 0.0, 1.0), (
        "C0 on the reading its selection sat beside: rates differenced must have sat one exam"
    )
    later = stands([*run, closed(4, second_pick, "C4.1", overlap=reread)])
    assert later.selection == standing_now.selection
    assert later.vs_origin.headline.rate_a == pytest.approx(1 / 9), (
        "a later round that holds re-reads the line, and the standing follows the NEWEST reading"
    )
    pass_stopped = OverlapReading.unpaired(ReadingState.PASS_STOPPED, 5, True)
    unread = stands([*run, closed(5, third_pick, "C5.1", selected="C5.1", overlap=pass_stopped)])
    assert (unread.selection.label, unread.parent) == ("C5.1", standing_now.selection)
    assert (unread.vs_origin.state, unread.vs_origin.headline) == (
        ReadingState.PASS_STOPPED,
        None,
    ), "a pick nobody has read against C0 yet says so — never the pick before it under its name"
    assert (standing_now.rounds_without_advance, unread.rounds_without_advance) == (0, 0), (
        "round 3 advanced, and an unread pick is no stall"
    )
    assert later.rounds_without_advance == 1

    # A head-to-head pairs two optimizers only on one bench under one grader.
    h2h_stores = workspace(tmp_path / "h2h")
    potter_a, capo_b = SubjectSpec("campaign", "potter_a"), SubjectSpec("campaign", "capo_b")
    potter_course = SubjectSpec("course", "potter_a", "cycle_potter_a_fork_r1")
    _h2h_campaign(h2h_stores, "potter_a", 1, selected=0.5, rebased=True)
    _h2h_campaign(h2h_stores, "capo_b", 2, selected=0.7, incurred=0.3, loop=0.15, wall=50)
    shared = subject_evidence(h2h_stores, [potter_a, capo_b]).head_to_head
    assert shared is not None and shared.rows[0].bench_set is not None
    budget = ArmBudget(
        usd=2.0, max_rounds=3, determinism=None, lives=None, convergence_patience=None
    )
    for h2h_id in ("real4", "real5", "real6", "real7"):
        h2h_stores.campaigns.declare_head_to_head(
            HeadToHeadRecord(
                head_to_head_id=h2h_id,
                created_at="",
                bench_set=shared.rows[0].bench_set,
                budget=budget,
            )
        )
    for subjects, third, verdict, differs_on, family in (
        ((potter_a, capo_b), None, True, [], 1),
        ((potter_course, capo_b), None, True, [], 1),
        ((potter_course, potter_a, capo_b), None, True, [], 1),
        (
            (potter_a, capo_b),
            {"cid": "gepa_c", "n": 3, "seed": 1},
            False,
            ["split", "bench_rows", "optimizer_models"],
            1,
        ),
        (
            (potter_a, capo_b),
            {"cid": "capo_d", "n": 4, "origin": 0.6},
            False,
            ["origin_reading"],
            1,
        ),
        ((potter_a, capo_b), {"cid": "capo_h", "n": 8, "scoring": "0.5 * env_reward"}, None, [], 0),
        ((potter_a, capo_b), {"cid": "capo_f", "n": 6, "at": 1}, True, [], 3),
        (
            (potter_a, capo_b),
            {"cid": "potter_g", "n": 7, "proposer_model": "other/model"},
            False,
            ["optimizer_models"],
            1,
        ),
        (
            (potter_a, capo_b),
            {"cid": "capo_s", "n": 9, "steered": True},
            False,
            ["human_intervened"],
            1,
        ),
    ):
        case = f"{subjects} beside {third}"
        asked = [*subjects]
        if third is not None:
            asked.append(_h2h_campaign(h2h_stores, **{"selected": 0.7, **third}))
        if not family:
            with pytest.raises(ValueError):
                subject_evidence(h2h_stores, asked)
            continue
        evidence = subject_evidence(h2h_stores, asked)
        h2h = evidence.head_to_head
        assert evidence.scorer_id == auto_scorer_id(_H2H_FORMULA, None, judge_instrument=None)
        assert h2h is not None and (h2h.verdict, h2h.guard.differs_on) == (verdict, differs_on), (
            case
        )
        tested = [
            p.reading
            for p in h2h.pairs
            if p.reading.headline is not None and p.reading.headline.family is not None
        ]
        assert len(tested) == family, f"{case}: the tests one correction was taken across"
        first_a, first_b = (m.address.path[-1].campaign_id for m in (tested[0].a, tested[0].b) if m)
        lift, coverage = tested[0].headline, tested[0].coverage
        assert lift is not None and coverage is not None and coverage.scored == 6, case
        assert lift.estimate.value == pytest.approx({"potter_a": 0.2, "capo_b": -0.2}[first_a])
        assert {first_a, first_b} == {"potter_a", "capo_b"} and lift.estimate.side != "spans", case
        differs = "differs" if differs_on else "uncontrolled"
        assert [r.guard.state.value for r in h2h.rows] == ["uncontrolled"] * 2 + [differs] * bool(
            third
        ), case
        rows = {r.campaign_id: r for r in h2h.rows}
        potter_row, capo_row = rows["potter_a"], rows["capo_b"]
        for ratio, times in (
            ("incurred_usd_ratio", 1.5),
            ("optimizer_incurred_usd_ratio", 3.0),
            ("worked_ratio", 0.5),
        ):
            assert getattr(capo_row, ratio) == pytest.approx(times * getattr(potter_row, ratio)), (
                f"{case}: priced on INCURRED spend, so capo's bill prices arrival"
            )
        assert capo_row.bench.cost.lift_per_usd == pytest.approx(0.3 / 0.25), case

    # The arms of one declared head-to-head are controlled only on their record.
    optimizer_hour = {
        "potter": ("potter", 10),
        "capo": ("capo", 11),
        "gepa": ("gepa", 12),
        "levi": ("levi", 13),
        "waiting": ("capo", 14),
        "capped": ("capo", 15),
    }
    for h2h_id, arms, verdict, guards in (
        ("real4", ("potter", "capo"), True, ["controlled"] * 2),
        ("real4", ("potter", "capo", "gepa", "levi"), False, ["controlled"] * 3 + ["uncontrolled"]),
        (
            "real6",
            ("potter", "capped", "gepa", "levi"),
            False,
            ["controlled", "controlled", "uncontrolled", "differs"],
        ),
        ("real7", ("potter", "capo", "waiting"), None, ["controlled"] * 3),
    ):
        case = f"{h2h_id} over {arms}"
        declared = []
        for key in arms:
            optimizer, hour = optimizer_hour[key]
            record = "real5" if key == "levi" else h2h_id
            declared.append(
                _h2h_campaign(
                    h2h_stores,
                    f"{optimizer}_{key}_{record}",
                    hour,
                    selected=0.6,
                    arm=Arm(head_to_head_id=record, arm_key=key, treatment_digest=key),
                    graded=key != "waiting",
                )
            )
        if "capped" in arms:
            capped = f"capo_capped_{h2h_id}"
            h2h_stores.campaigns.write_run_limits(
                CycleHop(campaign_id=capped, cycle_id=f"cycle_{capped}"),
                SpendCeilings(1.0, None),
                rounds=None,
                pause_at_round=None,
                reserve=SpendCeilings(),
            )
        h2h = subject_evidence(h2h_stores, declared).head_to_head
        assert h2h is not None and h2h.guard.head_to_head_id == h2h_id, case
        assert [r.guard.state.value for r in h2h.rows] == guards, case
        assert h2h.verdict is verdict, case
        if verdict is None:
            assert h2h.verdict_absent is ReadingState.PENDING, f"{case}: a waiting arm is pending"
