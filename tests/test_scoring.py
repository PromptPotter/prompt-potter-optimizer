"""A wrong score or a wrong decision.

Owns `application/scoring/`, `shared/statistics.py`, `optimizers/potter/pobb/` and
`optimizers/potter/escalation/`, the runner's election, `diagnostics/verify.py` and `evidence/`.
The run completes, the dashboard looks fine, and a different candidate should have won.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from itertools import count, product
from pathlib import Path
from statistics import mean, stdev
from typing import Any, cast

import pytest
import yaml
from factories import (
    PARENT,
    PROMPT_INFO,
    arm_state,
    arm_walk,
    close_round,
    jumping_clock,
    level_series,
    measurement,
    measurements,
    minted_campaign,
    optimizer_state,
    pipeline_schema,
    pobb_knobs,
    prompted,
    round_result,
    samples,
    score_summary,
    seat,
    set_cycle,
    sheet,
    spend_book,
    walking_cycle,
    workspace,
)
from offline_world import Router
from pydantic import ValidationError

from promptpotter.application import optimizers
from promptpotter.application.bench import node_context
from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.bench.node_context import NodeContext
from promptpotter.application.bench.resume_and_fork.repair import repair_cut
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.diagnostics.verify import verify_member, verify_reading
from promptpotter.application.evidence.head_to_head import GuardDifference
from promptpotter.application.evidence.read import subject_evidence
from promptpotter.application.evidence.subjects import SubjectSpec, parse_subject
from promptpotter.application.initialization.session import LaunchClock, Session
from promptpotter.application.intelligence.rasch import (
    candidate_abilities,
    responses_of,
    theta_lift_over_parent,
)
from promptpotter.application.jobs.launcher.admission import PassStoppedError, paid_pass
from promptpotter.application.optimizer_manifest import binding_optimizer, resolve_optimizer
from promptpotter.application.optimizers import nodes as node_contract
from promptpotter.application.optimizers import paper_templates
from promptpotter.application.optimizers.capo import prompts as capo_prompts
from promptpotter.application.optimizers.capo.state import CapoObjective
from promptpotter.application.optimizers.gepa.members import MinibatchGate
from promptpotter.application.optimizers.gepa.state import MinibatchBar, MinibatchBars
from promptpotter.application.optimizers.nodes import Panel, Proposals, ReadArm, RoundContext
from promptpotter.application.optimizers.potter.dispatch.facade import build_bundle
from promptpotter.application.optimizers.potter.dispatch.injections.panels import _vs_parent
from promptpotter.application.optimizers.potter.dispatch.layout import (
    NODE_LAYOUTS,
    coerce_l1_layout,
)
from promptpotter.application.optimizers.potter.dispatch.schemas import L3PlanOutput
from promptpotter.application.optimizers.potter.escalation.rules import (
    EscalationInputs,
    NextAction,
    decide_escalation,
)
from promptpotter.application.optimizers.potter.escalation.state import (
    HighWater,
    LadderAsk,
    LayerReading,
    as_read_by,
    ask_l2_escalation,
    l2_fired,
    l3_fired,
    observe,
)
from promptpotter.application.optimizers.potter.knobs import EscalationLadder
from promptpotter.application.optimizers.potter.pobb.checks import EliminationGate, PoBBCheck
from promptpotter.application.optimizers.potter.records import (
    L2Memory,
    L3Memory,
    Ladder,
    LayerCounters,
)
from promptpotter.application.run_callbacks import RunCallbacks
from promptpotter.application.run_phase_control import RunControl
from promptpotter.application.runner import bench as bench_runner
from promptpotter.application.runner.bench import (
    BenchLine,
    bench_selection,
    bench_series,
    read_bench,
    score_on_bench,
)
from promptpotter.application.runner.campaign_result import bank_campaign_result, line_launches
from promptpotter.application.runner.cycle_close import build_cycle_result
from promptpotter.application.runner.inner.spawn_context import publish_inner_spawn_context
from promptpotter.application.runner.measurement import ReferencePairs
from promptpotter.application.runner.round import execute_round, run_init
from promptpotter.application.runner.termination import standing_tripped, target_tripped
from promptpotter.application.scoring import query_loop
from promptpotter.application.scoring.classification import DegradationCheck, scoreable_rows
from promptpotter.application.scoring.evaluators import (
    DEFAULT_CELL_FORMULA,
    compute_accuracy,
    compute_degraded_rate,
    compute_error_rate,
)
from promptpotter.application.scoring.formula import compiler
from promptpotter.application.scoring.formula.compiler import (
    auto_scorer_id,
    compile_scorer,
    origin_anchors,
    parse_dials,
    realize_dials,
)
from promptpotter.application.scoring.formula.matchers import MATCHERS
from promptpotter.application.scoring.judges.grounding import ANSWER_GROUNDING
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
    PairedGrade,
    cells_read,
    closest_to_bar,
    crowning_cells,
    elect_round_winner,
    elimination_p_best,
    level_band,
    readable_lifts,
)
from promptpotter.config.paths import optimizers_root
from promptpotter.domain.bench import (
    BenchPass,
    BenchPasses,
    DatasetSplit,
    LineRun,
    PassStop,
    partition_bank,
)
from promptpotter.domain.campaign import Arm, Compare, HeadToHeadRecord, Optimize, Purpose
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.command_kinds import PauseCyclePayload, SubmitArmsPayload, SubmittedArm
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.judges import JudgeSpec, JudgeStage, JudgeVerdict
from promptpotter.domain.opt_search_point import NodePrompt, OptSearchPoint
from promptpotter.domain.paired_reading import (
    ROUND_LIFT_SPEC,
    ArmKey,
    ArmName,
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
from promptpotter.domain.phase_views import BenchGradedView
from promptpotter.domain.phases import CampaignPhase, RunPhase, StopLoop, StopReason, WalkEnd
from promptpotter.domain.pipeline_schema import ManifestNodeOverlay, PipelineNode
from promptpotter.domain.pool import BlockPool
from promptpotter.domain.results import (
    ArmSlot,
    Cut,
    CutKind,
    IndividualWalk,
    InRunCells,
    Measured,
    OverlapReading,
    PanelTrigger,
    RoundAdvance,
    RoundResult,
    RunStanding,
    TriggeredReading,
    VerifyPass,
    VerifyStrategy,
    individual_cells,
    is_floor_pinned,
    is_leader_eligible,
    line_by_individual,
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
    PhaseRecord,
    TokenUsageRecord,
)
from promptpotter.domain.sample import ArchiveEntry, Bank, Sample
from promptpotter.domain.scoring import (
    MAX_PASS_ROWS,
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
    ScoringFormulaError,
    UngradedCellError,
    WalkedCell,
    anchored_criterion_dials,
    turn_scalars,
)
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.spend import Budget, StepUsage, TokenUsageKind
from promptpotter.infrastructure.ledger import CycleEventLog, ledger_chain
from promptpotter.infrastructure.llm.spend_book import Hold, spending_under
from promptpotter.infrastructure.llm.telemetry import (
    at_round,
    reset_cycle_ledger,
    set_cycle_ledger,
)
from promptpotter.infrastructure.producer_lock import cycle_held, release_cycle
from promptpotter.infrastructure.runtime_flags import derive_run_state, standing_controls
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_ledger_walks,
    scan_standing_rounds,
)
from promptpotter.infrastructure.store.measurement_archive import config_key
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.answer_text import extract_gsm8k_number
from promptpotter.shared.errors import (
    BadRequestError,
    ConflictError,
    ErrorCategory,
    PayloadInvalidError,
)
from promptpotter.shared.measurement_context import RoleScope
from promptpotter.shared.statistics import p_exceeds, paired_mean_t

_H2H_FORMULA = "env_reward"


def _keyed(cell: GradedCell, sample_key: str) -> GradedCell:
    return cell.filed(dataclasses.replace(cell.facts, sample_key=sample_key))


def _objective(cell: GradedCell) -> float:
    assert cell.grade.objective is not None, f"cell {cell.key} carries no grade"
    return cell.grade.objective


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
        sheet=sheet((_keyed(row, key_of[int(row.key)]) for row in rows), scorer_id),
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
    read_under: dict[str, Any] = {
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
    return read_pair(**(read_under | asked))


def _filed(
    stores: Any, individual: str, rows: list[GradedCell], *, role: str = "panel", sp_hash: str = ""
) -> list[WalkedCell]:
    entry = ArchiveEntry(
        config_key=config_key([("", {"individual": individual})]),
        prompt_fields_id=sp_hash or individual,
        node_configs=[],
        pipeline_params={},
        dataset_hash="ds",
    )
    keyed = [row.facts for row in rows]
    answers = stores.archive.file_answers(
        entry,
        [(facts, "A") for facts in keyed],
        role=role,
        source="optimization_loop",
        created_at="",
    )
    return [(facts.sample_key, answer, False) for facts, answer in zip(keyed, answers, strict=True)]


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Each matcher grades an answer against its gold.
    matcher = {name: m.score for name, m in MATCHERS.items()}
    for match, args, expected in (
        (
            matcher["blocksworld_plan_match"],
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
            matcher["blocksworld_plan_match"],
            (
                "[PLAN]\nunstack the yellow block from on top of the orange block\npick up the "
                "orange block\nstack the orange block on top of the red block\n[PLAN END]",
                _BLOCKSWORLD_GOLD,
            ),
            0.0,
        ),
        (
            matcher["meeting_plan_match"],
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
            matcher["meeting_plan_match"],
            (
                "SOLUTION: You start at Marina District at 9:00AM. You travel to Mission District "
                "in 20 minutes and arrive at 9:20AM. You meet Stephanie for 120 minutes from 9:20AM "
                "to 11:20AM.",
                _MEETING_GOLD,
            ),
            0.0,
        ),
        (matcher["aime_match"], (r"First: \boxed{10}. Rechecking: \boxed{42}", "42"), 1.0),
        (matcher["aime_match"], (r"\boxed{undefined} The answer is 42", "42"), 1.0),
        (matcher["aime_match"], ("no numbers", "42"), 0.0),
        (extract_gsm8k_number, ("#### 1,234",), 1234.0),
        (extract_gsm8k_number, ("I calculated 99 but #### 42",), 42.0),
        (extract_gsm8k_number, ("no numbers",), None),
        (matcher["gsm8k_match"], ("42.0", "#### 42"), 1.0),
        (matcher["gsm8k_match"], ("#### 99", "#### 42"), 0.0),
        (matcher["label_match"], ("First try **No**. Corrected: **Yes**", "yes"), 1.0),
        (matcher["label_match"], ("plain text answer", "Plain Text Answer"), 1.0),
        (matcher["label_match"], ("foo", "bar"), 0.0),
        (matcher["label_match"], ("so the answer is **D**", "(D)"), 1.0),
        (matcher["label_match"], ("**(b)**", "B"), 1.0),
        (matcher["label_match"], ("**C**", "(D)"), 0.0),
        # Bolds nothing: the last non-empty line answers, the reasoning above it does not.
        (
            matcher["label_match"],
            ("The story is about a merger, a business matter.\n\nBusiness\n", "Business"),
            1.0,
        ),
        (
            matcher["label_match"],
            ("The story is about a merger.\n\nBusiness\n\nWorld", "Business"),
            0.0,
        ),
        # SQuAD's normalizer, articles and all, but a number keeps its own point and sign.
        (matcher["em_match"], ("so it is **the Beatles**", "Beatles"), 1.0),
        (matcher["em_match"], ("**A.**", "a"), 1.0),
        (matcher["em_match"], ("", "a"), 0.0),
        (matcher["em_match"], ("**the**", "a"), 1.0),
        (matcher["em_match"], ("**13**", "1.3"), 0.0),
        (matcher["em_match"], ("**.5**", "-.5"), 0.0),
        (matcher["em_match"], ("**-5 points.**", "-5 points"), 1.0),
        # The last box wins, braces nest, and the Hendrycks string rules equate spellings.
        (
            matcher["math_match"],
            (r"\boxed{\dfrac{1}{2}} so \boxed{\left( 1, 2 \right)}", "(1,2)"),
            1.0,
        ),
        (matcher["math_match"], (r"\boxed{\dfrac{3}{4}}", r"\frac34"), 1.0),
        (matcher["math_match"], (r"\boxed{10\text{ cm}}", "10"), 1.0),
        (matcher["math_match"], ("**x = 5**", "5"), 1.0),
        (matcher["math_match"], (r"\boxed{0.75}", r"\frac{3}{4}"), 0.0),
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
        "(",
    ):
        with pytest.raises(ScoringFormulaError):
            compile_scorer(formula, verifier_graded=False)
    # `turns` is compacted out of cold rows, so a formula reaching it is refused at COMPILE: a
    # bare Call reaches `eval()` as a NameError, which reads as a missing TERM.
    for formula in ("turns[0]", "turns.index", "len(turns)"):
        with pytest.raises(ScoringFormulaError):
            compile_scorer(formula, None, verifier_graded=True)

    # A cell read off the wire keeps its facts and drops any grade it arrived with.
    banked = {
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
    usage = StepUsage(
        input=3, output=4, estimated=True, cost_usd=0.5, model="m", reasoning=0, cache_read=0
    )
    warning = NodeWarning(
        step="n", code="c", message="m", kind="transient", details=(1, {"a": 2}), stats={"x": 1}
    )
    cell = MeasuredCell(
        sample_key="k",
        predicted="p",
        cached=True,
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
    for held in (usage, warning, cell.pipeline, cell, MeasuredCell(sample_key="k0")):
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

    def replied(predicted: str, *ranking: str) -> MeasuredCell:
        pipeline = dataclasses.replace(answered.pipeline, result_ranking=ranking)
        return dataclasses.replace(answered, predicted=predicted, pipeline=pipeline)

    blob, ranked = replied("Weigh it.\n**a**", "Weigh it.\n**a**"), replied("x", "x", "a")
    readings = [
        (read.ground_truth_rank, read.n_candidates, read.fitness)
        for formula, row in (
            ("label_match(predicted, ground_truth)", blob),
            ("list_rr(predicted, ground_truth)", blob),
            ("label_match(predicted, ground_truth)", ranked),
            ("rr(ground_truth_rank)", ranked),
        )
        for read in [compile_scorer(formula, verifier_graded=False).grade(row).grade]
    ]
    assert readings == [(None, None, 1.0), (2, 2, 0.5), (2, 2, 0.0), (2, 2, 0.5)], (
        "a reply's lines rank only under the formula reading them; the backend's ranking always"
    )
    composed = compile_scorer("skill_opened", "fitness * 0.5", verifier_graded=True)

    def channels(error: str | None = None, **reads: float) -> MeasuredCell:
        fault: dict[str, Any] = {"error_category": "SERVER"} if error else {}
        cell: dict[str, Any] = {"query": "q", "ground_truth": "", "predicted": "p", "error": error}
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
        with pytest.raises(ScoringFormulaError):
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
        sid: int, said: str, tokens: int, usd: dict[str, float] | None = None, **wire: Any
    ) -> MeasuredCell:
        steps = {"solve": {"input": tokens, "output": 0, **(usd or {})}}
        cell: dict[str, Any] = {"query": "q", "predicted": said, "ground_truth": "a", "error": None}
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
    assert len(rows) == 5, "the walk measured every cell it was given"
    solved_cheap, solved_costly, miss_cheap, miss_costly = (_objective(r) for r in rows[:4])
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
        return [_objective(r) for r in charged.sheet(cells)]

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
    offline: Router,
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
    cut: dict[str, Any] = {"error": "ran past its envelope", "error_category": "HALTED"}
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
    offline.llm.says = lambda _body: "A"
    # A cell that RAN and left a trace, so the no-trace arm cannot be what fired.
    traced = MeasuredCell(
        sample_key="k0",
        predicted=NO_RESULT,
        pipeline=PipelineData(reasoning_trace="searched the docs, found the founding date"),
    )
    spec = JudgeSpec(name="answer_grounding", stages=[JudgeStage(model="m", provider="openai")])

    async def grounded() -> JudgeVerdict:
        return await ANSWER_GROUNDING.grade(spec, traced)

    verdict = asyncio.run(grounded())
    assert verdict.score is None, f"a sentinel answer was graded as {verdict.label!r}"
    billed = offline.llm.sent
    assert billed == [], f"a cell with no answer was billed a grading: {billed}"

    # A merge never shrinks what was already measured.
    prior = list(sheet(measurements([1.0 if i < 10 else 0.0 for i in range(20)])))
    winner = sheet(
        measurements([1.0 if sid in {10, 12, 15} else 0.0 for sid in range(10, 18)], range(10, 18))
    )
    by_sid = {int(r.key): r for r in merge_known_outcomes(prior, winner)}
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


def _charged_by_length() -> tuple[CellSheet, CellSheet]:
    # A length-priced composite: 14 verdicts won and one lost; every tie is the longer answer's loss.
    scorer = compile_scorer(
        "label_match(predicted, ground_truth)",
        "fitness * (0.92 + 0.08 * 692.0 / max(692.0, tokens))",
        verifier_graded=False,
    )

    def walked(hits: set[int], tokens: int) -> CellSheet:
        steps = {"step_tokens": {"solve": {"input": tokens, "output": 0}}}
        return scorer.sheet(
            measurement(
                sid,
                None,
                query=f"q{sid}",
                predicted="a" if sid in hits else "b",
                ground_truth="a",
                error=None,
                pipeline_data=steps,
            ).facts
            for sid in range(34)
        )

    return walked(set(range(17)), 2768), walked(set(range(14, 18)), 692)


_PEER_BANK = samples(16)
# Each search row's place in the order the search reads it, under the peer cycle's own split.
_PEER_READ_ORDER = {
    s.id: n
    for n, s in enumerate(partition_bank(_PEER_BANK, DatasetSplit(bench=0, demo=2), seed=0).search)
}


_PEER_HOP = CycleHop(campaign_id="peer", cycle_id="cycle_peer")


@contextmanager
def _peer_cycle(
    stores: Stores,
    hop: CycleHop,
    monkeypatch: Any,
    optimizer: str,
    nodes: dict[str, Any],
    solves: Callable[[str, Sample], bool | None],
    *,
    bench: int = 0,
    backend_id: str = "",
    control: RunControl | None = None,
    **optimization: Any,
) -> Iterator[tuple[Cycle, RunCallbacks]]:
    """Bound as a run seam binds it; the backend answers right where ``solves`` says, errs on None."""

    async def _measure(sample: Sample, _session: Any, *, pipeline_params: Any) -> MeasuredCell:
        prompt = pipeline_params["solve"]["prompt"]
        solved = solves(prompt, sample)
        row: dict[str, Any] = {
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
    schema = pipeline_schema(
        name=f"{optimizer}-e2e",
        nodes=[PipelineNode(name="solve", tunes_llm=False, prompt_info=PROMPT_INFO)],
    )
    origin = OptSearchPoint(
        prompts={
            "solve": NodePrompt(
                fields={"instruction": "Answer.", "answer_format": "Reply with the letter."}
            )
        }
    ).configured(None, schema)

    def measured_origin(session: Session) -> CellSheet:
        rows = [
            asyncio.run(_measure(s, session, pipeline_params={"solve": {"prompt": "Answer."}}))
            for s in list(session.state.scoring.require_partition().search)[:4]
        ]
        return session.state.scoring.require_scorer().sheet(rows)

    with at_round(1):
        seated = seat(
            stores,
            schema=schema,
            bank=_PEER_BANK,
            bench=bench,
            demo=2,
            # The campaign's composite REWARDS length, the opposite of CAPO's objective.
            scorer=compile_scorer(
                "label_match(predicted, ground_truth)",
                "fitness * min(1.0, target_prompt_chars / 200.0)",
                verifier_graded=False,
            ),
            optimization={
                "optimizer": optimizer,
                "elimination_n_min": 2,
                "nodes": nodes,
                **optimization,
            },
            dataset_name=schema.name,
            backend_id=backend_id,
            control=control,
            hop=hop,
            origin=(origin, measured_origin),
            framing=TaskDecomposition(pipeline_purpose="Answer each query with its letter."),
        )
        cycle, callbacks = seated.cycle, seated.callbacks
        bound = set_cycle_ledger(callbacks.ledger)
        try:
            publish_inner_spawn_context(cycle.session)
            with binding_optimizer(cycle.config.optimization):
                run_init(cycle, callbacks)
                with spending_under(cycle.session.state.control.book):
                    yield cycle, callbacks
        finally:
            reset_cycle_ledger(bound)


def _asked(body: dict[str, Any]) -> str:
    """What an optimizer call sent the provider, every message of it."""
    return "\n".join(str(m["content"]) for m in body["messages"])


def _bench_pass(
    stores: Stores, individual: str, reward: float, *, cells: int = 4, stop: PassStop | None = None
) -> BenchPass:
    held_out = samples(4, query="b")
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    # The election reads θ on one δ ruler: easy cells {0..19} sit low, hard {20..39} high.
    ruler = _ruler({i: (-1.5 if i < 20 else 1.5) for i in range(40)})
    eps = pobb_knobs().epsilon
    weak_on_easy = [measurement(i, float(i < 16)) for i in range(20)]
    able_on_hard = [measurement(i, float(i < 34)) for i in range(20, 40)]
    by_id = {
        ArmKey("weak_on_easy"): sheet(weak_on_easy),
        ArmKey("able_on_hard"): sheet(able_on_hard),
    }
    assert sum(r.hit for r in weak_on_easy) > sum(r.hit for r in able_on_hard)
    winner_id, abilities = elect_round_winner(
        list(by_id),
        by_id,
        sheet(measurement(i, float(i < 20)) for i in range(40)),
        4,
        ruler,
        epsilon=eps,
    )
    assert winner_id == "able_on_hard", "a round winner elects by ability, not subset accuracy"
    assert abilities.theta["able_on_hard"] > abilities.theta["weak_on_easy"]
    # 6 hits far below θ, 13 misses far above, one miss near it the arm gained.
    spread = _ruler({**dict.fromkeys(range(6), -2.8), 6: 1.0, **dict.fromkeys(range(7, 20), 4.0)})
    one_cell = {ArmKey("one"): sheet(measurement(i, float(i < 7)) for i in range(20))}
    parent_six = sheet(measurement(i, float(i < 6)) for i in range(20))
    assert elect_round_winner(list(one_cell), one_cell, parent_six, 6, spread, epsilon=eps)[0] is (
        None
    ), "one discordant cell of 20 promoted: a lift its own paired evidence cannot carry"

    # A thin arm cannot win on a margin inside its own noise; `coverage_floor` IS PoBB's `n_min`.
    flat = _ruler(dict.fromkeys(range(28), 0.0))
    half = sheet(measurements([1.0] * 14 + [0.0] * 14))
    deep = sheet(measurements([1.0] * 21 + [0.0] * 7))
    shallow = sheet(measurements([1.0] * 5 + [0.0], range(14, 20)))
    ab = candidate_abilities({"deep": deep, "shallow": shallow}, half, flat)
    p = {c: p_exceeds(ab.theta[c], ab.theta_se[c], *ab.bar[c]) for c in ("deep", "shallow")}
    assert ab.theta["shallow"] > ab.theta["deep"], "the thin arm's POINT lift is the larger"
    assert ab.theta_se["shallow"] > 1.8 * ab.theta_se["deep"], "on nearly twice the SE"
    assert p["deep"] > p["shallow"], "so it demonstrated less"
    deep_key, shallow_key = ArmKey("deep"), ArmKey("shallow")
    thin_arms = {deep_key: deep, shallow_key: shallow}
    assert elect_round_winner(list(thin_arms), thin_arms, half, 6, flat, epsilon=eps)[0] == (
        "deep"
    ), "a thin arm won"
    assert elect_round_winner([shallow_key], {shallow_key: shallow}, half, 6, flat, epsilon=eps)[
        0
    ] == ("shallow"), "ranking on P must never DISQUALIFY: a lone wide-posterior gain still wins"
    behind = sheet(measurements([1.0] * 4 + [0.0] * 2))
    behind_lift = theta_lift_over_parent(candidate_abilities({"b": behind}, half, flat), "b")
    assert behind_lift is not None and behind_lift > 0.0
    assert (
        elect_round_winner([ArmKey("b")], {ArmKey("b"): behind}, half, 6, flat, epsilon=eps)[0]
        is None
    ), "a θ lift over the parent's whole panel never crowns an arm that LOST the cells it shares"
    swapped = sheet(measurement(i, float(0 < i < 20 or i == 20)) for i in range(40))
    assert (
        elect_round_winner(
            [ArmKey("s")],
            {ArmKey("s"): swapped},
            sheet(measurement(i, float(i < 20)) for i in range(40)),
            4,
            ruler,
            epsilon=eps,
        )[0]
        is None
    ), "one cell won and one lost is a tie, whatever the δ weighting or the fit's last digit"
    longer, terse_parent = _charged_by_length()
    level = _ruler(dict.fromkeys(range(34), 0.0))
    assert elect_round_winner(
        [ArmKey("l")], {ArmKey("l"): longer}, terse_parent, 6, level, epsilon=eps
    )[0] == ("l"), "a cost term's tie-break was counted as a verdict lost"

    # The bar is what the parent can do, not the draw that crowned it: no later race reads those cells.
    crowned_on = sheet(measurements([1.0] * 4, range(30, 34)))

    def won(electable: int) -> RoundResult:
        return round_result(
            1,
            selected="c0",
            voted={"electable": electable},
            cells={ArmKey("c0"): crowned_on},
        )

    assert crowning_cells([won(2), round_result(2, improved=False)], "c0") == {
        cell.key for cell in crowned_on
    }, "a parent's cells stay out of every race it judges, a HELD round's included"
    assert not crowning_cells([won(2)], "c1"), "an ancestor's are its child's own measurements"
    assert not crowning_cells([won(1)], "c0"), "a LONE arm was never picked over another"

    # A held round leads on the arm nearest the bar admission reads, over the readable arms.
    twenty = sheet(measurements([1.0] * 20 + [0.0] * 8))
    near_key = ArmKey("near")
    arms = {
        near_key: sheet(measurements([1.0] * 5 + [0.0] * 3)),  # closest to the bar, on a wide SE
        deep_key: sheet(measurements([1.0] * 18 + [0.0] * 10)),  # further off, on the whole panel
        ArmKey("thin"): sheet(measurements([1.0] * 3)),  # the highest θ, under the floor
    }
    held_id, held_abilities = elect_round_winner(list(arms), arms, twenty, 6, flat, epsilon=eps)
    reads = readable_lifts(list(arms), 6, held_abilities)
    assert held_id is None, "no arm cleared the bar"
    assert held_abilities.theta["thin"] > held_abilities.bar["thin"][0], (
        "the floor, not θ, bars thin"
    )
    assert set(reads) == {"near", "deep"}, "an arm under the floor is not readable"
    assert reads[deep_key] < reads[near_key] <= 0.0
    assert closest_to_bar(reads) == "near", "the lead is named on the θ lift"
    cold = candidate_abilities({"off": sheet(measurements([1.0] * 8, range(26, 34)))}, twenty, None)
    assert cold.cells["off"] == 2 and not readable_lifts([ArmKey("off")], 6, cold), (
        "cold, the floor counts the cells θ was READ on — those shared with the parent"
    )
    warm = candidate_abilities({"off": sheet(measurements([1.0] * 8, range(24, 32)))}, half, flat)
    assert warm.cells["off"] == 4, "warm, the floor counts only the cells the ruler carries"
    assert closest_to_bar(reads, beside=near_key) == "deep"

    # A STOP IS NOT A VERDICT: only an invalid measurement bars an arm from leading.
    for arm, eligible in (
        (arm_state("C1.3", cut="broken"), False),
        (arm_state("C1.5", invalid_reason="no_op_variant"), False),
        (
            arm_state(
                "C1.2",
                cut="eliminated",
                elimination={"p_best": 0.048, "epsilon": 0.05, "gate": "epsilon"},
            ),
            True,
        ),
        (
            arm_state("C1.1", locked_in=True, elimination={"p_best": 0.96, "gate": "lock_in"}),
            True,
        ),
        (arm_state("C1.4"), True),
    ):
        assert is_leader_eligible(arm) is eligible, f"{arm.label} decides leading wrongly"

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

        def capo_says(body: dict[str, Any]) -> str:
            if "Create overall 15 prompts" in _asked(body):
                return json.dumps(
                    ["Solve the task, A.", "Solve the task, B.", "Solve the task, C."]
                )
            minted.append(f"Go {len(minted)}.")
            return f"<prompt>{minted[-1]}</prompt>"

        paid: list[int] = []
        book = spend_book(None, usd_reserve=None)
        # Empty while the origin is measured, which no round's budget pays for.
        running: list[bool] = []
        cutting: list[bool] = []

        def even_cells(_prompt: str, sample: Sample) -> bool:
            if running:
                paid.append(sample.id)
                if cutting and len(paid) >= budget:
                    book.hold_of = Hold(Budget(usd=0.0), book.reserve)
            return _PEER_READ_ORDER[sample.id] % 2 == 0

        with _peer_cycle(
            workspace(root),
            _PEER_HOP,
            monkeypatch,
            "capo",
            capo_nodes,
            even_cells,
            # A backend to file under, so the kept population replays and only offspring are bought.
            backend_id="capo-e2e",
            control=RunControl(book=book),
            panel_gate=panel_gate,
        ) as (cycle, callbacks):
            offline.llm.says = capo_says
            session = cycle.session
            running.append(True)
            search = list(session.state.scoring.require_partition().search)
            if cut_round == 1:
                # Run init scores the origin through the gateway — here on half of CAPO's first block.
                asyncio.run(
                    score_search_point(
                        cycle.parent_point,
                        search[:2],
                        session,
                        label="origin",
                        measured=None,
                    )
                )
            else:
                _, uncut = asyncio.run(execute_round(cycle, 1, callbacks))
                assert uncut is None, f"{case}: round 1 ran inside its budget"
            paid.clear()
            parent = ArmKey(cycle.parent.id)
            cutting.append(True)
            cut_walk = execute_round(cycle, cut_round, callbacks)
            if panel_gate == "strict":
                with pytest.raises(StopLoop) as halted:
                    asyncio.run(cut_walk)
                assert halted.value.reason is StopReason.SPEND_BUDGET, (
                    f"{case}: the bound that cut it names the stop"
                )
                assert cycle.rounds[-1].round == cut_round - 1, (
                    f"{case}: an arm was crowned on a holed panel"
                )
                return
            closed, cut = asyncio.run(cut_walk)
            assert cut is StopReason.SPEND_BUDGET, f"{case}: the round names its cut"
            rows = {key: closed.rows(key) for key in closed.panel}
            assert sum(len(closed.rows(key)) for key in closed.arms) == (
                replayed + len(paid) + parent_cells
            ), f"{case}: a cell nobody elects on, or a parent read where the archive lacks it"
            assert (parent in closed.panel) is (cut_round == 2), (
                f"{case}: a retained parent is ONE arm, its re-read folded into its own cells"
            )
            folded: list[Any] = []
            monkeypatch.setattr(
                session.store.ruler, "fold", lambda _s, got, **_: folded.extend(got)
            )
            cycle.difficulty.fold(closed)
            parent_arm = closed.arms[parent]
            assert isinstance(parent_arm, Cut | Measured), f"{case}: the parent never concluded"
            observed = [cell for walked, cell, _ in folded if walked == parent_arm.sp_hash]
            assert len(observed) == len(set(observed)) == len(closed.rows(parent)), (
                f"{case}: the δ fit takes each of the parent's observations ONCE"
            )
            [short] = [key for key in rows if len(rows[key]) == 1]
            assert closed.folds[short].accuracy == 1.0, (
                f"{case}: the cut arm reads perfect on its one cell"
            )
            carried = [ind.id for ind in cycle.population]
            assert len(carried) == 3 and short not in carried, f"{case}: elected below the floor"
            full = next(key for key in list(rows)[3:] if key != short)
            assert full in carried, f"{case}: an offspring the budget paid for in full was dropped"
            assert cycle.rounds[-1].round == cut_round
            cycle.unwind_unclosed()
            assert [rr.round for rr in cycle.rounds] == [0] and cycle.selection.round == 0, (
                f"{case}: a stop between this election and its close leaves no result"
            )

    # Round 2's parent is a retained member: its re-read lands on cells its own walk holds.
    for cut_round, budget, replayed, parent_cells in ((1, 17, 0, 2), (2, 5, 12, 0)):
        for panel_gate in ("strict", "off"):
            cut_by_budget(cut_round, budget, replayed, parent_cells, panel_gate)

    # A survey round's K starts and its reference are arms, and its banked matrix is their cells.
    starts = {ArmKey(f"c{i}"): sheet(measurements([1.0, 0.0, float(i % 2)])) for i in range(3)} | {
        PARENT: sheet(measurements([0.0] * 3))
    }
    survey = round_result(1, candidates_scored=3, improved=False, cells=starts)
    assert {key: survey.rows(key) for key in survey.arms} == starts, (
        "a survey arm's rows are its cells, the reference's among them"
    )
    assert survey.results == starts[PARENT], "a held survey headlines its reference's cells"
    assert [c.key for c in InRunCells([survey]).sheet("c1", RoleScope.DECISION)] == [
        c.key for c in starts[ArmKey("c1")]
    ], "a decision reads a survey start off the same cells"

    # One round composes a peer's crossover and its state-reading selector with potter's race.
    name = "ga-composition"
    capo = yaml.safe_load((optimizers_root() / "capo" / "pipeline.yaml").read_text("utf-8"))
    potter = yaml.safe_load((optimizers_root() / "potter" / "pipeline.yaml").read_text("utf-8"))
    seed = ["capo_init"]
    walk = ["blocks", "mating", "capo_crossover", "pobb", "score", "population"]
    manifest_dir = tmp_path / name
    manifest_dir.mkdir()
    (manifest_dir / "resolved_schemas.json").write_text("{}", "utf-8")

    writes = count(1)

    def composed(walk: list[str], seed: list[str] = seed, unwalked: tuple[str, ...] = ()) -> None:
        # The demo pool's initializer is the shared pools package's, a third registrant beside the two.
        init = ["demo_pool"]
        nodes = {n: (capo["nodes"] | potter["nodes"])[n] for n in (*init, *seed, *walk, *unwalked)}
        manifest = manifest_dir / "pipeline.yaml"
        manifest.write_text(
            yaml.safe_dump(
                {
                    "name": name,
                    "version": "v0",
                    "available_models": capo["available_models"],
                    "nodes": nodes,
                    "pipelines": {"run_init": init, "default": walk, "initial_population": seed},
                    "initial_population_walks": "before_default",
                    "resolved_prompts": {
                        key: capo["resolved_prompts"][key]
                        for key in ("capo_init/1", "capo_crossover/1")
                    },
                }
            ),
            "utf-8",
        )
        # The manifest cache keys on mtime, which two writes inside one clock tick share.
        stamp = manifest.stat().st_mtime_ns + next(writes) * 1_000_000_000
        os.utime(manifest, ns=(stamp, stamp))

    class Composed(node_contract.OptimizerRuntime):
        prompt_sources = (paper_templates, capo_prompts)

        def arms(self, selected: Any) -> int:
            return 2

    Composed.name, Composed.manifest_dir = name, manifest_dir
    shipped, origins = optimizers.RUNTIMES.table, optimizers.RUNTIMES.origins
    monkeypatch.setitem(
        vars(optimizers.RUNTIMES), "_loaded", ({**shipped, name: Composed()}, origins)
    )
    open_rosters: dict[str, tuple[list[str], tuple[str, ...]]] = {
        "a race reading its μ off a selector the roster lacks": (
            ["blocks", "mating", "capo_crossover", "paired_t", "score", "theta_election"],
            (),
        ),
        "a controller firing layers that consult members the roster lacks": (
            [*walk, "escalation"],
            (),
        ),
        "a declared member no walk reaches, whose knobs a run would carry unread": (
            walk,
            ("theta_election",),
        ),
    }
    for case, (open_walk, unwalked) in open_rosters.items():
        composed(open_walk, unwalked=unwalked)
        with pytest.raises(PayloadInvalidError) as refused:
            resolve_optimizer(name, {}, gate="off")
        assert refused.value.code == "optimizer_roster_open", f"{case} reached round 1"
    composed(["adaptive_queue", *walk[1:]])
    queued = resolve_optimizer(
        name, {"adaptive_queue": ManifestNodeOverlay(config={"sp_budget_round": 7})}, gate="off"
    )
    assert queued.round_cells(40) == 7, (
        "potter's queue, the one potter member in a peer's roster, sized its panel off another's knobs"
    )
    composed(walk)
    replies = iter(["Merged.", "Other."])

    crossed: list[str] = []

    def crossover_says(body: dict[str, Any]) -> str:
        asked = _asked(body)
        if "Create overall 15 prompts" in asked:
            return json.dumps(["Solve it, A.", "Solve it, B."])
        crossed.append(asked)
        return f"<prompt>{next(replies)}</prompt>"

    composed_nodes = {
        "blocks": {"config": {"block_size": 4, "max_blocks": 3}},
        "capo_init": {"config": {"size": 2, "k_max": 0}},
        "mating": {"config": {"offspring": 2}},
    }
    root = tmp_path / "composition"
    with _peer_cycle(
        workspace(root),
        _PEER_HOP,
        monkeypatch,
        name,
        composed_nodes,
        lambda prompt, _s: "Merged." in prompt,
        backend_id="composition-e2e",
    ) as (cycle, callbacks):
        offline.llm.says = crossover_says
        session = cycle.session
        assert not cycle.population
        closed, cut = asyncio.run(execute_round(cycle, 1, callbacks))
    assert cut is None
    assert crossed and all("Solve it, A." in a and "Solve it, B." in a for a in crossed), (
        "a crossover saw one parent"
    )
    assert cycle.slices.get(CapoObjective).length_norm, (
        "CAPO's initial population banked no length norm for the objective its selector ranks on"
    )
    for panel_arm in closed.panel.values():
        lineage = panel_arm.individual.lineage
        assert [(made.node, made.mode) for made in lineage.variations] == [
            (f"{name}:mating", "deterministic"),
            (f"{name}:capo_crossover", "llm"),
        ], "an arm's lineage names the peer's mating and crossover"
        assert len(set(lineage.parent_ids)) == 2, "a crossover child has two parents"
    assert closed.election is not None
    winner = closed.election.selected
    assert winner is not None
    assert "Merged." in closed.arms[winner].individual.prompts["solve"].fields["instruction"], (
        "CAPO's selector elected it off potter's race"
    )
    assert cycle.population[0].id == winner, (
        "the individual the next round mutates from does not lead the population the run carries"
    )

    # A cut θ round reads its cold parent on the cells the archive holds GRADED, guard and election alike.
    composed([*walk[:-1], "theta_election"])
    replies = iter(["Merged.", "Other."])
    theta_book = spend_book(None, usd_reserve=None)
    # Empty until round 1 walks, so the origin's cells pay toward no cut.
    theta_walking: list[bool] = []
    theta_paid: list[int] = []

    def paid_then_cut(_prompt: str, sample: Sample) -> bool:
        if theta_walking:
            theta_paid.append(sample.id)
            if len(theta_paid) >= 2:
                theta_book.hold_of = Hold(Budget(usd=0.0), theta_book.reserve)
        return True

    async def unpriced(sample: Sample, _session: Any, *, pipeline_params: Any) -> MeasuredCell:
        # No `target_prompt_chars`, so the composite cannot grade the row it files.
        return MeasuredCell.from_wire(
            {
                "sample_key": sample.key,
                "query": sample.query,
                "ground_truth": sample.ground_truth,
                "predicted": sample.ground_truth,
                "error": None,
                "cached": False,
                "pipeline_data": {"total_time": 0.1},
            }
        )

    with _peer_cycle(
        workspace(tmp_path / "theta-cut"),
        _PEER_HOP,
        monkeypatch,
        name,
        composed_nodes,
        paid_then_cut,
        backend_id="theta-cut-e2e",
        control=RunControl(book=theta_book),
        panel_gate="off",
    ) as (cycle, callbacks):
        offline.llm.says = crossover_says
        session = cycle.session
        search = list(session.state.scoring.require_partition().search)
        with monkeypatch.context() as patched:
            patched.setattr(query_loop, "measure_sample", unpriced)
            asyncio.run(
                score_search_point(
                    cycle.parent_point, search[1:2], session, label="origin", measured=None
                )
            )
        asyncio.run(
            score_search_point(
                cycle.parent_point, search[:1], session, label="origin", measured=None
            )
        )
        theta_walking.append(True)
        with pytest.raises(StopLoop) as unwound:
            asyncio.run(execute_round(cycle, 1, callbacks))
    assert unwound.value.reason is StopReason.SPEND_BUDGET
    assert cycle.rounds[-1].round == 0, "a cut round stood with no arm its selector could read"

    # The bench grades the pick the optimizer declared, over a higher-composite round.
    def bench_says(body: dict[str, Any]) -> str:
        if "Create overall 15 prompts" in _asked(body):
            return json.dumps(["Solve it GOOD.", "Solve it OK."])
        return "<prompt>Solve it BAD.</prompt>"

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
        _PEER_HOP,
        monkeypatch,
        "capo",
        bench_nodes,
        lambda prompt, s: "GOOD" in prompt or ("OK" in prompt and s.id % 2 == 0),
        bench=4,
        backend_id="capo-e2e",
    ) as (cycle, callbacks):
        offline.llm.says = bench_says
        session = cycle.session
        picked = asyncio.run(execute_round(cycle, 1, callbacks))[0]
        head, fold = picked.head, picked.fold
        assert head is not None and fold is not None and fold.accuracy is not None
        assert picked.election is not None
        crowned = picked.election.selected
        assert crowned is not None
        [bar] = [picked.rows(key) for key in picked.arms if key not in picked.panel]
        linked = cycle.difficulty.ruler_of(picked.election.ruler)
        assert linked is not None and set(linked.delta) >= {
            cell.key for cell in picked.rows(crowned).scoreable
        }, "the round's own cells join the δ fit before its election, so θ reads every one"
        read = candidate_abilities({crowned: picked.rows(crowned)}, bar, linked)
        fit = picked.fit(crowned)
        assert fit is not None and (fit.theta, fit.theta_se) == (
            read.theta[crowned],
            read.theta_se[crowned],
        ), "an arm carries the θ the election takes of its rows, on the scale it stamped"
        assert target_tripped(cycle, fold.accuracy) is StopReason.TARGET_HIT
        origin = cycle.origin_round.origin.individual
        origin_pass = asyncio.run(
            score_on_bench(
                session,
                cycle.points.measured(cycle.measured(origin.id)),
                individual_id=origin.id,
                subject="origin",
                label="C0",
                round_num=0,
                cb=callbacks,
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
                    session, cycle.rounds, points=cycle.points, banked=banked, cb=callbacks
                )
            )

        # The one stop reaches a pass in flight: the cycle reads as scoring, the pause is taken at
        # the next cell, and the pass banks as stopped on the cells it walked.
        stores = session.store
        stores.campaigns.create_campaign(minted_campaign(_PEER_HOP))
        stores.campaigns.mint_cycle(_PEER_HOP)
        cycle_dir = stores.campaigns.cycle_dir(_PEER_HOP)
        loop_control, session.state.control = (
            session.state.control,
            RunControl(enclosing=(cycle_dir,)),
        )
        pause = PauseCyclePayload(
            campaign_id=_PEER_HOP.campaign_id, cycle_id=_PEER_HOP.cycle_id, reason="stop"
        )
        measure = vars(query_loop)["measure_sample"]
        walked: list[int] = []
        phases: list[RunPhase] = []
        acks: list[str] = []

        async def _stopped_mid_pass(
            sample: Sample, at: Any, *, pipeline_params: Any
        ) -> MeasuredCell:
            walked.append(sample.id)
            if len(walked) == 2:
                phases.append(derive_run_state(cycle_dir).run_phase)
                acks.append((await CommandDispatcher(stores).dispatch(CommandCall(pause))).ack)
                phases.append(derive_run_state(cycle_dir).run_phase)
            cell: MeasuredCell = await measure(sample, at, pipeline_params=pipeline_params)
            return cell

        async def _bench_pick() -> BenchPass:
            async with paid_pass(stores=stores, verb="bench", hop=_PEER_HOP, declared=Budget()):
                return await score_on_bench(
                    session,
                    cycle.points.measured(head),
                    individual_id=head.individual.id,
                    subject="selected",
                    label=head.label,
                    round_num=1,
                    cb=callbacks,
                )

        with monkeypatch.context() as patched:
            patched.setattr(query_loop, "measure_sample", _stopped_mid_pass)
            with pytest.raises(PassStoppedError):
                asyncio.run(_bench_pick())
        session.state.control = loop_control
        assert (phases, acks) == ([RunPhase.SCORING, RunPhase.PAUSED], ["accepted"]), (
            "a pause was refused by a pass in flight, or the cycle never read as scoring"
        )
        [*_, stopped_view] = [
            record.view
            for _, record in callbacks.ledger.iter()
            if isinstance(record, PhaseRecord) and record.event == "graded"
        ]
        assert isinstance(stopped_view, BenchGradedView)
        paused_pass = stopped_view.bench_pass
        assert stopped_view.state is ReadingState.PASS_STOPPED and paused_pass.stop is not None
        assert paused_pass.stop.cause is StopReason.PAUSED and 0 < len(paused_pass.cells) < 4, (
            "a paused pass banked no record of the cells it walked, or never stopped"
        )
        assert standing_controls(cycle_dir).pause is None and not cycle_held(cycle_dir), (
            "the pass left its pause unapplied, or kept the cycle after it stopped"
        )
        with pytest.raises(PayloadInvalidError):
            asyncio.run(
                score_search_point(
                    cycle.parent_point,
                    search * (MAX_PASS_ROWS // len(search) + 1),
                    session,
                    label="origin",
                    measured=None,
                )
            )

        passes = graded()
        [taken] = passes.selections.values()
        assert (taken.candidate_id, taken.round, taken.stop) == (head.individual.id, 1, None), (
            "the bench grades the pick the optimizer declared"
        )
        assert {key for key, _, _ in paused_pass.cells} <= {
            key for key, _, replayed in taken.cells if replayed
        }, "the pass after a stop bought again a cell the stopped pass had banked"
        assert graded(taken).selections[taken.candidate_id] is taken, "a held pass was sent again"
        left = taken.model_copy(update={"sp_hash": "a pick the line left"})
        resent = graded(left).selections[taken.candidate_id]
        assert [c[:2] for c in resent.cells] == [c[:2] for c in taken.cells]
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
            session.state.scoring.require_scorer(),
            line=BenchLine(
                hop=session.hop,
                on_line=True,
                inner=False,
                held_by=None,
                trigger="at_end",
                held_out=4,
                instrument_id="capo-e2e",
                run=LineRun(
                    selecting=False,
                    ending=StopReason.ROUNDS_BUDGET,
                    selection=ArmName(key=ArmKey(taken.candidate_id), round=1, label=head.label),
                    rounds_closed=1,
                ),
                spend=None,
            ),
        )
        assert bench.selected is not None and bench.origin is not None
        selected_accuracy, origin_accuracy = bench.selected.accuracy, bench.origin.accuracy
        assert selected_accuracy is not None and origin_accuracy is not None
        assert (selected_accuracy.value, origin_accuracy.value) == (1.0, 0.0)
        session.state.clock = LaunchClock.now()
        result = build_cycle_result(
            cycle,
            session,
            stop_reason=StopReason.ROUNDS_BUDGET,
            cycle_error=None,
            finished_at="",
            spend=None,
            bench=bench,
            langfuse_trace_id=None,
        )
        assert result.result_round == bench.selected.round == 1, "the result names the round graded"

    # A bench line reads the pick it holds, or says why it reads nothing.
    line_stores = workspace(tmp_path / "line")
    pick = prompted(instruction="the declared pick")
    spent = PassStop(cause=StopReason.SPEND_BUDGET, warning=None)
    forbidden = PassStop(cause=WalkEnd.CLIENT_ERROR, warning="HTTP 403 :: model is not allowed")
    line_scorer = compile_scorer(_H2H_FORMULA, verifier_graded=False)
    line_cases: tuple[
        tuple[
            dict[str, Any] | None,
            dict[str, Any],
            ReadingState,
            bool,
            float | None,
            tuple[float | None, list[float | None]],
            str | None,
        ],
        ...,
    ] = (
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
            {"origin_stop": forbidden},
            {},
            ReadingState.PASS_STOPPED,
            True,
            None,
            (None, [1.0]),
            "origin",
        ),
    )
    for banked, run, state, can_grade, lift, series, stopped in line_cases:
        case = f"banked={banked} run={run}"
        line_passes: BenchPasses | None = None
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
            line_passes = BenchPasses(
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
            inner=False,
            held_by=run.get("held_by"),
            trigger=run.get("trigger", "at_end"),
            held_out=4,
            instrument_id="i",
            run=LineRun(
                selecting=run.get("selecting", False),
                ending=run.get("ending", StopReason.ROUNDS_BUDGET),
                selection=ArmName(key=ArmKey(selection), round=1, label="C1.1"),
                rounds_closed=1,
            ),
            spend=None,
        )
        line_bench = read_bench(line_stores, line_passes, line_scorer, line=line)
        assert (line_bench.status.state, line_bench.status.can_grade) == (state, can_grade), case
        headline = line_bench.vs_origin.headline
        assert (None if headline is None else headline.estimate.value) == lift, case
        assert line_bench.status.subject == stopped or not stopped, f"{case}: names who stopped"
        if stopped == "origin":
            assert "model is not allowed" in line_bench.status.sentence, f"{case}: says why"
        pick_arm = arm_state("C1.1", slot=ArmSlot(round=1, idx=0, total=1), individual=pick)
        on_line = round_result(1, arms={ArmKey(pick.id): pick_arm}, selected=pick.id, cells={})
        assert bench_series(line_stores, line_passes, line_scorer, rounds=[on_line]) == series, case


# 4. An authored round — the operator's arm in the seat a proposer fills


def test_an_authored_round_measures_the_operators_arm_and_asks_no_proposer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    authored = workspace(tmp_path / "authored")
    hop, cycle_dir = walking_cycle(authored)
    measured: list[str] = []

    def operator_solves(prompt: str, _sample: Sample) -> bool:
        measured.append(prompt)
        return "Operator" in prompt

    def submit(key: str) -> Any:
        payload = SubmitArmsPayload(
            campaign_id=hop.campaign_id,
            cycle_id=hop.cycle_id,
            arms=[
                SubmittedArm(
                    prompt_fields={"solve": {"instruction": "Operator wrote this."}},
                    changes_description="the operator's own wording",
                )
            ],
        )
        return CommandDispatcher(authored).dispatch(CommandCall(payload, key))

    # Only a roster whose every read has a walked writer runs authored: the seat writes no state.
    for optimizer, closes in (("potter", True), ("capo", False), ("gepa", False), ("levi", True)):
        try:
            resolve_optimizer(optimizer, {}, gate="author")
            refused = None
        except PayloadInvalidError as exc:
            refused = exc.code
        assert (refused is None) is closes and refused in (None, "optimizer_roster_open"), (
            f"{optimizer} under `arm_gate: author`: a gate reading what no walked member writes "
            "reached round 1"
        )
    # What earlier runs earned on this answer space reaches the origin's critique, not round 1 alone.
    monkeypatch.setattr(
        node_context, "earned_blocks_for", lambda *_a: {"persona": ("Answer tersely.",)}
    )
    try:
        with _peer_cycle(
            authored,
            hop,
            monkeypatch,
            "potter",
            {},
            operator_solves,
            control=RunControl(cycle_dir=cycle_dir),
            arm_gate="author",
        ) as (cycle, callbacks):
            offline.llm.says = lambda _body: "ok"
            at_origin = RoundContext(cycle=cycle, round_num=0, callbacks=callbacks)
            origin_read = build_bundle(
                NodeContext(at_origin, optimizers.NODE_MEMBERS.get("l1_critique")), Ladder()
            )
            assert [b.text for b in origin_read.block_window().get("persona", ())[:1]] == [
                "Answer tersely."
            ], "round 0's critique composed before the block pool was seeded"
            measured.clear()

            async def at_the_hold() -> RoundResult:
                with pytest.raises(ConflictError):
                    await submit("arms-before-the-hold")

                async def answer() -> None:
                    while derive_run_state(cycle_dir).gate != "arms":
                        await asyncio.sleep(0)
                    await submit("arms-at-the-hold")

                (held_round, held_cut), _ = await asyncio.gather(
                    execute_round(cycle, 1, callbacks), answer()
                )
                assert held_cut is None
                return held_round

            # The hold polls on the loop's clock, which jumps past each wait.
            offline.llm.sent.clear()
            closed = jumping_clock(at_the_hold())
            # The critique still reads the round; every other call it made filled the seat.
            critique_node = optimizers.NODE_MEMBERS.get("l1_critique")
            assert isinstance(critique_node, node_contract.LlmNode)
            assert critique_node.response_model is not None
            critique = critique_node.response_model.__name__
            asked = [
                body.get("response_format", {}).get("json_schema", {}).get("name")
                for body in offline.llm.sent
            ]
            assert all(name == critique for name in asked), (
                f"an authored round called a model beside its critique: {asked}"
            )
            # A resume re-seats the pool its banked round names, never one a later write left.
            banked = closed.model_copy(update={"optimizer_state": cycle.close_state(1)})
            set_cycle(cycle, slices=[BlockPool(entries=())])
            cycle.replay_priors([banked])
            resumed = build_bundle(
                NodeContext(
                    dataclasses.replace(at_origin, round_num=2),
                    optimizers.NODE_MEMBERS.get("l1_generate"),
                ),
                Ladder(),
            )
            assert [b.text for b in resumed.block_window().get("persona", ())[:1]] == [
                "Answer tersely."
            ], "a resumed run handed its proposer a pool its banked round does not name"
            l3: Any = optimizers.NODE_MEMBERS.get("l3_plan")
            replanning = NodeContext(at_origin, l3)
            set_cycle(cycle, slices=[L3Memory(plan="attack the default label")])
            l3.land(replanning, l3.parse(replanning, L3PlanOutput(plan="")))
            assert cycle.slices.get(L3Memory).plan == "", (
                "an empty replan kept the plan it replaced: every later prompt read a dropped strategy"
            )
    finally:
        release_cycle(cycle_dir)
    [arm] = closed.panel.values()
    assert arm.individual.prompts["solve"].fields["instruction"] == "Operator wrote this.", (
        "the operator's arm ran"
    )
    assert any("Operator wrote this." in prompt for prompt in measured), "it was measured"
    assert [made.mode for made in arm.individual.lineage.variations] == ["operator"]


# 5. Elimination — who is cut, and when


def test_an_arm_is_cut_only_on_evidence_that_it_trails(built_stores: Stores) -> None:
    # ε ramps in from its floor; the depths come from `fit_theta`'s dispersion rule.
    cfg = pobb_knobs(epsilon=0.30, epsilon_floor=0.15)
    parent = ArmKey("parent")
    graded = PoBBCheck(cfg, n_min=6, n_samples=28, ruler=None, parent=parent)
    for n, epsilon in ((6, 0.15), (9, 0.225), *((n, 0.30) for n in range(12, 29))):
        assert graded.epsilon_at(n) == pytest.approx(epsilon), f"ε at {n} cells"
    flat = PoBBCheck(
        pobb_knobs(epsilon=0.30, epsilon_floor=0.30),
        n_min=6,
        n_samples=28,
        ruler=None,
        parent=parent,
    )
    assert flat.epsilon_at(6) == pytest.approx(0.30), "a floor at ε does not ramp"
    shipped = PoBBCheck(pobb_knobs(), n_min=6, n_samples=28, ruler=None, parent=parent)
    assert shipped.epsilon_at(shipped.n_min) == pytest.approx(shipped.epsilon), (
        "the manifest sits floor and ε on the same value, so an untouched campaign never grades"
    )

    def behind_perfect_prior(n: int, misses: int, *, parent_cells: int = 28) -> Any:
        check = PoBBCheck(cfg, n_min=6, n_samples=28, ruler=None, parent=parent)
        check.register_completed(sheet(measurements([1.0] * 28)), key=ArmKey("winner"))
        check.register_completed(sheet(measurements([1.0] * parent_cells)), key=parent)
        check.set_current(ArmKey("arm"))
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
    assert behind_perfect_prior(9, 2, parent_cells=5) is None, (
        "cut under the coverage floor: cold, the election reads θ on the cells the parent answered"
    )
    shallow = behind_perfect_prior(12, 2, parent_cells=9)
    assert shallow is not None and shallow.check_result["epsilon"] == pytest.approx(0.225), (
        "the ε ramp counted rows walked, not the cells θ is read on"
    )
    late = behind_perfect_prior(26, 4)
    assert late is not None and late.outcome == "eliminated"
    assert late.check_result["epsilon"] == pytest.approx(0.30)
    unpaired = PoBBCheck(pobb_knobs(epsilon=0.05), n_min=4, n_samples=20, ruler=None, parent=parent)
    unpaired.register_completed(
        sheet(measurements([1.0] * 8, sample_ids=list(range(8)))), key=ArmKey("lucky_leader")
    )
    unpaired.register_completed(sheet(measurements([0.0] * 20)), key=parent)
    unpaired.set_current(ArmKey("challenger"))
    challenger = sheet(measurements([0.0] * 5, sample_ids=[9, 12, 13, 14, 8]))
    assert unpaired.check(challenger.cells) is None, "an arm was cut on cells its rival never sat"
    longer, terse_parent = _charged_by_length()
    charged = PoBBCheck(cfg, n_min=6, n_samples=34, ruler=None, parent=parent)
    charged.register_completed(terse_parent, key=parent)
    standings: list[node_contract.RaceSnapshot] = []
    charged.set_current(ArmKey("l"), on_snapshot=standings.append)
    charged.check(longer.cells)
    assert standings[-1].p_best > 0.5, "a cost term's tie-breaks pinned p_best at a coin"

    # The collapse gate reads the answer, not the labels: a verifier proves an answer wrong by an
    # unsolved cell, and alike-and-all-solved is NOT collapsed.
    def answered(fitness: list[float], **over: Any) -> list[GradedCell]:
        return [
            GradedCell.from_wire(
                {
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
        return PoBBCheck(pobb_knobs(), n_min=6, n_samples=28, ruler=None, parent=parent).check(rows)

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
                "sample_key": str(i),
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
    def uncharged(*values: float) -> list[PairedGrade]:
        return [PairedGrade(v, v) for v in values]

    sids = [str(s) for s in range(12)]
    strong, weak = uncharged(*[0.66] * 12), uncharged(*[0.30] * 12)
    prior = ArmKey("prior")
    p_strong, _ = elimination_p_best(strong, {prior: weak}, sids, None)
    assert p_strong > 0.9, f"graded gate failed to discriminate: {p_strong}"
    assert elimination_p_best(weak, {prior: strong}, sids, None)[0] < 0.1
    p_tie, _ = elimination_p_best(weak, {prior: list(weak)}, sids, None)
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
    nothing_solved = uncharged(*[0.0] * 6)
    p_thin, _ = elimination_p_best(
        nothing_solved, {prior: uncharged(0.0, 0.0, 0.0, 1.0, 0.0, 0.0)}, prefix, warm
    )
    assert p_thin == pytest.approx(0.25), f"one adverse cell may not reach past 0.25: {p_thin}"
    assert p_thin > epsilon, "a one-cell verdict must not be cuttable"
    p_wide, _ = elimination_p_best(
        nothing_solved, {prior: uncharged(1.0, 0.0, 1.0, 1.0, 0.0, 0.0)}, prefix, warm
    )
    assert p_wide == pytest.approx(0.0625), f"three adverse cells support a cut: {p_wide}"
    assert p_wide < epsilon, "the width is there — ε decides"

    # A fatal row ends a candidate on one sighting, and an advisory never does.
    def warned(kind: str | None) -> dict[str, Any]:
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
        return round_result(1, candidates_scored=1, cells={ArmKey("c0"): sheet(rows)})

    assert repair_cut([bounded(ErrorCategory.HALTED)]).rounds == [], (
        "a cell a declared bound CUT is settled: the same bound cuts the next attempt"
    )
    assert repair_cut([bounded(ErrorCategory.UNSCOREABLE)]).rounds == [1], (
        "a cell that ran to its own end can answer differently on a re-measure"
    )

    # GEPA's gate tests each arm against the minibatch scores of ITS parent, never one round bar.
    bars = MinibatchBars(
        bars={
            "weak_parent_child": MinibatchBar(parent_id="p1", objectives={"0": 0.1, "1": 0.1}),
            "strong_parent_child": MinibatchBar(parent_id="p2", objectives={"0": 0.9, "1": 0.9}),
        }
    )
    weak_child, strong_child, unbarred = (ArmKey(cid) for cid in (*bars.bars, "unbarred_child"))
    gated = seat(built_stores, optimization={"optimizer": "gepa"})
    gated.cycle.emit(bars, gated.round_num)
    gate = MinibatchGate()
    minibatch = samples(2)

    def no_catch_up(*_: Any) -> Any:
        raise AssertionError("the gate caught up a cell")

    race = gate.race(
        NodeContext(gated, gate),
        Panel(cells=minibatch, order=minibatch, block_size=2),
        Proposals(proposals=[]),
        no_catch_up,
    )
    level = [measurement(i, 1.0, objective=0.5) for i in range(2)]
    stops = race.close({weak_child: level, strong_child: level})
    assert (
        list(stops) == [strong_child] and stops[strong_child].check_result["parent_id"] == "p2"
    ), "one arm's parent bar decided another arm's gate"
    with pytest.raises(KeyError):
        race.close({unbarred: level})


# 6. Escalation and stop — live, and folded back on resume


def _line_read(
    round_num: int,
    improved: bool,
    *picks: tuple[str, Sequence[float | None]],
    c0: Sequence[float | None],
    ids: dict[str, str] | None = None,
    triggered: Sequence[tuple[str, Sequence[float | None]]] = (),
) -> OverlapReading:
    bank = samples(len(c0), "t")
    hop = CycleHop(campaign_id="camp", cycle_id="cyc")

    def member(label: str, grades: Sequence[float | None]) -> MemberRows:
        rnd = int(label[1:].split(".")[0])
        arm = ArmName(key=ArmKey((ids or {}).get(label, label.lower())), round=rnd, label=label)
        return _pair_member(
            arm.key,
            [
                measurement(i, g)
                if g is not None
                else measurement(i, None, error_category="SERVER")
                for i, g in enumerate(grades)
            ],
            bank,
            scope=RoleScope.REPORT,
            address=MemberAddress(path=(hop,), individual_id=arm.key, arm=arm, pass_role=None),
        )

    def read(
        label: str, grades: Sequence[float | None], under: tuple[str, Sequence[float | None]]
    ) -> PairedReading:
        return _read_pair(
            member(*under),
            member(label, grades),
            bank,
            scope=RoleScope.REPORT,
            measurands=grade_measurands("s"),
            spec=ROUND_LIFT_SPEC,
        )

    origin = ("C0", c0)
    readings = [read(label, grades, origin) for label, grades in picks]
    return OverlapReading.of(
        round_num,
        improved,
        positions=range(len(c0)),
        lead=readings[-1],
        earlier=readings[:-1],
        triggered=[
            TriggeredReading(trigger=PanelTrigger.N_MIN, reading=read(label, grades, origin))
            for label, grades in triggered
        ],
        vs_parent=read(*picks[-1], picks[-2]) if len(picks) >= 2 else None,
    )


# Eight panel cells C0 misses and one it takes: a pick clean on all nine clears zero over it.
_C0_PANEL: Sequence[float | None] = [0.0] * 8 + [1.0]
_CLEAN_PANEL: Sequence[float | None] = [1.0] * 9
_TIED_PANEL: Sequence[float | None] = [1.0, 0.0] + [0.0] * 6 + [1.0]


def test_the_ladder_climbs_and_the_run_stops_only_on_evidence_it_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A θ-stall verdict must clear its own error: θ advances once for real (+0.467), then by noise.
    stall_rounds = [
        (0.4853, -0.1296, 0.216),
        (0.6248, 0.3376, 0.202),
        (0.6248, 0.3498, 0.198),
        (0.6248, 0.3498, 0.198),
    ]

    def high_water(comp: float | None, thetas: list[float], se: float | None) -> HighWater:
        # Round i read thetas[i] on one series; the peak is the FIRST round at the max.
        origin = thetas[0] if thetas else None
        rest = thetas[1:]
        return HighWater(
            composite_fitness=comp,
            series=level_series(origin, rest, origin_se=se, ses=[se] * len(rest)),
            theta_round=max(range(len(thetas)), key=thetas.__getitem__) if thetas else None,
        )

    def climb(*, with_se: bool) -> list[str]:
        ladder, actions = Ladder(), []
        for upto, (comp, _, se) in enumerate(stall_rounds):
            asked = ask_l2_escalation(
                ladder,
                best=high_water(
                    comp, [t for _, t, _ in stall_rounds[: upto + 1]], se if with_se else None
                ),
                escalation_ladder=EscalationLadder.FULL,
                l2_patience=2,
                l3_patience=1,
            )
            actions.append(str(asked.next_action))
            if asked.next_action != NextAction.FIRE_L2:
                break
            ladder = l2_fired(ladder, asked.l2)
        return actions

    assert climb(with_se=True) == ["fire_l2", "fire_l2", "fire_l2", "fire_l3"], (
        "a θ step inside its own SE is a stall"
    )
    assert NextAction.FIRE_L3 not in climb(with_se=False)
    # The SE bar applies on the θ scale only: a composite-scale verdict has no error term.
    series = level_series(0.34, [0.35, 0.60], ses=[0.20, 0.20])

    def l2_read(
        comp: float, entry_comp: float, rnd: int | None, entry_rnd: int | None
    ) -> tuple[int, str | None]:
        entered = LayerCounters(
            best_composite_fitness_at_entry=entry_comp, theta_peak_round_at_entry=entry_rnd
        )
        asked = ask_l2_escalation(
            Ladder(l2=entered),
            best=HighWater(composite_fitness=comp, series=series, theta_round=rnd),
            escalation_ladder=EscalationLadder.FULL,
            l2_patience=9,
            l3_patience=None,
        )
        return asked.l2.stall_count, asked.l2.comparator

    assert l2_read(0.0, 0.0, 1, 0) == (1, "theta"), "a θ step inside its SE cleared the stall"
    assert l2_read(0.0, 0.0, 2, 0) == (0, "theta"), "a θ step past its SE left the stall"
    assert l2_read(0.7, 0.6, None, None) == (0, "composite"), (
        "with no θ on either side the composite lift is the verdict"
    )

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
    # `--diag` asks the climb with no `observe` before it: on the L1 arm that composes no layer.
    diag_asks = {
        ask_l2_escalation(
            ladder,
            best=high_water(fitness, [], None),
            escalation_ladder=EscalationLadder.L1,
            l2_patience=l2_patience,
            l3_patience=l3_patience,
        ).next_action
        for ladder in (Ladder(), l2_fired(Ladder(), LayerReading(5, 0.5, None, None)))
        for fitness in (None, 0.5)
        for l2_patience in (0, 2)
        for l3_patience in (None, 0, 1)
    }
    assert diag_asks == {NextAction.CONTINUE}, "--diag fired a layer the L1 arm cannot reach"

    # A heal fire spends no L3 patience.
    healed = Ladder()
    seen: list[float] = []

    def stretch(theta: float, *, heal_first: bool) -> LadderAsk:
        nonlocal healed
        asks: list[LadderAsk] = []
        while not asks or asks[-1].next_action is NextAction.FIRE_L2:
            seen.append(theta)
            asked = ask_l2_escalation(
                healed,
                best=high_water(0.5, seen, 0.2),
                escalation_ladder=EscalationLadder.FULL,
                l2_patience=2,
                l3_patience=1,
            )
            asks.append(asked)
            if asked.next_action is NextAction.FIRE_L2:
                healed = l2_fired(healed, asked.l2)
                if heal_first and len(asks) == 1:
                    healed = l3_fired(healed, None)
        return asks[-1]

    gate = stretch(0.3, heal_first=True)
    assert gate.next_action is NextAction.FIRE_L3, (
        "the heal wiped L2; the gate reached is L3's FIRST"
    )
    healed = l3_fired(healed, gate.l3)
    gate = stretch(0.6, heal_first=True)
    assert gate.next_action is NextAction.FIRE_L3, "a later heal must not re-base the comparison"
    healed = l3_fired(healed, gate.l3)
    assert stretch(0.6, heal_first=False).next_action is NextAction.STOP_L3_PATIENCE, (
        "the patience a heal did not spend still binds a flat run"
    )

    # A landed fire moves the ladder, and an unlanded one moves nothing.
    landed = Ladder()
    trace: list[tuple[int, int, float | None, int, int, float | None, int]] = []

    def snapshot() -> None:
        held = landed
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
        return ask_l2_escalation(
            landed,
            best=high_water(fitness, [], None),
            escalation_ladder=EscalationLadder.FULL,
            l2_patience=3,
            l3_patience=2,
        )

    landed = l2_fired(landed, ask_at(0.60).l2)
    snapshot()
    landed = l2_fired(
        landed, ask_at(0.60).l2
    )  # an unimproved ask reads a stall the landing commits
    snapshot()
    unlanded = ask_at(0.60)
    assert unlanded.l2.stall_count == 2, "the ask read the stall"
    assert as_read_by(landed, unlanded).l2.stall_count == 2, (
        "the prompt that fire composed still reported the verdict that asked for it"
    )
    snapshot()
    landed = l3_fired(landed, None)  # a heal: it bumps L3, the patience ratchet stays unset
    snapshot()
    landed = l3_fired(landed, ask_at(0.75).l3)
    snapshot()
    assert trace == [
        (1, 0, 0.60, 0, 0, None, 0),
        (2, 1, 0.60, 0, 0, None, 0),
        (2, 1, 0.60, 0, 0, None, 0),
        (0, 0, None, 1, 0, None, 0),
        (0, 0, None, 2, 0, 0.75, 0),
    ], "an unparseable fire adopted something, or L3 firing kept L2's progress"

    # The L1 stall and the lives bank read the same rounds.
    def close(rnd: int, *, improved: bool, electable: int = 2) -> RoundResult:
        return round_result(rnd, improved=improved, voted={"electable": electable}, cells={})

    sequence = [(True, 2), (True, 2), (True, 2), (True, 2), (False, 0), (False, 2), (False, 2)]
    closes = [close(i, improved=up, electable=n) for i, (up, n) in enumerate(sequence, start=1)]

    def observed(advances: list[RoundAdvance]) -> Ladder:
        ladder = Ladder()
        for advance in advances:
            ladder, _ = observe(
                ladder, advance=advance, l1_patience=99, escalation_ladder=EscalationLadder.FULL
            )
        return ladder

    assert observed([c.overlap.advance for c in closes]).l1_stall_count == 3, (
        "three trailing non-improving rounds, one uncompared — all three advance the stall"
    )
    origin_close = close(0, improved=False, electable=0)
    ledger = [origin_close, origin_close, *closes]
    assert [stalls_left(ledger[: 2 + n], (2, 4)) for n in range(8)] == [2, 3, 4, 4, 4, 4, 3, 2], (
        "the origin closes TWICE and moves no bank; a round nothing reached costs no life"
    )
    drained = [close(i, improved=False) for i in (8, 9)]
    assert stalls_left([*ledger, *drained], (2, 4)) == 0
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
    watched = _line_read(
        2, True, ("C2.1", _TIED_PANEL), c0=_C0_PANEL, triggered=[("C2.2", _CLEAN_PANEL)]
    )
    assert watched.advance is RoundAdvance.NOT_SEPARATED, "a triggered arm is read, never adopted"
    assert line_by_individual(watched)["c2.2"].rate == 1.0, "and served beside the line"
    assert observed([line.advance for line in lines]).l1_stall_count == 2, (
        "a round that crowned a winner and resolved NOTHING advances L1 patience"
    )

    # A reading that failed moves the stall clock neither way.
    won = lines[0]
    lead = won.lead
    stopped = OverlapReading.of(
        3,
        True,
        positions=won.positions,
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
        triggered=(),
        vs_parent=None,
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
    assert observed([rr.overlap.advance for rr in rounds[1:]]).l1_stall_count == 1, (
        "the live stall reads what the standing reads"
    )
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
    told = _vs_parent(whole.vs_parent) or ""
    assert told.startswith("  C2.1 vs its parent C1.1") and told.endswith("NOT separated"), (
        "the generator was told a pick's move over C0, or five flips read as separated"
    )
    assert _vs_parent(lines[0].vs_parent) is None, "a first pick's parent is C0, read once"
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
        _PEER_HOP,
        monkeypatch,
        "capo",
        {},
        lambda _p, _s: True,
        lives={"start": 2, "cap": 4},
        convergence_patience=3,
    ) as (cycle, _):
        lives = cycle.config.optimization.lives
        assert lives is not None
        origin_key = ArmKey(cycle.parent.id)
        origin_arm = arm_state("C0", slot=ArmSlot(round=0, idx=0, total=1), individual=cycle.parent)
        origin = round_result(0, arms={origin_key: origin_arm}, selected=origin_key, cells={})

        def stop_after(*closes: tuple[bool, int]) -> tuple[int | None, StopReason | None]:
            line = [
                origin,
                *(
                    round_result(n, improved=improved, voted={"electable": electable})
                    for n, (improved, electable) in enumerate(closes, 1)
                ),
            ]
            set_cycle(cycle, line=line)
            standing = RunStanding.after(line, lives=lives.bank)
            return stalls_left(line, lives.bank), standing_tripped(cycle, standing)

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
            closing = round_result(1, improved=True, overlap=readings[advance])
            folds = {key: score_summary(composite_fitness=objective) for key in closing.folds}
            set_cycle(
                cycle,
                line=[round_result(0, improved=False), closing.model_copy(update={"folds": folds})],
            )
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

    # Folded back on resume: a layer fires AFTER the round it reads has closed, so what it
    # authors is on no close.
    cycle_dir = CycleDir(tmp_path / "fired")
    fired_ledger = CycleEventLog.open(cycle_dir)
    # Appended here, as the store appends a restate.
    carried = resolve_optimizer("potter", {}, gate="off").slices
    live = node_contract.SliceMap(carried)
    closed = round_result(1, optimizer_state=optimizer_state(), cells={})
    line = [round_result(0, optimizer_state=optimizer_state(), cells={}), closed]
    closing = seat(workspace(tmp_path / "fired_ws")).cycle
    for n in (1, 2):
        close_round(fired_ledger, line[:n], cycle=closing)

    def restate() -> None:
        state = closed.closed_state.holding(
            live.get(L2Memory), live.get(L3Memory), live.get(Ladder)
        )
        standing = scan_standing_rounds(ledger_chain(cycle_dir, moment=None)).rounds
        fired_ledger.append(standing[1].close.restated(state))

    def fired(record: Callable[[Ladder], Ladder]) -> None:
        live.put(record(live.get(Ladder)))

    floor = NODE_LAYOUTS["l1_generate"].floor
    moved = coerce_l1_layout({"persona": ["critique"]}, base=floor)
    assert moved is not None and moved != floor
    handed = live.get(L2Memory)
    live.put(
        handed.steered_onto(
            "l1_generate", {"layout": moved.model_dump(mode="json"), "n_variants": 5}
        )
    )
    assert handed == L2Memory() and live.get(L2Memory) != handed, (
        "a fire answers the NEXT memory: the one it was handed is what the closed round banked"
    )
    fired(lambda ladder: l2_fired(ladder, LayerReading(1, 0.5, None, None)))
    restate()
    live.put(L3Memory(plan="attack the default label"))
    fired(lambda ladder: l3_fired(ladder, None))
    restate()

    def resumed(last: int) -> tuple[L2Memory, L3Memory, Ladder]:
        standing = scan_standing_rounds(ledger_chain(cycle_dir, moment=None)).rounds
        state = node_contract.SliceMap(carried)
        state.seat(standing[last].close.closed_state)
        return state.get(L2Memory), state.get(L3Memory), state.get(Ladder)

    kept = resumed(1)
    assert kept == (live.get(L2Memory), live.get(L3Memory), live.get(Ladder)), (
        "paused inside round 2: both fires belong to the boundary the resume keeps"
    )
    assert kept[2].l3.fires == 1 and kept[1].plan
    assert resumed(0) == (L2Memory(), L3Memory(), Ladder()), (
        "rewound to re-run round 1: the fires are discarded with it"
    )


# 7. Paired readings over shared cells


def _reference_reading(
    stores: Stores,
    reference: Sequence[float | None],
    arm: Sequence[float | None],
    *,
    cut: CutKind | None,
) -> PairedReading:
    bank = samples(len(reference), "t")
    scorer = compile_scorer("label_match(predicted, ground_truth)", verifier_graded=False)

    def rows(grades: Sequence[float | None]) -> CellSheet:
        return sheet(
            (
                measurement(s.id, g, sample_key=s.key)
                for s, g in zip(bank, grades, strict=True)
                if g is not None
            ),
            scorer.id,
        )

    bar = OptSearchPoint()
    key, scored = ArmKey("c1"), arm_state("C1.1", slot=ArmSlot(round=1, idx=0, total=1), cut=cut)
    reference_rows = rows(reference)
    pairs = ReferencePairs.of_round(
        seat(stores, bank=bank, scorer=scorer),
        {key: scored},
        declared=[cell.key for cell in reference_rows],
    )
    against = ArmKey(bar.id)
    reference_arm = ReadArm(against, arm_state("parent"), reference_rows)
    return pairs.read(ReadArm(key, scored, rows(arm)), against, reference_arm)


_H2H_BANK = samples(12)


def _h2h_config(
    optimizer: str, seed: int, scoring: str, proposer_model: str | None
) -> CampaignConfig:
    return CampaignConfig.model_validate(
        {
            "optimization": {
                "optimizer": optimizer,
                "degradation_threshold": 0.0,
                "nodes": {}
                if proposer_model is None
                else {"l1_generate": {"config": {"model": proposer_model}}},
            },
            "dataset_split": {"bench": 6},
            "run_seed": seed,
            "scoring": scoring,
        }
    )


def _evidence_campaign(
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
    purpose: Purpose = Optimize(),  # noqa: B008 — frozen
    proposer_model: str | None = None,
    graded: bool = True,
    steered: bool = False,
) -> SubjectSpec:
    if stores.campaigns.load_campaign(cid) is not None:
        return SubjectSpec("campaign", cid)
    split = DatasetSplit(bench=6)
    partition = partition_bank(_H2H_BANK, split, seed=seed)
    root = CycleHop(campaign_id=cid, cycle_id=f"cycle_{cid}")
    stores.campaigns.create_campaign(
        minted_campaign(
            root,
            config=_h2h_config(cid.split("_")[0], seed, scoring, proposer_model),
            purpose=purpose,
            dataset_name="ds",
            created_at=f"2026-09-26T00:00:{n:02d}Z",
        )
    )
    hop = CycleHop(campaign_id=cid, cycle_id=f"{root.cycle_id}_fork_r1") if rebased else root
    panel = [
        measurement(
            s.id,
            None,
            query=s.query,
            sample_key=s.key,
            pipeline_data={"env_reward": 0.5},
        )
        for s in partition.search[:4]
    ]
    walked = _filed(stores, f"panel_{cid}", panel, sp_hash="origin")
    # A rebase fork replays its own origin and redraws the one partition.
    ledgers: dict[CycleHop, CycleEventLog] = {}
    held = Bank.of(_H2H_BANK)
    stores.tenant_datasets.put_bank(held)
    for cycle in {root, hop}:
        stores.campaigns.mint_cycle(cycle)
        stores.campaigns.write_bank_partition(
            cycle, partition.record(dataset_hash=held.dataset_hash)
        )
        ledgers[cycle] = CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(cycle)))
        arm_walk(
            ledgers[cycle],
            ((s.id, cell) for cell, s in zip(walked, partition.search, strict=False)),
        )
    if rebased:
        stores.campaigns.mark_superseded(root, hop.cycle_id)
    if graded:
        key, pick = (
            ArmKey(f"{cid}:selected"),
            arm_state("C1.1", slot=ArmSlot(round=1, idx=0, total=1)),
        )
        answered = sheet(
            cell.filed(dataclasses.replace(cell.facts, answer=answer))
            for cell, (_, answer, _) in zip(panel, walked, strict=True)
        )
        closed = round_result(1, arms={key: pick}, selected=key, cells={key: answered})
        close_round(
            ledgers[hop],
            [round_result(0, cells={}), closed],
            cycle=seat(stores, bank=_H2H_BANK).cycle,
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
                sample_key=s.key,
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
            sample_keys=[s.key for s in partition.bench],
            stop=None,
            scorer_id=scoring,
            reads_before=int(role == "selected") * n,
        )
    hour = n if at is None else at
    started = f"2026-09-26T{hour:02d}:00:00Z"
    # The optimizer billed on the root; a rebase fork replayed its cells and graded the pick.
    calls: list[tuple[TokenUsageKind, float, bool]] = [
        ("optimizer", loop, False),
        ("backend", incurred - loop - 0.05, True),
        ("bench", 0.05, True),
    ]
    for kind, usd, cached in calls:
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
    bank_campaign_result(
        stores,
        hop,
        ledger=ledgers[hop],
        clock=LaunchClock(started_at=started, monotonic=0.0),
        finished_at=f"2026-09-26T{hour:02d}:{wall // 60:02d}:{wall % 60:02d}Z",
        optimizer_phases=frozenset(),
    )
    campaign = stores.campaigns.load_campaign(cid)
    assert campaign is not None and len(line_launches(stores, campaign)) == 1
    return SubjectSpec("campaign", cid)


def test_a_pair_is_one_estimate_over_the_cells_both_members_scored_or_says_why_it_is_none(
    built_stores: Stores, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    assert (band_lo, band_hi) == pytest.approx((mean(cand) - half, mean(cand) + half), abs=1e-9), (
        "on the normal quantile an eight-cell band is a fifth too narrow"
    )
    # scipy `t.ppf(0.975, 3)` over the SE floor 1/(4n) at n = 4.
    assert band([0.0] * 4) == pytest.approx((0.0, 3.182446305284263 / (4 * 4)), abs=1e-9), (
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
    assert (lift.rate_a, lift.rate_b) == pytest.approx((0.6, 0.8))
    estimate = lift.estimate
    assert estimate.value == pytest.approx(t_mean), "the lift is the paired mean"
    assert (estimate.ci_lo, estimate.ci_hi, estimate.p_value) == pytest.approx((t_lo, t_hi, t_p))
    assert estimate.side == "spans"
    assert estimate.p_floor == 0.25, "three cells differ, so no exact test can read below 2/2**3"
    assert lift.flips is not None
    assert (lift.flips.gained, lift.flips.lost, lift.flips.unchanged) == (2, 1, 2)
    sparse = samples(34, "t", query="w")
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
    four = samples(4, "t")

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
    worth = {four[sid].key: _objective(measurement(sid, g)) for sid, g in by_slot.items()}
    twice = _pair_member("twice", walked, four)
    cells = twice.sheet
    assert cells.column(ROW_GRADES["fitness"]) == standing, "a second reading is a second row"
    assert cells.column(ROW_GRADES["objective"]) == worth
    assert responses_of(cells) == worth, "the ruler read another row than the column"
    assert cells_read("accuracy", cells, (), None) == 4
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
    with pytest.raises(UngradedCellError):
        ROW_GRADES["fitness"].read(ungraded.sheet.cells[-1])

    # A pair that cannot be read says which way instead of reading zero.
    six = samples(6, "t")
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
    with pytest.raises(ValidationError):
        said(ReadingState.READ)

    # An arm is read on the cells its reference was read on.
    whole_panel: list[float | None] = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0]
    for ref_grades, arm_grades, cut, coverage_state, scored, size, rates, level in (
        # The reference sat four of the six cells: 5/6 over the arm's own walk, 3/4 on those four.
        (
            [1.0, 0.0, 1.0, 0.0, None, None],
            [1.0, 1.0, 1.0, 0.0, 1.0, 1.0],
            None,
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
            "eliminated",
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
            None,
            CoverageState.PARTIAL,
            5,
            6,
            (0.6, 0.8),
            None,
        ),
    ):
        on_reference = _reference_reading(
            built_stores, ref_grades, arm_grades, cut=cast(CutKind | None, cut)
        )
        lift, coverage, set_read = (
            on_reference.headline,
            on_reference.coverage,
            on_reference.cell_set,
        )
        assert lift is not None and coverage is not None and set_read is not None
        assert (set_read.name, set_read.size) == (CellSetName.REFERENCE_CELLS, size), coverage_state
        assert (coverage.state, coverage.scored) == (coverage_state, scored), coverage_state
        assert (lift.rate_a, lift.rate_b) == rates, f"{coverage_state}: rates over the shared cells"
        assert on_reference.reference_level("fitness") == level, (
            f"{coverage_state}: a reference level is served only on its whole cell set"
        )
        assert on_reference.beside[0].measurand.key == "objective"

    # A verify reads its fresh cells alone and pairs its lift over the origin.
    def banked(strategy: VerifyStrategy = "random", round_num: int = 3) -> VerifyPass:
        return VerifyPass(
            arm=ArmName(key=ArmKey("c"), round=round_num, label="C3.1"),
            sp_hash="h",
            cells=[(f"k{sid}", f"r.{sid}", False) for sid in range(100, 110)],
            positions=list(range(100, 110)),
            strategy=strategy,
            seed=1,
            scorer_id="s",
        )

    def keyed(rows: list[GradedCell]) -> CellSheet:
        return sheet((_keyed(row, f"k{row.key}") for row in rows), "s")

    recorded = keyed(measurements([1.0] * 20))
    fresh = keyed(measurements([1.0, 0.0] * 5, range(100, 110)))
    # The origin: half the round's cells, none of the fresh ones.
    c0_rows = keyed([*measurements([0.0, 1.0] * 10), *measurements([0.0] * 10, range(100, 110))])
    hop = CycleHop(campaign_id="camp", cycle_id="cyc")
    c0 = ArmName(key=ArmKey("c0"), round=0, label="C0")

    def verified(pass_: VerifyPass, fresh_rows: CellSheet = fresh) -> Any:
        arm = c0 if pass_.arm.round == 0 else pass_.arm
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

    origin_sp, edit_sp = prompted(instruction="origin"), prompted(instruction="edit")
    as_origin = walk("r0", {0: 1.0, 1: 0.0, 2: 1.0})
    rounds = [
        round_result(0, candidates_scored=0, cells={ArmKey(origin_sp.id): as_origin}),
        round_result(
            1,
            candidates_scored=0,
            cells={
                ArmKey(edit_sp.id): walk("c1", {2: 1.0, 3: 1.0}),
                ArmKey(origin_sp.id): walk("r1", {2: 0.0, 3: 1.0}),
            },
            overlap_results={origin_sp.id: walk("ov", {4: 1.0, 5: 1.0})},
        ),
    ]
    in_run = InRunCells(rounds)

    def origin_read(scope: RoleScope) -> dict[str, float | None]:
        return {c.key: c.grade.fitness for c in in_run.sheet(origin_sp.id, scope)}

    assert origin_read(RoleScope.REPORT) == {
        "k0": 1.0,
        "k1": 0.0,
        "k2": 0.0,
        "k3": 1.0,
        "k4": 1.0,
        "k5": 1.0,
    }, "ONE set: the later reading of cell 2 stands, and the overlap rows are the origin's own"
    assert origin_read(RoleScope.DECISION) == {"k0": 1.0, "k1": 0.0, "k2": 0.0, "k3": 1.0}, (
        "a decision reads 2/4, never the two bought after it"
    )
    assert origin_read(RoleScope.BENCH) == {}
    assert [cell.key for cell in rounds[1].sheet_of(origin_sp.id)] == ["k2", "k3"]

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
        arm_walk(
            CycleEventLog.open(CycleDir(built_stores.campaigns.cycle_dir(cycle_hop))),
            zip(slots, _filed(built_stores, f"keyed_{cycle_id}", rows), strict=True),
        )
        return scan_ledger_walks(
            ledger_chain(CycleDir(built_stores.campaigns.cycle_dir(cycle_hop)), moment=None)
        )

    first = filed_cycle("cycle_a", dict(enumerate(asks_one_thing)))
    assert len(individual_cells(first, "c0", RoleScope.REPORT)) == 2, "one query, two cells"
    second = filed_cycle("cycle_b", {7: asks_one_thing[1], 9: asks_one_thing[0]})
    folded = individual_cells([*first, *second], "c0", RoleScope.REPORT)
    later = individual_cells(second, "c0", RoleScope.REPORT)
    assert {key: answer for key, answer, _ in folded} == {
        key: answer for key, answer, _ in later
    }, "the same two samples at other slots are still two cells, at the later read"
    assert len(later) == 2

    # A run stands on its selection, read against the origin it was promoted from.
    origin, first_pick, second_pick, third_pick = (
        prompted(instruction=text) for text in ("origin", "one", "three", "five")
    )
    named = {"C0": origin, "C1.1": first_pick, "C3.2": second_pick, "C5.1": third_pick}
    ids = {label: sp.id for label, sp in named.items()}

    def closed(rnd: int, on: OptSearchPoint, *arms: str, selected: str = "", **reading: Any) -> Any:
        """A held round ends on its parent *on*, re-read as the reference arm."""
        proposed = {
            ArmKey(sp.id): arm_state(
                label, slot=ArmSlot(round=rnd, idx=idx, total=len(arms)), individual=sp
            )
            for idx, label in enumerate(arms)
            for sp in [named.get(label) or prompted(instruction=label)]
        }
        return round_result(
            rnd,
            arms={ArmKey(on.id): arm_state("parent", individual=on)} | proposed,
            improved=bool(selected),
            selected=ids[selected] if selected else None,
            voted={} if selected else {"ended_on": on.id},
            cells={},
            **reading,
        )

    def stands(run: list[Any]) -> RunStanding:
        return RunStanding.after(run, lives=None)

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
    assert at_c0.selection is not None
    assert (at_c0.selection.label, at_c0.parent) == ("C0", None)
    assert at_c0.vs_origin == PairedReading.unread(ReadingState.SAME_INDIVIDUAL)
    held = stands(run[:3])
    assert held.selection is not None
    assert held.selection.label == "C1.1", "a held round keeps the selection before it"
    assert held.vs_origin == first_line.lead
    standing_now = stands(run)
    assert standing_now.selection is not None and standing_now.parent is not None
    assert standing_now.selection.model_dump() == {
        "key": second_pick.id,
        "round": 3,
        "label": "C3.2",
    }
    assert standing_now.parent.label == "C1.1"
    assert (standing_now.rounds_closed, standing_now.improved, standing_now.advanced) == (
        3,
        2,
        1,
    ), "promotions and separations are counted apart: a promotion is a point estimate"
    vs_c0 = standing_now.vs_origin
    assert vs_c0.coverage is not None and vs_c0.headline is not None
    assert (vs_c0.coverage.scored, vs_c0.headline.rate_a, vs_c0.headline.rate_b) == (9, 0.0, 1.0), (
        "C0 on the reading its selection sat beside: rates differenced must have sat one exam"
    )
    held_on = stands([*run, closed(4, second_pick, "C4.1", overlap=reread)])
    assert held_on.selection == standing_now.selection
    assert held_on.vs_origin.headline is not None
    assert held_on.vs_origin.headline.rate_a == pytest.approx(1 / 9), (
        "a later round that holds re-reads the line, and the standing follows the NEWEST reading"
    )
    pass_stopped = OverlapReading.unpaired(ReadingState.PASS_STOPPED, 5, True)
    unread = stands([*run, closed(5, third_pick, "C5.1", selected="C5.1", overlap=pass_stopped)])
    assert unread.selection is not None
    assert (unread.selection.label, unread.parent) == ("C5.1", standing_now.selection)
    assert (unread.vs_origin.state, unread.vs_origin.headline) == (
        ReadingState.PASS_STOPPED,
        None,
    ), "a pick nobody has read against C0 yet says so — never the pick before it under its name"
    assert (standing_now.rounds_without_advance, unread.rounds_without_advance) == (0, 0), (
        "round 3 advanced, and an unread pick is no stall"
    )
    assert held_on.rounds_without_advance == 1

    # A head-to-head pairs two optimizers only on one bench under one grader.
    h2h_stores = workspace(tmp_path / "h2h")
    potter_a, capo_b = SubjectSpec("campaign", "potter_a"), SubjectSpec("campaign", "capo_b")
    potter_course = SubjectSpec("course", "potter_a", "cycle_potter_a_fork_r1")
    _evidence_campaign(h2h_stores, "potter_a", 1, selected=0.5, rebased=True)
    _evidence_campaign(h2h_stores, "capo_b", 2, selected=0.7, incurred=0.3, loop=0.15, wall=50)
    shared = subject_evidence(h2h_stores, [potter_a, capo_b]).head_to_head
    assert shared is not None and shared.rows[0].bench_set is not None
    for lens in ("score:(", "score:'x'"):
        masked = parse_subject(f"course:potter_a/cycle_potter_a_fork_r1;lens={lens}")
        with pytest.raises(ScoringFormulaError) as refused:
            subject_evidence(h2h_stores, [masked, capo_b])
        assert refused.value.http_status == 400, f"the lens {lens!r} is the caller's input"
    declared = minted_campaign(
        CycleHop(campaign_id="arm", cycle_id="cycle_arm"),
        config=_h2h_config("potter", 0, _H2H_FORMULA, None),
        purpose=Compare(arm=Arm(head_to_head_id="any", arm_key="any")),
    ).declaration
    for h2h_id in ("real4", "real5", "real6", "real7", "real8"):
        alike = declared.shared_part()
        if h2h_id == "real8":
            # Declared over defaults its arms no longer run under.
            alike = alike.model_copy(update={"bench_defaults_pin": "moved"})
        h2h_stores.campaigns.declare_head_to_head(
            HeadToHeadRecord(
                head_to_head_id=h2h_id,
                created_at="",
                origin="origin",
                shared=alike,
                ruler_version=0,
            )
        )
    h2h_cases: tuple[
        tuple[tuple[SubjectSpec, ...], dict[str, Any] | None, bool | None, list[str], int], ...
    ] = (
        ((potter_a, capo_b), None, True, [], 1),
        ((potter_course, capo_b), None, True, [], 1),
        ((potter_course, potter_a, capo_b), None, True, [], 1),
        (
            (potter_a, capo_b),
            {"cid": "gepa_c", "n": 3, "seed": 1},
            False,
            ["bench_rows"],
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
        # capo_b and capo_f run one treatment, so they contrast nothing and never pair.
        ((potter_a, capo_b), {"cid": "capo_f", "n": 6, "at": 1}, True, [], 2),
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
    )
    for subjects, third, verdict, differs_on, family in h2h_cases:
        case = f"{subjects} beside {third}"
        roster = [*subjects]
        if third is not None:
            roster.append(_evidence_campaign(h2h_stores, **{"selected": 0.7, **third}))
        if not family:
            with pytest.raises(BadRequestError):
                subject_evidence(h2h_stores, roster)
            continue
        evidence = subject_evidence(h2h_stores, roster)
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
        ("real4", ("potter", "capo", "gepa", "levi"), True, ["controlled"] * 3 + ["uncontrolled"]),
        (
            "real6",
            ("potter", "capped", "gepa", "levi"),
            False,
            ["controlled", "controlled", "uncontrolled", "differs"],
        ),
        ("real7", ("potter", "capo", "waiting"), None, ["controlled"] * 3),
        ("real8", ("potter", "capo"), False, ["differs"] * 2),
    ):
        case = f"{h2h_id} over {arms}"
        arm_subjects = []
        for key in arms:
            optimizer, hour = optimizer_hour[key]
            record = "real5" if key == "levi" else h2h_id
            arm_subjects.append(
                _evidence_campaign(
                    h2h_stores,
                    f"{optimizer}_{key}_{record}",
                    hour,
                    selected=0.6,
                    purpose=Compare(arm=Arm(head_to_head_id=record, arm_key=key)),
                    graded=key != "waiting",
                )
            )
        if "capped" in arms:
            capped = f"capo_capped_{h2h_id}"
            h2h_stores.campaigns.update_campaign(capped, standing_budget=Budget(usd=1.0))
        h2h = subject_evidence(h2h_stores, arm_subjects).head_to_head
        assert h2h is not None and h2h.guard.head_to_head_id == h2h_id, case
        assert [r.guard.state.value for r in h2h.rows] == guards, (
            f"{case}: {[r.guard.differs_on for r in h2h.rows]}"
        )
        assert h2h.verdict is verdict, case
        tested_by_state = Counter(
            p.guard.state for p in h2h.pairs if p.reading.headline is not None
        )
        for pair in h2h.pairs:
            if pair.reading.headline is not None and pair.reading.headline.family is not None:
                assert pair.reading.headline.family.n_tests == tested_by_state[pair.guard.state], (
                    f"{case}: a controlled and an uncontrolled pair were corrected as one family"
                )
        assert (GuardDifference.DEFAULTS in h2h.guard.differs_on) is (h2h_id == "real8"), (
            f"{case}: arms run under defaults their record was not declared over, unsaid"
        )
        if verdict is None:
            assert h2h.verdict_absent is ReadingState.PENDING, f"{case}: a waiting arm is pending"
