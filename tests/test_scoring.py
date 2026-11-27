"""A wrong score or a wrong decision.

Owns `application/scoring/`, `shared/statistics.py`, `optimizers/potter/pobb.py` and
`optimizers/potter/escalation.py`, the runner's election, `scoring/passes.py` and `evidence/`.
The run completes, the dashboard looks fine, and a different candidate should have won.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import random
import signal
from collections import Counter
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from contextlib import (
    AbstractAsyncContextManager,
    AbstractContextManager,
    asynccontextmanager,
    contextmanager,
)
from itertools import count, product
from math import comb, sqrt
from pathlib import Path
from statistics import fmean, stdev
from typing import Any, NamedTuple, NoReturn, cast
from unittest.mock import Mock

import httpx
import pytest
import yaml
from factories import (
    BENCH_TARGET,
    PARENT,
    PROMPT_INFO,
    arm_key,
    arm_state,
    arm_walk,
    bench_line,
    bench_pass,
    bench_passes,
    cycle_ledger,
    defaulted_config,
    delta_ruler,
    election_knobs,
    fake_connector,
    filed,
    filed_under,
    inner_sandbox,
    ledger_records,
    measurement,
    measurements,
    member_rows,
    on_jumping_clock,
    opened_round,
    optimizer_state,
    pipeline_schema,
    pobb_knobs,
    prompted,
    round_result,
    run_wiring,
    samples,
    score_summary,
    scorer_of,
    set_cycle,
    sheet,
    spend_book,
    walking_cycle,
    workspace,
)
from offline_world import FakeHarness, Router
from pydantic import SecretStr, ValidationError
from world import Shared, World, serving, shared_run

from promptpotter import connectors
from promptpotter.application.bench import node_context
from promptpotter.application.bench.cycle import Cycle, ReadArm, RoundContext
from promptpotter.application.bench.difficulty import (
    JudgingRuler,
    ruler_clock,
    session_ruler_scope,
)
from promptpotter.application.bench.node_context import NodeContext, own_rows
from promptpotter.application.caller import Caller
from promptpotter.application.commands import measuring
from promptpotter.application.commands.dispatcher import (
    Applier,
    CommandCall,
    CommandDispatcher,
)
from promptpotter.application.config_map import check_couplings
from promptpotter.application.datasets.authored import defaults_config
from promptpotter.application.evidence.head_to_head import (
    GuardDifference,
    GuardMember,
    GuardState,
    pair_guard,
)
from promptpotter.application.evidence.read import subject_evidence
from promptpotter.application.host_surface import (
    IDEMPOTENCY_KEY,
    CallTool,
    CancelTask,
    HostSession,
    HostTask,
    TaskResultOf,
    TaskStatusOf,
)
from promptpotter.application.initialization.scoring_scope import (
    open_diagnostic,
    scorer_setup,
    verb_ledger,
)
from promptpotter.application.initialization.session import LaunchClock, Session
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.intelligence.rasch import (
    RoundAbilities,
    candidate_abilities,
    responses_of,
    theta_lift_over_parent,
)
from promptpotter.application.jobs import mint
from promptpotter.application.jobs.launcher.admission import (
    PassStoppedError,
    paid_pass,
)
from promptpotter.application.mask.backprop import ForkDecline, select_branch_round
from promptpotter.application.mask.record import SpineCycle
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.optimizers import nodes as node_contract
from promptpotter.application.optimizers.capo.members import (
    seed_instructions,
)
from promptpotter.application.optimizers.gepa.members import (
    MinibatchBar,
    MinibatchBars,
    MinibatchGate,
)
from promptpotter.application.optimizers.nodes import NODE_MEMBERS, RoundSamples
from promptpotter.application.optimizers.potter.dispatch.facade import DispatchHub, PotterView
from promptpotter.application.optimizers.potter.dispatch.schemas import (
    L1Edit,
    L3PlanOutput,
    fork_proposal_model,
)
from promptpotter.application.optimizers.potter.election import (
    RoundDecision,
    RoundWinner,
    seated,
)
from promptpotter.application.optimizers.potter.escalation import (
    EscalationInputs,
    LadderAsk,
    NextAction,
    _rebase_request,
    as_read_by,
    ask_l2_escalation,
    bank_and_route,
    decide_escalation,
    l2_fired,
    l3_fired,
)
from promptpotter.application.optimizers.potter.knobs import EscalationLadder, PoBBKnobs
from promptpotter.application.optimizers.potter.l1 import applied_edits
from promptpotter.application.optimizers.potter.pobb import PoBBCheck, field_p_best
from promptpotter.application.optimizers.potter.records import (
    L3Memory,
    Ladder,
    LayerCounters,
)
from promptpotter.application.optimizers.potter.validators import (
    detect_invariants,
    l1_evidence_grounding,
    l1_prompt_fields_open,
)
from promptpotter.application.optimizers.variation.members import (
    Cross,
    donor_take,
    most_donated,
)
from promptpotter.application.run_phase_control import RunControl
from promptpotter.application.runner import bench as line_bench
from promptpotter.application.runner.bench import (
    LineSpan,
    bench_selection,
    line_round_budget,
    pair_on_bench,
    read_bench,
    read_curve,
    score_on_bench,
)
from promptpotter.application.runner.entry import build_cycle_result
from promptpotter.application.runner.round import (
    ReferencePairs,
    announce_opening,
    close_round,
    execute_round,
    hold_for_operator,
    post_round,
)
from promptpotter.application.runner.termination import panel_gate_tripped, standing_tripped
from promptpotter.application.scoring import closed_rounds, evaluators, query_loop
from promptpotter.application.scoring import passes as held_out
from promptpotter.application.scoring.classification import DegradationCheck, scoreable_rows
from promptpotter.application.scoring.evaluators import (
    SampleEvaluator,
    compute_degraded_rate,
    compute_error_rate,
    compute_fitness,
)
from promptpotter.application.scoring.formula import compiler
from promptpotter.application.scoring.formula.compiler import (
    auto_scorer_id,
    origin_anchors,
    parse_dials,
    realize_dials,
)
from promptpotter.application.scoring.formula.matchers import MATCHERS
from promptpotter.application.scoring.judges.grounding import ANSWER_GROUNDING
from promptpotter.application.scoring.judges.registry import two_valued_terms
from promptpotter.application.scoring.metrics import compute_composite_fitness, fold_cells
from promptpotter.application.scoring.paired import (
    MemberRows,
    RoundPairs,
    absent_pair,
    as_family,
    grade_measurands,
    origin_floor,
    read_arms,
    read_pair,
)
from promptpotter.application.scoring.passes import verify_member, verify_reading
from promptpotter.application.scoring.race_protocol import Trigger, View, WalkView
from promptpotter.application.scoring.sample_measurement import measure_sample
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.application.scoring.selection import (
    crowning_cells,
    level_band,
    ranked_first,
    readable_lifts,
)
from promptpotter.config.paths import optimizers_root
from promptpotter.config.settings import settings
from promptpotter.connectors import n8n
from promptpotter.connectors.protocol import Connector
from promptpotter.domain.arm_reading import order_floor
from promptpotter.domain.arms import (
    ArmPosition,
    BankedSearchPointError,
    Concluded,
    Cut,
    CutKind,
    Measured,
    PointContext,
    Proposal,
    Refused,
    is_leader_eligible,
)
from promptpotter.domain.bench import (
    COST_ABSENCE_WORDS,
    BenchTrigger,
    CampaignRefusal,
    CostAbsence,
    DatasetSplit,
    PassStop,
    partition_bank,
)
from promptpotter.domain.campaign import (
    BenchSet,
    Optimize,
    StoppingRule,
)
from promptpotter.domain.campaign_config import OptimizerChoice
from promptpotter.domain.cell_fold import (
    ScoreSummary,
    is_prior_dominated,
    repairable_holes,
    unscoreable_cells,
)
from promptpotter.domain.cell_terms import CELL_TERMS, CellTerm
from promptpotter.domain.command_events import CommandAckRecord
from promptpotter.domain.command_kinds import (
    CancelPayload,
    CommandKind,
    CommandPayload,
    Door,
    HeldOutPayload,
    PauseCyclePayload,
    SkipSearchpointPayload,
    SubmitArmsPayload,
    SubmittedArm,
    VerifyCandidatePayload,
)
from promptpotter.domain.crowning_bar import CrowningBar, NextParent
from promptpotter.domain.cycle_paths import CycleDir, CycleHop, Subject
from promptpotter.domain.judges import JudgeSpec, JudgeStage, JudgeVerdict
from promptpotter.domain.opt_search_point import Locus, NodePrompt, OptSearchPoint
from promptpotter.domain.optimizer_template import PROMPT_FIELDS
from promptpotter.domain.origin_panel import (
    NotSeparatedStall,
    OverlapReading,
    PanelTrigger,
    RoundAdvance,
    RoundEnding,
    StallEffect,
    TriggeredReading,
    line_by_individual,
    origin_panel,
    round_advance,
)
from promptpotter.domain.paired_reading import (
    ROUND_LIFT_SPEC,
    ArmKey,
    ArmName,
    CellSet,
    CellSetBasis,
    CellSetName,
    CoverageState,
    EstimatorSpec,
    Measurand,
    MeasurandKind,
    MeasuredLift,
    MemberAddress,
    PairedReading,
    ReadingState,
    RoundArm,
)
from promptpotter.domain.pass_kinds import IndividualWalk, individual_cells
from promptpotter.domain.phases import (
    PauseCause,
    RunPhase,
    StopOutcome,
    StopReason,
    WalkEnd,
    stop_reason_outcome,
)
from promptpotter.domain.pipeline_params import PipelineParams
from promptpotter.domain.pipeline_schema import (
    ManifestNodeOverlay,
    PipelineNode,
)
from promptpotter.domain.pool import BlockPool
from promptpotter.domain.records.held_out import PassGradedRecord
from promptpotter.domain.records.run_records import (
    ArmAct,
    Ended,
    RebaseRequest,
    RoundClosedRecord,
    RoundWarningRecord,
    Running,
    RunPhaseRecord,
    StopLoop,
)
from promptpotter.domain.results import InRunCells, Reelection, RoundResult
from promptpotter.domain.results_health import is_deprecated
from promptpotter.domain.ruler import RulerScope, RulerStamp, p_better
from promptpotter.domain.run_standing import (
    LineOutlay,
    RunStanding,
    declared_selection,
    line_reference,
    rounds_without_advance,
    stalls_left,
)
from promptpotter.domain.sample import Bank, Sample
from promptpotter.domain.scoring import (
    CRITERION_BASE,
    NO_CELLS,
    NO_RESULT,
    ROW_GRADES,
    CellSheet,
    GradedCell,
    MeasuredCell,
    PipelineData,
    Scorer,
    ScoringFormulaError,
    UngradedCellError,
    WalkedCell,
    anchored_criterion_dials,
    is_answer_collapsed,
    turn_scalars,
)
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.domain.spend import Budget
from promptpotter.domain.verification import (
    BenchPass,
    BenchPasses,
    NoiseBand,
    VerifyPass,
    VerifyStrategy,
)
from promptpotter.domain.wounds import ValidationReason
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.spend_book import (
    SpendBook,
    require_spend_book,
)
from promptpotter.infrastructure.producer_lock import cycle_held, release_cycle
from promptpotter.infrastructure.projections.live_dashboard import (
    LiveDashboardProjection,
)
from promptpotter.infrastructure.run_scope import run_scope, scoped
from promptpotter.infrastructure.store.archive_queries import OpenAt
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_ledger_walks,
)
from promptpotter.infrastructure.store.dataset_access import DEFINITION_FILE
from promptpotter.infrastructure.store.io import write_yaml
from promptpotter.infrastructure.store.stores import Stores, descend_store
from promptpotter.presentation.cli.commands.launch import ctrl_c_stops_pass
from promptpotter.shared.answer_text import extract_gsm8k_number
from promptpotter.shared.errors import (
    ConflictError,
    ErrorCategory,
    NotFoundError,
    PayloadInvalidError,
    PotterError,
    SendRefusedError,
)
from promptpotter.shared.identity import Principal, default_identity
from promptpotter.shared.measurement_context import MeasurementRole, RoleScope
from promptpotter.shared.statistics import paired_mean_t

_H2H_FORMULA = "env_reward"


def _keyed(cell: GradedCell, sample_key: str) -> GradedCell:
    return cell.filed(dataclasses.replace(cell.facts, sample_key=sample_key))


def _objective(cell: GradedCell) -> float:
    assert cell.grade.objective is not None, f"cell {cell.key} carries no grade"
    return cell.grade.objective


def _read_pair(a: MemberRows, b: MemberRows, panel: list[Sample], **asked: Any) -> PairedReading:
    hit = Measurand(kind=MeasurandKind.GRADE, key="hit", scorer_id="s", binary=True, unit="rate")
    read_under: dict[str, Any] = {
        "a": a,
        "b": b,
        "cell_set": CellSetName.ORIGIN_PANEL,
        "cells": [s.key for s in panel],
        "masked": False,
        "dataset_hash": "d",
        "measurands": [hit],
        "spec": EstimatorSpec(alpha=0.05),
        "scope": RoleScope.DECISION,
        "instrument_id": "i",
    }
    return read_pair(**(read_under | asked))


# 1. Scorer formulas

_LLM_ONLY = pipeline_schema(nodes=[PipelineNode(name="llm_only", tunes_llm=False)])
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


_SCORE = {name: m.score for name, m in MATCHERS.items()}


def _assert_graded(
    rows: tuple[tuple[Callable[..., Any], tuple[str, ...], float | None], ...],
) -> None:
    for match, args, expected in rows:
        assert match(*args) == expected, f"{match.__name__}{args!r} must grade {expected}"


def test_the_blocksworld_matcher_replays_the_plan_against_its_gold() -> None:
    match = _SCORE["blocksworld_plan_match"]
    _assert_graded(
        (
            (
                match,
                (
                    "Clear orange first.\n[PLAN]\nunstack the yellow block from on top of the "
                    "orange block\nput down the yellow block\npick up the orange block\nstack the "
                    "orange block on top of the red block\n[PLAN END]",
                    _BLOCKSWORLD_GOLD,
                ),
                1.0,
            ),
            # The final layout reads right, but the hand still held yellow when orange was picked up.
            (
                match,
                (
                    "[PLAN]\nunstack the yellow block from on top of the orange block\npick up the "
                    "orange block\nstack the orange block on top of the red block\n[PLAN END]",
                    _BLOCKSWORLD_GOLD,
                ),
                0.0,
            ),
        )
    )


def test_the_meeting_matcher_replays_the_plan_against_its_gold() -> None:
    match = _SCORE["meeting_plan_match"]
    _assert_graded(
        (
            (
                match,
                (
                    "Go early and wait. SOLUTION: You start at Marina District at 9:00AM. You "
                    "travel to Mission District in 20 minutes and arrive at 9:20AM. You wait until "
                    "10:30AM. You meet Stephanie for 120 minutes from 10:30AM to 12:30PM.",
                    _MEETING_GOLD,
                ),
                1.0,
            ),
            # No wait: the replay reaches Stephanie at 9:20AM, before her window opens.
            (
                match,
                (
                    "SOLUTION: You start at Marina District at 9:00AM. You travel to Mission "
                    "District in 20 minutes and arrive at 9:20AM. You meet Stephanie for 120 "
                    "minutes from 9:20AM to 11:20AM.",
                    _MEETING_GOLD,
                ),
                0.0,
            ),
        )
    )


def test_the_aime_and_gsm8k_matchers_read_the_number_they_name() -> None:
    aime, gsm8k = _SCORE["aime_match"], _SCORE["gsm8k_match"]
    _assert_graded(
        (
            (aime, (r"First: \boxed{10}. Rechecking: \boxed{42}", "42"), 1.0),
            (aime, (r"\boxed{undefined} The answer is 42", "42"), 1.0),
            (aime, ("no numbers", "42"), 0.0),
            (extract_gsm8k_number, ("#### 1,234",), 1234.0),
            (extract_gsm8k_number, ("I calculated 99 but #### 42",), 42.0),
            (extract_gsm8k_number, ("no numbers",), None),
            (gsm8k, ("42.0", "#### 42"), 1.0),
            (gsm8k, ("#### 99", "#### 42"), 0.0),
        )
    )


def test_the_label_matcher_reads_the_last_bold_or_the_last_line() -> None:
    label = _SCORE["label_match"]
    _assert_graded(
        (
            (label, ("First try **No**. Corrected: **Yes**", "yes"), 1.0),
            (label, ("plain text answer", "Plain Text Answer"), 1.0),
            (label, ("foo", "bar"), 0.0),
            (label, ("so the answer is **D**", "(D)"), 1.0),
            (label, ("**(b)**", "B"), 1.0),
            (label, ("**C**", "(D)"), 0.0),
            # Bolds nothing: the last non-empty line answers, the reasoning above it does not.
            (
                label,
                ("The story is about a merger, a business matter.\n\nBusiness\n", "Business"),
                1.0,
            ),
            (label, ("The story is about a merger.\n\nBusiness\n\nWorld", "Business"), 0.0),
        )
    )


def test_the_exact_matcher_normalizes_as_squad_does_but_a_number_keeps_its_point_and_sign() -> None:
    em = _SCORE["em_match"]
    _assert_graded(
        (
            (em, ("so it is **the Beatles**", "Beatles"), 1.0),
            (em, ("**A.**", "a"), 1.0),
            (em, ("", "a"), 0.0),
            (em, ("**the**", "a"), 1.0),
            (em, ("**13**", "1.3"), 0.0),
            (em, ("**.5**", "-.5"), 0.0),
            (em, ("**-5 points.**", "-5 points"), 1.0),
        )
    )


def test_the_math_matcher_takes_the_last_box_and_equates_hendrycks_spellings() -> None:
    math = _SCORE["math_match"]
    _assert_graded(
        (
            (math, (r"\boxed{\dfrac{1}{2}} so \boxed{\left( 1, 2 \right)}", "(1,2)"), 1.0),
            (math, (r"\boxed{\dfrac{3}{4}}", r"\frac34"), 1.0),
            (math, (r"\boxed{10\text{ cm}}", "10"), 1.0),
            (math, ("**x = 5**", "5"), 1.0),
            (math, (r"\boxed{0.75}", r"\frac{3}{4}"), 0.0),
        )
    )


def test_a_formula_reaching_past_the_ast_allowlist_is_refused_at_compile() -> None:
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
            scorer_of(formula)
    # `turns` is compacted out of cold rows, so a formula reaching it is refused at COMPILE: a
    # bare Call reaches `eval()` as a NameError, which reads as a missing TERM.
    for formula in ("turns[0]", "turns.index", "len(turns)"):
        with pytest.raises(ScoringFormulaError):
            scorer_of(formula, verifier_graded=True)


class _WireCell(NamedTuple):
    banked: dict[str, Any]
    stale: dict[str, Any]
    answered: MeasuredCell
    plain: Scorer
    composed: Scorer
    channels: Callable[..., MeasuredCell]


@pytest.fixture
def wire_cell() -> _WireCell:
    """A banked cell's wire under a stale grade, and the formulas that regrade it."""
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
        "stale_outcome": "retried",
        "degraded_obs_count": 2,
    }
    stale = {"fitness": 1.0, "objective": 1.0, "unscored": "an older formula's"}
    plain = scorer_of("env_reward", verifier_graded=True)
    answered = MeasuredCell.from_wire({**banked, **stale, "error": None, "error_category": None})
    composed = scorer_of("skill_opened", "fitness * 0.5", verifier_graded=True)

    def channels(error: str | None = None, **reads: float) -> MeasuredCell:
        fault: dict[str, Any] = {"error_category": "SERVER"} if error else {}
        cell: dict[str, Any] = {"query": "q", "ground_truth": "", "predicted": "p", "error": error}
        return measurement(0, None, pipeline_data=reads, **cell, **fault).facts

    return _WireCell(banked, stale, answered, plain, composed, channels)


def test_a_cell_read_off_the_wire_keeps_its_facts_and_drops_any_grade_it_arrived_with(
    wire_cell: _WireCell,
) -> None:
    banked, stale, *_ = wire_cell
    facts = MeasuredCell.from_wire({**banked, **stale})
    assert facts.wire() == banked, "a fact was lost, or a grade reached the facts"
    assert (facts.cost_s, facts.elapsed_s, facts.shown_s) == (1.5, 2.0, 1.5), (
        "latency reads the step timings, the elapsed the total"
    )


def test_a_replys_lines_rank_only_under_the_formula_reading_them(wire_cell: _WireCell) -> None:
    answered = wire_cell.answered

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
        for read in [scorer_of(formula).grade(row).grade]
    ]
    assert readings == [(None, None, 1.0), (2, 2, 0.5), (2, 2, 0.0), (2, 2, 0.5)], (
        "a reply's lines rank only under the formula reading them; the backend's ranking always"
    )


def test_grading_writes_no_fact_and_a_regrade_reads_only_its_own_formula(
    wire_cell: _WireCell,
) -> None:
    _, _, _, plain, composed, channels = wire_cell
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
    ordered = scorer_of(
        "1.0", "fitness * (0.85 + 0.15 * skill_opened)", verifier_graded=True
    ).grade(channels(skill_opened=0.0))
    assert (ordered.grade.fitness, ordered.grade.objective) == (1.0, 0.85), (
        "the stamps are ORDERED: a composite naming `fitness` reads the one just landed"
    )


def test_a_cell_the_formula_cannot_grade_is_unscored_and_a_regrade_recovers_it(
    wire_cell: _WireCell,
) -> None:
    _, _, _, plain, composed, channels = wire_cell
    # UNSCORED is a third state: ERRORED would stamp 0.0 and abandon the candidate's whole walk.
    first = composed.grade(channels(skill_opened=1.0, env_reward=0.25))
    unread = composed.grade(channels(env_reward=1.0))
    assert (unread.grade.fitness, unread.grade.objective, unread.scored) == (None, None, False)
    assert not unread.facts.errored and unread.facts.error_category is None, (
        "a cell the formula cannot grade is no fault of the backend"
    )
    assert scoreable_rows([first, unread]) == [first], "an unscored cell entered the mean"
    recovered = plain.grade(unread.facts)
    assert (recovered.grade.fitness, recovered.scored) == (1.0, True), "a regrade recovers it"


def test_a_formula_that_raises_halts_loud_and_an_errored_row_never_evaluates_it(
    wire_cell: _WireCell,
) -> None:
    channels = wire_cell.channels
    raising = scorer_of("1.0 / tokens", verifier_graded=True)
    with pytest.raises(ScoringFormulaError):
        raising.grade(channels(tokens=0.0))
    errored = raising.grade(channels("HTTP 502", tokens=0.0))
    assert (errored.grade.fitness, errored.grade.objective, errored.scored) == (0.0, 0.0, False), (
        "an errored row is graded 0.0 without evaluating a formula that would raise on it"
    )


def test_formula_arithmetic_is_on_floats_so_a_tenant_buys_no_unbounded_cpu_or_memory(
    wire_cell: _WireCell,
) -> None:
    channels = wire_cell.channels
    for formula in ("'x' * 10**10", "abs('x')", "label_match(predicted, 'x' * 10**10)"):
        with pytest.raises(ScoringFormulaError):
            scorer_of(formula)
    for formula in ("9**9**9", "pow(9, pow(9, 9))", "predicted * 10**10", "-predicted"):
        with pytest.raises(ScoringFormulaError):
            scorer_of(formula).grade(channels())
    halved = scorer_of("label_match(predicted, 'p') * 2**-1")
    assert halved.grade(channels()).grade.fitness == 0.5, (
        "a helper's string argument or float arithmetic was refused"
    )


_FIELDS_GOLD = json.dumps(
    {
        "type_of_document": ["invoice"],
        "requires_action": [True],
        "extract_depth": [2, 3],
        "who": [""],
        "issuer": None,
    }
)
_FIELDS = ("type_of_document", "requires_action", "extract_depth", "who")
_JOINT = "fields_match(predicted, ground_truth)"


def _classified(sid: int, said: str) -> MeasuredCell:
    cell: dict[str, Any] = {"query": f"q{sid}", "predicted": said, "ground_truth": _FIELDS_GOLD}
    return measurement(sid, None, **cell).facts


def test_each_graded_field_is_matched_alone_and_the_cell_reads_its_share() -> None:
    joint = scorer_of(_JOINT)
    for said, missed in (
        (
            '{"type_of_document":" Invoice ","requires_action":"TRUE","extract_depth":3,"who":" "}',
            [],
        ),
        (
            '{"type_of_document":"invoice","requires_action":"yes","extract_depth":2.0,"who":""}',
            ["requires_action"],
        ),
        (
            '{"type_of_document":"invoice","requires_action":true,"extract_depth":1,"who":null}',
            ["extract_depth", "who"],
        ),
        ('{"type_of_document":"invoice","requires_action":true,"extract_depth":"2"}', ["who"]),
        ("NO_RESULT", list(_FIELDS)),
    ):
        graded = joint.grade(_classified(0, said))
        hits = dict(graded.grade.field_hits or ())
        assert [name for name, hit in hits.items() if not hit] == missed, (
            f"{said!r} must miss exactly {missed}: case, spacing, a bool's or a number's spelling "
            "never decide, a listed gold accepts any of its values, '' is a value and a null "
            "gold grades nothing"
        )
        assert graded.grade.fitness == pytest.approx(1 - len(missed) / len(_FIELDS)), (
            "a cell's fitness is the share of its graded fields it got right"
        )
        assert GradedCell.from_wire(graded.wire()) == graded, "the per-field verdicts left the wire"


def test_theta_reads_each_cells_share_of_fields_and_the_fold_says_which_field_moved() -> None:
    joint = scorer_of(_JOINT)
    walked = joint.sheet(
        [
            _classified(
                0,
                '{"type_of_document":"invoice","requires_action":true,"extract_depth":3,"who":""}',
            ),
            _classified(
                1,
                '{"type_of_document":"receipt","requires_action":true,"extract_depth":1,"who":""}',
            ),
        ]
    )
    assert sorted(responses_of(walked).values()) == [0.5, 1.0], (
        "θ is fit on each cell's share of fields, never on one item per field"
    )
    assert compute_composite_fitness(walked, joint, _LLM_ONLY).field_accuracy == {
        "type_of_document": 0.5,
        "requires_action": 1.0,
        "extract_depth": 0.5,
        "who": 1.0,
    }, "the arm's fold says which field moved"


def test_a_gold_the_field_matcher_cannot_read_is_refused() -> None:
    joint = scorer_of(_JOINT)
    for refused in ("not json", '{"who": "x"}', '{"issuer": null}'):
        with pytest.raises(ScoringFormulaError):
            joint.grade(measurement(0, None, predicted="{}", ground_truth=refused).facts)


class _TokenCells(NamedTuple):
    solve: Callable[..., MeasuredCell]
    per_sample: str
    per_cell: str


@pytest.fixture
def token_cells() -> _TokenCells:
    """A `solve` cell priced by its tokens, and the per-sample and per-cell formulas grading it."""

    def solve(
        sid: int, said: str, tokens: int, usd: dict[str, float] | None = None, **wire: Any
    ) -> MeasuredCell:
        steps = {"solve": {"input": tokens, "output": 0, **(usd or {})}}
        cell: dict[str, Any] = {"query": "q", "predicted": said, "ground_truth": "a", "error": None}
        return measurement(sid, None, pipeline_data={"step_tokens": steps}, **cell, **wire).facts

    return _TokenCells(
        solve,
        "label_match(predicted, ground_truth)",
        "fitness * (0.92 + 0.08 * 692.0 / max(692.0, tokens))",
    )


def test_a_dial_reads_its_term_against_the_origin_and_never_sums_a_raw_unit(
    token_cells: _TokenCells,
) -> None:
    solve, per_sample, _ = token_cells

    def composite(scorer: Scorer, *cells: MeasuredCell) -> float | None:
        return compute_composite_fitness(scorer.sheet(cells), scorer, _LLM_ONLY).composite_fitness

    core = CELL_TERMS
    anchors = origin_anchors(
        sheet(
            GradedCell.from_wire(solve(0, said, t).wire()) for said, t in (("a", 600), ("b", 784))
        ),
        core,
    )
    assert anchors["tokens"] == pytest.approx(692.0), "the anchor is the origin's mean"
    dialled = scorer_of(
        per_sample,
        realize_dials(parse_dials("tokens=0.08, latency=0", core), anchors, core),
    )
    for tokens, expected in ((692, 1.0), (6920, 0.92 + 0.08 * 0.1), (100, 1.0)):
        assert composite(dialled, solve(0, "a", tokens)) == pytest.approx(expected), (
            f"a hit at {tokens} tokens reads its tokens against the origin's"
        )
    read_back = anchored_criterion_dials(
        realize_dials(parse_dials("tokens=0.08,cached=0.1,errored=0.2", core), anchors, core)
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


def test_a_miss_is_charged_its_cost_and_a_solved_cell_scores_its_composite(
    token_cells: _TokenCells,
) -> None:
    solve, per_sample, per_cell = token_cells
    charged = scorer_of(per_sample, per_cell)
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
    assert compute_composite_fitness(walked, charged, _LLM_ONLY).composite_fitness == pytest.approx(
        (solved_cheap + solved_costly + miss_cheap + miss_costly) / 4
    ), "the faulted cell left the mean"
    assert is_prior_dominated(rows[2:4]) and not is_prior_dominated(rows[1:4]), (
        "the 0% floor is a fact about correctness, never about a charged miss's objective"
    )
    assert is_prior_dominated(rows[:2]), "the ceiling is the floor's twin: a costly hit is a hit"

    def objectives(*cells: MeasuredCell) -> list[float]:
        return [_objective(r) for r in charged.sheet(cells)]

    solved = objectives(*(solve(i, "a", t) for i, t in enumerate((100, 692, 1000, 2076, 10**7))))
    assert solved[0] == solved[1] and solved[1:] == sorted(set(solved[1:]), reverse=True)
    costliest_hit, cheapest_miss = objectives(solve(0, "a", 10**7), solve(1, "b", 100))
    assert costliest_hit > cheapest_miss, "length made a miss outscore a hit"
    long_perfect = objectives(*(solve(i, "a", 10**6) for i in range(4)))
    short_half = objectives(*(solve(i, "ab"[i % 2], 100) for i in range(4)))
    assert sum(long_perfect) > sum(short_half), "length outranked a 2x accuracy gap"
    by_cost = scorer_of(per_sample, "fitness / (1.0 + cost)")
    at_rate, billed = by_cost.sheet(
        [solve(0, "a", 692, {"rate_priced_usd": 1.0}), solve(0, "a", 692, {"cost_usd": 3.0})]
    )
    assert (at_rate.grade.objective, billed.grade.objective) == (0.5, 0.25), (
        "a cost term reads the provider's bill, else our rate's price"
    )
    assert "cost" not in compiler.cell_channels_of(solve(0, "a", 692), None), (
        "most cells report no dollars, and an unpriced cell has no cost term"
    )
    assert "cost" not in compiler.cell_channels_of(solve(0, "a", 692, cost=9.0), None), (
        "a dataset observation named for a core channel filled it, undeclared"
    )


def test_a_connectors_declared_dial_parses_and_anchors_as_a_core_one_does() -> None:
    core = CELL_TERMS
    l4 = compiler.campaign_terms(connectors.CONNECTORS.get("promptpotter"))
    with pytest.raises(PayloadInvalidError):
        parse_dials("rounds=0.1", core)

    def inner(sid: int, rounds: int) -> GradedCell:
        report = {"floored": 0, "held_out_lift": 0.0, "rounds": rounds}
        row = measurement(sid, None, pipeline_data={"cell_report": report}).facts
        return GradedCell.from_wire(row.wire())

    l4_anchors = origin_anchors(sheet([inner(0, 3), inner(1, 5)]), l4)
    assert l4_anchors["rounds"] == pytest.approx(4.0), "the inner rounds dial lost its anchor"
    l4_dials = anchored_criterion_dials(
        realize_dials(parse_dials("rounds=0.2", l4), l4_anchors, l4)
    )
    assert l4_dials is not None and l4_dials["rounds"].anchor == pytest.approx(4.0)


async def _init(dataset_name: str, stores: Stores) -> None:
    async with init_services(dataset_name, identity=stores.identity, stores=stores):
        pass


def _probe_term(name: str, **rest: Any) -> CellTerm:
    return CellTerm(name=name, direction="high", description="A probe term.", **rest)


def _table_refuses(monkeypatch: pytest.MonkeyPatch, *probe: Connector) -> bool:
    with monkeypatch.context() as patched:
        try:
            serving(patched, *probe)
        except RuntimeError:
            return True
    return False


def _connector_refuses(monkeypatch: pytest.MonkeyPatch, **declared: Any) -> bool:
    return _table_refuses(monkeypatch, fake_connector("probe", **declared))


def _package_evaluator_refuses(monkeypatch: pytest.MonkeyPatch, name: str) -> bool:
    async def one(**_: Any) -> float:
        return 1.0

    ev = SampleEvaluator(name=name, description="A probe.", compute=one)
    with monkeypatch.context() as patched:
        patched.setattr(evaluators, "_SAMPLE_EVALUATORS", (*evaluators._SAMPLE_EVALUATORS, ev))
        try:
            evaluators._validate_registry()
        except ValueError:
            return True
        return _table_refuses(monkeypatch)


# The campaign's doors are run init's own: a connector reporting `rounds` as L4's does, a
# pipeline mapping `env_reward`.
def _reporting_connector() -> Connector:
    return fake_connector("reporting", reported_keys=("rounds",))


def _judges_refused(world: World, names: Iterable[str]) -> set[str]:
    with world.family() as held:
        held["pipeline"]["nodes"]["llm_only"]["optimizer"]["observation_mappings"].append(
            {"pipeline_key": "env_reward", "output_field": "env_reward"}
        )

    async def refused() -> set[str]:
        async with world.opened(connector=_reporting_connector()) as session:
            return {name for name in names if _judge_refuses(session, name)}

    return asyncio.run(refused())


def _judge_refuses(session: Session, name: str) -> bool:
    spec = JudgeSpec(
        name="sealqa",
        stages=[JudgeStage(role="grade", temperature=0.0, model="m", provider="openai")],
    )
    config = defaulted_config(
        {"optimization": {"degradation_threshold": 0.4}, "judges": {name: spec.model_dump()}}
    )
    try:
        scorer_setup(
            session,
            config,
            partition=partition_bank(session.bank.samples, config.dataset_split, seed=0),
        )
    except ValueError:
        return True
    return False


def _observation_refuses(monkeypatch: pytest.MonkeyPatch, name: str) -> bool:
    stores = workspace()
    mapping = [
        {"pipeline_key": "final_ranking", "output_field": "generated_text", "is_llm": True},
        {"pipeline_key": name, "output_field": name},
    ]
    write_yaml(
        stores.tenant_datasets.dataset_dir("observed") / DEFINITION_FILE,
        {
            "pipeline": {
                "connector": "reporting",
                "nodes": {"llm_only": {"optimizer": {"observation_mappings": mapping}}},
            }
        },
    )
    with monkeypatch.context() as patched:
        serving(patched, _reporting_connector())
        try:
            asyncio.run(_init("observed", stores))
        except PayloadInvalidError as exc:
            # Past this door, the next refusal is the dataset's empty bank.
            return "refused_observation_keys" in exc.details
    raise AssertionError("an observation door admitted a dataset holding no rows")


def _assert_owned(
    monkeypatch: pytest.MonkeyPatch, world: World, owned: tuple[tuple[str, set[str]], ...]
) -> None:
    doors: dict[str, Callable[[str], bool]] = {
        "connector term": lambda n: _connector_refuses(monkeypatch, cell_terms=(_probe_term(n),)),
        "reported key": lambda n: _connector_refuses(monkeypatch, reported_keys=(n,)),
        "package evaluator": lambda n: _package_evaluator_refuses(monkeypatch, n),
        "observation key": lambda n: _observation_refuses(monkeypatch, n),
    }
    judged = _judges_refused(world, (name for name, _ in owned))
    for name, admitted_at in owned:
        refused_at = {door for door, refuses in doors.items() if refuses(name)}
        refused_at |= {"campaign judge"} if name in judged else set()
        assert refused_at == NAMESPACE_DOORS - admitted_at, (
            f"{name!r} admitted where another owns it"
        )


NAMESPACE_DOORS = frozenset(
    {"connector term", "reported key", "package evaluator", "campaign judge", "observation key"}
)


# Unrefused, a judge named `max` breaks every `max(...)` formula, and a connector term
# `n_turns` reads its report on a dial and the turn count in a formula.
def test_a_fresh_name_is_admitted_by_every_door_to_the_cell_namespace(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    _assert_owned(monkeypatch, world, (("fresh_term", set(NAMESPACE_DOORS)),))


def test_a_name_another_owner_binds_is_refused_by_every_door_to_the_cell_namespace(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    _assert_owned(
        monkeypatch,
        world,
        (
            # Two owners served it once: the grade's rank, and a report's read on the channel alone.
            ("ground_truth_rank", set()),
            ("error", set()),  # the cell's message; `PipelineData.error` reaches no formula
            ("errored", set()),  # a row-health term: the row answers it
            ("predicted", set()),  # bound off the cell
            ("label_match", set()),  # a matcher
            ("max", set()),  # a builtin
            ("n_turns", set()),  # a turn scalar
            ("n_tool_calls", set()),
            ("retrieve_step_turns", set()),  # a step's turn count
            ("retrieval_shortfall", set()),  # a package evaluator
        ),
    )


def test_a_name_a_report_or_an_observation_may_fill_is_refused_only_where_another_owner_binds(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    _assert_owned(
        monkeypatch,
        world,
        (
            ("latency", {"reported key"}),  # a core term: a report answers its channel
            ("target_prompt_chars", {"observation key"}),  # a PipelineData field, never a report's
            ("total_time", {"observation key"}),  # a PipelineData key: an observation fills it
            # The L4 connector's term: another connector may declare it, nothing its campaigns read.
            ("rounds", {"connector term", "reported key"}),
            ("env_reward", set(NAMESPACE_DOORS - {"campaign judge"})),  # a campaign's observation
        ),
    )


def test_an_anchored_term_reading_higher_is_better_is_refused_at_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _connector_refuses(
        monkeypatch, cell_terms=(_probe_term("probe_anchor", dial="anchored"),)
    ), "an anchored term reading higher-is-better was registered: a dial reads it as a cost"


def test_a_reported_core_channel_is_one_value_on_a_dial_and_in_a_formula() -> None:
    spent = measurement(
        0,
        1.0,
        pipeline_data={
            "cell_report": {"cost": 99.0},
            "step_tokens": {"n": {"input": 1, "output": 1, "cost_usd": 2.0}},
        },
    )
    channel = compiler.cell_channels_of(spent.facts, spent.grade)["cost"]
    assert compiler.objective_namespace(spent.facts, spent.grade)["cost"] == channel == 2.0, (
        "a reported core channel read one value on a dial and another in a formula"
    )


def test_a_conversation_reaches_the_formula_only_as_projected_scalars() -> None:
    # A step named `n` keeps the total.
    turns = [
        {"index": 1, "source": "agent", "step": "retrieve", "tools": ["bash", "bash"]},
        {"index": 2, "source": "agent", "step": "retrieve", "tools": []},
        {"index": 3, "source": "agent", "step": "n", "tools": ["bash"]},
    ]
    scalars = turn_scalars(turns)  # type: ignore[arg-type]
    assert scalars == {
        "n_turns": 3.0,
        "n_tool_calls": 3.0,
        "retrieve_step_turns": 2.0,
        "n_step_turns": 1.0,
    }
    assert turn_scalars([]) == {}, "no conversation is absence, never a zeroed count"
    talked = measurement(0, 0.0, pipeline_data={"env_reward": 1.0, "turns": turns, **scalars})
    assert (
        scorer_of(
            "env_reward * (1.0 if n_turns <= 4 else 0.5) * min(1.0, retrieve_step_turns / 2.0)",
            verifier_graded=True,
        )
        .grade(talked.facts)
        .grade.fitness
        == 1.0
    ), "a formula reads the projected scalars"


def test_the_scorer_id_names_the_grading_function(
    token_cells: _TokenCells, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A ruler must never pool grades across two grading functions.
    solve, per_sample, per_cell = token_cells
    defaulted = scorer_of(per_sample, None).grade(solve(2, "b", 692))
    assert defaulted.grade.objective == 0.0, "only a DECLARED cell formula charges a miss"
    assert auto_scorer_id(per_sample, None, judge_instrument=None) != auto_scorer_id(
        per_sample, CRITERION_BASE, judge_instrument=None
    ), "the id, never the text, names the grader"
    before = auto_scorer_id(per_sample, per_cell, judge_instrument=None)
    assert auto_scorer_id(per_sample, per_cell, judge_instrument="a judge") != before, (
        "one text over two graders is two"
    )
    monkeypatch.setattr(compiler, "MISS_COST_SHARE", 0.3)
    assert auto_scorer_id(per_sample, per_cell, judge_instrument=None) != before, (
        "the miss share is half the grading function"
    )


def test_a_formula_is_two_valued_by_its_expression_over_what_emits() -> None:
    # A two-valued fitness takes φ = 1 and an exact band; read as graded, φ is FITTED on 0/1 cells.
    stage = JudgeStage(role="grade", temperature=0.0, model="m", provider="openai")
    judges = {
        "answer": JudgeSpec(name="simpleqa", stages=[stage]),
        "grounded": JudgeSpec(name=ANSWER_GROUNDING.name, stages=[stage]),
    }
    emitters = two_valued_terms(judges) | {"env_reward"}
    assert two_valued_terms(judges) == {"answer"}, "a judge scoring 0.5 is graded"
    for formula, two_valued in (
        ("label_match(predicted, ground_truth)", True),
        ("answer", True),
        ("max(0.0, min(1.0, env_reward))", True),
        ("label_match(predicted, ground_truth) * em_match(predicted, ground_truth)", True),
        ("0.0 if error else 1.0", True),
        ("0.5 * label_match(predicted, ground_truth)", False),
        ("grounded", False),
        ("rr(ground_truth_rank)", False),
    ):
        scorer = scorer_of(formula, two_valued_terms=emitters)
        assert ("fitness" in scorer.two_valued) is two_valued, formula


# 2. Composite fitness and coverage


def test_a_round_scores_the_cells_its_prompt_answered_and_never_an_absence_as_zero() -> None:
    # A round that measured every sample and failed them all IS a 0.0; an unmeasured one is not.
    for rate in (compute_fitness, compute_error_rate, compute_degraded_rate):
        assert rate(results=[]) is None, f"{rate.__name__} read an empty round as 0.0"
    empty = compute_composite_fitness(sheet([]), scorer_of(), _LLM_ONLY)
    assert (empty.composite_fitness, empty.fitness, empty.total) == (None, None, 0), (
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
    assert compute_fitness(results=[deprecated]) == 0.0, "a deprecated row is a measured miss"
    # A provider's fault is the ABSENCE of a verdict: out of the mean, still on the error channel.
    faulted = [asked(0, "a", 1.0), asked(0, "ERROR", error="boom", error_category="SERVER")]
    assert compute_fitness(results=faulted) == 1.0, "a provider fault entered the mean"
    assert compute_error_rate(results=faulted) == 0.5, "a provider fault left the error channel"
    # Every row ERRORED with no label to miss: no rate either (an L4 cell cut throughout is not 0%).
    unlabelled = asked(0, "ERROR", ground_truth="", error="boom", error_category="HALTED")
    all_errored = compute_composite_fitness(sheet([unlabelled]), scorer_of(), _LLM_ONLY)
    assert (all_errored.fitness, all_errored.composite_fitness, all_errored.total) == (
        None,
        None,
        0,
    ), "a round of faults has no score"

    # Only a provider fault leaves the count: a refusal and a cut cell are the prompt's own.
    labelled = scorer_of()
    cut: dict[str, Any] = {"error": "ran past its envelope", "error_category": "HALTED"}
    walked = [asked(i, "a") for i in range(5)]
    walked += [asked(5 + i, "I cannot help with that request.") for i in range(5)]
    walked += [
        asked(10, "ERROR", **cut),
        asked(11, "ERROR", error="HTTP 502", error_category="SERVER"),
    ]
    scores = compute_composite_fitness(
        labelled.sheet([c.facts for c in walked]), labelled, _LLM_ONLY
    )
    assert (scores.total, len(walked) - scores.total) == (11, 1), "only the provider fault left"
    assert scores.fitness == pytest.approx(5 / 11), "the prompt's own failures left the count"
    assert scores.composite_fitness == pytest.approx(scores.fitness), (
        "the default formula is plain accuracy, so the decision metric and the headline agree"
    )
    assert not labelled.grade(asked(10, "ERROR", ground_truth="", **cut).facts).scored, (
        "with no label a cut cell has no miss to be, so it carries no verdict either"
    )


def test_a_judge_never_grades_or_bills_a_cell_whose_answer_is_the_no_result_sentinel(
    offline: Router,
) -> None:
    offline.llm.says = lambda _body: "A"
    # A cell that RAN and left a trace, so the no-trace arm cannot be what fired.
    traced = MeasuredCell(
        sample_key="k0",
        predicted=NO_RESULT,
        pipeline=PipelineData(reasoning_trace="searched the docs, found the founding date"),
    )
    spec = JudgeSpec(
        name="answer_grounding",
        stages=[JudgeStage(role="grade", temperature=0.0, model="m", provider="openai")],
    )

    async def grounded() -> JudgeVerdict:
        return await ANSWER_GROUNDING.grade(spec, traced)

    verdict = asyncio.run(grounded())
    assert verdict.score is None, f"a sentinel answer was graded as {verdict.label!r}"
    billed = offline.llm.sent
    assert billed == [], f"a cell with no answer was billed a grading: {billed}"


def test_a_merge_never_shrinks_what_was_measured_and_joins_only_its_own_scorer() -> None:
    prior = sheet(measurements([1.0 if i < 10 else 0.0 for i in range(20)]))
    winner = sheet(
        measurements([1.0 if sid in {10, 12, 15} else 0.0 for sid in range(10, 18)], range(10, 18))
    )
    merged = prior.merged(winner)
    by_sid = {int(r.key): r for r in merged}
    assert set(by_sid) == set(range(20)), "a merge dropped a measured cell"
    assert [by_sid[i].hit for i in (10, 11, 19)] == [True, False, False], "the later read stands"
    assert all(by_sid[i].hit is True for i in range(10)), "a merge rewrote an untouched cell"
    assert prior.merged(NO_CELLS) == prior, "an empty merge moved something"
    # The declaration rides the merge: a sheet another scorer graded cannot join.
    assert (merged.scorer_id, merged.two_valued) == (prior.scorer_id, prior.two_valued)
    with pytest.raises(ValueError, match="cannot join"):
        prior.merged(sheet(winner, scorer_id="another"))


def test_a_walk_fill_keeps_the_paid_row_over_its_replay_and_the_scored_over_the_errored() -> None:
    paid, replay = measurement(0, 1.0), measurement(0, 1.0, cached=True)
    errored = measurement(0, None, error="504: no reply", error_category="SERVER")
    scored = measurement(0, 0.0)
    # held row, the re-read's row -> the row the walk files (a later read never wins by position)
    rows = [
        (paid, replay, paid),  # bought, then re-read as parent: `MemberRows.bought` keeps it
        (replay, paid, paid),  # caught up after its replay
        (errored, scored, scored),
        (scored, errored, scored),
        (scored, measurement(0, 1.0), scored),  # two paid scored rows: the held one stands
    ]
    for held, read, stands in rows:
        [stood] = sheet([held]).filled_from(sheet([read]))
        assert stood is stands, f"{held} re-read as {read} filed {stood}"
    new = sheet([paid]).filled_from(sheet([measurement(1, 0.0)]))
    assert [c.key for c in new] == ["0", "1"], "a re-read dropped a cell the walk lacked"


# 3. Electing a round winner


def _charged_by_length() -> tuple[CellSheet, CellSheet]:
    # A length-priced composite: 14 verdicts won and one lost; every tie is the longer answer's loss.
    scorer = scorer_of(
        "label_match(predicted, ground_truth)",
        "fitness * (0.92 + 0.08 * 692.0 / max(692.0, tokens))",
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


def _bench_pass(
    stores: Stores,
    individual: str,
    reward: float | list[float],
    *,
    cells: int = 4,
    stop: PassStop | None = None,
) -> BenchPass:
    """*reward* is every row's, or each row's in turn."""
    held_out = samples(4, query="b")
    rewards = reward if isinstance(reward, list) else [reward] * len(held_out)
    rows = [
        measurement(
            s.id,
            None,
            query=s.query,
            sample_key=f"key:{s.query}",
            pipeline_data={"env_reward": paid},
        )
        for s, paid in zip(held_out[:cells], rewards, strict=False)
    ]
    return bench_pass(
        stores,
        individual,
        rows,
        [f"key:{s.query}" for s in held_out],
        stop=stop,
        scorer_id=auto_scorer_id(_H2H_FORMULA, None, judge_instrument=None),
    )


def _easy_low_hard_high() -> Any:
    """One δ ruler: easy cells {0..19} sit low, hard {20..39} high."""
    return delta_ruler({i: (-1.5 if i < 20 else 1.5) for i in range(40)})


def _level_parent() -> tuple[Any, CellSheet]:
    """A flat 28-cell ruler, and a parent that solved half of it."""
    return delta_ruler(dict.fromkeys(range(28), 0.0)), sheet(measurements([1.0] * 14 + [0.0] * 14))


def _elect(
    keys: list[ArmKey],
    rows: dict[ArmKey, CellSheet],
    parent: CellSheet,
    coverage_floor: int,
    ruler: Any,
    *,
    bar: CrowningBar,
) -> tuple[ArmKey | None, RoundAbilities]:
    """The election on the fit the round measures of the same rows."""
    abilities = candidate_abilities(rows, parent, ruler)
    vote = _winner(keys, ArmKey("p"), bar=bar, coverage_floor=coverage_floor)
    return _crown(vote, rows, parent, abilities), abilities


_PAIRS = RoundPairs(
    path=(CycleHop(campaign_id="camp", cycle_id="cyc"),),
    instrument_id="bank",
    dataset_hash="ds",
    measurands=(
        Measurand(
            kind=MeasurandKind.GRADE, key="fitness", scorer_id="test", binary=True, unit="rate"
        ),
    ),
)


def _winner(
    arms: list[ArmKey],
    parent: ArmKey,
    *,
    bar: CrowningBar,
    next_parent: NextParent = "crowned",
    coverage_floor: int = 6,
    cells: list[str] | None = None,
    scorer_id: str = "test",
) -> RoundWinner:
    fitness = _PAIRS.measurands[0].model_copy(update={"scorer_id": scorer_id})
    return RoundWinner(
        arms=arms,
        coverage_floor=coverage_floor,
        bar=bar,
        next_parent=next_parent,
        parent=parent,
        cells=cells,
        pairs=_PAIRS.model_copy(update={"measurands": (fitness,)}),
    )


def _decide(
    vote: RoundWinner,
    rows: Mapping[ArmKey, CellSheet],
    parent: CellSheet,
    abilities: RoundAbilities,
) -> RoundDecision:
    pairings = vote.pairings({**rows, vote.parent: parent}, {})
    return vote.decide(vote.subjects(pairings, abilities), abilities)


def _crown(
    vote: RoundWinner,
    rows: Mapping[ArmKey, CellSheet],
    parent: CellSheet,
    abilities: RoundAbilities,
) -> ArmKey | None:
    decided = _decide(vote, rows, parent, abilities)
    return decided.leading if decided.ending is RoundEnding.CROWNED else None


def test_a_round_winner_elects_by_ability_not_subset_accuracy() -> None:
    ruler = _easy_low_hard_high()
    bar = CrowningBar(statistic="posterior", admission=election_knobs().admission)
    with pytest.raises(ValidationError):
        # One discordant cell supports 0.75.
        election_knobs().model_validate(election_knobs().model_dump() | {"admission": 0.70})
    weak_on_easy = [measurement(i, float(i < 16)) for i in range(20)]
    able_on_hard = [measurement(i, float(i < 34)) for i in range(20, 40)]
    by_id = {
        ArmKey("weak_on_easy"): sheet(weak_on_easy),
        ArmKey("able_on_hard"): sheet(able_on_hard),
    }
    assert sum(r.hit for r in weak_on_easy) > sum(r.hit for r in able_on_hard)
    winner_id, abilities = _elect(
        list(by_id),
        by_id,
        sheet(measurement(i, float(i < 20)) for i in range(40)),
        4,
        ruler,
        bar=bar,
    )
    assert winner_id == "able_on_hard", "a round winner elects by ability, not subset accuracy"
    assert abilities.theta[ArmKey("able_on_hard")] > abilities.theta[ArmKey("weak_on_easy")]
    # 6 hits far below θ, 13 misses far above, one miss near it the arm gained.
    spread = delta_ruler(
        {**dict.fromkeys(range(6), -2.8), 6: 1.0, **dict.fromkeys(range(7, 20), 4.0)}
    )
    one_cell = {ArmKey("one"): sheet(measurement(i, float(i < 7)) for i in range(20))}
    parent_six = sheet(measurement(i, float(i < 6)) for i in range(20))
    assert _elect(list(one_cell), one_cell, parent_six, 6, spread, bar=bar)[0] is (None), (
        "one discordant cell of 20 promoted: a lift its own paired evidence cannot carry"
    )


def test_a_thin_arm_never_wins_on_a_margin_inside_its_own_noise() -> None:
    # `coverage_floor` IS PoBB's `n_min`.
    ruler, bar = (
        _easy_low_hard_high(),
        CrowningBar(statistic="posterior", admission=election_knobs().admission),
    )
    flat, half = _level_parent()
    deep = sheet(measurements([1.0] * 21 + [0.0] * 7))
    shallow = sheet(measurements([1.0] * 5 + [0.0], range(14, 20)))
    deep_key, shallow_key = ArmKey("deep"), ArmKey("shallow")
    thin_arms = {deep_key: deep, shallow_key: shallow}
    ab = candidate_abilities(thin_arms, half, flat)
    lifts = {c: theta_lift_over_parent(ab, c) for c in thin_arms}
    p = {c: p_better(lift) for c, lift in lifts.items() if lift is not None}
    assert ab.theta[shallow_key] > ab.theta[deep_key], "the thin arm's POINT lift is the larger"
    assert ab.theta_se[shallow_key] > 1.8 * ab.theta_se[deep_key], "on nearly twice the SE"
    assert p[deep_key] > p[shallow_key], "so it demonstrated less"
    assert _elect(list(thin_arms), thin_arms, half, 6, flat, bar=bar)[0] == ("deep"), (
        "a thin arm won"
    )
    assert _elect([shallow_key], {shallow_key: shallow}, half, 6, flat, bar=bar)[0] == (
        "shallow"
    ), "ranking on P must never DISQUALIFY: a lone wide-posterior gain still wins"
    behind = sheet(measurements([1.0] * 4 + [0.0] * 2))
    b = ArmKey("b")
    behind_lift = theta_lift_over_parent(candidate_abilities({b: behind}, half, flat), b)
    assert behind_lift is not None and behind_lift.value > 0.0
    assert _elect([b], {b: behind}, half, 6, flat, bar=bar)[0] is None, (
        "a θ lift over the parent's whole panel never crowns an arm that LOST the cells it shares"
    )
    swapped = sheet(measurement(i, float(0 < i < 20 or i == 20)) for i in range(40))
    assert (
        _elect(
            [ArmKey("s")],
            {ArmKey("s"): swapped},
            sheet(measurement(i, float(i < 20)) for i in range(40)),
            4,
            ruler,
            bar=bar,
        )[0]
        is None
    ), "one cell won and one lost is a tie, whatever the δ weighting or the fit's last digit"
    longer, terse_parent = _charged_by_length()
    level = delta_ruler(dict.fromkeys(range(34), 0.0))
    l_key = ArmKey("l")
    lengthy = _winner([l_key], ArmKey("p"), bar=bar, scorer_id=terse_parent.scorer_id)
    abilities = candidate_abilities({l_key: longer}, terse_parent, level)
    assert _crown(lengthy, {l_key: longer}, terse_parent, abilities) == "l", (
        "a cost term's tie-break was counted as a verdict lost"
    )


def test_a_crowned_picks_lucky_cells_stay_out_of_every_race_it_judges() -> None:
    # The bar is what the parent can do, not the luck that crowned it: no later race reads those cells.
    crowned_on = sheet(measurements([1.0] * 4 + [0.0], range(30, 35)))
    bar_rows = sheet(measurements([0.0, 0.0, 1.0, 1.0, 0.0], range(30, 35)))
    c0 = arm_key("c0")

    def member(key: ArmKey, rows: CellSheet) -> MemberRows:
        return _PAIRS.member(key, rows, name=None, role=None, cut=False)

    pair = _PAIRS.read(
        member(PARENT, bar_rows),
        member(c0, crowned_on),
        cell_set=CellSetName.MEASURED_BY_BOTH,
        cells=None,
        measurands=_PAIRS.measurands,
    )
    voted = {"electable": 1, "deciding_pair": pair}
    won = round_result(1, voted=voted, cells={c0: crowned_on, PARENT: bar_rows})
    assert crowning_cells([won, round_result(2, improved=False)], c0, {}) == {
        crowned_on.cells[0].key,
        crowned_on.cells[1].key,
    }, "a crowned pick's discordant wins stay out of every race it judges, a lone arm's included"
    assert not crowning_cells([won], arm_key("c1"), {}), (
        "an ancestor's are its child's own measurements"
    )
    unbanked = round_result(1, voted={"electable": 1}, cells={c0: crowned_on, PARENT: bar_rows})
    assert not crowning_cells([unbanked], c0, {}), "a crown read no pairing it never banked"
    # Under `overlap` the pair read the panel alone: a win off it is no crowning cell.
    on_panel = _PAIRS.read(
        member(PARENT, bar_rows),
        member(c0, crowned_on),
        cell_set=CellSetName.ORIGIN_PANEL,
        cells=["31", "32", "33"],
        measurands=_PAIRS.measurands,
    )
    panel_won = won.model_copy(
        update={
            "election": won.election
            and won.election.model_copy(update={"deciding_pair": on_panel}),
            "overlap": won.overlap.model_copy(update={"positions": (31, 32, 33)}),
        }
    )
    key_of = {slot: str(slot) for slot in range(30, 35)}
    assert crowning_cells([panel_won], c0, key_of) == {"31"}, (
        "an overlap crown's draw-only wins left the next draw"
    )


def test_an_overlap_vote_elects_on_the_origin_panel_an_arm_its_draw_held_back() -> None:
    # Campaign bigbench-multistep-d22x4-8b__f866d6, round 5, arm C5.3: θ lift +0.504, se 0.458.
    c53, parent = ArmKey("C5.3"), ArmKey("parent")
    # Cells 0-9 are the panel, won 7-3; cells 10-14 lost; 15-24 tied: the draw reads 7-8.
    rows = sheet(measurements([1.0] * 7 + [0.0] * 8 + [1.0] * 10))
    bar = sheet(measurements([0.0] * 7 + [1.0] * 18))
    panel = sorted(str(i) for i in range(10))
    abilities = RoundAbilities(
        theta={c53: 0.504}, theta_se={c53: 0.458}, bar={c53: (0.0, 0.0)}, cells={c53: 25}
    )
    crowning = CrowningBar(statistic="posterior", admission=election_knobs().admission)
    for cells, elected, why in (
        (None, None, "the draw elected an arm that lost more of its cells than it won"),
        (panel, c53, "the overlap vote held back an arm that won its panel rows 7-3"),
    ):
        vote = _winner([c53], parent, bar=crowning, cells=cells)
        # The banked inputs are what a replay re-elects on: the live vote is this same call.
        assert _crown(vote, {c53: rows}, bar, abilities) == elected, why


def test_a_point_reading_crowns_the_green_bar_its_interval_spans() -> None:
    # Campaign justlogic.d234__0dcfd7, round 1, C1.3: panel 18 vs 16 of 34 (3 gained, 1 lost),
    # its draw tied, θ level (lift -0.001). Arm b gains 2, loses 1, on a θ lift of +0.5.
    a, b, parent = ArmKey("C1.3"), ArmKey("b"), ArmKey("parent")
    draw = [float(i % 2) for i in range(34)]
    held = [1.0] * 16 + [0.0] * 18
    rows = {
        parent: sheet(measurements(held + draw)),
        a: sheet(measurements([*held[:15], 0.0, 1.0, 1.0, 1.0, *held[19:], *draw])),
        b: sheet(measurements([*held[:15], 0.0, 1.0, 1.0, *held[18:], *draw])),
    }
    panel = sorted(str(i) for i in range(34))
    abilities = RoundAbilities(
        theta={a: 0.092, b: 0.593},
        theta_se={a: 0.18, b: 0.18},
        bar={a: (0.093, 0.18), b: (0.093, 0.18)},
        cells={a: 68, b: 68},
    )
    admission = election_knobs().admission
    flipped = {**rows, a: rows[parent], parent: rows[a]}
    for bar, both, alone in (
        (CrowningBar(statistic="paired_point", admission=admission), a, a),
        (CrowningBar(statistic="posterior", admission=admission), None, None),
    ):
        for arms, on, crowned in (([a, b], rows, both), ([a], rows, alone), ([a], flipped, None)):
            vote = _winner(arms, parent, bar=bar, cells=panel)
            assert _crown(vote, on, on[parent], abilities) == crowned, (
                f"{bar.statistic} over {arms}: a net panel loss, a level θ or the rank moved"
            )


def test_a_round_crowning_none_moves_on_only_to_a_leader_its_parent_did_not_beat() -> None:
    # On the fixed panel paired lifts add: a step onto a leader that did not lose never falls below
    # the crown the line holds, and a pick is only ever a crown.
    parent, tied, lost, won = ArmKey("parent"), ArmKey("tied"), ArmKey("lost"), ArmKey("won")
    base = [1.0] * 10 + [0.0] * 10
    rows = {
        parent: sheet(measurements(base)),
        tied: sheet(measurements([0.0, 1.0, *base[2:10], 1.0, *base[11:]])),
        lost: sheet(measurements([0.0, 0.0, *base[2:10], 1.0, *base[11:]])),
        won: sheet(measurements([*base[:10], 1.0, *base[11:]])),
    }
    flat = delta_ruler(dict.fromkeys(range(20), 0.0))
    bar = CrowningBar(statistic="paired_point", admission=election_knobs().admission)
    crowned, moved, held = RoundEnding.CROWNED, RoundEnding.MOVED, RoundEnding.HELD
    table: tuple[tuple[list[ArmKey], NextParent, RoundEnding, ArmKey | None], ...] = (
        ([won, tied], "not_lost", crowned, won),
        ([tied], "not_lost", moved, tied),
        ([lost], "not_lost", held, None),
        ([lost], "leading", moved, lost),
        ([tied], "crowned", held, None),
    )
    for arms, next_parent, ending, stands_on in table:
        on = {k: rows[k] for k in arms}
        abilities = candidate_abilities(on, rows[parent], flat)
        vote = _winner(arms, parent, bar=bar, next_parent=next_parent)
        decided = _decide(vote, on, rows[parent], abilities)
        assert decided.ending is ending, f"{next_parent} over {arms}"
        assert vote.stands_on([decided.ending, decided.leading]) == stands_on, (
            "a replay's counterfactual is not the arm the round ends on"
        )


def test_a_round_that_crowns_none_hands_on_its_leader_and_keeps_its_crown() -> None:
    c0, c1, origin = arm_key("c0"), arm_key("c1"), ArmKey("origin")
    crowned = round_result(1)
    held = round_result(2, improved=False)
    stepped = round_result(2, improved=False, voted={"moved_to": c1})
    assert line_reference([crowned, held], origin=origin) == RoundArm(round=1, arm=c0), (
        "a round that ended on the parent it re-read moved the line"
    )
    assert line_reference([crowned, stepped], origin=origin) == RoundArm(round=2, arm=c1), (
        "the leader a round ended on is not what the next round stands on"
    )
    assert declared_selection([crowned, stepped]) is crowned, "an uncrowned leader became the pick"


def test_an_overlap_vote_elects_only_an_arm_the_panel_trigger_seats() -> None:
    def arm(name: str, idx: int, cut: CutKind | None = None) -> Concluded:
        return arm_state(name, position=ArmPosition(round=1, idx=idx, total=3), cut=cut)

    arms = {
        ArmKey("walked"): arm("walked", 0),
        ArmKey("stopped"): arm("stopped", 1, "eliminated"),
        ArmKey("thin"): arm("thin", 2),
    }
    rows = {
        ArmKey("walked"): sheet(measurements([1.0] * 8)),
        ArmKey("stopped"): sheet(measurements([1.0] * 8)),
        ArmKey("thin"): sheet(measurements([1.0] * 3)),
    }
    keys = list(arms)
    assert seated("draw", keys, arms, rows, 6) == keys, "the draw moved"
    assert seated("overlap", keys, arms, rows, 6) == [ArmKey("walked")], (
        "an arm the origin panel holds no reading of stayed electable on it"
    )


def test_informative_rows_without_an_overlap_vote_are_refused_at_every_selection() -> None:
    for optimizer, nodes, refused in (
        ("potter", {"theta_election": {"config": {"vote": "draw"}}}, True),
        ("capo-pobb", {"pobb": {"config": {"row_choice": "informative"}}}, True),
        ("potter", {}, False),
        ("capo-pobb", {}, False),
    ):
        # The one door a check-in, a mint and a resume's laid overlay each select through.
        choice = defaulted_config({"optimization": {"optimizer": optimizer, "nodes": nodes}})
        if not refused:
            select_optimizer(choice.optimization)
            continue
        with pytest.raises(PayloadInvalidError) as caught:
            select_optimizer(choice.optimization)
        assert caught.value.code == "coupling_refused", f"{optimizer}: refused for another reason"
        assert "optimization.nodes.pobb.config.row_choice" in str(caught.value), (
            f"{optimizer}: the refusal never named the knob that laid it"
        )


def test_an_epsilon_a_tied_field_reaches_is_named_before_it_cuts_the_leader() -> None:
    """PoBB's shares sum to 1: a parent and k arms tie at 1/(k+1), and an ε there cuts a tied leader."""
    for epsilon, n_variants, collides in ((0.3, 3, True), (0.25, 3, True), (0.3, 2, False)):
        nodes = {
            "pobb": {"config": {"epsilon": epsilon, "epsilon_floor": epsilon}},
            "l1_generate": {"config": {"n_variants": n_variants}},
        }
        config = defaulted_config({"optimization": {"optimizer": "potter", "nodes": nodes}})
        named = check_couplings(config, select_optimizer(config.optimization))
        assert any(c.severity == "collision" for c in named) is collides, (
            f"ε {epsilon} over {n_variants} arms and their parent: the tie the check reads moved"
        )


def test_a_held_round_leads_on_the_readable_arm_nearest_the_bar_admission_reads() -> None:
    bar, deep_key = (
        CrowningBar(statistic="posterior", admission=election_knobs().admission),
        ArmKey("deep"),
    )
    flat, half = _level_parent()
    twenty = sheet(measurements([1.0] * 20 + [0.0] * 8))
    near_key = ArmKey("near")
    arms = {
        near_key: sheet(measurements([1.0] * 11 + [0.0] * 5)),  # closest to the bar, on a wide SE
        deep_key: sheet(measurements([1.0] * 18 + [0.0] * 10)),  # further off, on the whole panel
        ArmKey("thin"): sheet(measurements([1.0] * 3)),  # the highest θ, under the floor
    }
    held_id, held_abilities = _elect(list(arms), arms, twenty, 6, flat, bar=bar)
    reads = readable_lifts(list(arms), 6, held_abilities)
    assert held_id is None, "no arm cleared the bar"
    thin = ArmKey("thin")
    assert held_abilities.theta[thin] > held_abilities.bar[thin][0], "the floor, not θ, bars thin"
    assert set(reads) == {"near", "deep"}, "an arm under the floor is not readable"
    assert reads[deep_key].value < reads[near_key].value <= 0.0
    vote = _winner(list(arms), ArmKey("p"), bar=bar)
    subjects = vote.subjects(vote.pairings({**arms, vote.parent: twenty}, {}), held_abilities)
    cells = {key: vote.covered(key, s, held_abilities) for key, s in subjects.items()}
    assert ranked_first(bar, subjects, cells) == "near", "the lead is named on the bar's rank"
    off = ArmKey("off")
    cold = candidate_abilities({off: sheet(measurements([1.0] * 8, range(26, 34)))}, twenty, None)
    assert cold.cells[off] == 2 and not readable_lifts([off], 6, cold), (
        "cold, the floor counts the cells θ was READ on — those shared with the parent"
    )
    warm = candidate_abilities({off: sheet(measurements([1.0] * 8, range(24, 32)))}, half, flat)
    assert warm.cells[off] == 4, "warm, the floor counts only the cells the ruler carries"
    del subjects[near_key]
    assert ranked_first(bar, subjects, cells) == "deep"


def test_only_an_invalid_measurement_bars_an_arm_from_leading() -> None:
    # A STOP IS NOT A VERDICT.
    for arm, eligible in (
        (arm_state("C1.3", cut="broken"), False),
        (arm_state("C1.5", invalid_reason=ValidationReason.NO_OP_VARIANT), False),
        (
            arm_state(
                "C1.2",
                cut="eliminated",
                elimination={"p_best": 0.048, "epsilon": 0.05, "gate": "epsilon"},
            ),
            True,
        ),
        (
            arm_state("C1.1", locked_in=True, elimination={"gate": "settled"}),
            True,
        ),
        (arm_state("C1.4"), True),
    ):
        assert is_leader_eligible(arm) is eligible, f"{arm.label} decides leading wrongly"


def test_a_survey_rounds_starts_and_reference_are_arms_and_its_matrix_their_cells() -> None:
    starts = {arm_key(f"c{i}"): sheet(measurements([1.0, 0.0, float(i % 2)])) for i in range(3)} | {
        PARENT: sheet(measurements([0.0] * 3))
    }
    survey = round_result(1, candidates_scored=3, improved=False, cells=starts)
    assert {key: survey.rows(key) for key in survey.arms} == starts, (
        "a survey arm's rows are its cells, the reference's among them"
    )
    assert survey.results == starts[PARENT], "a held survey headlines its reference's cells"
    c1 = arm_key("c1")
    assert [c.key for c in InRunCells([survey]).sheet(c1, RoleScope.DECISION)] == [
        c.key for c in starts[c1]
    ], "a decision reads a survey start off the same cells"


class _Composition(NamedTuple):
    name: str
    walk: list[str]
    composed: Callable[[list[str]], AbstractContextManager[None]]
    crossover_says: Callable[[dict[str, Any]], str]
    crossed: list[str]
    composed_nodes: dict[str, Any]


@pytest.fixture
def peer_composition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Composition:
    """CAPO's manifest shadowed from an operator's home over any walk, and its crossover's replies."""
    name = "capo"
    capo = yaml.safe_load((optimizers_root() / "capo" / "pipeline.yaml").read_text("utf-8"))
    potter = yaml.safe_load((optimizers_root() / "potter" / "pipeline.yaml").read_text("utf-8"))
    # Potter's race beside capo's selector runs on the draw, as capo-pobb lays it.
    switch = yaml.safe_load((optimizers_root() / "capo-pobb" / "pipeline.yaml").read_text("utf-8"))
    potter["nodes"]["pobb"]["config"] |= switch["swap"]["paired_t"]["config"]
    seed = ["capo_init"]
    walk = ["blocks", "mating", "capo_crossover", "pobb", "score", "population"]
    homes = count()

    @contextmanager
    def composed(walk: list[str]) -> Iterator[None]:
        # Shadows capo from an operator's home, as an install's edit does; one home per variant.
        home = tmp_path / f"home{next(homes)}"
        manifest_dir = home / "optimizers" / name
        manifest_dir.mkdir(parents=True)
        # The demo pool's initializer is the shared pools package's, a third registrant beside the two.
        init = ["demo_pool"]
        nodes = {n: (potter["nodes"] | capo["nodes"])[n] for n in (*init, *seed, *walk)}
        (manifest_dir / "pipeline.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": name,
                    "version": "v0",
                    "available_models": capo["available_models"],
                    "main": [],
                    "nodes": nodes,
                    "pipelines": {
                        "run_init": init,
                        "default": walk,
                        "initial_population": seed,
                    },
                    "initial_population_walks": "before_default",
                    "resolved_prompts": {
                        key: capo["resolved_prompts"][key]
                        for key in ("capo_init/1", "capo_crossover/1")
                    },
                }
            ),
            "utf-8",
        )
        with monkeypatch.context() as shadowing:
            shadowing.setenv("PROMPTPOTTER_HOME", str(home))
            yield

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
    return _Composition(name, walk, composed, crossover_says, crossed, composed_nodes)


def test_potters_queue_in_a_peers_roster_sizes_its_panel_off_its_own_knobs(
    peer_composition: _Composition,
) -> None:
    name, walk, composed, *_ = peer_composition
    with composed(["adaptive_queue", *walk[1:]]):
        queued = select_optimizer(
            OptimizerChoice(
                call_target=BENCH_TARGET,
                optimizer=name,
                nodes={"adaptive_queue": ManifestNodeOverlay(config={"sp_budget_round": 7})},
            )
        )
    assert queued.round_cells(40) == 7, (
        "potter's queue, the one potter member in a peer's roster, sized its panel off another's knobs"
    )


def test_capo_inits_reply_out_of_form_seeds_nothing_and_never_crashes() -> None:
    # `CapoInit.propose` books a None as its one refused child: an array of blanks, read as in
    # form, left the round without one arm or a word; a reply nested past the parser's stack
    # crashed the init.
    for reply, seeded in (
        ('Here: ["  A.", "", "B."]', ["A.", "B."]),
        (json.dumps(["  ", ""]), None),
        ("[" * 5000 + "]" * 5000, None),
        ("[" + "1+" * 20000 + "1]", None),
        ("no array at all", None),
    ):
        assert seed_instructions(reply) == seeded, f"{reply[:20]!r} seeded {seeded}"


def test_a_crossover_donor_gives_a_minority_of_the_loci_the_two_differ_on() -> None:
    # A crossover child continues its main.
    solve = pipeline_schema([PipelineNode(name="solve", tunes_llm=False, prompt_info=PROMPT_INFO)])
    for differing in range(1, len(PROMPT_FIELDS) + 1):
        pair = Cross(*(prompted(**dict.fromkeys(PROMPT_FIELDS[:differing], side)) for side in "md"))
        sizes = Counter(
            None if (take := donor_take(pair, solve, random.Random(seed))) is None else len(take)
            for seed in range(40)
        )
        most = most_donated(differing)
        assert set(sizes) == ({None} if differing < 2 else set(range(1, most + 1))), (
            f"over {differing} differing loci the donor gave {dict(sizes)}"
        )
        assert differing < 2 or 2 * most < differing or (differing, most) == (2, 1), (
            f"over {differing} differing loci the donor may give {most}: the main is no longer major"
        )


class _Filed(NamedTuple):
    stores: Stores
    hop: CycleHop

    @property
    def cycle_dir(self) -> Path:
        return self.stores.campaigns.cycle_dir(self.hop)


@pytest.fixture
def filed_cycle(world: World) -> _Filed:
    """A campaign minted by the world's real mint: what a command door finds."""
    return _Filed(world.stores, asyncio.run(world.minted()))


async def _held_pass(
    filed: _Filed, stop: Callable[[], Awaitable[object]], acting: str | None = None
) -> None:
    stores = filed.stores
    async with paid_pass(
        stores=stores,
        pass_kind=MeasurementRole.BENCH,
        at=stores.campaigns.campaign_cycle(filed.hop),
        declared=Budget(),
        sends=(),
        acting=acting,
    ):
        await stop()
        # What the walk and every wait inside a pass do once its cancel stands.
        walk = RunControl(
            commands=stores.commands,
            enclosing=(stores.subject(filed.hop),),
            acting=() if acting is None else (acting,),
        )
        if walk.pause_requested() and (abort := run_scope().abort_check) is not None and abort():
            raise asyncio.CancelledError("aborted")
        raise AssertionError("a stop left the pass running")


def _cancels(stores: Stores) -> list[object]:
    book = stores.commands.book()
    return [
        record.payload.command_id
        for _, record in book.lines()
        if isinstance(record.payload, CancelPayload) and book.status(record.command_id) == "applied"
    ]


def _passing(
    filed: _Filed, stop: Callable[[], Awaitable[object]]
) -> Callable[[Any], Awaitable[Any]]:
    async def _verify(dispatched: Any) -> Applier[None]:
        return Applier(lambda: _held_pass(filed, stop, dispatched.command_id))

    return _verify


def _verify_call(hop: CycleHop, candidate_id: str) -> CommandCall[Any]:
    return CommandCall(
        VerifyCandidatePayload(
            campaign_id=hop.campaign_id,
            cycle_id=hop.cycle_id,
            candidate_id=candidate_id,
        ),
        door=Door.HOST,
    )


async def _cancel_command(stores: Stores, command_id: str) -> str:
    sent = CommandCall(CancelPayload(command_id=command_id), door=Door.HOST)
    return (await CommandDispatcher(stores).dispatch(sent)).ack


# The cancel after the pass ended names the task the host cancel opened.
def test_a_host_cancel_stops_a_pass_by_its_commands_cancel(
    monkeypatch: pytest.MonkeyPatch, filed_cycle: _Filed
) -> None:
    stores, cycle_dir = filed_cycle.stores, filed_cycle.cycle_dir
    host = HostSession(Caller(stores))
    held_task: dict[str, str] = {}
    # The act runs on past the answer, so it can reach its pass before the host holds the id.
    answered = asyncio.Event()

    async def _cancel_held() -> HostTask:
        await answered.wait()
        return await host.answer(CancelTask(held_task["held"]))

    async def _host_cancel() -> tuple[HostTask, str]:
        sent = {
            "campaign_id": filed_cycle.hop.campaign_id,
            "cycle_id": filed_cycle.hop.cycle_id,
            "candidate_id": "held",
            IDEMPOTENCY_KEY: "held",
        }
        opened = await host.answer(CallTool("verify-candidate", sent))
        assert opened.task is not None
        held_task["held"] = opened.task.task_id
        answered.set()
        with pytest.raises(PotterError) as stopped:
            await host.answer(TaskResultOf(opened.task.task_id))
        return await host.answer(TaskStatusOf(opened.task.task_id)), stopped.value.code

    before = len(_cancels(stores))
    with monkeypatch.context() as patched:
        patched.setattr(measuring, "verify_candidate", _passing(filed_cycle, _cancel_held))
        cancelled, result_code = asyncio.run(_host_cancel())
    assert cancelled.status == "cancelled" and "cancel by" in cancelled.status_message, (
        "a host cancel left the pass running"
    )
    assert result_code == PassStoppedError.code, "a stopped pass's result is not its refusal"
    assert len(_cancels(stores)) == before + 1 and held_task["held"] in _cancels(stores), (
        "a host cancel ended the pass without its cancel"
    )
    assert stores.commands.book().stop(held_task["held"]) is None, "a door's stop outlived its pass"
    assert not cycle_held(cycle_dir), "the pass kept its cycle"
    # A cancel of a command that ended stands for nothing, so no stop waits for whatever holds
    # the cycle next.
    assert asyncio.run(_cancel_command(stores, held_task["held"])) == "applied", (
        "a cancel of an ended command was refused"
    )
    assert stores.commands.book().stop(held_task["held"]) is None, (
        "a cancel after its pass ended stood"
    )
    with pytest.raises(NotFoundError):
        asyncio.run(_cancel_command(stores, "never-filed"))


def test_a_ctrl_c_stops_a_pass_by_its_commands_cancel(
    monkeypatch: pytest.MonkeyPatch, filed_cycle: _Filed
) -> None:
    stores, cycle_dir = filed_cycle.stores, filed_cycle.cycle_dir
    landed: list[str] = []
    pressed_call = _verify_call(filed_cycle.hop, "pressed")

    async def _press() -> None:
        signal.raise_signal(signal.SIGINT)
        # A send already out bills either way: the press stops the pass at its next
        # checkpoint, never by cancelling the wait on what it paid for.
        while stores.commands.book().stop(pressed_call.command_id) is None:
            await asyncio.to_thread(lambda: None)
        landed.append(pressed_call.command_id)

    async def _ctrl_c() -> str:
        with monkeypatch.context() as patched:
            patched.setattr(measuring, "verify_candidate", _passing(filed_cycle, _press))
            with (
                pytest.raises(PassStoppedError) as ended,
                ctrl_c_stops_pass(CommandDispatcher(stores), pressed_call),
            ):
                await CommandDispatcher(stores).dispatch(pressed_call)
        return str(ended.value)

    before = len(_cancels(stores))
    pressed = asyncio.run(_ctrl_c())
    assert "cancel by" in pressed, "a Ctrl+C left the pass running"
    assert len(landed) == 1, "a Ctrl+C cancelled the pass's in-flight sends (bl1.B2)"
    assert len(_cancels(stores)) == before + 1, "a Ctrl+C ended the pass without its cancel"
    assert not cycle_held(cycle_dir), "the pass kept its cycle"


def test_a_command_resolves_an_l4_leaf_by_the_rule_a_read_takes(
    monkeypatch: pytest.MonkeyPatch, filed_cycle: _Filed, world: World
) -> None:
    stores = filed_cycle.stores
    # The command addresses the leaf by the id prefix a read takes, below its chain, so the act
    # reaches the leaf's campaign under its full id.
    peer = filed_cycle.hop
    sandbox = inner_sandbox(stores, peer.campaign_id, peer.cycle_id)
    (sandbox / stores.tenant_id).mkdir(exist_ok=True)
    inner = descend_store(stores, (peer,))
    leaf = asyncio.run(World(inner, world.net, world.scratch / "inner").minted())
    reached: list[CycleHop] = []

    async def _verify_reached(dispatched: Any) -> Any:
        reached.append(dispatched.at.hop)
        raise ConflictError("reached")

    addressed = Subject.parse(
        f"candidate:{leaf.campaign_id[:7]}/{leaf.cycle_id}/r1/c1;in={peer.campaign_id}::{peer.cycle_id}"
    )
    verify = VerifyCandidatePayload.model_validate(
        {**addressed.command_fields, "candidate_id": addressed.candidate_id}
    )
    with monkeypatch.context() as patched:
        patched.setattr(measuring, "verify_candidate", _verify_reached)
        with pytest.raises(ConflictError):
            asyncio.run(CommandDispatcher(stores).dispatch(CommandCall(verify, door=Door.HOST)))
    assert reached == [leaf], "a command resolved its leaf by another rule than a read's"


def test_a_campaigns_command_by_a_prefix_acts_on_its_full_id_as_a_cycles_does(
    monkeypatch: pytest.MonkeyPatch, filed_cycle: _Filed
) -> None:
    stores = filed_cycle.stores
    benched: list[str] = []

    async def _bench_reached(dispatched: Any) -> Any:
        benched.append(dispatched.payload.campaign_id)
        raise ConflictError("reached")

    bench_by_prefix = HeldOutPayload(campaign_id=filed_cycle.hop.campaign_id[:3])
    with monkeypatch.context() as patched:
        patched.setattr(measuring, "held_out", _bench_reached)
        with pytest.raises(ConflictError):
            asyncio.run(
                CommandDispatcher(stores).dispatch(CommandCall(bench_by_prefix, door=Door.HOST))
            )
    assert benched == [filed_cycle.hop.campaign_id], (
        "a campaign's command acted on the typed prefix"
    )


def test_a_host_task_is_filed_at_the_address_its_dispatch_acted_on(
    monkeypatch: pytest.MonkeyPatch, filed_cycle: _Filed
) -> None:
    stores = filed_cycle.stores
    host = HostSession(Caller(stores))
    # The filing names the pass's cycle even once another campaign shares the sent prefix.
    sent_by_prefix = {
        "campaign_id": filed_cycle.hop.campaign_id[:3],
        "cycle_id": filed_cycle.hop.cycle_id,
        "candidate_id": "c1",
        IDEMPOTENCY_KEY: "verify-by-prefix",
    }

    async def _tracked_by_prefix() -> HostTask:
        opened = await host.answer(CallTool("verify-candidate", sent_by_prefix))
        assert opened.task is not None
        return opened.task

    async def _verify_filed(_: Any) -> Applier[None]:
        async def _reached() -> None:
            raise ConflictError("reached")

        return Applier(_reached)

    with monkeypatch.context() as patched:
        patched.setattr(measuring, "verify_candidate", _verify_filed)
        tracked = asyncio.run(_tracked_by_prefix())
    filed = stores.commands.book().filing(tracked.task_id)
    assert filed is not None and isinstance(filed.payload, VerifyCandidatePayload), filed
    assert filed.payload.campaign_id == filed_cycle.hop.campaign_id, (
        "a host task filed the prefix it was sent by, which a later mint can make ambiguous"
    )


_BENCH_SPENT = PassStop(cause=StopReason.SPEND_BUDGET, warning=None)
_BENCH_REFUSED = PassStop(cause=WalkEnd.CLIENT_ERROR, warning=None)


def _check_bench_row(
    stores: Stores,
    banked: dict[str, Any] | None,
    run: dict[str, Any],
    state: ReadingState,
    can_run: bool,
    lift: float | None,
    series: tuple[float | None, list[float | None]],
    stopped: str | None,
) -> None:
    pick = prompted(instruction="the declared pick")
    scorer = scorer_of(_H2H_FORMULA)
    case = f"banked={banked} run={run}"
    passes: BenchPasses | None = None
    if banked is not None:
        origin_stop = banked.get("origin_stop")
        origin = _bench_pass(
            stores, "origin", 0.0, cells=4 - 4 * bool(origin_stop), stop=origin_stop
        )
        held_pick = banked.get("pick", pick.id)
        held = (
            []
            if held_pick == "origin"
            else [
                _bench_pass(
                    stores,
                    held_pick,
                    1.0,
                    cells=banked.get("cells", 4),
                    stop=banked.get("stop"),
                )
            ]
        )
        passes = bench_passes(origin, *held, tolerance=banked.get("tolerance", 0))
    chosen = "origin" if banked and banked.get("pick") == "origin" else pick.id
    line = bench_line(
        ArmName(key=ArmKey(chosen), round=1, label="C1.1"),
        on_live_cycle="held_by" not in run,
        **run,
    )
    bench = read_bench(stores, passes, scorer, line=line)
    assert (bench.status.state, bench.status.can_run) == (state, can_run), case
    headline = bench.vs_origin.headline
    assert (None if headline is None else headline.estimate.value) == lift, case
    assert bench.status.subject == stopped or not stopped, f"{case}: names who stopped"
    pick_arm = arm_state("C1.1", position=ArmPosition(round=1, idx=0, total=1), individual=pick)
    on_line = round_result(1, arms=[pick_arm], cells={})
    curve = read_curve(stores, passes, scorer, [LineSpan([on_line], None)], budget=1)
    assert ((None, []) if curve is None else (curve.origin, curve.levels)) == series, case


def test_a_bench_line_with_no_bank_says_why_it_reads_nothing() -> None:
    stores = workspace()
    for run, state, can_run in (
        ({"trigger": "manual"}, ReadingState.NOT_ASKED, True),
        ({"selecting": True, "ending": None}, ReadingState.PENDING, False),
        ({"ending": StopReason.CRASHED}, ReadingState.RUN_FAILED, True),
        ({}, ReadingState.NOT_ASKED, True),
        # A campaign its pass's admission refuses: no grade is offered.
        ({"refused": CampaignRefusal.NOT_ACTIVE}, ReadingState.NOT_ASKED, False),
        ({"held_by": "cyc_beside"}, ReadingState.HELD_ELSEWHERE, False),
    ):
        _check_bench_row(stores, None, run, state, can_run, None, (None, []), None)


def test_a_bench_line_reads_the_pick_it_holds_or_says_why_it_reads_nothing() -> None:
    stores = workspace()
    read, past, stop = ReadingState.READ, ReadingState.PAST_TOLERANCE, ReadingState.PASS_STOPPED
    rows: tuple[tuple[dict[str, Any], ReadingState, bool, float | None, float | None], ...] = (
        ({}, read, False, 1.0, 1.0),
        ({"pick": "origin"}, read, False, None, None),
        ({"pick": "displaced"}, ReadingState.NOT_ASKED, True, None, None),
        ({"cells": 3}, past, True, None, None),
        ({"cells": 3, "tolerance": 1}, read, False, 1.0, 1.0),
    )
    for banked, state, can_run, lift, level in rows:
        _check_bench_row(stores, banked, {}, state, can_run, lift, (0.0, [level]), None)
    spent = {"stop": _BENCH_SPENT}
    _check_bench_row(stores, spent, {}, stop, True, None, (0.0, [None]), "selected")
    refused = {"origin_stop": _BENCH_REFUSED}
    _check_bench_row(stores, refused, {}, stop, True, None, (None, [1.0]), "origin")


def _adopting(rnd: int, individual: OptSearchPoint | None) -> RoundResult:
    """Round *rnd* selecting *individual*, or selecting nothing."""
    if individual is None:
        return round_result(rnd, improved=False, cells={})
    arm = arm_state(
        f"C{rnd}.1", position=ArmPosition(round=rnd, idx=0, total=1), individual=individual
    )
    return round_result(rnd, arms=[arm], cells={})


def _area_passes(stores: Stores, first: str, second: str) -> tuple[BenchPass, ...]:
    """The origin's and two selections' passes over four held-out rows."""
    return (
        _bench_pass(stores, "origin", [0.2, 0.4, 0.2, 0.4]),
        _bench_pass(stores, first, [0.6, 0.4, 0.8, 0.6]),
        _bench_pass(stores, second, [1.0, 0.8, 0.6, 1.0]),
    )


def test_the_held_out_area_averages_each_row_over_the_round_budget_against_the_origin() -> None:
    stores = workspace()
    scorer = scorer_of(_H2H_FORMULA)
    first, second = prompted(instruction="first"), prompted(instruction="second")
    origin, first_pass, second_pass = _area_passes(stores, first.id, second.id)
    # Adopted at rounds 1 and 3 of a budget of 4: the second is held forward to round 4.
    spans = [LineSpan([_adopting(1, first), _adopting(2, None), _adopting(3, second)], None)]
    curve = read_curve(
        stores, bench_passes(origin, first_pass, second_pass), scorer, spans, budget=4
    )
    assert curve is not None and curve.area is not None
    area = curve.area.estimate
    assert curve.state is ReadingState.READ and area.value == pytest.approx(0.425), (
        "the area is not each row's mean over the budget less the origin's"
    )
    assert area.ci_lo is not None and area.ci_hi is not None and area.ci_lo < 0.425 < area.ci_hi
    assert curve.levels == pytest.approx([0.6, 0.6, 0.85, 0.85]), "a level is not its standing's"
    stopped = second_pass.model_copy(
        update={"stop": PassStop(cause=StopReason.SPEND_BUDGET, warning=None)}
    )
    for passes, state in (
        (bench_passes(origin, first_pass, stopped), ReadingState.PASS_STOPPED),
        (bench_passes(origin, first_pass), ReadingState.NOT_ASKED),
    ):
        read = read_curve(stores, passes, scorer, spans, budget=4)
        assert read is not None and (read.state, read.area) == (state, None), (
            "an area read over a pass that read nothing"
        )
    held = [LineSpan([_adopting(rnd, None) for rnd in (1, 2, 3)], None)]
    never = read_curve(stores, bench_passes(origin), scorer, held, budget=4)
    assert never is not None and never.area is not None
    assert (never.state, never.area.estimate.value) == (ReadingState.READ, 0.0), (
        "a line that never adopted read anything but a zero area"
    )


def test_a_rebase_discarded_adoption_stays_in_the_held_out_area() -> None:
    stores = workspace()
    scorer = scorer_of(_H2H_FORMULA)
    first, second = prompted(instruction="first"), prompted(instruction="second")
    # The first cycle adopts twice; its fork cuts at round 2, standing on round 1's adoption.
    spans = [
        LineSpan([_adopting(1, first), _adopting(2, second)], None),
        LineSpan([_adopting(1, first), _adopting(2, None), _adopting(3, None)], 2),
    ]
    budget = line_round_budget(spans, 4)
    assert budget == 5, "the fork's cap did not add the round its line has left"
    passes = bench_passes(*_area_passes(stores, first.id, second.id))
    curve = read_curve(stores, passes, scorer, spans, budget=budget)
    assert curve is not None and curve.area is not None
    assert curve.levels == pytest.approx([0.6, 0.85, 0.6, 0.6, 0.6]), (
        "a round the rebase discarded is not read at the selection it stood on"
    )
    assert curve.area.estimate.value == pytest.approx(0.35)


def test_a_parents_own_rows_are_its_latest_cell_per_sample_its_panel_top_ups_included() -> None:
    parent, other = ArmKey("parent"), ArmKey("other")

    def ran(n: int, parents: dict[int, float], *, panel: dict[int, float] | None = None) -> Any:
        cells = {parent: sheet(measurement(s, g) for s, g in parents.items())}
        cells[other] = sheet([measurement(9, 1.0)])
        top_ups = {parent: sheet(measurement(s, g) for s, g in (panel or {}).items())}
        return round_result(n, cells=cells, overlap_results=top_ups)

    for line, read in (
        ([], {}),
        ([ran(0, {0: 1.0, 1: 0.0})], {"0": 1.0, "1": 0.0}),
        ([ran(0, {0: 1.0, 1: 0.0}), ran(1, {1: 1.0})], {"0": 1.0, "1": 1.0}),
        ([ran(0, {0: 1.0}, panel={3: 0.0}), ran(1, {3: 1.0})], {"0": 1.0, "3": 1.0}),
        ([ran(0, {0: 1.0}), ran(1, {}, panel={0: 0.0})], {"0": 0.0}),
    ):
        rows = own_rows(line, parent)
        assert {c.key: c.grade.fitness for c in rows.cells} == read, (line, rows)


# 5. Elimination — who is cut, and when


class _Trailing(NamedTuple):
    cfg: PoBBKnobs
    parent: ArmKey
    behind_perfect_prior: Callable[..., Any]


@pytest.fixture
def trailing() -> _Trailing:
    """An ε ramping in from its floor, and an arm read behind a perfect prior."""
    # The depths come from `fit_theta`'s dispersion rule.
    cfg = pobb_knobs(epsilon=0.30, epsilon_floor=0.15)
    parent = ArmKey("parent")

    def behind_perfect_prior(n: int, misses: int, *, parent_cells: int = 28) -> Any:
        check = PoBBCheck(
            cfg,
            n_min=6,
            ruler=None,
            parent=parent,
        )
        field = {
            ArmKey("winner"): sheet(measurements([1.0] * n)),
            parent: sheet(measurements([1.0] * parent_cells)),
            ArmKey("arm"): sheet(measurements([0.0] * misses + [1.0] * (n - misses))),
        }
        return check.check(field, dict.fromkeys([ArmKey("winner"), ArmKey("arm")], 28)).get(
            ArmKey("arm")
        )

    return _Trailing(cfg, parent, behind_perfect_prior)


def test_epsilon_ramps_in_from_its_floor(trailing: _Trailing) -> None:
    cfg, parent, _ = trailing
    graded = PoBBCheck(cfg, n_min=6, ruler=None, parent=parent)
    for n, epsilon in ((6, 0.15), (9, 0.225), *((n, 0.30) for n in range(12, 29))):
        assert graded.epsilon_at(n) == pytest.approx(epsilon), f"ε at {n} cells"
    flat = PoBBCheck(
        pobb_knobs(epsilon=0.30, epsilon_floor=0.30),
        n_min=6,
        ruler=None,
        parent=parent,
    )
    assert flat.epsilon_at(6) == pytest.approx(0.30), "a floor at ε does not ramp"
    shipped = PoBBCheck(
        pobb_knobs(),
        n_min=6,
        ruler=None,
        parent=parent,
    )
    assert shipped.epsilon_at(shipped.n_min) == pytest.approx(shipped.epsilon), (
        "the manifest sits floor and ε on the same value, so an untouched campaign never grades"
    )


def test_an_arm_behind_a_perfect_prior_is_cut_on_the_epsilon_that_fired(
    trailing: _Trailing,
) -> None:
    behind_perfect_prior = trailing.behind_perfect_prior
    # The field shares one fit, so a single adverse cell leaves the arm a share above the ramp.
    assert behind_perfect_prior(6, 1) is None and behind_perfect_prior(9, 1) is None, (
        "one adverse cell was cut"
    )
    cut = behind_perfect_prior(9, 2)
    assert cut is not None and cut.check_result["epsilon"] == pytest.approx(0.225), (
        "the decision archives the bar that FIRED, never the configured ε"
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


def test_an_arm_is_never_cut_on_cells_its_rival_never_sat(trailing: _Trailing) -> None:
    _, parent, _ = trailing
    unpaired = PoBBCheck(
        pobb_knobs(epsilon=0.05),
        n_min=4,
        ruler=None,
        parent=parent,
    )
    field = {
        ArmKey("lucky_leader"): sheet(measurements([1.0] * 8, sample_ids=list(range(8)))),
        parent: sheet(measurements([0.0] * 20)),
        ArmKey("challenger"): sheet(measurements([0.0] * 5, sample_ids=[9, 12, 13, 14, 8])),
    }
    assert not unpaired.check(field, {ArmKey("challenger"): 28}), (
        "an arm was cut on cells its rival never sat"
    )


def test_a_cost_terms_tie_breaks_never_pin_p_best_at_a_coin(trailing: _Trailing) -> None:
    cfg, parent, _ = trailing
    longer, terse_parent = _charged_by_length()
    standings: list[node_contract.RaceSnapshot] = []
    charged = PoBBCheck(cfg, n_min=6, ruler=None, parent=parent, on_snapshot=standings.append)
    charged.check({parent: terse_parent, ArmKey("l"): longer}, {ArmKey("l"): 28})
    assert standings[-1].p_best > 0.5, "a cost term's tie-breaks pinned p_best at a coin"


def test_an_origin_reads_its_lift_over_the_constant_answer_on_the_labelled_cells_it_scored() -> (
    None
):
    truths = ["TRUE"] * 9 + ["FALSE"] * 3
    for said in (truths, ["TRUE"] * 12, ["FALSE"] * 12):
        walked = scorer_of().sheet(
            measurement(i, None, predicted=p, ground_truth=t).facts
            for i, (p, t) in enumerate(zip(said, truths, strict=True))
        )
        unlabelled = measurement(12, None, predicted="TRUE", ground_truth="").facts
        floor = origin_floor(walked.merged(scorer_of().sheet([unlabelled])))
        assert floor is not None and (floor.label, floor.floor) == ("TRUE", 0.75), (
            f"the floor is not the modal truth's share: {floor}"
        )
        paired = fmean(float(p == t) - (t == "TRUE") for p, t in zip(said, truths, strict=True))
        assert floor.lift.value == pytest.approx(paired), (
            "the lift is not the origin paired per cell"
        )
    assert origin_floor(scorer_of().sheet([unlabelled])) is None, "no label, no constant answer"


def _answered(fitness: list[float], **over: Any) -> list[GradedCell]:
    return [
        GradedCell.from_wire(
            {"sample_key": str(i), "ground_truth": "", "predicted": "done", "fitness": f, **over}
        )
        for i, f in enumerate(fitness)
    ]


def _parent_answering_apart(constant: list[GradedCell]) -> CellSheet:
    return sheet(
        GradedCell.from_wire({**cell.wire(), "predicted": "done" if i % 2 else "other"})
        for i, cell in enumerate(constant)
    )


def _labelled_uncertain() -> list[GradedCell]:
    return [
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


def _truthful_parent(labelled: list[GradedCell]) -> CellSheet:
    return sheet(
        GradedCell.from_wire({**cell.wire(), "predicted": cell.facts.ground_truth})
        for cell in labelled
    )


def test_one_label_on_an_n_min_prefix_read_against_no_parent_cuts_no_arm(
    trailing: _Trailing,
) -> None:
    _, parent, _ = trailing
    unpaired_check = PoBBCheck(pobb_knobs(), n_min=6, ruler=None, parent=parent)
    constant = _answered([1.0, 0.0, 0.0, 1.0, 0.0, 0.0])
    assert not unpaired_check.check({ArmKey("c"): sheet(constant)}, {ArmKey("c"): 28}), (
        "one label on an n_min prefix, read against no parent, cut an arm the panel never judged"
    )


# A verifier proves an answer wrong by an unsolved cell, and alike-and-all-solved is NOT collapsed.
# Collapse is read against the parent's answers on the same cells.
def test_the_collapse_refusal_reads_the_answer_against_the_parents_never_the_labels() -> None:
    constant = _answered([1.0, 0.0, 0.0, 1.0, 0.0, 0.0])
    apart = _parent_answering_apart(constant)
    assert is_answer_collapsed(constant, apart), (
        "a constant answerer is refused with no labels to read"
    )
    assert not is_answer_collapsed(constant, sheet(constant)), (
        "one answer the parent gave on every same cell is the draw's, read by θ, not a collapse"
    )
    assert not is_answer_collapsed(constant, NO_CELLS), "with no parent answer there is no contrast"
    assert not is_answer_collapsed(_answered([1.0] * 6), apart), (
        "one answer that solves every cell is not a collapse"
    )
    assert not is_answer_collapsed(
        _answered([1.0, 0.0, 0.0, 1.0, 0.0, 0.0], predicted="NO_RESULT"), apart
    ), "an agent that wrote no answer file answered nothing, which is not one answer"
    assert not is_answer_collapsed(
        _answered([0.0] * 6, error_category=ErrorCategory.PIPELINE), apart
    ), "a backend that broke on every cell is not a verdict on the idea"


def test_replies_reasoning_apart_to_one_graded_answer_are_one_answer_collapsed() -> None:
    labelled = _labelled_uncertain()
    truthful = _truthful_parent(labelled)
    assert is_answer_collapsed(labelled, truthful)
    reasoned = [
        GradedCell.from_wire(
            {**cell.wire(), "predicted": f"step {i}: **Uncertain**", "span": "Uncertain"}
        )
        for i, cell in enumerate(labelled)
    ]
    assert is_answer_collapsed(reasoned, truthful), (
        "two replies reasoning apart to one graded answer were counted as two answers"
    )


def test_a_reply_with_no_graded_span_is_not_counted_as_the_answer() -> None:
    labelled = _labelled_uncertain()
    spanless = [
        GradedCell.from_wire({**cell.wire(), "span": "" if i == 5 else "Uncertain"})
        for i, cell in enumerate(labelled)
    ]
    assert not is_answer_collapsed(spanless, _truthful_parent(labelled)), (
        "a reply with no graded span was counted as the answer"
    )


def test_a_rolling_race_sends_only_what_it_decided_and_banks_only_the_rows_it_took() -> None:
    # A rolling decision reads one returned row, so nothing past its decided calls is sent ahead
    # and a stop banks no cell a later decision might never have taken.
    rolling, batch = Trigger(each_read=True), Trigger(each_read=False)
    for trigger, cells, past, ahead in ((rolling, 4, 0, 0), (batch, 4, 1, 3), (batch, 2, 3, 0)):
        assert trigger.ahead(cells, past) == ahead, f"{trigger} at {cells}/{past} sent ahead"
    for trigger, taken, decided, sure in ((rolling, 3, 2, 3), (batch, 3, 2, 5)):
        assert trigger.sure(taken, decided) == sure, f"{trigger} banked past {sure}"


def test_the_fields_p_best_sums_to_one_discriminates_graded_rows_and_reads_no_slot() -> None:
    def uncharged(*values: float) -> CellSheet:
        return sheet(measurement(i, v, objective=v) for i, v in enumerate(values))

    cells = [str(i) for i in range(12)]
    strong, weak = uncharged(*[0.66] * 12), uncharged(*[0.30] * 12)
    field = {ArmKey("a"): weak, ArmKey("b"): strong, ArmKey("c"): weak}
    p_best, _, _ = field_p_best(field, cells)
    assert sum(p_best.values()) == pytest.approx(1.0), "the field's shares do not sum to 1"
    assert p_best[ArmKey("b")] > 0.8, f"graded rows failed to discriminate: {p_best}"
    reordered, _, _ = field_p_best(dict(reversed(field.items())), cells)
    assert reordered == p_best, "the order the field was walked in moved an arm's p_best"
    ties, _, _ = field_p_best({ArmKey("a"): weak, ArmKey("c"): weak}, cells)
    assert ties[ArmKey("a")] == pytest.approx(0.5, abs=0.03), "a tie reads as a coin"


def test_a_fatal_row_ends_a_candidate_on_one_sighting_and_an_advisory_never_does() -> None:
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
    late = degradation.check([*(measurement(i, 1.0) for i in range(17)), fatal[1]])
    assert late is not None
    assert (late.check_result["degraded_count"], late.check_result["total_scored"]) == (1, 18), (
        "a one-row cut read every row it scored as degraded"
    )


def test_an_empty_or_filtered_answer_is_a_graded_miss_and_cuts_only_by_the_rate() -> None:
    """banking77, 2026-11-18: one stop-finished empty answer at row 18 cut an arm that answered 17.
    A provider's filter trips on one row's text as an empty answer is one attempt's."""
    for code in ("content_empty", "content_filtered"):
        empty: dict[str, Any] = {
            "predicted": "",
            "pipeline_data": {
                "terminal_node": "llm_only",
                "step_tokens": {"llm_only": {"finish_reason": "stop", "reasoning": 0}},
                "diagnostics": {"warnings": [{"step": "llm_only", "code": code}]},
            },
        }
        degradation = DegradationCheck(threshold=0.4, min_samples=3)
        one_empty = [*(measurement(i, 1.0) for i in range(17)), measurement(17, 0.0, **empty)]
        assert is_deprecated(one_empty[-1].facts), f"{code} left the degradation rate"
        assert degradation.check(one_empty) is None, f"one {code} row cut an arm that answered 17"
        assert scoreable_rows(one_empty)[-1].grade.fitness == 0.0, f"the {code} row is no miss"
        mostly_empty = [measurement(i, 0.0, **empty) for i in range(2)] + [measurement(2, 1.0)]
        cut = degradation.check(mostly_empty)
        assert cut is not None and cut.check_result["fatal"] is not True, f"{code}: no rate cut"


@on_jumping_clock
async def test_a_rung_the_endpoint_refuses_reads_refused_on_its_arm_never_broken(
    offline: Router,
) -> None:
    said = {"detail": "Unsupported value: 'reasoning_effort' does not support 'high' here."}
    offline.backend.faults["/matches"] = [httpx.Response(400, json=said) for _ in range(12)]
    async with opened_round(
        workspace(), offline, samples(12, "TRUE"), optimization={"origin_gate": "off"}
    ) as opened:
        origin = opened.cycle.origin_round
    arm = origin.origin
    assert isinstance(arm, Cut) and arm.reason == Refused(parameter="reasoning_effort"), (
        f"a refused rung read {arm.state} {getattr(arm, 'reason', None)}, not REFUSED"
    )
    (reading,) = origin.arm_readings()
    assert (reading.end, reading.refused_parameter) == ("refused", "reasoning_effort"), (
        "the served arm does not say REFUSED or name the parameter"
    )


def test_a_hole_is_neither_a_stop_nor_a_deprecated_row() -> None:
    # `scored_samples - total` would count both.
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


def test_a_cell_a_declared_bound_cut_is_settled_and_never_re_measured() -> None:
    def holes(category: ErrorCategory) -> int:
        rows = [
            measurement(0, 1.0),
            measurement(1, None, error="no verdict", error_category=category),
        ]
        return repairable_holes(sheet(rows))

    assert holes(ErrorCategory.HALTED) == 0, (
        "a cell a declared bound CUT is settled: the same bound cuts the next attempt"
    )
    assert holes(ErrorCategory.UNSCOREABLE) == 1, (
        "a cell that ran to its own end can answer differently on a re-measure"
    )


# 6. Escalation and stop — live, and folded back on resume


def _line_read(
    round_num: int,
    improved: bool,
    *picks: tuple[str, Sequence[float | None]],
    c0: Sequence[float | None],
    ids: dict[str, str] | None = None,
    triggered: Sequence[tuple[str, Sequence[float | None]]] = (),
    not_separated: NotSeparatedStall = "counts",
) -> OverlapReading:
    """A crown moves the line onto itself; a round crowning none here holds its parent."""
    bank = samples(len(c0), "t")
    hop = CycleHop(campaign_id="camp", cycle_id="cyc")

    def member(label: str, grades: Sequence[float | None]) -> MemberRows:
        rnd = int(label[1:].split(".")[0])
        arm = ArmName(key=ArmKey((ids or {}).get(label, label.lower())), round=rnd, label=label)
        return member_rows(
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
            measurands=grade_measurands(
                dataclasses.replace(scorer_of(per_cell="0.6 * fitness"), id="s")
            ),
            spec=ROUND_LIFT_SPEC,
        )

    origin = ("C0", c0)
    readings = [read(label, grades, origin) for label, grades in picks]
    return OverlapReading.of(
        round_num,
        RoundEnding.CROWNED if improved else RoundEnding.HELD,
        positions=range(len(c0)),
        lead=readings[-1],
        earlier=readings[:-1],
        triggered=[
            TriggeredReading(trigger=PanelTrigger.N_MIN, reading=read(label, grades, origin))
            for label, grades in triggered
        ],
        vs_previous_pick=read(*picks[-1], picks[-2]) if len(picks) >= 2 else None,
        not_separated=not_separated,
    )


# Eight panel cells C0 misses and one it takes: a pick clean on all nine clears zero over it.
_C0_PANEL: Sequence[float | None] = [0.0] * 8 + [1.0]
_CLEAN_PANEL: Sequence[float | None] = [1.0] * 9
_TIED_PANEL: Sequence[float | None] = [1.0, 0.0] + [0.0] * 6 + [1.0]


def _stamped(rnd: int, stall: StallEffect) -> RoundResult:
    """A round closed with *stall*, the one stream L1 patience, the lives and L2/L3 patience read."""
    closed = round_result(rnd)
    return closed.model_copy(update={"overlap": closed.overlap.model_copy(update={"stall": stall})})


def test_a_layer_resets_on_a_later_reset_round_and_stalls_on_any_other() -> None:
    effects = [StallEffect.STARTS, StallEffect.COUNTS, StallEffect.COUNTS, StallEffect.RESETS]
    effects += [StallEffect.SKIPS, StallEffect.COUNTS]
    line = [_stamped(i, s) for i, s in enumerate(effects)]

    def read(upto: int, held: LayerCounters) -> tuple[int, int | None]:
        asked = ask_l2_escalation(
            Ladder(l2=held),
            rounds=line[: upto + 1],
            escalation_ladder=EscalationLadder.FULL,
            l2_patience=9,
            l3_patience="unbounded",
        )
        return asked.l2.stall_count, asked.l2.entered_round

    assert read(1, LayerCounters(stall_count=4)) == (4, 1), "seeding changed the count"
    assert read(2, LayerCounters(stall_count=4, entered_round=1)) == (5, 1), "a COUNTS round held"
    assert read(3, LayerCounters(stall_count=4, entered_round=1)) == (0, 3), "RESETS did not reset"
    assert read(3, LayerCounters(stall_count=4, entered_round=3)) == (5, 3), (
        "a RESETS round at the entry itself reset a layer it seeded"
    )
    assert read(5, LayerCounters(stall_count=0, entered_round=3)) == (1, 3), (
        "SKIPS and COUNTS after the entry stall it"
    )


def test_a_round_that_counts_for_l1_never_resets_l2_or_l3() -> None:
    def climb(effect: StallEffect) -> list[str]:
        ladder, actions = Ladder(), []
        line = [_stamped(0, StallEffect.STARTS)]
        for rnd in range(1, 6):
            line.append(_stamped(rnd, effect))
            asked = ask_l2_escalation(
                ladder,
                rounds=line,
                escalation_ladder=EscalationLadder.FULL,
                l2_patience=2,
                l3_patience=1,
            )
            actions.append(str(asked.next_action))
            if asked.next_action != NextAction.FIRE_L2:
                break
            ladder = l2_fired(ladder, asked.l2)
        return actions

    assert climb(StallEffect.COUNTS) == ["fire_l2", "fire_l2", "fire_l3"], "COUNTS reset a layer"
    assert climb(StallEffect.RESETS) == ["fire_l2"] * 5, "a RESETS line reached L3"


def test_the_l1_only_arm_reaches_no_layer_above_it_over_the_whole_predicate_space() -> None:
    # A deferral that never fires in one run is no suppression.
    grid = list(product([0, 1, 5], [0, 3], [False, True], [False, True], [False, True]))

    def actions(ladder: EscalationLadder) -> set[NextAction]:
        return {
            decide_escalation(
                EscalationInputs(
                    l1_stall_count=stall,
                    l1_patience=patience,
                    escalation_ladder=ladder,
                    l1_mandatory_breach=mandatory,
                    l1_zero_candidates=zero,
                    evidence_starved=starved,
                )
            ).next_action
            for stall, patience, mandatory, zero, starved in grid
        }

    assert actions(EscalationLadder.L1) == {NextAction.CONTINUE}, "the L1 arm escalated"
    assert NextAction.FIRE_L2 in actions(EscalationLadder.FULL), (
        "not vacuous: the full ladder fires"
    )


# Each stretch climbs the ladder the one before it left.
def test_a_heal_fire_spends_no_l3_patience() -> None:
    healed = Ladder()
    line = [_stamped(0, StallEffect.STARTS)]

    def stretch(first: StallEffect, *, heal_first: bool) -> LadderAsk:
        nonlocal healed
        asks: list[LadderAsk] = []
        while not asks or asks[-1].next_action is NextAction.FIRE_L2:
            line.append(_stamped(len(line), first if not asks else StallEffect.COUNTS))
            asked = ask_l2_escalation(
                healed,
                rounds=line,
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

    gate = stretch(StallEffect.COUNTS, heal_first=True)
    assert gate.next_action is NextAction.FIRE_L3, (
        "the heal wiped L2; the gate reached is L3's FIRST"
    )
    healed = l3_fired(healed, gate.l3)
    gate = stretch(StallEffect.RESETS, heal_first=True)
    assert gate.next_action is NextAction.FIRE_L3, "a later heal must not re-base the comparison"
    healed = l3_fired(healed, gate.l3)
    assert (
        stretch(StallEffect.COUNTS, heal_first=False).next_action is NextAction.STOP_L3_PATIENCE
    ), "the patience a heal did not spend still binds a flat run"


# Each fire lands on the ladder the fire before it left.
def test_a_landed_fire_moves_the_ladder_and_an_unlanded_one_moves_nothing() -> None:
    landed = Ladder()
    trace: list[tuple[int, int, int | None, int, int, int | None, int]] = []
    line = [_stamped(0, StallEffect.STARTS)] + [_stamped(i, StallEffect.COUNTS) for i in (1, 2, 3)]

    def snapshot() -> None:
        held = landed
        trace.append(
            (
                held.l2.fires,
                held.l2.stall_count,
                held.l2.entered_round,
                held.l3.fires,
                held.l3.stall_count,
                held.l3.entered_round,
                held.l1_stall_count,
            )
        )

    def ask_at(upto: int) -> LadderAsk:
        return ask_l2_escalation(
            landed,
            rounds=line[: upto + 1],
            escalation_ladder=EscalationLadder.FULL,
            l2_patience=3,
            l3_patience=2,
        )

    landed = l2_fired(landed, ask_at(1).l2)
    snapshot()
    landed = l2_fired(landed, ask_at(2).l2)  # a COUNTS round reads a stall the landing commits
    snapshot()
    unlanded = ask_at(3)
    assert unlanded.l2.stall_count == 2, "the ask read the stall"
    assert as_read_by(landed, unlanded).l2.stall_count == 2, (
        "the prompt that fire composed still reported the verdict that asked for it"
    )
    snapshot()
    landed = l3_fired(landed, None)  # a heal: it bumps L3, the patience ratchet stays unset
    snapshot()
    landed = l3_fired(landed, ask_at(3).l3)
    snapshot()
    assert trace == [
        (1, 0, 1, 0, 0, None, 0),
        (2, 1, 1, 0, 0, None, 0),
        (2, 1, 1, 0, 0, None, 0),
        (0, 0, None, 1, 0, None, 0),
        (0, 0, None, 2, 0, 3, 0),
    ], "an unparseable fire adopted something, or L3 firing kept L2's progress"


class _StallReads(NamedTuple):
    close: Callable[..., RoundResult]
    observed: Callable[[list[StallEffect]], Ladder]


@pytest.fixture
def stall_reads() -> _StallReads:
    """A round closed by whether it improved, and the L1 stall a ladder banks over the stamps."""

    def close(rnd: int, *, improved: bool, electable: int = 2) -> RoundResult:
        return round_result(rnd, improved=improved, voted={"electable": electable}, cells={})

    def observed(stalls: list[StallEffect]) -> Ladder:
        ladder = Ladder()
        for stall in stalls:
            ladder, _ = bank_and_route(
                ladder, stall=stall, l1_patience=99, escalation_ladder=EscalationLadder.FULL
            )
        return ladder

    return _StallReads(close, observed)


def test_the_l1_stall_and_the_lives_bank_read_the_same_rounds(stall_reads: _StallReads) -> None:
    close, observed = stall_reads
    sequence = [(True, 2), (True, 2), (True, 2), (True, 2), (False, 0), (False, 2), (False, 2)]
    closes = [close(i, improved=up, electable=n) for i, (up, n) in enumerate(sequence, start=1)]
    assert observed([c.overlap.stall for c in closes]).l1_stall_count == 3, (
        "three trailing non-improving rounds, one uncompared — all three advance the stall"
    )
    origin_close = close(0, improved=False, electable=0)
    stated = [origin_close, origin_close, *closes]
    assert [stalls_left(stated[: 2 + n], (2, 4)) for n in range(8)] == [2, 3, 4, 4, 4, 4, 3, 2], (
        "the origin closes TWICE and moves no bank; a round nothing reached costs no life"
    )
    drained = [close(i, improved=False) for i in (8, 9)]
    assert stalls_left([*stated, *drained], (2, 4)) == 0
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
    held = close(2, improved=False).overlap.stall
    switch: tuple[tuple[NotSeparatedStall, int], ...] = (("counts", 3), ("resets", 1), ("skips", 2))
    for not_separated, stalled in switch:
        tied = _line_read(3, True, ("C3.1", _TIED_PANEL), c0=_C0_PANEL, not_separated=not_separated)
        assert observed([lines[0].stall, held, tied.stall, held]).l1_stall_count == stalled, (
            f"a crown inside the noise did not pace the ladder as `{not_separated}` says"
        )
    moved = OverlapReading.unpaired(ReadingState.NOT_HELD, 4, RoundEnding.MOVED)
    assert (moved.advance, moved.stall) == (RoundAdvance.MOVED_UNCROWNED, StallEffect.COUNTS), (
        "a step onto an uncrowned leader read as an advance"
    )


def test_a_reading_that_failed_moves_the_stall_clock_neither_way(
    stall_reads: _StallReads,
) -> None:
    close, observed = stall_reads
    won = _line_read(1, True, ("C1.1", _CLEAN_PANEL), c0=_C0_PANEL)
    origin_close = close(0, improved=False, electable=0)
    lead = won.lead
    stopped = OverlapReading.of(
        3,
        RoundEnding.CROWNED,
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
        vs_previous_pick=None,
        not_separated="counts",
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
    assert observed([rr.overlap.stall for rr in rounds[1:]]).l1_stall_count == 1, (
        "the live stall reads what the standing reads"
    )
    unread_close = round_result(3, improved=True, overlap=stopped, voted={"electable": 2})
    assert stalls_left([origin_close, unread_close], (2, 4)) == 2, (
        "the life bank reads the stall column: an unread pick banks no life"
    )


def test_a_pick_one_panel_cell_holed_reads_on_the_cells_both_scored() -> None:
    holed = _line_read(1, True, ("C1.1", [*_CLEAN_PANEL[:8], None]), c0=_C0_PANEL)
    assert holed.lead.coverage is not None and holed.lead.headline is not None
    assert (holed.lead.coverage.scored, holed.lead.coverage.excluded_faulted) == (8, 1)
    assert holed.lead.headline.side == "above" and holed.advance is RoundAdvance.ADVANCED, (
        "one of nine panel cells errored: read on the eight both scored, and it decides"
    )


def test_no_pair_to_read_stands_alone_and_a_pair_owed_refused_or_unlanded_is_no_evidence() -> None:
    lead = _line_read(1, True, ("C1.1", _CLEAN_PANEL), c0=_C0_PANEL).lead
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
    for state, advance in promoted_on.items():
        pair = lead if state is ReadingState.READ else PairedReading.unread(state)
        assert round_advance(1, RoundEnding.CROWNED, pair) is advance, state
        assert round_advance(1, RoundEnding.HELD, pair) is RoundAdvance.HELD, state
        assert round_advance(1, RoundEnding.MOVED, pair) is RoundAdvance.MOVED_UNCROWNED, state
        assert round_advance(0, RoundEnding.CROWNED, pair) is RoundAdvance.ORIGIN, state


_LIVED = defaulted_config({"optimization": {"lives": {"start": 2, "cap": 4}}}).optimization
_UNPAID = LineOutlay(search_usd=None, worked_s=None)


def test_the_bench_stops_a_line_on_spent_lives() -> None:
    lives = _LIVED.lives
    assert lives is not None
    origin_arm = arm_state("C0", position=ArmPosition(round=0, idx=0, total=1))
    origin = round_result(0, arms=[origin_arm], cells={})

    def stop_after(*closes: tuple[bool, int]) -> tuple[int | None, StopReason | None]:
        line = [
            origin,
            *(
                round_result(n, improved=improved, voted={"electable": electable})
                for n, (improved, electable) in enumerate(closes, 1)
            ),
        ]
        standing = RunStanding.after(line, lives=lives.bank, outlay=_UNPAID)
        return stalls_left(line, lives.bank), standing_tripped(standing, _LIVED)

    held, promoted, uncompared = (False, 2), (True, 2), (False, 0)
    for closes, expected, why in (
        ((held,), (1, None), "one held round spends one life"),
        ((held, held), (0, StopReason.LIVES_EXHAUSTED), "the bank empties and stops"),
        ((held, uncompared), (1, None), "a round no arm reached the election of costs no life"),
        ((promoted, promoted, promoted, held, held), (2, None), "the bank fills to its cap"),
    ):
        assert stop_after(*closes) == expected, why


def test_the_campaign_ends_only_where_the_objective_is_spent_and_the_round_resolved() -> None:
    readings = {
        RoundAdvance.ADVANCED: _line_read(1, True, ("C1.1", _CLEAN_PANEL), c0=_C0_PANEL),
        RoundAdvance.NOT_SEPARATED: _line_read(1, True, ("C1.1", _TIED_PANEL), c0=_C0_PANEL),
        RoundAdvance.ADVANCED_UNPAIRED: OverlapReading.unpaired(
            ReadingState.NOT_HELD, 1, RoundEnding.CROWNED
        ),
        RoundAdvance.UNREAD: OverlapReading.unpaired(
            ReadingState.PASS_STOPPED, 1, RoundEnding.CROWNED
        ),
    }
    assert all(reading.advance is advance for advance, reading in readings.items())

    # One ceiling stop: the pick's own level on the origin panel at its one-sided anytime-valid
    # lower bound, `halt_at_fitness` its target; no pair, no stop.
    def outcome(reading: OverlapReading, target: float) -> StopReason | None:
        closing = round_result(1, improved=True, overlap=reading)
        folds = {key: score_summary(composite_fitness=1.0) for key in closing.folds}
        line = [round_result(0, improved=False), closing.model_copy(update={"folds": folds})]
        standing = RunStanding.after(line, lives=None, outlay=_UNPAID)
        return standing_tripped(standing, _LIVED.model_copy(update={"halt_at_fitness": target}))

    swept = _line_read(1, True, ("C1.1", [1.0] * 9), c0=[0.0] * 9)
    for reading, target, stop, why in (
        (readings[RoundAdvance.ADVANCED_UNPAIRED], 0.0, None, "an unpaired pick met a ceiling"),
        (readings[RoundAdvance.UNREAD], 0.0, None, "an unread pick met a ceiling"),
        (swept, 0.55, StopReason.TARGET_HIT, "a pick whose anytime bound clears it ran on"),
        (swept, 0.6, None, "9 of 9 at a 0.6 ceiling: a short draw's rate is no level"),
    ):
        assert outcome(reading, target) is stop, why


def test_a_citation_names_the_panel_holding_its_words_whole_and_in_order() -> None:
    rendered = {"first": "concatenate the strings", "second": "the cat sat"}
    for citation, panel in [("cat", "second"), ("conc", None), ("the … sat", "second")]:
        grounding, failures = l1_evidence_grounding(
            citation, citable=list(rendered), rendered=rendered
        )
        assert (None if grounding is None else grounding.field) == panel, (
            f"{citation!r} was filed under a panel holding it only inside another word"
        )
        assert bool(failures) == (panel is None), f"{citation!r} cited nothing yet stood"


def test_an_l1_edit_lands_on_its_one_anchor_in_order_and_never_writes_answer_format() -> None:
    # Every L1 child's text is `applied_edits`', and its held fields `l1_prompt_fields_open`'s.
    parent_text = "Answer the query."
    fields = {"instruction": parent_text, "answer_format": "Reply with the letter."}
    parent = OptSearchPoint(prompts={"solve": NodePrompt(fields=fields)})
    missed = ValidationReason.EDIT_ANCHOR_NOT_ONE_SPAN
    for steps, written in (
        # Two edits on one field: the second anchors on the text the first left.
        (
            (("insert_after", parent_text, " Zq7."), ("replace", "Zq7.", "Check twice.")),
            f"{parent_text} Check twice.",
        ),
        # A span the text holds twice names no place to edit.
        ((("insert_after", parent_text, " Zq7. Zq7."), ("replace", "Zq7.", "Check.")), missed),
        ((("replace", "a span the parent never held", "Check."),), missed),
        ((("replace", "", "Rewritten whole."),), "Rewritten whole."),
        # A hard-wrapped span quoted with its line break reflowed still names its one place.
        (
            (("insert_after", parent_text, " Zq7\n  Zq8."), ("replace", "Zq7 Zq8.", "Check.")),
            f"{parent_text} Check.",
        ),
    ):
        at = "solve.instruction"
        edits = [
            L1Edit.model_validate({"at": at, "op": op, "anchor": a, "text": t})
            for op, a, t in steps
        ]
        texts, failures = applied_edits(edits, parent, pooled=None)
        got = failures[0].reason if failures else texts[Locus("solve", "instruction")]
        assert got == written, f"{steps} wrote {got!r}, not {written!r}"
    schema = pipeline_schema(
        nodes=[PipelineNode(name="solve", tunes_llm=False, prompt_info=PROMPT_INFO)]
    )
    writes = {Locus("solve", "answer_format"): "Say anything.", Locus("solve", "instruction"): "x"}
    refused = l1_prompt_fields_open(writes, pipeline_schema=schema)
    assert [(f.axis, f.reason) for f in refused] == [
        ("solve.answer_format", ValidationReason.FORBIDDEN_AXIS)
    ], "an L1 edit of answer_format, which the scorer parses, reached the backend"
    edited = OptSearchPoint(prompts={"solve": NodePrompt(fields={**fields, "instruction": "x"})})
    checked = detect_invariants(
        [Proposal(individual=c) for c in (parent, edited)], parent, PipelineParams()
    )
    assert [[f.reason for f in c.validation_failures] for c in checked] == [
        [ValidationReason.NO_OP_VARIANT],
        [],
    ], "a variant restating its parent was measured, or an edit refused as one"


def test_a_fork_no_spine_node_may_take_is_declined_with_its_reason() -> None:
    # A fresh store's line whose levels are all withheld: the proposal used to vanish unsaid.
    def spine(theta: float | None) -> list[SpineCycle]:
        return [
            SpineCycle(
                cycle_id="c",
                parent_cycle_id=None,
                cut_round=None,
                theta_by_round={0: None, 1: theta, 2: None},
                pick_by_round={1: "C0", 2: "p2"},
            )
        ]

    def branch(theta: float | None, *, at: int = 2, changing: bool = False) -> int | ForkDecline:
        return select_branch_round(
            spine(theta), cycle_id="c", current_round=at, origin_id="C0", changing=changing
        )

    assert (branch(None), branch(0.4), branch(0.4, changing=True), branch(0.4, at=3)) == (
        ForkDecline.NO_THETA,
        ForkDecline.NOTHING_NEW,
        1,
        ForkDecline.NO_PICK,
    ), "a fork the spine cannot take was declined without the reason its record names"


def _human_intervened(space: Any, hop: Any) -> bool:
    index = space.campaigns.load(hop, moment=None)
    assert index is not None
    return bool(index.human_intervened)


# Each press reads the inbox and the phase the press before it left.
def test_a_loop_command_is_acted_on_by_the_loop_it_reached_or_refused_never_dropped(
    world: World,
) -> None:
    commanded, command_hop = world.stores, asyncio.run(world.minted())
    command_dir = walking_cycle(commanded, command_hop)
    commands = CycleEventLog.open(CycleDir(command_dir))
    control = RunControl(commands=commanded.commands, cycle=commanded.subject(command_hop))
    address = {"campaign_id": command_hop.campaign_id, "cycle_id": command_hop.cycle_id}

    def press(payload: CommandPayload, key: str) -> str:
        outcome = asyncio.run(
            CommandDispatcher(commanded).dispatch(CommandCall(payload, key, door=Door.HOST))
        )
        return outcome.accepted.command_id

    def acks(command_id: str) -> list[str]:
        return [
            record.status
            for record in commanded.commands.book().subject_events(commanded.subject(command_hop))
            if isinstance(record, CommandAckRecord) and record.command_id == command_id
        ]

    skip = SkipSearchpointPayload(**address)
    try:
        untaken = press(skip, "skip-1")
        assert acks(untaken) == ["accepted"], "a press was acked as a cut"
        assert not _human_intervened(commanded, command_hop), "the press alone marked it babysat"
        # A second RUNNING record is a new launch: the press the last one never reached is not its.
        commands.append(RunPhaseRecord(state=Running()))
        commanded.void_inbox(command_hop)
        assert commanded.inbox(command_hop).skips == ()
        with scoped(ledger=commands):
            assert not control.spend_skip(), "a skip no walk took cut the next launch's arm"
            taken = press(skip, "skip-2")
            assert control.spend_skip()
            assert not control.spend_skip(), "one press cut two searchpoints"
            assert (acks(taken), acks(untaken)) == (["accepted", "applied"], ["accepted", "voided"])
            press(PauseCyclePayload(**address), "pause-1")
            assert control.pause_requested()
            waiting = commanded.run_state(command_hop).stop
            assert waiting is not None and waiting.stop_reason is None, (
                "a pause the loop has not reached read as a run already stopped"
            )
            paused_by = control.take_pause()
            assert paused_by is not None and paused_by[0] is PauseCause.COMMAND
            commands.append(
                RunPhaseRecord.stop(StopReason.PAUSED, cause=paused_by[0], detail=paused_by[1])
            )
    finally:
        release_cycle(command_dir)
    paused = commanded.run_state(command_hop)
    assert paused.run_phase is RunPhase.PAUSED
    assert paused.stop is not None and paused.stop.cause is PauseCause.COMMAND
    for late, key in ((skip, "skip-3"), (PauseCyclePayload(**address), "pause-2")):
        with pytest.raises(ConflictError):
            press(late, key)
    assert commanded.inbox(command_hop).skips == ()
    assert not control.pause_requested(), "a pause with no run to stop was left standing"


def test_a_holed_panel_stops_as_no_interrupt_and_the_backends_hole_on_its_own_stop() -> None:
    # The dyck shape: one UNSCOREABLE row per arm, its error set.
    holed = [
        measurement(arm, None, error=f"arm {arm}: no evidence", error_category="UNSCOREABLE")
        for arm in (0, 1)
    ]
    stop = panel_gate_tripped(holed, None, "strict")
    assert stop is not None and stop_reason_outcome(stop.reason) is not StopOutcome.PAUSED, (
        "a hole no signal made paused the run, so it read as an interrupt"
    )
    backend = measurement(2, None, error="504: no reply in 164s", error_category="SERVER")
    served = panel_gate_tripped([*holed, backend], None, "strict")
    assert served is not None and served.reason is StopReason.BACKEND_HOLE, (
        "a backend's hole lost the stop its L4 outer re-asks on"
    )


# 7. Paired readings over shared cells


def _reference_reading(
    reference: Sequence[float | None],
    arm: Sequence[float | None],
    *,
    cut: CutKind | None,
) -> PairedReading:
    bank = samples(len(reference), "t")
    scorer = scorer_of(per_cell="0.6 * fitness")

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
    key, scored = (
        ArmKey("c1"),
        arm_state("C1.1", position=ArmPosition(round=1, idx=0, total=1), cut=cut),
    )
    reference_rows = rows(reference)
    pairs = ReferencePairs(
        pairs=RoundPairs(
            path=(CycleHop(campaign_id="camp", cycle_id="cyc"),),
            instrument_id="bank",
            dataset_hash=Bank.of(bank).dataset_hash,
            measurands=tuple(grade_measurands(scorer)),
        ),
        declared=[cell.key for cell in reference_rows],
        names={key: ArmName(key=key, round=1, label="C1.1")},
    )
    against = ArmKey(bar.id)
    reference_arm = ReadArm(against, arm_state("parent"), reference_rows)
    return pairs.read(ReadArm(key, scored, rows(arm)), against, reference_arm)


_PAIRED_CAND = [0.90, 0.10, 0.85, 0.20, 0.75, 0.30, 0.95, 0.05]


def test_the_paired_t_matches_ttest_rel_and_brackets_the_evidence_it_tests() -> None:
    cand = _PAIRED_CAND
    prior = [0.10, 0.85, 0.15, 0.80, 0.20, 0.70, 0.05, 0.90]
    reference = 0.8750918683549795  # scipy 1.17.1 `ttest_rel(cand, prior).pvalue`
    mean_d, lo, hi, p_two, se = paired_mean_t(cand, prior)
    n, diffs = len(cand), [c - p for c, p in zip(cand, prior, strict=True)]
    assert se is not None and abs(se - stdev(diffs) / n**0.5) < 1e-12, "it brackets on its SE"
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
    assert paired_mean_t([0.5], [0.1])[1:] == (None,) * 4, "one cell brackets nothing"


def test_a_level_band_covers_its_level_at_the_ends_of_its_support() -> None:
    def band(grades: list[float], declared: bool) -> tuple[float | None, float | None]:
        # `label_match` declares its fitness two-valued; a bare sheet's scorer declares nothing.
        cells = measurements(grades)
        level = level_band(
            scorer_of().graded(cells) if declared else sheet(cells), "fitness", "rate"
        )
        assert level is not None
        return level.ci_lo, level.ci_hi

    # scipy `beta.ppf(0.025, 20, 1)`, the exact Clopper-Pearson bound; the Wald-t read 0.974.
    assert band([1.0] * 20, True) == pytest.approx((0.831566, 1.0), abs=1e-6), (
        "a two-valued scorer's fitness band read its column's binariness, never its scorer's"
    )
    assert band([0.0] * 6, True) == pytest.approx((0.0, 0.459258), abs=1e-6)
    for declared in (True, False):
        covered = 0.0
        for k in range(21):
            lo, hi = band([1.0] * k + [0.0] * (20 - k), declared)
            assert lo is not None and hi is not None
            covered += comb(20, k) * 0.95**k * 0.05 ** (20 - k) * (lo <= 0.95 <= hi)
        assert covered >= 0.95, (
            f"declared={declared}: the band covered p = 0.95 at n = 20 {covered:.0%} of the time"
        )
    assert band([1.0], False) == (None, None), "one graded cell brackets nothing"


def _shared_pair() -> tuple[list[Sample], MemberRows, MemberRows, PairedReading]:
    # Samples 0 and 1 are one query under two truths: two cells, wherever a query would key one.
    bank = [
        Sample(id=i, query="same" if i < 2 else f"q{i}", ground_truth=f"t{i}") for i in range(7)
    ]
    a = member_rows(
        "a",
        [
            *measurements([1.0, 0.0, 0.0, 1.0, 1.0]),
            measurement(5, None, error_category="SERVER"),
            measurement(6, 1.0),
        ],
        bank,
    )
    b = member_rows(
        "b",
        [*measurements([1.0, 1.0, 1.0, 0.0, 1.0, 1.0]), measurement(6, None, unscored="no term")],
        bank,
    )
    return bank, a, b, _read_pair(a, b, bank)


def test_a_pair_is_one_estimate_over_the_cells_both_members_scored() -> None:
    _, _, _, reading = _shared_pair()
    lift, coverage = reading.headline, reading.coverage
    assert lift is not None and coverage is not None and reading.cell_set is not None
    assert reading.cell_set.size == 7
    assert (coverage.shared, coverage.scored) == (7, 5), "the pair reads the cells both scored"
    assert (coverage.excluded_faulted, coverage.excluded_unscored) == (1, 1)
    assert coverage.state is CoverageState.PARTIAL
    t_mean, t_lo, t_hi, _, _ = paired_mean_t([1.0, 1.0, 1.0, 0.0, 1.0], [1.0, 0.0, 0.0, 1.0, 1.0])
    assert (lift.rate_a, lift.rate_b) == pytest.approx((0.6, 0.8))
    estimate = lift.estimate
    assert estimate.value == pytest.approx(t_mean), "the lift is the paired mean"
    assert (estimate.ci_lo, estimate.ci_hi) == pytest.approx((t_lo, t_hi))
    assert lift.p_value == 1.0, (
        "2 gained and 1 lost is the exact sign test's 1.0, never the t's 0.62"
    )

    assert lift.side == "spans"
    assert lift.p_floor == 0.25, "three cells differ, so no exact test can read below 2/2**3"
    assert (lift.flips.gained, lift.flips.lost, lift.flips.unchanged) == (2, 1, 2)


def test_the_floor_gates_a_pair_claim_whose_interval_clears_zero() -> None:
    sparse = samples(34, "t", query="w")
    gained = _read_pair(
        member_rows("a", measurements([0.0] * 34), sparse),
        member_rows("b", measurements([1.0] * 4 + [0.0] * 30), sparse),
        sparse,
    ).headline
    assert gained is not None and (gained.estimate.ci_lo or 0.0) > 0.0
    assert (gained.side, gained.p_value) == ("spans", 0.125), (
        "the floor gates the claim: Student-t's interval clears zero, yet four differing cells cap p"
    )


def test_a_measurand_beside_the_headline_is_read_over_the_same_scored_cells() -> None:
    bank, a, b, reading = _shared_pair()
    assert reading.headline is not None
    composite = Measurand(
        kind=MeasurandKind.GRADE,
        key="objective",
        scorer_id="s",
        binary=False,
        unit="score",
    )
    (beside,) = _read_pair(a, b, bank, measurands=[reading.headline.measurand, composite]).beside
    assert not beside.measurand.binary
    assert beside.estimate.value == pytest.approx(0.6 * reading.headline.estimate.value), (
        "a measurand beside the headline is read over the SAME scored cells"
    )


def test_a_tables_pairs_share_one_holm_correction() -> None:
    bank, _, _, reading = _shared_pair()
    clear = _read_pair(
        member_rows("a", measurements([0.0] * 6), bank),
        member_rows("b", measurements([1.0] * 6), bank),
        bank,
    )
    assert reading.headline is not None and clear.headline is not None
    p_wide, p_clear = reading.headline.p_value, clear.headline.p_value
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


def test_an_arm_of_one_replicate_reads_its_declaration_and_a_pooled_one_the_t() -> None:
    bank = samples(8, "t", query="w")
    measurand = grade_measurands(scorer_of())[0]
    assert measurand.binary, "label_match declares its fitness two-valued"
    a = member_rows("a", measurements([1.0] * 2 + [0.0] * 6), bank)
    b = member_rows("b", measurements([1.0] * 7 + [0.0]), bank)
    b2 = member_rows("b2", measurements([1.0] * 6 + [0.0] * 2), bank)

    def lift(arm_a: list[MemberRows], arm_b: list[MemberRows]) -> MeasuredLift:
        read = read_arms(
            arm_a, arm_b, cells=[s.key for s in bank], measurand=measurand, spec=ROUND_LIFT_SPEC
        ).lift
        assert read is not None
        return read

    single = lift([a], [b])
    assert (single.flips.gained, single.flips.lost) == (5, 0)
    assert single.p_value == pytest.approx(0.0625), "5 gained, 0 lost is exact McNemar's 2/2**5"
    pooled = lift([a], [b, b2])
    assert not pooled.measurand.binary, "a mean over two replicates is no 0/1 cell: the t reads it"


def test_a_cell_measured_twice_is_one_standing_row_to_every_reader() -> None:
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
    twice = member_rows("twice", walked, four)
    cells = twice.sheet
    assert cells.column(ROW_GRADES["fitness"]) == standing, "a second reading is a second row"
    assert cells.column(ROW_GRADES["objective"]) == worth
    assert responses_of(cells) == worth, "the ruler read another row than the column"
    fold = fold_cells(cells, "rate")
    assert (fold.total, fold.fitness) == (4, 0.75)
    assert fold.composite_fitness == pytest.approx(sum(worth.values()) / 4)
    level = level_band(cells, "fitness", "rate")
    assert level is not None and level.value == fold.fitness
    assert {key: row.grade.fitness for key, row in twice.by_key().items()} == standing
    once = member_rows("once", measurements(list(by_slot.values())), four)
    unmoved = _read_pair(once, twice, four)
    lift, coverage = unmoved.headline, unmoved.coverage
    assert lift is not None and coverage is not None
    assert (coverage.scored, coverage.excluded_faulted) == (4, 0)
    assert (lift.rate_a, lift.rate_b, lift.estimate.value) == (0.75, 0.75, 0.0), (
        "paired against an individual that walked each cell once at those grades, nothing moved"
    )
    assert (lift.flips.gained, lift.flips.lost, lift.flips.unchanged) == (0, 0, 4)
    ungraded = member_rows(
        "ungraded", [*walked[:-1], measurement(3, None, unscored="no latency recorded")], four
    )
    assert ungraded.sheet.column(ROW_GRADES["fitness"]) == {
        four[sid].key: grade for sid, grade in by_slot.items() if sid < 3
    }
    level = level_band(ungraded.sheet, "fitness", "rate")
    assert level is not None and level.value == pytest.approx(2 / 3)
    short = _read_pair(once, ungraded, four).coverage
    assert short is not None and short.scored == 3, "an unscored cell left the pair"
    with pytest.raises(UngradedCellError):
        ROW_GRADES["fitness"].read(ungraded.sheet.cells[-1])


def test_a_pair_that_cannot_be_read_says_why_instead_of_reading_zero() -> None:
    six = samples(6, "t")
    panel = six[:4]
    left = member_rows("a", measurements([1.0, 0.0, 1.0, 0.0]), six)

    def right(rows: list[GradedCell] | None = None, **read_under: Any) -> MemberRows:
        return member_rows(
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


def test_an_arm_is_read_on_the_cells_its_reference_was_read_on() -> None:
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
        on_reference = _reference_reading(ref_grades, arm_grades, cut=cast(CutKind | None, cut))
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


def test_the_origin_panel_holds_the_size_it_is_given_of_the_cells_c0_walked() -> None:
    # Unset, `round_origin_panel` passes the round's own size: every offline leg runs that default.
    for size in (20, 30, 40):
        held = origin_panel(
            range(40),
            line_cells=(),
            poolable=range(50),
            benchmark_of=dict.fromkeys(range(50)),
            size=size,
        )
        assert len(held) == size and set(held) <= set(range(40)), f"a panel of {size} held {held}"
    with pytest.raises(ValidationError):
        defaulted_config({"origin_panel_cells": 41})  # past the origin's 40 cells


def test_c0_is_topped_up_on_a_scope_the_line_drew_never_paired_on_its_own_scope_only() -> None:
    # C0 sat scope `a` alone; the line drew `b`.
    seated = [
        Sample(id=i, query=f"q{i}", ground_truth=None, measured_dataset=ds)
        for i, ds in enumerate("aabb")
    ]
    assert origin_panel(
        [0, 1],
        line_cells=range(4),
        poolable=range(4),
        benchmark_of=dict(enumerate("aabb")),
        size=2,
    ) == [0, 1, 2, 3], "a scope the line drew and C0 never sat was left off the panel"
    on_bench = pair_on_bench(
        member_rows(
            "c0",
            measurements([0.0, 1.0, 0.0, 0.0]),
            seated,
            scope=RoleScope.BENCH,
            dataset_hash=None,
        ),
        member_rows(
            "c1",
            measurements([1.0, 1.0, 1.0, 0.0]),
            seated,
            scope=RoleScope.BENCH,
            dataset_hash=None,
        ),
        sample_keys=[s.key for s in seated],
        scorer=dataclasses.replace(scorer_of(per_cell="0.6 * fitness"), id="s"),
        instrument_id="i",
    ).lift("fitness")
    assert on_bench is not None and on_bench.estimate.value == pytest.approx(0.5), (
        "the lift lost its pairing"
    )


def _banked(strategy: VerifyStrategy = "random", round_num: int = 3) -> VerifyPass:
    return VerifyPass(
        arm=ArmName(key=ArmKey("c"), round=round_num, label="C3.1"),
        sp_hash="h",
        cells=[(f"k{sid}", f"r.{sid}", False) for sid in range(100, 110)],
        sample_keys=[f"k{sid}" for sid in range(100, 110)],
        stop=None,
        scorer_id="s",
        strategy=strategy,
        seed=1,
    )


@dataclasses.dataclass(frozen=True)
class _VerifyRig:
    scorer: Scorer
    keyed: Callable[[list[GradedCell]], CellSheet]
    verified: Callable[..., Any]


def _verify_rig() -> _VerifyRig:
    # Its sheets carry the declaration of the scorer that reads them: fitness two-valued.
    scorer = dataclasses.replace(scorer_of(per_cell="0.6 * fitness"), id="s")

    def keyed(rows: list[GradedCell]) -> CellSheet:
        return scorer.graded(_keyed(row, f"k{row.key}") for row in rows)

    recorded = keyed(measurements([1.0] * 20))
    fresh = keyed(measurements([1.0, 0.0] * 5, range(100, 110)))
    # The origin: half the round's cells, none of the fresh ones.
    c0_rows = keyed([*measurements([0.0, 1.0] * 10), *measurements([0.0] * 10, range(100, 110))])
    hop = CycleHop(campaign_id="camp", cycle_id="cyc")
    c0 = ArmName(key=ArmKey("c0"), round=0, label="C0")

    def verified(
        pass_: VerifyPass,
        fresh_rows: CellSheet = fresh,
        recorded: CellSheet = recorded,
        reader: Scorer = scorer,
    ) -> Any:
        arm = c0 if pass_.arm.round == 0 else pass_.arm
        return verify_reading(
            pass_,
            scorer=reader,
            fresh=fresh_rows,
            recorded=recorded,
            measured=verify_member(
                hop, arm, recorded.merged(fresh_rows), bought=10, instrument_id="bank"
            ),
            origin=verify_member(hop, c0, c0_rows, bought=0, instrument_id="bank"),
        )

    return _VerifyRig(scorer, keyed, verified)


def test_a_verify_reads_its_fresh_cells_alone_and_pairs_its_lift_over_the_origin() -> None:
    verify = _verify_rig().verified(_banked())
    assert verify.fresh.fitness is not None and verify.recorded.fitness is not None
    assert verify.fresh.fitness.value == pytest.approx(0.5), "the fresh cells, never the pool"
    assert verify.recorded.fitness.value == pytest.approx(1.0)
    assert verify.fitness_difference is not None
    assert verify.fitness_difference.value == pytest.approx(-0.5)
    pair = verify.vs_origin
    assert pair.coverage is not None and pair.headline is not None
    assert (verify.fresh.n, verify.recorded.n, pair.coverage.scored) == (10, 20, 30)
    assert pair.headline.estimate.value == pytest.approx(0.5), (
        "paired per cell both scored, never the difference of two rates over different cells"
    )
    assert verify.held is False, (
        "the recorded level sits above the fresh band, so the round's claim did not survive"
    )


def test_a_verify_holds_where_the_fresh_band_reaches_the_recorded_level() -> None:
    rig = _verify_rig()
    assert (
        rig.verified(_banked(), rig.keyed(measurements([1.0] * 10, range(100, 110)))).held is True
    )
    # 14/20 against 20/20 is Fisher's p 0.020: two bands overlapping is a ~0.6% test, not a 5% one.
    for hits, held in ((14, False), (19, True)):
        fell = rig.keyed(measurements([1.0] * hits + [0.0] * (20 - hits), range(100, 120)))
        assert rig.verified(_banked(), fell).held is held, f"{hits}/20 fresh against 20/20 recorded"


def test_a_level_recorded_on_four_cells_is_a_wide_band_never_a_point() -> None:
    rig = _verify_rig()
    four = rig.keyed(measurements([1.0] * 4))
    assert rig.verified(_banked(), recorded=four).held is True, (
        "a verify compared the fresh band against the recorded level's point"
    )


def test_a_constant_graded_side_keeps_the_band_its_support_allows() -> None:
    rig = _verify_rig()
    graded = dataclasses.replace(rig.scorer, two_valued=frozenset({"hit"}))
    for level, n, held in ((0.79, 5, True), (0.30, 5, True), (0.30, 6, False)):
        fell, stood = (
            graded.graded(_keyed(row, f"k{row.key}") for row in measurements([value] * n, keys))
            for value, keys in ((level, range(100, 100 + n)), (0.80, range(n)))
        )
        assert rig.verified(_banked(), fell, stood, graded).held is held, f"{n}x{level} vs {n}x0.80"


def test_a_verify_of_hard_picks_reads_no_verdict_from_the_level() -> None:
    hard = _verify_rig().verified(_banked("hard"))
    assert hard.held is None, (
        "hard picks sit below the level whatever the candidate is worth: no verdict from the level"
    )


def test_a_verify_of_the_origin_has_nothing_to_be_lifted_over() -> None:
    at_origin = _verify_rig().verified(_banked(round_num=0))
    assert at_origin.vs_origin.state is ReadingState.SAME_INDIVIDUAL, (
        "the origin has nothing to be lifted over, and 0.0 there would read as no better than C0"
    )
    assert at_origin.vs_origin.headline is None


def test_an_individuals_cells_are_one_set_and_a_decision_never_reads_an_overlap_row() -> None:
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


def test_a_cell_is_its_samples_content_whatever_query_it_shares_or_slot_it_holds(
    built_stores: Stores,
) -> None:
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
            zip(
                slots,
                filed(
                    built_stores.archive,
                    [row.facts for row in rows],
                    filed_under(
                        [("", {"individual": f"keyed_{cycle_id}"})],
                        prompt_fields_id=f"keyed_{cycle_id}",
                    ),
                ),
                strict=True,
            ),
        )
        return scan_ledger_walks(built_stores.campaigns.chain(cycle_hop))

    first = filed_cycle("cycle_a", dict(enumerate(asks_one_thing)))
    assert len(individual_cells(first, "c0", RoleScope.REPORT)) == 2, "one query, two cells"
    second = filed_cycle("cycle_b", {7: asks_one_thing[1], 9: asks_one_thing[0]})
    folded = individual_cells([*first, *second], "c0", RoleScope.REPORT)
    later = individual_cells(second, "c0", RoleScope.REPORT)
    assert {key: answer for key, answer, _ in folded} == {
        key: answer for key, answer, _ in later
    }, "the same two samples at other slots are still two cells, at the later read"
    assert len(later) == 2


class _StandingRun(NamedTuple):
    run: list[RoundResult]
    closed: Callable[..., RoundResult]
    stands: Callable[[list[Any]], RunStanding]
    first_line: OverlapReading
    newest: OverlapReading
    reread: OverlapReading
    second_pick: OptSearchPoint
    third_pick: OptSearchPoint


@pytest.fixture
def standing_run() -> _StandingRun:
    """A line promoted twice off C0 and held once between, and the standing read after any prefix."""
    origin, first_pick, second_pick, third_pick = (
        prompted(instruction=text) for text in ("origin", "one", "three", "five")
    )
    named = {"C0": origin, "C1.1": first_pick, "C3.2": second_pick, "C5.1": third_pick}
    ids = {label: sp.id for label, sp in named.items()}

    def closed(rnd: int, on: OptSearchPoint, *arms: str, selected: str = "", **reading: Any) -> Any:
        """A held round ends on its parent *on*, re-read as the reference arm."""
        proposed = [
            arm_state(
                label,
                position=ArmPosition(round=rnd, idx=idx, total=len(arms)),
                individual=named.get(label) or prompted(instruction=label),
            )
            for idx, label in enumerate(arms)
        ]
        return round_result(
            rnd,
            arms=[arm_state("parent", individual=on), *proposed],
            improved=bool(selected),
            selected=ids[selected] if selected else None,
            voted={} if selected else {"parent": on.id},
            cells={},
            **reading,
        )

    def stands(run: list[Any]) -> RunStanding:
        return RunStanding.after(run, lives=None, outlay=LineOutlay(search_usd=None, worked_s=None))

    # C0 is re-read between the lines: the panel cell it took in round 1 it misses by round 3.
    first_line = _line_read(1, True, ("C1.1", _TIED_PANEL), c0=_C0_PANEL, ids=ids)
    newest = _line_read(
        3, True, ("C1.1", _TIED_PANEL), ("C3.2", _CLEAN_PANEL), c0=[0.0] * 9, ids=ids
    )
    reread = _line_read(4, False, ("C3.2", _CLEAN_PANEL), c0=_C0_PANEL, ids=ids)
    run = [
        closed(0, origin, "C0", selected="C0"),
        closed(1, first_pick, "C1.1", "C1.2", selected="C1.1", overlap=first_line),
        closed(2, first_pick, "C2.1"),  # held: nobody promoted, no line re-read
        closed(3, second_pick, "C3.1", "C3.2", selected="C3.2", overlap=newest),
    ]
    return _StandingRun(run, closed, stands, first_line, newest, reread, second_pick, third_pick)


def test_a_run_stands_on_its_selection_read_against_the_origin_it_was_promoted_from(
    standing_run: _StandingRun,
) -> None:
    run, closed, stands, first_line, newest, reread, second_pick, third_pick = standing_run
    assert (first_line.advance, newest.advance) == (
        RoundAdvance.NOT_SEPARATED,
        RoundAdvance.ADVANCED,
    )
    at_c0 = stands(run[:1])
    assert at_c0.selection is not None
    assert at_c0.selection.label == "C0"
    assert at_c0.vs_origin == PairedReading.unread(ReadingState.SAME_INDIVIDUAL)
    held = stands(run[:3])
    assert held.selection is not None
    assert held.selection.label == "C1.1", "a held round keeps the selection before it"
    assert held.vs_origin == first_line.lead
    standing_now = stands(run)
    assert standing_now.selection is not None
    assert standing_now.selection.model_dump() == {
        "key": second_pick.id,
        "round": 3,
        "label": "C3.2",
    }
    assert (standing_now.rounds_closed, standing_now.improved, standing_now.advanced) == (
        3,
        2,
        1,
    ), "promotions and separations are counted apart: a promotion is a point estimate"
    assert (at_c0.candidates_tested, standing_now.candidates_tested) == (0, 5), (
        "the climb's per-candidate divisor counts the arms rounds after the origin proposed, "
        "never the parent each re-read"
    )
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
    pass_stopped = OverlapReading.unpaired(ReadingState.PASS_STOPPED, 5, RoundEnding.CROWNED)
    unread = stands([*run, closed(5, third_pick, "C5.1", selected="C5.1", overlap=pass_stopped)])
    assert unread.selection is not None
    assert unread.selection.label == "C5.1"
    assert (unread.vs_origin.state, unread.vs_origin.headline) == (
        ReadingState.PASS_STOPPED,
        None,
    ), "a pick nobody has read against C0 yet says so — never the pick before it under its name"
    assert (standing_now.rounds_without_advance, unread.rounds_without_advance) == (0, 0), (
        "round 3 advanced, and an unread pick is no stall"
    )
    assert held_on.rounds_without_advance == 1


def test_the_climb_prices_c0s_lift_by_what_the_line_paid_and_an_absent_denominator_says_why(
    standing_run: _StandingRun,
) -> None:
    # C0's lift is +100 points.
    run, _, stands, *_ = standing_run
    at_c0, held, standing_now = stands(run[:1]), stands(run[:3]), stands(run)

    def climb(usd: float | None, worked: float | None) -> dict[str, tuple[str | None, str]]:
        paid = RunStanding.after(
            run, lives=None, outlay=LineOutlay(search_usd=usd, worked_s=worked)
        )
        assert paid.climb is not None
        return {rate.key: (rate.value, rate.sub) for rate in paid.climb.rates}

    unpriced, unspent = climb(None, None), climb(0.0, 50.0)
    assert unpriced["lift_per_usd"] == (None, COST_ABSENCE_WORDS[CostAbsence.UNPRICED_TOKENS])
    assert unspent["lift_per_usd"] == (None, COST_ABSENCE_WORDS[CostAbsence.ZERO_SPEND])
    assert unpriced["time_per_lift"] == (None, "no launch's clock parsed"), (
        "no clock is an absent time, never an instant climb"
    )
    priced = climb(2.0, 50.0)
    assert [value for value, _ in priced.values()] == ["+50.0\xa0pts", "+20.0\xa0pts", "0.5s"], (
        "a rate's lift is priced in points per dollar, per candidate tested and in worked "
        "seconds per point"
    )
    # Both legs come off one round reading: over C0 at round 1 they are one number.
    assert at_c0.vs_previous_pick is None
    assert held.vs_previous_pick == held.vs_origin and held.climb is not None
    assert held.climb.vs_previous_pick == held.climb.lift, "one pair read on two sheets at round 1"
    assert standing_now.vs_previous_pick is not None
    assert standing_now.vs_previous_pick == run[-1].overlap.vs_previous_pick


def _guarded(**moved: Any) -> GuardMember:
    """One campaign's guard member, its standing defaults, *moved* laid over them."""
    bench = BenchSet(
        instrument_id="i",
        dataset_hash="d",
        split=None,
        bench_rows=CellSet.of(
            CellSetName.BENCH_SPLIT, CellSetBasis.DECLARED, ["k0", "k1"], dataset_hash="d"
        ),
        scorer_id="s",
        models={},
        origin="o",
    )
    member = GuardMember(
        purpose=Optimize(),
        bench_set=bench,
        budget=defaults_config().optimization.budget,
        stopping=StoppingRule(lives=None),
        determinism=None,
        bench_defaults_pin="pin",
        optimizer_models=("m",),
        human_intervened=False,
        left_comparison=(),
    )
    return member._replace(**moved)


def test_a_pair_differs_on_exactly_what_its_members_ran_apart_on() -> None:
    other_rows = CellSet.of(
        CellSetName.BENCH_SPLIT, CellSetBasis.DECLARED, ["k0", "k2"], dataset_hash="d"
    )
    base = _guarded()
    assert base.bench_set is not None
    for moved, read_apart, differs_on in (
        ({}, False, []),
        (
            {"bench_set": base.bench_set.model_copy(update={"bench_rows": other_rows})},
            False,
            [GuardDifference.BENCH_ROWS],
        ),
        ({"budget": Budget(usd=1.0)}, False, [GuardDifference.BUDGET]),
        ({"bench_defaults_pin": "moved"}, False, [GuardDifference.DEFAULTS]),
        ({"optimizer_models": ("other/model",)}, False, [GuardDifference.OPTIMIZER_MODELS]),
        ({"human_intervened": True}, False, [GuardDifference.HUMAN_INTERVENED]),
        ({}, True, [GuardDifference.ORIGIN_READING]),
    ):
        guard = pair_guard([base, _guarded(**moved)], None, origin_read_apart=read_apart)
        assert guard.differs_on == differs_on, f"{moved}, origin read apart {read_apart}"
        assert guard.state is (GuardState.DIFFERS if differs_on else GuardState.UNCONTROLLED)


def test_an_arm_differences_each_rows_replicate_means_and_clusters_on_the_row() -> None:
    bank = samples(6, "t", query="w")
    measurand = grade_measurands(scorer_of(_H2H_FORMULA))[0]

    def seeded(level: float, tilt: float) -> list[float]:
        return [level + 0.1 * (i % 2) + tilt * (i % 3) for i in range(len(bank))]

    def arm(*levels: tuple[float, float]) -> list[MemberRows]:
        return [
            member_rows(
                f"r{n}", [measurement(i, v, objective=v) for i, v in enumerate(seeded(*lv))], bank
            )
            for n, lv in enumerate(levels)
        ]

    older, newer = ((0.1, 0.0), (0.2, 0.0)), ((0.1, 0.4), (0.0, 0.4))
    read = read_arms(
        arm(*older),
        arm(*newer),
        cells=[s.key for s in bank],
        measurand=measurand,
        spec=ROUND_LIFT_SPEC,
    )
    means = [
        [fmean(col) for col in zip(*(seeded(*lv) for lv in side), strict=True)]
        for side in (older, newer)
    ]
    known = {s.key: b - a for s, a, b in zip(bank, *means, strict=True)}
    assert read.differences == pytest.approx(known), "each row: b's replicate mean less a's"
    assert read.lift is not None and read.lift.estimate.value == pytest.approx(
        fmean(known.values())
    )
    pairs = [
        y - x
        for i in range(len(bank))
        for x in (seeded(*lv)[i] for lv in older)
        for y in (seeded(*lv)[i] for lv in newer)
    ]
    assert (read.se_clustered, read.se_naive) == pytest.approx(
        (stdev(known.values()) / sqrt(len(known)), stdev(pairs) / sqrt(len(pairs)))
    ), "the row is the cluster: its replicate pairings are never independent rows"


@pytest.fixture(scope="module")
def head_to_head(tmp_path_factory: pytest.TempPathFactory) -> Shared:
    """potter, capo and gepa as the arms of head-to-head `h2h`, and a capo campaign outside it:
    ~3.6 s once, read by three facts (~25 ms each)."""

    async def ran(w: World) -> tuple[list[str], str]:
        arms = await w.head_to_head({k: k for k in ("potter", "capo", "gepa")}, "h2h", rows=60)
        lone = await w.campaign("capo", rows=60)
        return [a.session.campaign_id for a in arms], lone.session.campaign_id

    return shared_run(tmp_path_factory, ran)


@pytest.mark.usefixtures("optimizing_memory")
def test_a_head_to_heads_arms_are_controlled_and_corrected_as_one_family(
    world: World, head_to_head: Shared
) -> None:
    stores = world.adopt(head_to_head).stores
    arms, _ = head_to_head.made
    table = subject_evidence(stores, [Subject(c) for c in arms]).head_to_head
    assert table is not None and table.verdict is True
    assert [r.guard.state for r in table.rows] == [GuardState.CONTROLLED] * 3
    assert [(c.a.optimizer, c.b.optimizer) for c in table.contrasts] == [
        ("potter", "capo"),
        ("potter", "gepa"),
        ("capo", "gepa"),
    ], "each treatment is one arm, the older arm `a`"
    for contrast in table.contrasts:
        lift = contrast.lift
        assert lift is not None and lift.family is not None
        assert (lift.family.id, lift.family.n_tests) == ("h2h", 3)
        assert lift.estimate.value == pytest.approx(fmean(contrast.differences.values()))
        assert contrast.q_value is not None and contrast.q_value <= lift.family.p_adjusted, (
            "BH never gates harder than Holm"
        )
    ranked = sorted(
        (c.lift for c in table.contrasts if c.lift is not None), key=lambda x: x.p_value
    )
    assert ranked[0].family is not None
    assert ranked[0].family.p_adjusted == pytest.approx(min(1.0, 3 * ranked[0].p_value))
    potter = Subject(arms[0], stores.campaigns.campaign_cycle_dirs(arms[0])[0].name)
    for roster in ([potter, *map(Subject, arms[1:])], [potter, *map(Subject, arms)]):
        again = subject_evidence(stores, roster).head_to_head
        assert again is not None and again.contrasts == table.contrasts, (
            "a cycle read as a subject of its own beside its campaign"
        )


def test_an_embedded_head_to_head_is_filed_as_its_command(head_to_head: Shared) -> None:
    with head_to_head.reading() as stores:
        filed = [record.kind for _, record in stores.commands.book().lines()]
    assert filed == [CommandKind.HEAD_TO_HEAD, CommandKind.MINT_CAMPAIGN], (
        f"an embedded head-to-head went unfiled: {filed}"
    )


def test_a_head_to_head_mint_raising_at_its_second_arm_leaves_the_first_stamped(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    drawn = mint.fresh_campaign_id
    calls: list[str] = []

    def second_raises(session: Session) -> str:
        if calls:
            raise RuntimeError("the second arm's mint died")
        calls.append(drawn(session))
        return calls[0]

    monkeypatch.setattr(mint, "fresh_campaign_id", second_raises)
    with pytest.raises(RuntimeError, match="second arm"):
        asyncio.run(world.head_to_head({"p": "potter", "c": "capo"}, "torn", rows=60))
    (first,) = world.stores.campaigns.list_campaigns(lifecycle="all")
    hop = CycleHop(campaign_id=first.campaign_id, cycle_id=first.root_cycle_id)
    stops = [r for r in world.records(hop) if isinstance(r, RunPhaseRecord)]
    assert stops and isinstance(stops[-1].state, Ended), (
        "an arm minted before its sibling's raise was left unstamped"
    )


@pytest.mark.usefixtures("optimizing_memory")
def test_a_campaign_outside_the_head_to_head_joins_its_treatments_arm_uncorrected_with_it(
    world: World, head_to_head: Shared
) -> None:
    stores = world.adopt(head_to_head).stores
    arms, lone = head_to_head.made
    table = subject_evidence(stores, [Subject(c) for c in (*arms, lone)]).head_to_head
    assert table is not None and table.verdict is True
    states = {r.campaign_id: r.guard.state for r in table.rows}
    assert states[lone] is GuardState.UNCONTROLLED and states[arms[1]] is GuardState.CONTROLLED
    [capo] = [c for c in table.contrasts if {c.a.optimizer, c.b.optimizer} == {"potter", "capo"}]
    assert capo.b.replicates == [arms[1], lone], "one treatment is one arm, over its replicates"
    tested = Counter(c.guard.state for c in table.contrasts if c.lift is not None)
    for contrast in table.contrasts:
        assert contrast.lift is not None and contrast.lift.family is not None
        assert contrast.lift.family.n_tests == tested[contrast.guard.state], (
            "a controlled and an uncontrolled contrast were corrected as one family"
        )


@pytest.mark.usefixtures("optimizing_memory")
def test_an_act_on_an_arm_takes_it_out_of_its_head_to_head(
    world: World, head_to_head: Shared
) -> None:
    # A rewind or a fork leaves the comparison; a skipped step is a human's hand on the run.
    stores = world.adopt(head_to_head).stores
    arms, _ = head_to_head.made
    acted = dict(zip(arms, (ArmAct.REWIND, ArmAct.FORK, ArmAct.SKIP), strict=True))
    for cid, act in acted.items():
        campaign = stores.campaigns.load_campaign(cid)
        assert campaign is not None
        stores.campaigns.act(stores.campaigns.live_cycle(campaign.root_hop), act)
    table = subject_evidence(stores, [Subject(c) for c in arms]).head_to_head
    assert table is not None
    states = {r.campaign_id: r.guard.state for r in table.rows}
    assert [states[c] for c in arms] == [
        GuardState.UNCONTROLLED,
        GuardState.UNCONTROLLED,
        GuardState.DIFFERS,
    ], f"{acted} left another standing than {states}"


def test_a_two_valued_scorers_headline_separates_on_its_paired_flips() -> None:
    # `round_advance` reads separation off this side: 11 gained, 0 lost advances the line.
    bank = samples(12, "t")

    def member(name: str, grades: list[float]) -> MemberRows:
        rows = [measurement(i, g, objective=g) for i, g in enumerate(grades)]
        return member_rows(name, rows, bank)

    measurands = grade_measurands(dataclasses.replace(scorer_of(), id="s"))
    lift = _read_pair(
        member("c0", [0.0] * 11 + [1.0]), member("c1", [1.0] * 12), bank, measurands=measurands
    ).headline
    assert lift is not None and lift.measurand.binary, "a matcher's 0/1 fitness read as graded"
    assert lift.side == "above", "11 gained, 0 lost read inside the noise"


# 8. A bare-answer backend — the n8n eval harness


_HARNESS_GOLD = '{"requires_action": ["true"]}'
_HARNESS_CONTRACT = {
    "output_schema": {"type": "object", "properties": {"requires_action": {"type": "string"}}},
    "answer_fields": ["requires_action"],
}
_HARNESS_CANDIDATE = {
    "prompt": "## requires_action\nTrue when the sender asks for a reply.",
    **_HARNESS_CONTRACT,
}
_HARNESS_ROW = Sample(id=0, query='{"subject": "Invoice 1023"}', ground_truth=_HARNESS_GOLD)


@pytest.fixture
def harness_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, world: World
) -> Callable[..., MeasuredCell]:
    monkeypatch.setattr(settings, "N8N_HARNESS_ENDPOINTS", {"classify": FakeHarness.URL})
    monkeypatch.setattr(settings, "N8N_HARNESS_TOKEN", SecretStr("harness-secret"))

    def cell(params: dict[str, Any], book_usd: float | None = None) -> MeasuredCell:
        async def measured() -> MeasuredCell:
            async with world.opened(connector=n8n.CONNECTOR) as session:
                with (
                    scoped(ledger=CycleEventLog(tmp_path / "harness.jsonl")),
                    scoped(round=1),
                    scoped(book=spend_book(book_usd, usd_reserve=None)),
                ):
                    return await measure_sample(
                        _HARNESS_ROW, session, PipelineParams.model_validate(params)
                    )

        return asyncio.run(measured())

    return cell


def test_an_n8n_cell_delivers_the_candidate_and_reads_the_harnesss_answer(
    harness_cell: Callable[..., MeasuredCell], offline: Router
) -> None:
    read = harness_cell({"harness": {**_HARNESS_CANDIDATE, "model": "another/model"}})
    headers, sent = offline.harness.asked[-1]
    rode = (headers.get("x-eval-secret"), headers.get("authorization"))
    assert rode == ("harness-secret", None), (
        "the secret rode another header than the operator named"
    )
    assert sent == {
        "case_id": _HARNESS_ROW.key,
        "input": json.loads(_HARNESS_ROW.query),
        "expected": json.loads(_HARNESS_GOLD),
        "prompt": _HARNESS_CANDIDATE["prompt"],
        "params": _HARNESS_CONTRACT,
    }, "the harness was sent another candidate, or a model a dataset aimed it at"
    assert (read.predicted, read.error_category) == ("true", None), (
        "the harness's answer was not what the cell read"
    )
    assert read.cost_s is not None, "a harness cell's round trip went unclocked"


def test_an_n8n_object_answer_is_graded_as_its_fields(
    harness_cell: Callable[..., MeasuredCell], offline: Router
) -> None:
    offline.harness.says = lambda body: {"case_id": body["case_id"], "answer": {"a": "x"}}
    assert json.loads(harness_cell({"harness": _HARNESS_CANDIDATE}).predicted) == {"a": "x"}, (
        "an object answer was graded as anything but its fields"
    )


def test_an_unprompted_n8n_cell_asks_the_harness_for_its_production_text(
    harness_cell: Callable[..., MeasuredCell], offline: Router
) -> None:
    harness_cell({"harness": {}})
    assert offline.harness.asked[-1][1]["prompt"] == "", (
        "an unprompted cell asked the harness for anything but its production text"
    )


def test_an_n8n_harness_that_answered_nothing_is_a_hole_never_a_wrong_answer(
    harness_cell: Callable[..., MeasuredCell], offline: Router
) -> None:
    replies: tuple[Callable[[dict[str, Any]], dict[str, Any] | httpx.Response], ...] = (
        lambda body: {"case_id": body["case_id"], "answer": "  ", "raw": {}},
        lambda _body: httpx.Response(403, json={"message": "Authorization data is wrong!"}),
    )
    for reply in replies:
        offline.harness.says = reply
        hole = harness_cell({"harness": _HARNESS_CANDIDATE})
        assert hole.errored and hole.predicted != NO_RESULT, (
            "a harness that answered nothing was graded as a wrong answer"
        )


def test_an_n8n_cell_no_rate_prices_is_refused_under_a_dollar_ceiling(
    harness_cell: Callable[..., MeasuredCell], offline: Router
) -> None:
    asked = len(offline.harness.asked)
    with pytest.raises(SendRefusedError):
        harness_cell({"harness": _HARNESS_CANDIDATE}, book_usd=0.5)
    assert len(offline.harness.asked) == asked, (
        "a cell no rate prices was sent under a dollar ceiling it cannot be held to"
    )


def test_a_merge_of_two_reads_of_one_cell_fits_theta_on_one_row() -> None:
    stamp = RulerStamp(
        id="v", version=0, own=None, scope=RulerScope(scorer="s", dataset="ds"), excluded=()
    )
    judging = JudgingRuler(None, stamp)
    first = sheet(measurements([float(i % 2) for i in range(10)]))
    merged = first.merged(sheet(measurements([1.0, 0.0], [0, 1])))
    assert len(merged) == 10, "a merge held one cell twice"
    reading = judging.frontier(merged)
    assert reading is not None and reading.cells == 10, "θ was fit on a cell's duplicate"
    standing = sheet(measurements([1.0, 0.0, *[float(i % 2) for i in range(2, 10)]]))
    folded = judging.frontier(standing)
    assert folded is not None and (reading.theta, reading.se) == (folded.theta, folded.se), (
        "θ over the merge is not θ over the cells it stands on"
    )


_PEER_BANK = samples(16, "TRUE")


# Each search row's place in the order the search reads it, under the peer cycle's own split.
_PEER_READ_ORDER = {
    s.id: n
    for n, s in enumerate(
        partition_bank(_PEER_BANK, DatasetSplit(bench=0, demo=2, tolerance=0), seed=0).search
    )
}


def _peer_round(
    stores: Stores,
    offline: Router,
    optimizer: str,
    nodes: dict[str, Any],
    solves: Callable[[str, Sample], bool],
    *,
    bank: Sequence[Sample] = _PEER_BANK,
    bench: int = 0,
    **optimization: Any,
) -> AbstractAsyncContextManager[RoundContext]:
    """The peer campaign's round 1 opened through run init's door on *bank*; the backend answers
    right where *solves* says. Its composite REWARDS length, the opposite of CAPO's objective."""
    rows = {s.query: s for s in bank}
    offline.backend.answers = lambda prompt, query: solves(prompt, rows[query])
    return opened_round(
        stores,
        offline,
        bank,
        optimization={
            "optimizer": optimizer,
            "elimination_n_min": 2,
            "degradation_threshold": 0.0,
            "nodes": nodes,
            **optimization,
        },
        split={"bench": bench, "demo": 2},
        layer={
            "scoring": {
                "per_sample": "label_match(predicted, ground_truth)",
                "per_cell": "fitness * min(1.0, target_prompt_chars / 200.0)",
            },
        },
    )


def _asked(body: dict[str, Any]) -> str:
    """What an optimizer call sent the provider, every message of it."""
    return "\n".join(str(m["content"]) for m in body["messages"])


_CAPO_NODES = {
    "blocks": {"config": {"block_size": 4, "max_blocks": 3}},
    "capo_init": {"config": {"size": 3, "k_max": 0}},
    "mating": {"config": {"offspring": 2}},
    "few_shot": {"config": {"k_max": 0}},
    "survival": {"config": {"size": 3}},
}


def _capo_says() -> Callable[[dict[str, Any]], str]:
    """CAPO's seed batch, three prompts, then each mutation a fresh one."""
    minted: list[str] = []

    def says(body: dict[str, Any]) -> str:
        if "Create overall 15 prompts" in _asked(body):
            return json.dumps(["Solve the task, A.", "Solve the task, B.", "Solve the task, C."])
        minted.append(f"Go {len(minted)}.")
        return f"<prompt>{minted[-1]}</prompt>"

    return says


@pytest.mark.usefixtures("optimizing_memory")
@on_jumping_clock
async def test_a_round_the_budget_cuts_elects_only_past_the_panel_gate_and_stands_once_closed(
    offline: Router,
) -> None:
    # Round 1 races the seeds with its offspring; its parent is the origin run init walked. Each case
    # opens its own campaign: it writes its own cut, under its own panel gate, on its own archive.
    async def cut_by_budget(
        cut_round: int, budget: int, replayed: int, parent_cells: int, panel_gate: str
    ) -> None:
        case = f"round {cut_round} cut under panel_gate={panel_gate}"

        paid: list[int] = []
        # Empty while the origin is measured, which no round's budget pays for.
        running: list[bool] = []
        cutting: list[bool] = []

        def even_cells(_prompt: str, sample: Sample) -> bool:
            if running:
                paid.append(sample.id)
                if cutting and len(paid) >= budget:
                    require_spend_book("the cut").share = Budget(usd=0.0)
            return _PEER_READ_ORDER[sample.id] % 2 == 0

        async with _peer_round(
            workspace(default_identity(f"cut-{cut_round}-{panel_gate}")),
            offline,
            "capo",
            _CAPO_NODES,
            even_cells,
            panel_gate=panel_gate,
        ) as ctx:
            cycle = ctx.cycle
            offline.llm.says = _capo_says()
            session = cycle.session
            running.append(True)
            if cut_round == 2:
                uncut = await execute_round(cycle, 1)
                assert uncut.cut is None, f"{case}: round 1 ran inside its budget"
                await post_round(cycle, uncut, session, is_final_round=False)
            paid.clear()
            parent = ArmKey(cycle.parent.id)
            cutting.append(True)

            async def walked_cut() -> Any:
                with scoped(round=cut_round):
                    return await execute_round(cycle, cut_round)

            cut_walk = walked_cut()
            if panel_gate == "strict":
                with pytest.raises(StopLoop) as halted:
                    await cut_walk
                assert halted.value.reason is StopReason.SPEND_BUDGET, (
                    f"{case}: the bound that cut it names the stop"
                )
                assert cycle.rounds[-1].round == cut_round - 1, (
                    f"{case}: an arm was crowned on a holed panel"
                )
                return
            _, closed, cut, _ = await cut_walk
            assert cut is not None and cut.reason is StopReason.SPEND_BUDGET, (
                f"{case}: the round names its cut"
            )
            assert cut.detail, f"{case}: a kept cut halts the loop with no words"
            rows = {key: closed.rows(key) for key in closed.proposed}
            assert sum(len(closed.rows(key)) for key in closed.arms) == (
                replayed + len(paid) + parent_cells
            ), f"{case}: a cell nobody elects on, or a parent read where the archive lacks it"
            assert (parent in closed.proposed) is (cut_round == 2), (
                f"{case}: a retained parent is ONE arm, its re-read folded into its own cells"
            )
            parent_arm = closed.arms[parent]
            assert isinstance(parent_arm, Cut | Measured), f"{case}: the parent never concluded"
            held = session.store.ruler.rows(session_ruler_scope(session), ruler_clock(session))
            observed = {cell for walked, cell, _ in held if walked == parent_arm.config_key}
            assert {cell.key for cell in closed.rows(parent)} <= observed, (
                f"{case}: a parent's observation never reached the δ fit its election read"
            )
            [short] = [key for key in rows if len(rows[key]) == 1]
            assert closed.folds[short].fitness == 1.0, (
                f"{case}: the cut arm reads perfect on its one cell"
            )
            carried = [ind.id for ind in cycle.population]
            assert len(carried) == 3 and short not in carried, f"{case}: elected below the floor"
            full = next(key for key in list(rows)[3:] if key != short)
            assert full in carried, f"{case}: an offspring the budget paid for in full was dropped"
            assert cycle.rounds[-1].round == cut_round
            cycle.unwind_unclosed()
            assert [rr.round for rr in cycle.rounds] == list(range(cut_round)) and (
                cycle.selection.round == cut_round - 1
            ), f"{case}: a stop between this election and its close leaves no result"

    # Round 1's parent reads the 4 cells run init walked (CAPO's first block); round 2's is a
    # retained member, whose re-read lands on cells its own walk holds.
    for cut_round, budget, replayed, parent_cells in ((1, 17, 0, 4), (2, 5, 12, 0)):
        for panel_gate in ("strict", "off"):
            await cut_by_budget(cut_round, budget, replayed, parent_cells, panel_gate)


@pytest.mark.usefixtures("optimizing_memory")
@on_jumping_clock
async def test_a_cut_theta_round_reads_its_cold_parent_on_the_cells_the_archive_holds_graded(
    monkeypatch: pytest.MonkeyPatch, offline: Router, peer_composition: _Composition
) -> None:
    # The guard and the election alike.
    name, walk, composed, crossover_says, _, composed_nodes = peer_composition
    # Empty until round 1 walks, so the origin's cells pay toward no cut.
    theta_walking: list[bool] = []
    theta_paid: list[int] = []

    def paid_then_cut(_prompt: str, sample: Sample) -> bool:
        if theta_walking:
            theta_paid.append(sample.id)
            if len(theta_paid) >= 2:
                require_spend_book("the cut").share = Budget(usd=0.0)
        return True

    measure = vars(query_loop)["measure_sample"]

    async def unpriced(sample: Sample, at: Any, pipeline_params: Any) -> MeasuredCell:
        cell: MeasuredCell = await measure(sample, at, pipeline_params)
        if theta_walking or _PEER_READ_ORDER[sample.id] != 1:
            return cell
        # The origin's second row filed with no `target_prompt_chars`: the composite cannot grade it.
        unread = dataclasses.replace(cell.pipeline, target_prompt_chars=None)
        return dataclasses.replace(cell, pipeline=unread)

    monkeypatch.setattr(query_loop, "measure_sample", unpriced)
    with composed([*walk[:-1], "theta_election"]):
        async with _peer_round(
            workspace(), offline, name, composed_nodes, paid_then_cut, panel_gate="off"
        ) as ctx:
            cycle = ctx.cycle
            offline.llm.says = crossover_says
            theta_walking.append(True)
            with pytest.raises(StopLoop) as unwound:
                await execute_round(cycle, 1)
    assert unwound.value.reason is StopReason.SPEND_BUDGET
    assert cycle.rounds[-1].round == 0, "a cut round stood with no arm its selector could read"


def _bench_says(body: dict[str, Any]) -> str:
    if "Create overall 15 prompts" in _asked(body):
        return json.dumps(["Solve it GOOD.", "Solve it OK."])
    return "<prompt>Solve it BAD.</prompt>"


# More bench rows than the connector sends at once (`termnorm.py`'s `max_cells_in_flight`), so a
# pause can land on a pass with rows still unsent.
_BENCH_ROWS = 20
_BENCH_BANK = samples(32, "TRUE")


class _Bench(NamedTuple):
    rounds: list[RoundResult]
    ledger: CycleEventLog
    session: Session
    points: PointContext
    picked: RoundResult
    head: Cut | Measured
    fold: ScoreSummary
    origin_pass: BenchPass


def _bench_solves(prompt: str, sample: Sample) -> bool:
    return "GOOD" in prompt or ("OK" in prompt and sample.id % 2 == 0)


@asynccontextmanager
async def _peer_bench(offline: Router) -> AsyncIterator[Cycle]:
    """The bench's CAPO campaign held open in its run, round 0 closed by run init's door."""
    bench_nodes = {
        "blocks": {"config": {"block_size": 4, "max_blocks": 2}},
        "capo_init": {"config": {"size": 2, "k_max": 0}},
        "mating": {"config": {"offspring": 1}},
        "few_shot": {"config": {"k_max": 0}},
        "survival": {"config": {"size": 2}},
    }
    offline.llm.says = _bench_says
    async with _peer_round(
        workspace(),
        offline,
        "capo",
        bench_nodes,
        _bench_solves,
        bank=_BENCH_BANK,
        bench=_BENCH_ROWS,
    ) as ctx:
        yield ctx.cycle


async def _banked_bench(offline: Router) -> tuple[Cycle, BenchPass]:
    """Its round 1 run and closed, whose declared pick tops a higher-composite arm; then the
    origin's bench pass banked as a pass outside its loop banks it."""
    async with _peer_bench(offline) as cycle:
        session, stores = cycle.session, cycle.session.store
        await post_round(cycle, await execute_round(cycle, 1), session, is_final_round=True)
    async with open_diagnostic(stores, stores.campaigns.campaign_cycle(session.hop)) as opened:
        origin = cycle.origin_round.origin.individual
        async with _outside_pass(opened.session):
            origin_pass = await score_on_bench(
                opened.session,
                opened.points.measured(cycle.measured(origin.id)),
                individual_id=origin.id,
                subject="origin",
                label="C0",
                round_num=0,
            )
    return cycle, origin_pass


@pytest.fixture(scope="module")
def banked_line(tmp_path_factory: pytest.TempPathFactory) -> Shared:
    """`_banked_bench`'s line: ~0.6 s once, ~25 ms a fact on its own copy."""

    async def banked(w: World) -> tuple[CycleHop, BenchPass]:
        with scoped(memory=OpenAt()):
            cycle, origin_pass = await _banked_bench(w.net)
        return cycle.session.hop, origin_pass

    return shared_run(tmp_path_factory, banked)


@asynccontextmanager
async def _bench_at(stores: Stores, hop: CycleHop, origin_pass: BenchPass) -> AsyncIterator[_Bench]:
    """*hop*'s line opened as every pass outside its loop opens it, its rounds as its line's
    bench reads them (`bench.py::line_spans`)."""
    at, ledger = (
        stores.campaigns.campaign_cycle(hop),
        CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(hop))),
    )
    async with open_diagnostic(stores, at) as opened:
        scorer = opened.session.state.scoring.scorer
        rounds = closed_rounds.closed_rounds(stores, hop, scorer, moment=None)
        picked = rounds[-1]
        head, fold = picked.head, picked.fold
        assert head is not None and fold is not None and fold.fitness is not None
        yield _Bench(rounds, ledger, opened.session, opened.points, picked, head, fold, origin_pass)


@asynccontextmanager
async def _bench_cycle(world: World, line: Shared) -> AsyncIterator[_Bench]:
    """This fact's own copy of *line*, the backend answering its rows as it did."""
    stores = world.adopt(line).stores
    rows = {s.query: s for s in _BENCH_BANK}
    world.net.backend.truth |= {query: s.ground_truth for query, s in rows.items()}
    world.net.backend.answers = lambda prompt, query: _bench_solves(prompt, rows[query])
    hop, origin_pass = line.made
    async with _bench_at(stores, hop, origin_pass) as bench:
        yield bench


@asynccontextmanager
async def _outside_pass(session: Session) -> AsyncIterator[SpendBook]:
    """A bench pass on *session*'s cycle outside its loop, admitted and filed as one is: under
    the round an `at_end` launch grades, its last (`passes.py::grade_line_bench`)."""
    stores = session.store
    standing = stores.campaigns.standing_rounds(session.hop, moment=None).rounds
    async with paid_pass(
        stores=stores,
        pass_kind=MeasurementRole.BENCH,
        at=stores.campaigns.campaign_cycle(session.hop),
        declared=Budget(),
        sends=(),
    ) as book:
        with verb_ledger(stores, session.hop), scoped(round=max(standing)):
            yield book


async def _graded(
    bench: _Bench,
    *held: BenchPass,
    trigger: BenchTrigger = "at_end",
    spent: bool = False,
) -> BenchPasses:
    """*bench*'s selections graded in a pass of their own; *spent* empties its book first."""
    async with _outside_pass(bench.session) as book:
        if spent:
            book.share = Budget(usd=0.0)
        return await bench_selection(
            bench.session,
            bench.rounds,
            points=bench.points,
            banked=bench_passes(bench.origin_pass, *held),
            trigger=trigger,
        )


async def _pause_mid_pass(
    bench: _Bench, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[RunPhase], list[str], Any]:
    """A pause sent as the bench pass walks its second cell; the last graded view it filed."""
    ledger, stores, hop = bench.ledger, bench.session.store, bench.session.hop
    pause = PauseCyclePayload(campaign_id=hop.campaign_id, cycle_id=hop.cycle_id, reason="stop")
    measure = vars(query_loop)["measure_sample"]
    walked: list[int] = []
    phases: list[RunPhase] = []
    acks: list[str] = []

    async def _stopped_mid_pass(sample: Sample, at: Any, pipeline_params: Any) -> MeasuredCell:
        walked.append(sample.id)
        if len(walked) == 2:
            phases.append(stores.run_state(hop).run_phase)
            acks.append(
                (await CommandDispatcher(stores).dispatch(CommandCall(pause, door=Door.HOST))).ack
            )
            phases.append(stores.run_state(hop).run_phase)
        cell: MeasuredCell = await measure(sample, at, pipeline_params)
        return cell

    with monkeypatch.context() as patched:
        patched.setattr(query_loop, "measure_sample", _stopped_mid_pass)
        with pytest.raises(PassStoppedError):
            await _graded(bench)
    [*_, stopped_view] = [
        record for record in ledger_records(ledger) if isinstance(record, PassGradedRecord)
    ]
    return phases, acks, stopped_view


class _ClosedOrigin(NamedTuple):
    drawn: list[Sample]
    session: Session
    origin_sp: JobSearchPoint


def _closed_origin(cycle: Cycle) -> _ClosedOrigin:
    """Round 0 as run init's door closed it, and its session on the bank less one row it
    recorded, so a band's rescore can only read the rest."""
    session = cycle.session
    origin = cycle.origin_round.origin.individual
    round_zero = session.store.campaigns.standing_rounds(session.hop, moment=None).rounds[0].close
    assert round_zero.election is not None
    recorded_keys = {key for key, _, _ in round_zero.cells.arms[round_zero.election.ended_on]}
    drawn = [s for s in session.bank.samples if s.key in recorded_keys]
    kept = [s for s in session.bank.samples if s.key != drawn[0].key]
    return _ClosedOrigin(
        drawn, dataclasses.replace(session, bank=Bank.of(kept)), cycle.points.job(origin)
    )


@pytest.mark.usefixtures("optimizing_memory")
@on_jumping_clock
async def test_a_pause_reaches_a_bench_pass_in_flight_and_it_banks_as_stopped_on_the_cells_it_walked(
    monkeypatch: pytest.MonkeyPatch, world: World, banked_line: Shared
) -> None:
    async with _bench_cycle(world, banked_line) as bench:
        stores, hop = bench.session.store, bench.session.hop
        cycle_dir = stores.campaigns.cycle_dir(hop)
        phases, acks, stopped_view = await _pause_mid_pass(bench, monkeypatch)
        assert (phases, acks) == ([RunPhase.SCORING, RunPhase.PAUSED], ["accepted"]), (
            "a pause was refused by a pass in flight, or the cycle never read as scoring"
        )
        assert isinstance(stopped_view, PassGradedRecord)
        paused_pass = stopped_view.verification
        assert stopped_view.state is ReadingState.PENDING and paused_pass.stop is not None, (
            "a paused pass read as faulted, when `resume` sends it again"
        )
        walked = len(paused_pass.cells)
        assert paused_pass.stop.cause is StopReason.PAUSED and 0 < walked < _BENCH_ROWS, (
            "a paused pass banked no record of the cells it walked, or never stopped"
        )
        assert stores.inbox(hop).pause is None and not cycle_held(cycle_dir), (
            "the pass left its pause unapplied, or kept the cycle after it stopped"
        )


@pytest.mark.usefixtures("optimizing_memory")
@on_jumping_clock
async def test_a_noise_band_stands_beside_the_recorded_level_of_exactly_the_rows_it_rescored(
    offline: Router,
) -> None:
    async with _peer_bench(offline) as bench:
        ledger = cycle_ledger(bench)
        drawn, session, origin_sp = _closed_origin(bench)
        band = await held_out.measure_noise_floor(session, origin_sp, 2)
        assert isinstance(band.reading, NoiseBand) and len(band.reading.raw) == 2
        assert band.reading.recorded.n == len(band.verification.sample_keys) == len(drawn) - 1, (
            "the band stood beside a recorded level over rows it never re-scored (bl3.B2)"
        )
        assert band in [
            record for record in ledger_records(ledger) if isinstance(record, PassGradedRecord)
        ], "the band was not filed on its cycle's ledger"


@pytest.mark.usefixtures("optimizing_memory")
@on_jumping_clock
async def test_a_noise_floor_that_scored_cells_and_read_no_band_files_them_unread(
    monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    async with _peer_bench(offline) as bench:
        _, session, origin_sp = _closed_origin(bench)
        replicates: list[int] = []

        async def _second_stops(*args: Any, **kwargs: Any) -> Any:
            walk = await score_search_point(*args, **kwargs)
            replicates.append(len(replicates))
            return (
                walk
                if len(replicates) == 1
                else dataclasses.replace(walk, stopped=WalkEnd.CLIENT_ERROR)
            )

        with monkeypatch.context() as patched:
            patched.setattr(held_out, "score_search_point", _second_stops)
            band = await held_out.measure_noise_floor(session, origin_sp, 2)
    assert band.verification.cells and band.reading is None, "the scored cells were not kept"
    assert band.state is ReadingState.MEMBER_UNSCOREABLE, "a band read from one replicate"


@pytest.mark.usefixtures("optimizing_memory")
@on_jumping_clock
async def test_a_rescore_that_raises_ends_the_bands_mark_at_its_brackets_exit(
    monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    async with _peer_bench(offline) as bench:
        ledger = cycle_ledger(bench)
        _, session, origin_sp = _closed_origin(bench)

        async def _outage(*_: object, **__: object) -> NoReturn:
            raise RuntimeError("the rescore raised")

        failed_from = ledger.next_offset
        with monkeypatch.context() as patched:
            patched.setattr(held_out, "score_search_point", _outage)
            with pytest.raises(RuntimeError):
                await held_out.measure_noise_floor(session, origin_sp, 2)
        dashboard = LiveDashboardProjection()
        dashboard.feed(run_wiring("llm_only"))
        for at, record in enumerate(ledger_records(ledger)):
            if at >= failed_from:
                dashboard.feed(record)
        served = dashboard.value()
        assert served is not None
        assert [(m.state, m.failed_as) for m in served.verification if m.kind == "noise_floor"] == [
            ("failed", ReadingState.PASS_STOPPED)
        ], "a noise floor that raised left its mark running"


@pytest.mark.usefixtures("optimizing_memory")
@on_jumping_clock
async def test_a_spent_budget_stops_the_bench_pass_and_the_held_passes_beside_it_stand(
    world: World,
    banked_line: Shared,
) -> None:
    async with _bench_cycle(world, banked_line) as bench:
        earlier = bench.origin_pass.model_copy(
            update={"arm": ArmName(key=ArmKey("an earlier pick"), round=7, label="C7.1")}
        )
        short = await _graded(bench, earlier, spent=True)
        stop = short.selections[bench.head.individual.id].stop
        assert stop is not None and stop.cause is StopReason.SPEND_BUDGET, (
            "a spent budget stops the pass and names the bound"
        )
        assert short.selections[earlier.arm.key] is earlier, "a held pass was dropped"


# The refused pass holds the cells the paused pass banked.
@pytest.mark.usefixtures("optimizing_memory")
@on_jumping_clock
async def test_a_refusal_mid_walk_banks_the_cells_the_pass_held_never_zero_over_paid_rows(
    monkeypatch: pytest.MonkeyPatch, world: World, banked_line: Shared
) -> None:
    async with _bench_cycle(world, banked_line) as bench:
        paused_pass = (await _pause_mid_pass(bench, monkeypatch))[2].verification

        async def _refused_mid_pass(*_: Any) -> MeasuredCell:
            raise SendRefusedError("no rate for this model", category=ErrorCategory.NO_RATE)

        with monkeypatch.context() as patched:
            patched.setattr(query_loop, "measure_sample", _refused_mid_pass)
            no_rate = (await _graded(bench)).selections[bench.head.individual.id]
        assert no_rate.stop is not None and no_rate.stop.cause is StopReason.NO_RATE, (
            "a refused pass did not name its refusal"
        )
        assert {key for key, _, _ in paused_pass.cells} <= {key for key, _, _ in no_rate.cells}, (
            "a refused pass banked none of the cells it held (bl3.B4)"
        )


# Each regrade reads the last pass.
@pytest.mark.usefixtures("optimizing_memory")
@on_jumping_clock
async def test_the_bench_grades_the_pick_the_optimizer_declared_over_a_higher_composite_round(
    offline: Router,
) -> None:
    # Its own line: the result it builds reads the run's `Cycle` (`entry.py::build_cycle_result`).
    cycle, origin_pass = await _banked_bench(offline)
    async with _bench_at(cycle.session.store, cycle.session.hop, origin_pass) as picked:
        session, head = picked.session, picked.head
        passes = await _graded(picked)
        [taken] = passes.selections.values()
        assert (taken.arm.key, taken.arm.round, taken.stop) == (head.individual.id, 1, None), (
            "the bench grades the pick the optimizer declared"
        )
        assert (await _graded(picked, taken)).selections[taken.arm.key] is taken, (
            "a held pass was sent again"
        )
        left = taken.model_copy(update={"sp_hash": "a pick the line left"})
        resent = (await _graded(picked, left)).selections[taken.arm.key]
        assert [c[:2] for c in resent.cells] == [c[:2] for c in taken.cells]
        assert all(replayed for *_, replayed in resent.cells), "a resent pass bought a cell again"
        bench = read_bench(
            session.store,
            passes,
            session.state.scoring.scorer,
            line=bench_line(
                taken.arm,
                hop=session.hop,
                instrument_id="capo-e2e",
            ),
        )
        assert bench.selected is not None and bench.origin is not None
        selected_accuracy, origin_fitness = bench.selected.fitness, bench.origin.fitness
        assert selected_accuracy is not None and origin_fitness is not None
        # The pick answers the even rows ("OK"), the origin none: each read on the bench's rows.
        assert (picked.fold.composite_fitness or 0) < max(
            fold.composite_fitness or 0 for fold in picked.picked.folds.values()
        ), "guard: a higher-composite arm stood beside the declared pick"
        even = fmean(s.id % 2 == 0 for s in session.state.scoring.partition.bench)
        assert selected_accuracy.value == pytest.approx(even) and origin_fitness.value == 0.0
        session.state.clock = LaunchClock.now()
        result = build_cycle_result(
            cycle,
            session,
            stop_reason=StopReason.ROUNDS_BUDGET,
            stepped=False,
            cycle_error=None,
            finished_at="",
            spend=None,
            bench=bench,
            selected=select_optimizer(cycle.config.optimization),
            fitness_ceiling=None,
        )
        assert result.result_round == bench.selected.round == 1, "the result names the round graded"


def _adopting_twice(picked: _Bench, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """An earlier round of the line adopting the round's other measured arm: the pick and it."""
    head, round_ = picked.head, picked.picked
    origin = picked.rounds[0].origin.individual.id
    other = next(
        key
        for key, arm in round_.arms.items()
        if key not in {head.individual.id, origin} and isinstance(arm, Measured | Cut)
    )
    assert round_.election is not None
    earlier = round_.model_copy(
        update={"election": round_.election.model_copy(update={"leading": other})}
    )
    spans = line_bench.line_spans
    monkeypatch.setattr(
        line_bench, "line_spans", lambda *a: [LineSpan([earlier], None), *spans(*a)]
    )
    return head.individual.id, other


@pytest.mark.usefixtures("optimizing_memory")
@on_jumping_clock
async def test_a_member_whose_point_moved_banks_its_pass_refused_and_the_roster_grades_on(
    monkeypatch: pytest.MonkeyPatch, world: World, banked_line: Shared
) -> None:
    async with _bench_cycle(world, banked_line) as picked:
        session = picked.session
        head, moved = _adopting_twice(picked, monkeypatch)
        measured = PointContext.measured

        def rebuild(points: PointContext, arm: Concluded) -> JobSearchPoint:
            if arm.individual.id == moved:
                raise BankedSearchPointError("moved under it")
            return measured(points, arm)

        monkeypatch.setattr(PointContext, "measured", rebuild)
        async with _outside_pass(session):
            line_bench.graded(session, picked.origin_pass, subject="origin")
        banked = await _graded(picked, trigger="every_adoption")
        folded = line_bench.line_passes(session.store, session.bound_campaign)
        assert folded is not None and folded.refused == banked.refused == {
            moved: "moved under it"
        }, "the moved member's refusal was not banked on its pass"
        assert not folded.selections[moved].cells and folded.selections[head].cells, (
            "the refusal spent on the moved member, or stopped the rest of the roster"
        )
        read = line_bench._read_pass(
            session.store,
            folded.selections[moved],
            session.state.scoring.scorer,
            tolerance=folded.tolerance,
            refused=folded.refused,
        )
        assert read.outcome.state is ReadingState.MEMBER_UNSCOREABLE, (
            "the fold read a refused pass as anything but unscoreable"
        )


@pytest.mark.usefixtures("optimizing_memory")
@on_jumping_clock
async def test_every_adoption_sets_aside_one_pass_per_selection_it_will_grade(
    monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    # Its own line: the set-aside reads the run's `Cycle` (`bench.py::reserve_selection_pass`).
    cycle, origin_pass = await _banked_bench(offline)
    async with _bench_at(cycle.session.store, cycle.session.hop, origin_pass) as picked:
        session = picked.session
        _adopting_twice(picked, monkeypatch)
        monkeypatch.setattr(line_bench, "cell_channels_of", lambda *_: {"cost": 0.01})
        held: dict[BenchTrigger, float] = {}
        async with _outside_pass(session) as book:
            for trigger in ("at_end", "every_adoption"):
                # A set-aside the fold reads (`Budget.amount` keeps 6 places), below one pass's price.
                book.set_aside(0.001, 1, paid_by="host")
                cycle.config = cycle.config.model_copy(update={"bench_trigger": trigger})
                line_bench.reserve_selection_pass(cycle, session)
                held[trigger] = book.count().set_aside.usd_amount
        assert held["at_end"] > 0.001, "vacuous — the selection's pass never raised the set-aside"
        assert held["every_adoption"] == pytest.approx(2 * held["at_end"]), (
            "the set-aside does not price every adoption the end pass grades"
        )


@pytest.mark.usefixtures("optimizing_memory")
@on_jumping_clock
async def test_an_authored_round_measures_the_operators_arm_and_asks_no_proposer(
    monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    authored = workspace()
    measured: list[str] = []

    def operator_solves(prompt: str, _sample: Sample) -> bool:
        measured.append(prompt)
        return "Operator" in prompt

    def submit(hop: CycleHop, key: str) -> Any:
        payload = SubmitArmsPayload(
            campaign_id=hop.campaign_id,
            cycle_id=hop.cycle_id,
            arms=[
                SubmittedArm(
                    prompt_fields={"llm_only": {"instruction": "Operator wrote this."}},
                    changes_description="the operator's own wording",
                )
            ],
        )
        return CommandDispatcher(authored).dispatch(CommandCall(payload, key, door=Door.HOST))

    # What earlier runs earned on this answer space reaches the origin's critique, not round 1 alone.
    monkeypatch.setattr(
        node_context, "earned_blocks_for", lambda *_a: {"persona": ("Answer tersely.",)}
    )
    offline.llm.says = lambda _body: "ok"
    async with _peer_round(
        authored, offline, "potter", {}, operator_solves, arm_gate="author"
    ) as ctx:
        cycle, hop = ctx.cycle, ctx.cycle.session.hop
        at_origin = RoundContext(cycle=cycle, round_num=0)
        origin_read = PotterView(NodeContext(at_origin, NODE_MEMBERS.get("l1_critique")), Ladder())
        assert [b.text for b in origin_read.block_window().get("persona", ())[:1]] == [
            "Answer tersely."
        ], "round 0's critique composed before the block pool was seeded"
        l1 = cast("node_contract.Proposer", NODE_MEMBERS.get("l1_generate"))
        seed = cast("node_contract.Proposer", NODE_MEMBERS.get("mating"))
        seeded = node_contract.RoundWalk("default", (seed,), (l1,), "author")
        blank = node_contract.RoundOpening({}, None, replayed=False)
        stated = l1.opening(NodeContext(at_origin, l1), blank)
        assert announce_opening(at_origin, seeded) == stated != blank, (
            "a seed walking ahead of L1 stated the round's standing and arms in L1's place"
        )
        measured.clear()
        with pytest.raises(ConflictError):
            await submit(hop, "arms-before-the-hold")
        held = asyncio.Event()

        async def gate_declared(*args: Any, **kwargs: Any) -> Any:
            held.set()
            return await hold_for_operator(*args, **kwargs)

        monkeypatch.setattr(
            "promptpotter.application.runner.round.hold_for_operator", gate_declared
        )

        async def answer() -> None:
            await held.wait()
            assert authored.run_state(hop).run_phase is RunPhase.GATE, (
                "the hold never reached the ledger"
            )
            await submit(hop, "arms-at-the-hold")

        # The hold polls on the loop's clock, which jumps past each wait.
        offline.llm.sent.clear()
        (_, closed, held_cut, _), _ = await asyncio.gather(execute_round(cycle, 1), answer())
        assert held_cut is None
        # The critique still reads the round; every other call it made filled the seat.
        critique_node = NODE_MEMBERS.get("l1_critique")
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
        resumed = PotterView(
            NodeContext(
                dataclasses.replace(at_origin, round_num=2),
                NODE_MEMBERS.get("l1_generate"),
            ),
            Ladder(),
        )
        assert [b.text for b in resumed.block_window().get("persona", ())[:1]] == [
            "Answer tersely."
        ], "a resumed run handed its proposer a pool its banked round does not name"
        l3: Any = NODE_MEMBERS.get("l3_plan")
        replanning = NodeContext(at_origin, l3)
        set_cycle(cycle, slices=[L3Memory(plan="attack the default label")])
        l3.land(replanning, l3.parse(replanning, L3PlanOutput(plan="")))
        assert cycle.slices.get(L3Memory).plan == "", (
            "an empty replan kept the plan it replaced: every later prompt read a dropped strategy"
        )
    [arm] = closed.proposed.values()
    assert arm.individual.prompts["llm_only"].fields["instruction"] == "Operator wrote this.", (
        "the operator's arm ran"
    )
    assert any("Operator wrote this." in prompt for prompt in measured), "it was measured"
    assert [made.mode for made in arm.individual.lineage.variations] == ["operator"]


@pytest.mark.usefixtures("optimizing_memory")
@on_jumping_clock
async def test_gepas_gate_tests_each_arm_against_the_minibatch_scores_of_its_own_parent(
    built_stores: Stores, offline: Router
) -> None:
    bars = MinibatchBars(
        bars={
            "weak_parent_child": MinibatchBar(parent_id="p1", objectives={"0": 0.1, "1": 0.1}),
            "strong_parent_child": MinibatchBar(parent_id="p2", objectives={"0": 0.9, "1": 0.9}),
        }
    )
    weak_child, strong_child = (ArmKey(cid) for cid in bars.bars)
    gate = MinibatchGate()
    minibatch = samples(2)
    async with opened_round(
        built_stores, offline, samples(60, "TRUE"), optimization={"optimizer": "gepa"}
    ) as gated:
        gated.cycle.emit(bars, gated.round_num)
        race = gate.race(
            NodeContext(gated, gate), RoundSamples(cells=minibatch, order=minibatch, block_size=2)
        )
    assert race.priors == {}, "the gate paired a prior, so the bench would catch it up"
    level = [measurement(i, 1.0, objective=0.5) for i in range(2)]
    walk = WalkView(level, minibatch, 0, len(level))
    stops = race.decide(View(dict.fromkeys((weak_child, strong_child), walk), False)).stops
    assert (
        list(stops) == [strong_child] and stops[strong_child].check_result["parent_id"] == "p2"
    ), "one arm's parent bar decided another arm's gate"


@pytest.mark.usefixtures("optimizing_memory")
@on_jumping_clock
async def test_a_round_file_the_disk_refuses_is_one_warning_and_the_next_round_files_its_own(
    monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    # Round 2 closes over the round 1 whose file the disk refused.
    async with _peer_round(
        workspace(), offline, "capo", _CAPO_NODES, lambda _p, s: s.id % 2 == 0, panel_gate="off"
    ) as ctx:
        cycle = ctx.cycle
        session = cycle.session
        offline.llm.says = _capo_says()
        with monkeypatch.context() as unwritable:
            unwritable.setattr(closed_rounds, "write_json", Mock(side_effect=OSError("full")))
            await post_round(cycle, await execute_round(cycle, 1), session, is_final_round=False)
        with scoped(round=2):
            await post_round(cycle, await execute_round(cycle, 2), session, is_final_round=True)
        records = ledger_records(cycle_ledger(cycle))
    warned = [r.kind for r in records if isinstance(r, RoundWarningRecord)]
    closed = [r.round for r in records if isinstance(r, RoundClosedRecord)]
    round_files = session.store.campaigns.layout(session.hop)
    assert warned == ["derived_file_stale"] and closed == [0, 1, 2], (
        "a round file's disk fault unwound its close or went unreported"
    )
    assert round_files.round_file(2).exists(), "a round file's disk fault stopped the next"


@pytest.mark.usefixtures("optimizing_memory")
@on_jumping_clock
async def test_a_layer_forking_on_an_arm_its_round_left_unelected_re_elects_it(
    offline: Router,
) -> None:
    # The round the fork hangs from is restated electing that arm, so the line continues from it.
    offline.llm.says = lambda _body: "ok"
    async with _peer_round(workspace(), offline, "potter", {}, lambda _p, _s: True) as ctx:
        cycle = ctx.cycle
        origin_arm = arm_state(
            "C0", position=ArmPosition(round=0, idx=0, total=1), individual=cycle.parent
        )
        # Walked on no cell this cycle's bank draws, so no panel renders a row the bank lacks.
        c0, c1, c2 = keys = [arm_key(f"c{i}") for i in range(3)]
        unwalked = dict.fromkeys(keys, sheet(()))
        line = [
            round_result(0, arms=[origin_arm], cells={}),
            round_result(1, candidates_scored=3, optimizer_state=optimizer_state(), cells=unwalked),
        ]
        forking = NodeContext(
            RoundContext(cycle=cycle, round_num=2),
            NODE_MEMBERS.get("l2_context"),
        )
        set_cycle(cycle, line=line)
        assert PotterView(forking, Ladder()).reelect_offer == {}, (
            "an improving line, which builds on its parent, was offered a fork"
        )
        # Each closed as the runner closes a round: the fork reads the round it hangs from off disk.
        session = cycle.session
        line[1] = await close_round(cycle, line[1], 1, session)
        stall = round_result(
            2, improved=False, candidates_scored=1, optimizer_state=optimizer_state(), cells={}
        )
        set_cycle(cycle, line=[*line, stall])
        with scoped(round=2):
            await close_round(cycle, stall, 2, session)
        forking = NodeContext(
            RoundContext(cycle=cycle, round_num=3),
            NODE_MEMBERS.get("l2_context"),
        )
        stalled = PotterView(forking, Ladder())
        offer = stalled.reelect_offer
        # c0 sits on both rounds' panels: one entry, its first listing.
        assert offer == {
            "C1.1": RoundArm(round=1, arm=c0),
            "C1.2": RoundArm(round=1, arm=c1),
            "C1.3": RoundArm(round=1, arm=c2),
        }, "a stalled layer may stand a fork on another arm than the picks and those they outranked"
        assert "ELECTIONS" in DispatchHub.render("rebase_capability", stalled), (
            "an arm two rounds list kept the layer's prompt from composing"
        )
        standing = fork_proposal_model(tuple(offer))
        refork = _rebase_request(
            forking, "l2_context", standing(reason="r", stand_on="C1.2"), offer
        )
        assert isinstance(refork, RebaseRequest) and refork.fork_from_round == 2, (
            "a fork standing on a round-1 arm cuts at round 2"
        )
        hangs_from = refork.hangs_from
        assert hangs_from is not None and hangs_from.election is not None
        assert (hangs_from.election.selected, hangs_from.optimizer_state.population) == (
            c1,
            [line[1].arms[c1].individual],
        ), "the restated round elects another arm than the one the layer stood the fork on"
        assert line[1].election is not None and line[1].election.selected == c0, "guard"
        assert (
            hangs_from.election.reelection
            == Reelection(by=Principal("l2_context/round_3", "l2_context"), reason="r")
            and hangs_from.election.verdict_reason == line[1].election.verdict_reason
        ), "a re-election is not told apart from the selector's own verdict"
        stood_on_pick = _rebase_request(
            forking, "l2_context", standing(reason="r", stand_on="C1.1"), offer
        )
        assert isinstance(stood_on_pick, RebaseRequest) and stood_on_pick.hangs_from is None, (
            "a fork on the round's own pick restated an election it already holds"
        )
        with pytest.raises(ValidationError):
            standing.model_validate({"reason": "r", "stand_on": "C9.9"})
