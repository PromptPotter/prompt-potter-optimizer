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
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import (
    AbstractContextManager,
    contextmanager,
)
from itertools import count, product
from math import sqrt
from pathlib import Path
from statistics import fmean, mean, stdev
from typing import Any, NamedTuple, NoReturn, cast
from unittest.mock import Mock

import httpx
import pytest
import yaml
from factories import (
    PARENT,
    PROMPT_INFO,
    arm_state,
    arm_walk,
    bench_line,
    bench_pass,
    bench_passes,
    billed_call,
    close_round,
    defaulted_config,
    delta_ruler,
    election_knobs,
    fake_connector,
    filed,
    filed_under,
    inner_sandbox,
    jumping_clock,
    ledger_records,
    level_series,
    loop_session,
    measurement,
    measurements,
    member_rows,
    mint_fork,
    minted_campaign,
    minted_dataset,
    optimizer_state,
    pipeline_schema,
    pobb_knobs,
    prompted,
    round_result,
    run_init_round,
    samples,
    score_summary,
    scorer_of,
    seat,
    set_cycle,
    sheet,
    spend_book,
    walking_cycle,
    workspace,
)
from offline_world import FakeHarness, Router
from pydantic import SecretStr, ValidationError

from promptpotter import connectors
from promptpotter.application import optimizers
from promptpotter.application.bench import node_context
from promptpotter.application.bench.cycle import Cycle, ReadArm, RoundContext, SliceMap
from promptpotter.application.bench.difficulty import (
    judging_ruler,
    regraded_ruler,
    ruler_clock,
    session_ruler_scope,
)
from promptpotter.application.bench.node_context import NodeContext
from promptpotter.application.bench.resume_and_fork.repair import repair_cut
from promptpotter.application.caller import Caller
from promptpotter.application.commands import measuring
from promptpotter.application.commands.dispatcher import (
    Applier,
    CommandCall,
    CommandDispatcher,
)
from promptpotter.application.evidence.head_to_head import GuardDifference, GuardState
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
from promptpotter.application.initialization.scoring_scope import scorer_setup
from promptpotter.application.initialization.session import LaunchClock, Session
from promptpotter.application.initialization.wiring import complete_registries, init_services
from promptpotter.application.intelligence.rasch import (
    candidate_abilities,
    responses_of,
    theta_lift_over_parent,
)
from promptpotter.application.jobs.launcher.admission import (
    PassStoppedError,
    paid_pass,
)
from promptpotter.application.optimizer_manifest import (
    select_optimizer,
)
from promptpotter.application.optimizers import nodes as node_contract
from promptpotter.application.optimizers.capo.members import CapoObjective
from promptpotter.application.optimizers.gepa.members import (
    MinibatchBar,
    MinibatchBars,
    MinibatchGate,
)
from promptpotter.application.optimizers.nodes import Panel
from promptpotter.application.optimizers.potter.dispatch.facade import DispatchHub, PotterView
from promptpotter.application.optimizers.potter.dispatch.layout import (
    NODE_LAYOUTS,
    coerce_l1_layout,
)
from promptpotter.application.optimizers.potter.dispatch.schemas import (
    L3PlanOutput,
    fork_proposal_model,
)
from promptpotter.application.optimizers.potter.escalation import (
    EscalationInputs,
    HighWater,
    LadderAsk,
    LayerReading,
    NextAction,
    _rebase_request,
    as_read_by,
    ask_l2_escalation,
    bank_and_route,
    decide_escalation,
    l2_fired,
    l3_fired,
)
from promptpotter.application.optimizers.potter.knobs import (
    ElectionKnobs,
    EscalationLadder,
    PoBBKnobs,
)
from promptpotter.application.optimizers.potter.members import L1Generate
from promptpotter.application.optimizers.potter.pobb import PoBBCheck, field_p_best
from promptpotter.application.optimizers.potter.records import (
    L1Readout,
    L2Memory,
    L3Memory,
    Ladder,
    LayerCounters,
)
from promptpotter.application.optimizers.variation.members import (
    Cross,
    donor_take,
    most_donated,
)
from promptpotter.application.run_phase_control import RunControl
from promptpotter.application.runner.bench import (
    bank_campaign_result,
    bench_selection,
    bench_series,
    line_launches,
    pair_on_bench,
    read_bench,
    score_on_bench,
)
from promptpotter.application.runner.entry import build_cycle_result
from promptpotter.application.runner.inner.tasks import inner_spawn_of
from promptpotter.application.runner.loop import open_origin_round
from promptpotter.application.runner.round import (
    ReferencePairs,
    announce_opening,
    execute_round,
    hold_for_operator,
    run_init,
)
from promptpotter.application.runner.termination import standing_tripped
from promptpotter.application.scoring import closed_rounds, evaluators, query_loop
from promptpotter.application.scoring import passes as held_out
from promptpotter.application.scoring.classification import DegradationCheck, scoreable_rows
from promptpotter.application.scoring.closed_rounds import RoundFileProjection
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
from promptpotter.application.scoring.metrics import compute_composite_fitness, fold_cells
from promptpotter.application.scoring.paired import (
    MemberRows,
    absent_pair,
    as_family,
    grade_measurands,
    read_pair,
)
from promptpotter.application.scoring.passes import verify_member, verify_reading
from promptpotter.application.scoring.sample_measurement import measure_sample
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.application.scoring.selection import (
    cells_read,
    closest_to_bar,
    crowning_cells,
    elect_round_winner,
    level_band,
    readable_lifts,
)
from promptpotter.application.views.render.sample import fmt_query_result
from promptpotter.config.paths import optimizers_root
from promptpotter.config.settings import settings
from promptpotter.connectors import n8n
from promptpotter.connectors.protocol import PROBE_WORKLOAD, Connector
from promptpotter.domain.arm_reading import ArmFit, DisplayMetric, order_floor
from promptpotter.domain.arms import (
    ArmSlot,
    Cut,
    CutKind,
    Measured,
    Proposed,
    Refused,
    is_leader_eligible,
)
from promptpotter.domain.bench import (
    COST_ABSENCE_WORDS,
    CampaignRefusal,
    CostAbsence,
    DatasetSplit,
    PassStop,
    partition_bank,
)
from promptpotter.domain.campaign import Arm, Compare, HeadToHeadRecord, Optimize, Purpose
from promptpotter.domain.campaign_config import CampaignConfig, OptimizerChoice
from promptpotter.domain.cell_fold import (
    ScoreSummary,
    is_floor_pinned,
    merge_known_outcomes,
    unscoreable_cells,
)
from promptpotter.domain.cell_terms import CELL_TERMS, CellTerm
from promptpotter.domain.command_events import CommandAckRecord
from promptpotter.domain.command_kinds import (
    CancelPayload,
    CommandKind,
    CommandPayload,
    Door,
    GradeBenchPayload,
    PauseCyclePayload,
    SkipSearchpointPayload,
    SubmitArmsPayload,
    SubmittedArm,
    VerifyCandidatePayload,
)
from promptpotter.domain.crowning_bar import BAR_ADMISSION, CrowningBar
from promptpotter.domain.cycle_paths import CycleDir, CycleHop, Subject
from promptpotter.domain.dashboard_rows import RoundHealth, RunLimits
from promptpotter.domain.judges import JudgeSpec, JudgeStage, JudgeVerdict
from promptpotter.domain.opt_search_point import NodePrompt, OptSearchPoint
from promptpotter.domain.optimizer_state import PARSE_FAILURE_ALL_NO_OP
from promptpotter.domain.optimizer_template import PROMPT_FIELDS
from promptpotter.domain.origin_panel import (
    OverlapReading,
    PanelTrigger,
    RoundAdvance,
    TriggeredReading,
    election_rows,
    line_by_individual,
    origin_panel,
    round_advance,
)
from promptpotter.domain.paired_reading import (
    ROUND_LIFT_SPEC,
    ArmKey,
    ArmName,
    CellSetName,
    CoverageState,
    EstimatorSpec,
    Measurand,
    MeasurandKind,
    MemberAddress,
    PairedReading,
    ReadingState,
    RoundArm,
)
from promptpotter.domain.pass_kinds import IndividualWalk, individual_cells
from promptpotter.domain.phase_views import VerificationGradedView
from promptpotter.domain.phases import CampaignPhase, PauseCause, RunPhase, StopReason, WalkEnd
from promptpotter.domain.pipeline_params import PipelineParams
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import (
    ManifestNodeOverlay,
    ObservationMapping,
    PipelineNode,
)
from promptpotter.domain.pool import BlockPool
from promptpotter.domain.records.partition import PartitionRecord
from promptpotter.domain.records.run_records import (
    ArmRecord,
    CampaignCeilingRecord,
    ComparisonExit,
    ForkSpec,
    ForkTrigger,
    InterventionRecord,
    PhaseRecord,
    RoundEnteredRecord,
    RoundWarningRecord,
    RunPhaseRecord,
    RunWiringRecord,
    StopLoop,
)
from promptpotter.domain.results import InRunCells, Reelection, RoundResult
from promptpotter.domain.results_health import is_deprecated
from promptpotter.domain.ruler import ThetaCaveat
from promptpotter.domain.run_standing import (
    LineOutlay,
    RunStanding,
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
from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition
from promptpotter.domain.spend import Budget, SpendRollup, TokenUsageKind
from promptpotter.domain.verification import (
    BenchPasses,
    NoiseBand,
    VerificationPass,
    VerifyStrategy,
)
from promptpotter.domain.wounds import fatal_validation_failures
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.ledger import CycleEventLog, ledger_chain
from promptpotter.infrastructure.llm.spend_book import (
    SpendBook,
    require_spend_book,
)
from promptpotter.infrastructure.producer_lock import cycle_held, release_cycle
from promptpotter.infrastructure.projections.live_dashboard.projection import (
    LiveDashboardProjection,
)
from promptpotter.infrastructure.run_scope import run_scope, scoped
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_ledger_walks,
    scan_standing_rounds,
)
from promptpotter.infrastructure.store.dataset_access import DEFINITION_FILE
from promptpotter.infrastructure.store.io import write_yaml
from promptpotter.infrastructure.store.read_model import LedgerIndex
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
from promptpotter.shared.identity import SYSTEM_PRINCIPAL, Principal
from promptpotter.shared.measurement_context import MeasurementRole, RoleScope
from promptpotter.shared.plugin_registry import Registry
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
        "spec": EstimatorSpec(alpha=0.05, null_value=0.0),
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


@pytest.mark.usefixtures("offline")
def test_each_matcher_grades_an_answer_against_its_gold() -> None:
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


@pytest.mark.usefixtures("offline")
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


@pytest.mark.usefixtures("offline")
def test_a_cell_read_off_the_wire_keeps_its_facts_and_drops_any_grade_it_arrived_with(
    wire_cell: _WireCell,
) -> None:
    banked, stale, *_ = wire_cell
    facts = MeasuredCell.from_wire({**banked, **stale})
    assert facts.wire() == banked, "a fact was lost, or a grade reached the facts"
    assert (facts.cost_s, facts.elapsed_s, facts.shown_s) == (1.5, 2.0, 1.5), (
        "latency reads the step timings, the elapsed the total"
    )


@pytest.mark.usefixtures("offline")
def test_a_grade_is_a_new_record_and_keeps_nothing_of_the_formula_before_it(
    wire_cell: _WireCell,
) -> None:
    _, _, answered, plain, *_ = wire_cell
    graded = plain.grade(answered)
    assert (graded.grade.fitness, graded.grade.unscored) == (0.25, None), "the stale grade stood"
    assert graded.wire() == {**answered.wire(), "fitness": 0.25, "objective": 0.25}, (
        "a graded cell is its facts plus this formula's grade"
    )


@pytest.mark.usefixtures("offline")
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


@pytest.mark.usefixtures("offline")
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


@pytest.mark.usefixtures("offline")
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


@pytest.mark.usefixtures("offline")
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


@pytest.mark.usefixtures("offline")
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


@pytest.mark.usefixtures("offline")
def test_each_graded_field_is_matched_alone_and_the_cell_and_theta_read_their_share() -> None:
    joint = scorer_of("fields_match(predicted, ground_truth)")
    gold = json.dumps(
        {
            "type_of_document": ["invoice"],
            "requires_action": [True],
            "extract_depth": [2, 3],
            "who": [""],
            "issuer": None,
        }
    )

    def classified(sid: int, said: str) -> MeasuredCell:
        cell: dict[str, Any] = {"query": f"q{sid}", "predicted": said, "ground_truth": gold}
        return measurement(sid, None, **cell).facts

    fields = ("type_of_document", "requires_action", "extract_depth", "who")
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
        ("NO_RESULT", list(fields)),
    ):
        graded = joint.grade(classified(0, said))
        hits = dict(graded.grade.field_hits or ())
        assert [name for name, hit in hits.items() if not hit] == missed, (
            f"{said!r} must miss exactly {missed}: case, spacing, a bool's or a number's spelling "
            "never decide, a listed gold accepts any of its values, '' is a value and a null "
            "gold grades nothing"
        )
        assert graded.grade.fitness == pytest.approx(1 - len(missed) / len(fields)), (
            "a cell's fitness is the share of its graded fields it got right"
        )
        assert GradedCell.from_wire(graded.wire()) == graded, "the per-field verdicts left the wire"
    walked = joint.sheet(
        [
            classified(
                0,
                '{"type_of_document":"invoice","requires_action":true,"extract_depth":3,"who":""}',
            ),
            classified(
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
    assert "MISS type_of_document,extract_depth" in fmt_query_result(
        1, walked.cells[1].facts, walked.cells[1].grade, display_tags={}
    ), "the readout names the fields a cell missed"
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


@pytest.mark.usefixtures("offline")
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


@pytest.mark.usefixtures("offline")
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


@pytest.mark.usefixtures("offline")
def test_a_connectors_declared_dial_parses_and_anchors_as_a_core_one_does() -> None:
    core = CELL_TERMS
    l4 = compiler.campaign_terms("promptpotter")
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


@pytest.mark.usefixtures("offline")
def test_every_door_admitting_a_name_to_the_cell_namespace_refuses_one_another_owner_binds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Unrefused, a judge named `max` breaks every `max(...)` formula, and a connector term
    # `n_turns` reads its report on a dial and the turn count in a formula.
    shipped = connectors.CONNECTORS

    def term(name: str, **rest: Any) -> CellTerm:
        return CellTerm(name=name, direction="high", description="A probe term.", **rest)

    def table_refuses(*, probe: Connector | None = None) -> bool:
        members = [*connectors._builtins(), *([("probe", probe)] if probe else [])]
        with monkeypatch.context() as patched:
            patched.setattr(
                connectors,
                "CONNECTORS",
                Registry(shipped.group, members.copy, connectors._validate),
            )
            try:
                complete_registries(every_treatment=False)
            except RuntimeError:
                return True
        return False

    def connector_refuses(**declared: Any) -> bool:
        return table_refuses(probe=fake_connector("probe", **declared))

    async def one(**_: Any) -> float:
        return 1.0

    def package_evaluator_refuses(name: str) -> bool:
        ev = SampleEvaluator(name=name, description="A probe.", compute=one)
        with monkeypatch.context() as patched:
            patched.setattr(evaluators, "_SAMPLE_EVALUATORS", (*evaluators._SAMPLE_EVALUATORS, ev))
            try:
                evaluators._validate_registry()
            except ValueError:
                return True
            return table_refuses()

    # The campaign's doors are run init's own: a connector reporting `rounds` as L4's does, a
    # pipeline mapping `env_reward`.
    reporting = fake_connector("reporting", reported_keys=("rounds",))
    observed = PipelineNode(
        name="llm_only",
        tunes_llm=False,
        observation_name="llm_only",
        observation_mappings=[ObservationMapping(pipeline_key="env_reward")],
    )

    def judge_refuses(name: str) -> bool:
        stores = workspace(tmp_path / "judge")
        session = loop_session(
            stores,
            pipeline_schema(nodes=[observed]),
            samples(2),
            client=BackendClient(reporting, workload=PROBE_WORKLOAD, jobs_dir=stores.jobs_dir),
        )
        spec = JudgeSpec(name="sealqa", stages=[JudgeStage(model="m", provider="openai")])
        config = defaulted_config(
            {"optimization": {"degradation_threshold": 0.4}, "judges": {name: spec.model_dump()}}
        )
        try:
            scorer_setup(
                session, config, partition=partition_bank(session.bank.samples, None, seed=0)
            )
        except ValueError:
            return True
        return False

    def observation_refuses(name: str) -> bool:
        stores = workspace(tmp_path / "observed")
        mapping = [
            {"pipeline_key": "final_ranking", "output_field": "generated_text", "is_llm": True},
            {"pipeline_key": name, "output_field": name},
        ]
        write_yaml(
            stores.tenant_datasets.dataset_dir("observed") / DEFINITION_FILE,
            {
                "pipeline": {
                    "backend_type": "reporting",
                    "nodes": {"llm_only": {"optimizer": {"observation_mappings": mapping}}},
                }
            },
        )
        members = [*connectors._builtins(), ("reporting", reporting)]
        with monkeypatch.context() as patched:
            patched.setattr(
                connectors,
                "CONNECTORS",
                Registry(shipped.group, members.copy, connectors._validate),
            )
            connectors.CONNECTORS.complete()
            try:
                asyncio.run(_init("observed", stores))
            except PayloadInvalidError as exc:
                # Past this door, the next refusal is the dataset's empty bank.
                return "refused_observation_keys" in exc.details
        raise AssertionError("an observation door admitted a dataset holding no rows")

    doors: dict[str, Callable[[str], bool]] = {
        "connector term": lambda n: connector_refuses(cell_terms=(term(n),)),
        "reported key": lambda n: connector_refuses(reported_keys=(n,)),
        "package evaluator": package_evaluator_refuses,
        "campaign judge": judge_refuses,
        "observation key": observation_refuses,
    }
    for name, admitted_at in (
        ("fresh_term", set(doors)),
        ("latency", {"reported key"}),  # a core term: a report answers its channel
        # Two owners served it once: the grade's rank, and a report's read on the channel alone.
        ("ground_truth_rank", set()),
        ("target_prompt_chars", {"observation key"}),  # a PipelineData field, never a report's
        ("error", set()),  # the cell's message; `PipelineData.error` reaches no formula
        ("errored", set()),  # a row-health term: the row answers it
        ("predicted", set()),  # bound off the cell
        ("label_match", set()),  # a matcher
        ("max", set()),  # a builtin
        ("total_time", {"observation key"}),  # a PipelineData key: an observation fills it
        ("n_turns", set()),  # a turn scalar
        ("n_tool_calls", set()),
        ("retrieve_step_turns", set()),  # a step's turn count
        ("retrieval_shortfall", set()),  # a package evaluator
        # The L4 connector's term: another connector may declare it, nothing its campaigns read.
        ("rounds", {"connector term", "reported key"}),
        ("env_reward", set(doors) - {"campaign judge"}),  # a campaign's observation
    ):
        refused_at = {door for door, refuses in doors.items() if refuses(name)}
        assert refused_at == set(doors) - admitted_at, f"{name!r} admitted where another owns it"
    assert connector_refuses(cell_terms=(term("probe_anchor", dial="anchored"),)), (
        "an anchored term reading higher-is-better was registered: a dial reads it as a cost"
    )


@pytest.mark.usefixtures("offline")
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


@pytest.mark.usefixtures("offline")
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


@pytest.mark.usefixtures("offline")
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


# 2. Composite fitness and coverage


def test_a_round_scores_the_cells_its_prompt_answered_and_never_an_absence_as_zero(
    offline: Router,
) -> None:
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
    control: RunControl | None = None,
    book: SpendBook | None = None,
    origin_replicates: int | None = None,
    **optimization: Any,
) -> Iterator[tuple[Cycle, CycleEventLog]]:
    """Bound as a run seam binds it; the backend answers right where ``solves`` says, errs on None."""

    async def _measure(
        sample: Sample, _session: Any, pipeline_params: PipelineParams
    ) -> MeasuredCell:
        prompt = pipeline_params.node("solve").laid_keys()["prompt"]
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
        nodes=[PipelineNode(name="solve", tunes_llm=False, prompt_info=PROMPT_INFO)],
    )
    origin = OptSearchPoint(
        prompts={
            "solve": NodePrompt(
                fields={"instruction": "Answer.", "answer_format": "Reply with the letter."}
            )
        }
    ).configured(PipelineParams(), schema)

    def measured_origin(session: Session) -> CellSheet:
        rows = [
            asyncio.run(
                _measure(
                    s, session, PipelineParams.model_validate({"solve": {"prompt": "Answer."}})
                )
            )
            for s in list(session.state.scoring.partition.search)[:4]
        ]
        return session.state.scoring.scorer.sheet(rows)

    with scoped(round=1):
        seated = seat(
            stores,
            schema=schema,
            bank=_PEER_BANK,
            bench=bench,
            demo=2,
            # The campaign's composite REWARDS length, the opposite of CAPO's objective.
            scorer=scorer_of(
                "label_match(predicted, ground_truth)",
                "fitness * min(1.0, target_prompt_chars / 200.0)",
            ),
            optimization={
                "optimizer": optimizer,
                "elimination_n_min": 2,
                "nodes": nodes,
                **optimization,
            },
            dataset_name=f"{optimizer}-e2e",
            control=control,
            hop=hop,
            origin=(origin, measured_origin),
            framing=TaskDecomposition(pipeline_purpose="Answer each query with its letter."),
            origin_replicates=origin_replicates,
        )
        cycle, ledger = seated.cycle, seated.ledger
        with scoped(
            ledger=ledger,
            spawn=inner_spawn_of(cycle.session),
            determinism=cycle.config.optimization.determinism,
        ):
            run_init(cycle, ledger)
            with scoped(book=spend_book() if book is None else book):
                yield cycle, ledger


def _asked(body: dict[str, Any]) -> str:
    """What an optimizer call sent the provider, every message of it."""
    return "\n".join(str(m["content"]) for m in body["messages"])


def _potter_says(*instructions: str) -> Callable[[dict[str, Any]], str]:
    """Potter's L1 proposing one variant per instruction, in order; every other node a critique."""

    def says(body: dict[str, Any]) -> str:
        if body.get("response_format", {}).get("json_schema", {}).get("name") != "L1GenerateOutput":
            return json.dumps({"priority_fix": "instruction: check each premise"})
        variants = [
            {
                "targets_rows": [],
                "prompt_fields_updates": {"solve": {"instruction": text}},
                "changes_description": text,
            }
            for text in instructions
        ]
        return json.dumps({"variants": variants})

    return says


def _bench_pass(
    stores: Stores, individual: str, reward: float, *, cells: int = 4, stop: PassStop | None = None
) -> VerificationPass:
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


@pytest.mark.usefixtures("optimizing_memory")
def test_a_round_winner_elects_by_ability_not_subset_accuracy() -> None:
    ruler = _easy_low_hard_high()
    bar = CrowningBar(statistic="posterior", admission=election_knobs().admission)
    with pytest.raises(ValidationError):
        ElectionKnobs(admission=0.70)  # one discordant cell supports 0.75: it would promote
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
        bar=bar,
    )
    assert winner_id == "able_on_hard", "a round winner elects by ability, not subset accuracy"
    assert abilities.theta["able_on_hard"] > abilities.theta["weak_on_easy"]
    # 6 hits far below θ, 13 misses far above, one miss near it the arm gained.
    spread = delta_ruler(
        {**dict.fromkeys(range(6), -2.8), 6: 1.0, **dict.fromkeys(range(7, 20), 4.0)}
    )
    one_cell = {ArmKey("one"): sheet(measurement(i, float(i < 7)) for i in range(20))}
    parent_six = sheet(measurement(i, float(i < 6)) for i in range(20))
    assert elect_round_winner(list(one_cell), one_cell, parent_six, 6, spread, bar=bar)[0] is (
        None
    ), "one discordant cell of 20 promoted: a lift its own paired evidence cannot carry"


@pytest.mark.usefixtures("optimizing_memory")
def test_a_thin_arm_never_wins_on_a_margin_inside_its_own_noise() -> None:
    # `coverage_floor` IS PoBB's `n_min`.
    ruler, bar = (
        _easy_low_hard_high(),
        CrowningBar(statistic="posterior", admission=election_knobs().admission),
    )
    flat, half = _level_parent()
    deep = sheet(measurements([1.0] * 21 + [0.0] * 7))
    shallow = sheet(measurements([1.0] * 5 + [0.0], range(14, 20)))
    ab = candidate_abilities({"deep": deep, "shallow": shallow}, half, flat)
    lifts = {c: theta_lift_over_parent(ab, c) for c in ("deep", "shallow")}
    p = {c: lift.p_better for c, lift in lifts.items() if lift is not None}
    assert ab.theta["shallow"] > ab.theta["deep"], "the thin arm's POINT lift is the larger"
    assert ab.theta_se["shallow"] > 1.8 * ab.theta_se["deep"], "on nearly twice the SE"
    assert p["deep"] > p["shallow"], "so it demonstrated less"
    deep_key, shallow_key = ArmKey("deep"), ArmKey("shallow")
    thin_arms = {deep_key: deep, shallow_key: shallow}
    assert elect_round_winner(list(thin_arms), thin_arms, half, 6, flat, bar=bar)[0] == ("deep"), (
        "a thin arm won"
    )
    assert elect_round_winner([shallow_key], {shallow_key: shallow}, half, 6, flat, bar=bar)[0] == (
        "shallow"
    ), "ranking on P must never DISQUALIFY: a lone wide-posterior gain still wins"
    behind = sheet(measurements([1.0] * 4 + [0.0] * 2))
    behind_lift = theta_lift_over_parent(candidate_abilities({"b": behind}, half, flat), "b")
    assert behind_lift is not None and behind_lift.value > 0.0
    assert (
        elect_round_winner([ArmKey("b")], {ArmKey("b"): behind}, half, 6, flat, bar=bar)[0] is None
    ), "a θ lift over the parent's whole panel never crowns an arm that LOST the cells it shares"
    swapped = sheet(measurement(i, float(0 < i < 20 or i == 20)) for i in range(40))
    assert (
        elect_round_winner(
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
    assert elect_round_winner(
        [ArmKey("l")], {ArmKey("l"): longer}, terse_parent, 6, level, bar=bar
    )[0] == ("l"), "a cost term's tie-break was counted as a verdict lost"


@pytest.mark.usefixtures("optimizing_memory")
def test_a_crowned_picks_lucky_cells_stay_out_of_every_race_it_judges() -> None:
    # The bar is what the parent can do, not the luck that crowned it: no later race reads those cells.
    crowned_on = sheet(measurements([1.0] * 4 + [0.0], range(30, 35)))
    bar_rows = sheet(measurements([0.0, 0.0, 1.0, 1.0, 0.0], range(30, 35)))
    won = round_result(
        1,
        selected="c0",
        voted={"electable": 1},
        cells={ArmKey("c0"): crowned_on, PARENT: bar_rows},
    )
    assert crowning_cells([won, round_result(2, improved=False)], "c0") == {
        crowned_on.cells[0].key,
        crowned_on.cells[1].key,
    }, "a crowned pick's discordant wins stay out of every race it judges, a lone arm's included"
    assert not crowning_cells([won], "c1"), "an ancestor's are its child's own measurements"
    luck = crowning_cells([won], "c0")
    assert not luck & {c.key for c in election_rows(crowned_on, NO_CELLS, luck)}, (
        "the bar read a crowning cell off a draw that held it"
    )


@pytest.mark.usefixtures("optimizing_memory")
def test_a_held_round_leads_on_the_readable_arm_nearest_the_bar_admission_reads() -> None:
    bar, deep_key = (
        CrowningBar(statistic="posterior", admission=election_knobs().admission),
        ArmKey("deep"),
    )
    flat, half = _level_parent()
    twenty = sheet(measurements([1.0] * 20 + [0.0] * 8))
    near_key = ArmKey("near")
    arms = {
        near_key: sheet(measurements([1.0] * 5 + [0.0] * 3)),  # closest to the bar, on a wide SE
        deep_key: sheet(measurements([1.0] * 18 + [0.0] * 10)),  # further off, on the whole panel
        ArmKey("thin"): sheet(measurements([1.0] * 3)),  # the highest θ, under the floor
    }
    held_id, held_abilities = elect_round_winner(list(arms), arms, twenty, 6, flat, bar=bar)
    reads = readable_lifts(list(arms), 6, held_abilities)
    assert held_id is None, "no arm cleared the bar"
    assert held_abilities.theta["thin"] > held_abilities.bar["thin"][0], (
        "the floor, not θ, bars thin"
    )
    assert set(reads) == {"near", "deep"}, "an arm under the floor is not readable"
    assert reads[deep_key].value < reads[near_key].value <= 0.0
    assert closest_to_bar(reads) == "near", "the lead is named on the θ lift"
    cold = candidate_abilities({"off": sheet(measurements([1.0] * 8, range(26, 34)))}, twenty, None)
    assert cold.cells["off"] == 2 and not readable_lifts([ArmKey("off")], 6, cold), (
        "cold, the floor counts the cells θ was READ on — those shared with the parent"
    )
    warm = candidate_abilities({"off": sheet(measurements([1.0] * 8, range(24, 32)))}, half, flat)
    assert warm.cells["off"] == 4, "warm, the floor counts only the cells the ruler carries"
    assert closest_to_bar(reads, beside=near_key) == "deep"


# Round 2 re-reads the leader round 1 held.
@pytest.mark.usefixtures("optimizing_memory")
def test_a_held_leader_ahead_of_its_parent_is_re_read_once_ahead_of_l1s_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    # The cells that ranked it first are the ones its luck rode.
    def one_more_than_parent(prompt: str, sample: Sample) -> bool:
        order = _PEER_READ_ORDER[sample.id]
        if "Guess." in prompt:
            return order % 4 == 0
        return order % 2 == 0 or ("Check each premise." in prompt and order == 1)

    with _peer_cycle(
        workspace(tmp_path / "retest"),
        _PEER_HOP,
        monkeypatch,
        "potter",
        {"l1_generate": {"config": {"n_variants": 2}}},
        one_more_than_parent,
    ) as (cycle, ledger):
        parent_id = cycle.parent.id
        offline.llm.says = _potter_says("Check each premise.", "Guess.")
        first = asyncio.run(execute_round(cycle, 1, ledger)).result
        assert first.election is not None and first.election.selected is None, "round 1 held"
        leader = first.election.leading
        assert leader is not None
        assert "Check" in first.arms[leader].individual.prompts["solve"].fields["instruction"]
        assert [m.id for m in cycle.population] == [parent_id, leader], (
            "a held leader ahead of its parent was dropped with its evidence"
        )
        offline.llm.says = _potter_says("Guess.")
        second = asyncio.run(execute_round(cycle, 2, ledger)).result
        assert next(iter(second.panel)) == leader and second.rows(leader), (
            "the round after a leader missed the bar did not re-score it, first"
        )
        assert cycle.output(L1Readout, 2).l1_proposed == 1, "a re-read counted as L1's yield"
        assert [m.id for m in cycle.population] == [parent_id], "a leader was re-read twice"


@pytest.mark.usefixtures("optimizing_memory")
def test_ab_replay_refits_the_whole_ruler_under_the_edited_formula(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    # The election's scale re-read off the archive: unchanged under its own formula, moved under
    # an edited one, since every row the ruler holds is re-graded, not only the record's own cells.
    def every_other(prompt: str, sample: Sample) -> bool:
        return _PEER_READ_ORDER[sample.id] % 2 == 0 or "Check" in prompt

    with _peer_cycle(
        workspace(tmp_path / "ab"),
        _PEER_HOP,
        monkeypatch,
        "potter",
        {"l1_generate": {"config": {"n_variants": 2}}},
        every_other,
    ) as (cycle, ledger):
        offline.llm.says = _potter_says("Check each premise.", "Guess.")
        first = asyncio.run(execute_round(cycle, 1, ledger)).result
    assert first.election is not None
    stamp, stores = first.election.ruler, cycle.session.store
    opt = cycle.config.optimization
    n_min, two_pl = opt.elimination_n_min, opt.enable_2pl_graduation
    read = judging_ruler(
        stores.ruler,
        stamp.scope,
        stamp.reach,
        stamp.excluded,
        objective_id=stamp.scope.scorer,
        n_min=n_min,
        enable_2pl=two_pl,
    ).ruler
    own = cycle.session.state.scoring.scorer
    same = regraded_ruler(stores, stamp, own, n_min=n_min, enable_2pl=two_pl)
    assert read is not None and same is not None and same.delta == read.delta, (
        "ab re-fit the election's scale differently under the formula the election read it with"
    )
    edited = scorer_of("label_match(predicted, ground_truth)", "1.0 - fitness")
    moved = regraded_ruler(stores, stamp, edited, n_min=n_min, enable_2pl=two_pl)
    assert moved is not None and moved.delta != read.delta, (
        "ab replayed an edited formula on the scale the old formula fit"
    )


@pytest.mark.usefixtures("optimizing_memory")
def test_only_an_invalid_measurement_bars_an_arm_from_leading() -> None:
    # A STOP IS NOT A VERDICT.
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
            arm_state("C1.1", locked_in=True, elimination={"gate": "settled"}),
            True,
        ),
        (arm_state("C1.4"), True),
    ):
        assert is_leader_eligible(arm) is eligible, f"{arm.label} decides leading wrongly"


_CAPO_NODES = {
    "blocks": {"config": {"block_size": 4, "max_blocks": 3}},
    "capo_init": {"config": {"size": 3, "k_max": 0}},
    "mating": {"config": {"offspring": 2}},
    "few_shot": {"config": {"k_max": 0}},
    "survival": {"config": {"size": 3}},
}


@pytest.mark.usefixtures("optimizing_memory")
def test_a_round_the_budget_cuts_elects_only_past_the_panel_gate_and_stands_once_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    # Round 1 races the seeds with its offspring; its parent is the origin, never bought a cell.
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
                    book.share = Budget(usd=0.0)
            return _PEER_READ_ORDER[sample.id] % 2 == 0

        with _peer_cycle(
            workspace(root),
            _PEER_HOP,
            monkeypatch,
            "capo",
            _CAPO_NODES,
            even_cells,
            book=book,
            panel_gate=panel_gate,
        ) as (cycle, ledger):
            offline.llm.says = capo_says
            session = cycle.session
            running.append(True)
            search = list(session.state.scoring.partition.search)
            if cut_round == 1:
                # Run init scores the origin through the gateway — here on half of CAPO's first block.
                asyncio.run(
                    score_search_point(
                        cycle.parent_point,
                        search[:2],
                        session,
                        kind=MeasurementRole.ORIGIN,
                        measured=None,
                    )
                )
            else:
                uncut = asyncio.run(execute_round(cycle, 1, ledger)).cut
                assert uncut is None, f"{case}: round 1 ran inside its budget"
            paid.clear()
            parent = ArmKey(cycle.parent.id)
            cutting.append(True)
            cut_walk = execute_round(cycle, cut_round, ledger)
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
            closed, cut, _ = asyncio.run(cut_walk)
            assert cut is StopReason.SPEND_BUDGET, f"{case}: the round names its cut"
            rows = {key: closed.rows(key) for key in closed.panel}
            assert sum(len(closed.rows(key)) for key in closed.arms) == (
                replayed + len(paid) + parent_cells
            ), f"{case}: a cell nobody elects on, or a parent read where the archive lacks it"
            assert (parent in closed.panel) is (cut_round == 2), (
                f"{case}: a retained parent is ONE arm, its re-read folded into its own cells"
            )
            parent_arm = closed.arms[parent]
            assert isinstance(parent_arm, Cut | Measured), f"{case}: the parent never concluded"
            held = session.store.ruler.rows(session_ruler_scope(session), ruler_clock(session))
            observed = {cell for walked, cell, _ in held if walked == parent_arm.sp_hash}
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
            assert [rr.round for rr in cycle.rounds] == [0] and cycle.selection.round == 0, (
                f"{case}: a stop between this election and its close leaves no result"
            )

    # Round 2's parent is a retained member: its re-read lands on cells its own walk holds.
    for cut_round, budget, replayed, parent_cells in ((1, 17, 0, 2), (2, 5, 12, 0)):
        for panel_gate in ("strict", "off"):
            cut_by_budget(cut_round, budget, replayed, parent_cells, panel_gate)


@pytest.mark.usefixtures("optimizing_memory")
def test_capo_races_block_after_block_while_more_than_mu_arms_stand_and_stops_at_mu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    def raced(failing: str) -> RoundResult:
        minted: list[str] = []

        def capo_says(body: dict[str, Any]) -> str:
            if "Create overall 15 prompts" in _asked(body):
                return json.dumps(
                    ["Solve the task, A.", "Solve the task, B.", "Solve the task, C."]
                )
            minted.append(f"Go {len(minted)}.")
            return f"<prompt>{minted[-1]}</prompt>"

        def misses_after_block_one(prompt: str, sample: Sample) -> bool:
            block = _PEER_READ_ORDER[sample.id] // 4
            return block == 0 or not any(f"task, {arm}." in prompt for arm in failing)

        with _peer_cycle(
            workspace(tmp_path / f"race-{failing}"),
            _PEER_HOP,
            monkeypatch,
            "capo",
            _CAPO_NODES,
            misses_after_block_one,
        ) as (cycle, ledger):
            offline.llm.says = capo_says
            return asyncio.run(execute_round(cycle, 1, ledger)).result

    def fates(closed: RoundResult) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        for key in closed.panel:
            arm = closed.arms[key]
            state = (
                arm.kind
                if isinstance(arm, Cut)
                else "locked_in"
                if isinstance(arm, Measured) and arm.locked_in is not None
                else "measured"
            )
            out.setdefault(state, []).append(len(closed.rows(key)))
        return out

    # Five arms, μ 3: nobody falls on block 1; C falls on block 2, and four still race block 3.
    assert fates(raced("C")) == {"eliminated": [8], "measured": [12] * 4}, (
        "a survivor set above μ stopped racing before max_blocks"
    )
    # B and C fall together on block 2, which leaves μ: the race is settled there.
    assert fates(raced("BC")) == {"eliminated": [8, 8], "locked_in": [8] * 3}, (
        "a race at μ survivors walked on, or settled before its cut"
    )


@pytest.mark.usefixtures("optimizing_memory")
def test_a_survey_rounds_starts_and_reference_are_arms_and_its_matrix_their_cells() -> None:
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


@pytest.mark.usefixtures("optimizing_memory")
def test_potters_queue_in_a_peers_roster_sizes_its_panel_off_its_own_knobs(
    peer_composition: _Composition,
) -> None:
    name, walk, composed, *_ = peer_composition
    with composed(["adaptive_queue", *walk[1:]]):
        queued = select_optimizer(
            OptimizerChoice(
                optimizer=name,
                nodes={"adaptive_queue": ManifestNodeOverlay(config={"sp_budget_round": 7})},
            )
        )
    assert queued.round_cells(40) == 7, (
        "potter's queue, the one potter member in a peer's roster, sized its panel off another's knobs"
    )


@pytest.mark.usefixtures("optimizing_memory")
def test_one_round_composes_a_peers_crossover_and_selector_with_potters_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router, peer_composition: _Composition
) -> None:
    name, walk, composed, crossover_says, crossed, composed_nodes = peer_composition
    root = tmp_path / "composition"
    with (
        composed(walk),
        _peer_cycle(
            workspace(root),
            _PEER_HOP,
            monkeypatch,
            name,
            composed_nodes,
            lambda prompt, _s: "Merged." in prompt,
        ) as (cycle, ledger),
    ):
        offline.llm.says = crossover_says
        assert not cycle.population
        closed, cut, _ = asyncio.run(execute_round(cycle, 1, ledger))
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


@pytest.mark.usefixtures("optimizing_memory")
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


@pytest.mark.usefixtures("optimizing_memory")
def test_a_cut_theta_round_reads_its_cold_parent_on_the_cells_the_archive_holds_graded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router, peer_composition: _Composition
) -> None:
    # The guard and the election alike.
    name, walk, composed, crossover_says, _, composed_nodes = peer_composition
    theta_book = spend_book(None, usd_reserve=None)
    # Empty until round 1 walks, so the origin's cells pay toward no cut.
    theta_walking: list[bool] = []
    theta_paid: list[int] = []

    def paid_then_cut(_prompt: str, sample: Sample) -> bool:
        if theta_walking:
            theta_paid.append(sample.id)
            if len(theta_paid) >= 2:
                theta_book.share = Budget(usd=0.0)
        return True

    async def unpriced(sample: Sample, _session: Any, _pipeline_params: Any) -> MeasuredCell:
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

    with (
        composed([*walk[:-1], "theta_election"]),
        _peer_cycle(
            workspace(tmp_path / "theta-cut"),
            _PEER_HOP,
            monkeypatch,
            name,
            composed_nodes,
            paid_then_cut,
            book=theta_book,
            panel_gate="off",
        ) as (cycle, ledger),
    ):
        offline.llm.says = crossover_says
        session = cycle.session
        search = list(session.state.scoring.partition.search)
        with monkeypatch.context() as patched:
            patched.setattr(query_loop, "measure_sample", unpriced)
            asyncio.run(
                score_search_point(
                    cycle.parent_point,
                    search[1:2],
                    session,
                    kind=MeasurementRole.ORIGIN,
                    measured=None,
                )
            )
        asyncio.run(
            score_search_point(
                cycle.parent_point,
                search[:1],
                session,
                kind=MeasurementRole.ORIGIN,
                measured=None,
            )
        )
        theta_walking.append(True)
        with pytest.raises(StopLoop) as unwound:
            asyncio.run(execute_round(cycle, 1, ledger))
    assert unwound.value.reason is StopReason.SPEND_BUDGET
    assert cycle.rounds[-1].round == 0, "a cut round stood with no arm its selector could read"


def _bench_says(body: dict[str, Any]) -> str:
    if "Create overall 15 prompts" in _asked(body):
        return json.dumps(["Solve it GOOD.", "Solve it OK."])
    return "<prompt>Solve it BAD.</prompt>"


class _PeerBench(NamedTuple):
    cycle: Cycle
    ledger: CycleEventLog
    session: Session
    stores: Stores
    cycle_dir: Path


class _Bench(NamedTuple):
    cycle: Cycle
    ledger: CycleEventLog
    session: Session
    stores: Stores
    cycle_dir: Path
    picked: RoundResult
    head: Cut | Measured
    fold: ScoreSummary
    origin_pass: VerificationPass
    graded: Callable[..., BenchPasses]


_PEER_PAUSE = PauseCyclePayload(
    campaign_id=_PEER_HOP.campaign_id, cycle_id=_PEER_HOP.cycle_id, reason="stop"
)


@contextmanager
def _peer_bench(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> Iterator[_PeerBench]:
    """The bench's CAPO cycle seated, and its campaign filed so a command reaches it."""
    bench_nodes = {
        "blocks": {"config": {"block_size": 4, "max_blocks": 2}},
        "capo_init": {"config": {"size": 2, "k_max": 0}},
        "mating": {"config": {"offspring": 1}},
        "few_shot": {"config": {"k_max": 0}},
        "survival": {"config": {"size": 2}},
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
    ) as (cycle, ledger):
        offline.llm.says = _bench_says
        stores = cycle.session.store
        peer = minted_campaign(_PEER_HOP)
        stores.campaigns.create_campaign(peer)
        minted_dataset(stores, peer)
        stores.campaigns.mint_cycle(_PEER_HOP)
        yield _PeerBench(
            cycle, ledger, cycle.session, stores, stores.campaigns.cycle_dir(_PEER_HOP)
        )


@contextmanager
def _bench_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> Iterator[_Bench]:
    """Its round, whose declared pick tops a higher-composite arm, and the origin's bench pass."""
    with _peer_bench(tmp_path, monkeypatch, offline) as (cycle, ledger, session, stores, cycle_dir):
        picked = asyncio.run(execute_round(cycle, 1, ledger)).result
        head, fold = picked.head, picked.fold
        assert head is not None and fold is not None and fold.fitness is not None
        origin = cycle.origin_round.origin.individual
        origin_pass = asyncio.run(
            score_on_bench(
                session,
                cycle.points.measured(cycle.measured(origin.id)),
                individual_id=origin.id,
                subject="origin",
                label="C0",
                round_num=0,
                ledger=ledger,
            )
        )

        def graded(*held: VerificationPass) -> BenchPasses:
            banked = bench_passes(origin_pass, *held)
            return asyncio.run(
                bench_selection(
                    session, cycle.rounds, points=cycle.points, banked=banked, ledger=ledger
                )
            )

        yield _Bench(
            cycle, ledger, session, stores, cycle_dir, picked, head, fold, origin_pass, graded
        )


def _pause_mid_pass(
    bench: _Bench, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[RunPhase], list[str], Any]:
    """A pause sent as the bench pass walks its second cell; the last graded view it filed."""
    cycle, ledger, session, stores = bench.cycle, bench.ledger, bench.session, bench.stores
    loop_control, session.state.control = (
        session.state.control,
        RunControl(commands=stores.commands, enclosing=(stores.subject(_PEER_HOP),)),
    )
    measure = vars(query_loop)["measure_sample"]
    walked: list[int] = []
    phases: list[RunPhase] = []
    acks: list[str] = []

    async def _stopped_mid_pass(sample: Sample, at: Any, pipeline_params: Any) -> MeasuredCell:
        walked.append(sample.id)
        if len(walked) == 2:
            phases.append(stores.run_state(_PEER_HOP).run_phase)
            acks.append(
                (
                    await CommandDispatcher(stores).dispatch(
                        CommandCall(_PEER_PAUSE, door=Door.HOST)
                    )
                ).ack
            )
            phases.append(stores.run_state(_PEER_HOP).run_phase)
        cell: MeasuredCell = await measure(sample, at, pipeline_params)
        return cell

    async def _bench_pick() -> BenchPasses:
        async with paid_pass(
            stores=stores,
            pass_kind=MeasurementRole.BENCH,
            hop=_PEER_HOP,
            declared=Budget(),
            sends=(),
        ):
            return await bench_selection(
                session,
                cycle.rounds,
                points=cycle.points,
                banked=bench_passes(bench.origin_pass),
                ledger=ledger,
            )

    with monkeypatch.context() as patched:
        patched.setattr(query_loop, "measure_sample", _stopped_mid_pass)
        with pytest.raises(PassStoppedError):
            asyncio.run(_bench_pick())
    session.state.control = loop_control
    [*_, stopped_view] = [
        record.view
        for record in ledger_records(ledger)
        if isinstance(record, PhaseRecord) and record.event == "graded"
    ]
    return phases, acks, stopped_view


async def _held_pass(
    stores: Stores, stop: Callable[[], Awaitable[object]], acting: str | None = None
) -> None:
    async with paid_pass(
        stores=stores,
        pass_kind=MeasurementRole.BENCH,
        hop=_PEER_HOP,
        declared=Budget(),
        sends=(),
        acting=acting,
    ):
        await stop()
        # What the walk and every wait inside a pass do once its cancel stands.
        walk = RunControl(
            commands=stores.commands,
            enclosing=(stores.subject(_PEER_HOP),),
            acting=() if acting is None else (acting,),
        )
        if walk.pause_requested() and (abort := run_scope().abort_check) is not None and abort():
            raise asyncio.CancelledError("aborted")
        raise AssertionError("a stop left the pass running")


class _ClosedOrigin(NamedTuple):
    drawn: list[Sample]
    session: Session
    origin_sp: JobSearchPoint


def _closed_origin(bench: _PeerBench) -> _ClosedOrigin:
    """Round 0 closed on the origin's walk, and its session on the bank less one row it recorded,
    so a band's rescore can only read the rest."""
    cycle, ledger, session = bench.cycle, bench.ledger, bench.session
    origin = cycle.origin_round.origin.individual
    c0_arm = cycle.origin_round.panel[ArmKey(origin.id)]
    ledger.append(RoundEnteredRecord(round=0))
    ledger.append(
        ArmRecord(
            round=0,
            arm=ArmKey(origin.id),
            state=Proposed(role=c0_arm.role, slot=c0_arm.slot, individual=c0_arm.individual),
        )
    )
    with scoped(round=0):
        c0_walk = asyncio.run(
            score_search_point(
                cycle.points.measured(cycle.measured(origin.id)),
                list(session.state.scoring.partition.search)[:4],
                session,
                kind=MeasurementRole.ORIGIN,
                measured=origin.id,
            )
        )
    c0_round = cycle.origin_round.model_copy(
        update={
            "optimizer_state": optimizer_state(),
            "cells": {ArmKey(origin.id): c0_walk.sheet},
        }
    )
    close_round(ledger, [c0_round], cycle=cycle)
    round_zero = bench.stores.campaigns.standing_rounds(_PEER_HOP, moment=None).rounds[0].close
    assert round_zero.election is not None
    recorded_keys = {key for key, _, _ in round_zero.cells.arms[round_zero.election.ended_on]}
    drawn = [s for s in session.bank.samples if s.key in recorded_keys]
    kept = [s for s in session.bank.samples if s.key != drawn[0].key]
    return _ClosedOrigin(
        drawn, dataclasses.replace(session, bank=Bank.of(kept)), cycle.points.job(origin)
    )


@pytest.mark.usefixtures("optimizing_memory")
def test_an_arm_is_read_on_the_theta_its_election_takes_over_every_cell_of_its_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    with _bench_cycle(tmp_path, monkeypatch, offline) as bench:
        cycle, picked, fold = bench.cycle, bench.picked, bench.fold
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
        optimization = cycle.config.optimization.model_copy(
            update={"halt_at_fitness": fold.fitness, "lives": None}
        )
        unpaid = LineOutlay(search_usd=None, worked_s=None)
        standing = RunStanding.after(list(cycle.rounds), lives=None, outlay=unpaid)
        assert fold.total == 4 and standing_tripped(standing, optimization) is None, (
            "a 4-cell point estimate met the target: the ceiling reads the paired lower bound"
        )


@pytest.mark.usefixtures("optimizing_memory")
def test_a_pause_reaches_a_bench_pass_in_flight_and_it_banks_as_stopped_on_the_cells_it_walked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    with _bench_cycle(tmp_path, monkeypatch, offline) as bench:
        stores, cycle_dir = bench.stores, bench.cycle_dir
        phases, acks, stopped_view = _pause_mid_pass(bench, monkeypatch)
        assert (phases, acks) == ([RunPhase.SCORING, RunPhase.PAUSED], ["accepted"]), (
            "a pause was refused by a pass in flight, or the cycle never read as scoring"
        )
        assert isinstance(stopped_view, VerificationGradedView)
        paused_pass = stopped_view.verification
        assert stopped_view.state is ReadingState.PENDING and paused_pass.stop is not None, (
            "a paused pass read as faulted, when `resume` sends it again"
        )
        assert paused_pass.stop.cause is StopReason.PAUSED and 0 < len(paused_pass.cells) < 4, (
            "a paused pass banked no record of the cells it walked, or never stopped"
        )
        assert stores.inbox(_PEER_HOP).pause is None and not cycle_held(cycle_dir), (
            "the pass left its pause unapplied, or kept the cycle after it stopped"
        )


# The cancel after the pass ended names the task the host cancel opened.
@pytest.mark.usefixtures("optimizing_memory")
def test_a_host_cancel_and_a_ctrl_c_each_stop_a_pass_by_its_commands_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    with _peer_bench(tmp_path, monkeypatch, offline) as bench:
        stores, cycle_dir = bench.stores, bench.cycle_dir
        # A cancel of a command that ended stands for nothing, so no stop waits for whatever holds
        # the cycle next.
        host = HostSession(Caller(stores))

        def cancels() -> list[object]:
            book = stores.commands.book()
            return [
                record.payload["command_id"]
                for _, record in book.lines()
                if record.kind == CommandKind.CANCEL and book.status(record.command_id) == "applied"
            ]

        def _passing(stop: Callable[[], Awaitable[object]]) -> Callable[[Any], Awaitable[Any]]:
            async def _verify(dispatched: Any) -> Applier[None]:
                return Applier(lambda: _held_pass(stores, stop, dispatched.command_id))

            return _verify

        def _verify_call(candidate_id: str) -> CommandCall[Any]:
            return CommandCall(
                VerifyCandidatePayload(
                    campaign_id=_PEER_HOP.campaign_id,
                    cycle_id=_PEER_HOP.cycle_id,
                    candidate_id=candidate_id,
                ),
                door=Door.HOST,
            )

        opened_task: dict[str, str] = {}
        # The act runs on past the answer, so it can reach its pass before the host holds the id.
        answered = asyncio.Event()

        async def _cancel_held() -> HostTask:
            await answered.wait()
            return await host.answer(CancelTask(opened_task["held"]))

        async def _host_cancel() -> tuple[HostTask, str]:
            sent = {
                "campaign_id": _PEER_HOP.campaign_id,
                "cycle_id": _PEER_HOP.cycle_id,
                "candidate_id": "held",
                IDEMPOTENCY_KEY: "held",
            }
            opened = await host.answer(CallTool("verify-candidate", sent))
            assert opened.task is not None
            opened_task["held"] = opened.task.task_id
            answered.set()
            with pytest.raises(PotterError) as stopped:
                await host.answer(TaskResultOf(opened.task.task_id))
            return await host.answer(TaskStatusOf(opened.task.task_id)), stopped.value.code

        before = len(cancels())
        with monkeypatch.context() as patched:
            patched.setattr(measuring, "verify_candidate", _passing(_cancel_held))
            cancelled, result_code = asyncio.run(_host_cancel())
        landed: list[str] = []

        async def _ctrl_c() -> str:
            pressed_call = _verify_call("pressed")

            async def _press() -> None:
                signal.raise_signal(signal.SIGINT)
                # A send already out bills either way: the press stops the pass at its next
                # checkpoint, never by cancelling the wait on what it paid for.
                while stores.commands.book().stop(pressed_call.command_id) is None:
                    await asyncio.to_thread(lambda: None)
                landed.append(pressed_call.command_id)

            with monkeypatch.context() as patched:
                patched.setattr(measuring, "verify_candidate", _passing(_press))
                with (
                    pytest.raises(PassStoppedError) as ended,
                    ctrl_c_stops_pass(CommandDispatcher(stores), pressed_call),
                ):
                    await CommandDispatcher(stores).dispatch(pressed_call)
            return str(ended.value)

        pressed = asyncio.run(_ctrl_c())
        assert cancelled.status == "cancelled" and "cancel by" in cancelled.status_message, (
            "a host cancel left the pass running"
        )
        assert result_code == PassStoppedError.code, "a stopped pass's result is not its refusal"
        assert "cancel by" in pressed, "a Ctrl+C left the pass running"
        assert len(landed) == 1, "a Ctrl+C cancelled the pass's in-flight sends (bl1.B2)"
        assert len(cancels()) == before + 2 and opened_task["held"] in cancels(), (
            "a host cancel or a Ctrl+C ended the pass without its cancel"
        )
        assert stores.commands.book().stop(opened_task["held"]) is None, (
            "a door's stop outlived its pass"
        )
        assert not cycle_held(cycle_dir), "the pass kept its cycle"

        async def _cancel(command_id: str) -> str:
            sent = CommandCall(CancelPayload(command_id=command_id), door=Door.HOST)
            return (await CommandDispatcher(stores).dispatch(sent)).ack

        assert asyncio.run(_cancel(opened_task["held"])) == "applied", (
            "a cancel of an ended command was refused"
        )
        assert stores.commands.book().stop(opened_task["held"]) is None, (
            "a cancel after its pass ended stood"
        )
        with pytest.raises(NotFoundError):
            asyncio.run(_cancel("never-filed"))


@pytest.mark.usefixtures("optimizing_memory")
def test_a_command_resolves_an_l4_leaf_by_the_rule_a_read_takes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    with _peer_bench(tmp_path, monkeypatch, offline) as bench:
        stores = bench.stores
        # The command addresses the leaf by the id prefix a read takes, below its chain, so the act
        # reaches the leaf's campaign under its full id.
        leaf = CycleHop(campaign_id="innerds__20260101-000000", cycle_id="inner-cycle-0")
        sandbox = inner_sandbox(stores, _PEER_HOP.campaign_id, _PEER_HOP.cycle_id)
        (sandbox / stores.tenant_id).mkdir(exist_ok=True)
        inner = descend_store(stores, (_PEER_HOP,))
        inner.campaigns.create_campaign(minted_campaign(leaf))
        inner.campaigns.mint_cycle(leaf)
        reached: list[CycleHop] = []

        async def _verify_reached(dispatched: Any) -> Any:
            reached.append(dispatched.hop)
            raise ConflictError("reached")

        addressed = Subject.parse(
            f"candidate:innerds/{leaf.cycle_id}/c1;in={_PEER_HOP.campaign_id}::{_PEER_HOP.cycle_id}"
        )
        verify = VerifyCandidatePayload.model_validate(
            {**addressed.command_fields, "candidate_id": addressed.candidate_id}
        )
        with monkeypatch.context() as patched:
            patched.setattr(measuring, "verify_candidate", _verify_reached)
            with pytest.raises(ConflictError):
                asyncio.run(CommandDispatcher(stores).dispatch(CommandCall(verify, door=Door.HOST)))
        assert reached == [leaf], "a command resolved its leaf by another rule than a read's"


@pytest.mark.usefixtures("optimizing_memory")
def test_a_campaigns_command_by_a_prefix_acts_on_its_full_id_as_a_cycles_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    with _peer_bench(tmp_path, monkeypatch, offline) as bench:
        stores = bench.stores
        benched: list[str] = []

        async def _bench_reached(dispatched: Any) -> Any:
            benched.append(dispatched.payload.campaign_id)
            raise ConflictError("reached")

        bench_by_prefix = GradeBenchPayload(campaign_id=_PEER_HOP.campaign_id[:3])
        with monkeypatch.context() as patched:
            patched.setattr(measuring, "grade_bench", _bench_reached)
            with pytest.raises(ConflictError):
                asyncio.run(
                    CommandDispatcher(stores).dispatch(CommandCall(bench_by_prefix, door=Door.HOST))
                )
        assert benched == [_PEER_HOP.campaign_id], "a campaign's command acted on the typed prefix"


@pytest.mark.usefixtures("optimizing_memory")
def test_a_host_task_is_filed_at_the_address_its_dispatch_acted_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    with _peer_bench(tmp_path, monkeypatch, offline) as bench:
        stores = bench.stores
        host = HostSession(Caller(stores))
        # The filing names the pass's cycle even once another campaign shares the sent prefix.
        sent_by_prefix = {
            "campaign_id": _PEER_HOP.campaign_id[:3],
            "cycle_id": _PEER_HOP.cycle_id,
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
        assert filed is not None and filed.payload["campaign_id"] == _PEER_HOP.campaign_id, (
            "a host task filed the prefix it was sent by, which a later mint can make ambiguous"
        )


@pytest.mark.usefixtures("optimizing_memory")
def test_a_noise_band_stands_beside_the_recorded_level_of_exactly_the_rows_it_rescored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    with _peer_bench(tmp_path, monkeypatch, offline) as bench:
        ledger = bench.ledger
        drawn, session, origin_sp = _closed_origin(bench)
        with scoped(ledger=ledger):
            band = asyncio.run(held_out.measure_noise_floor(session, origin_sp, 2))
        assert isinstance(band.reading, NoiseBand) and len(band.reading.raw) == 2
        assert band.reading.recorded.n == len(band.verification.sample_keys) == len(drawn) - 1, (
            "the band stood beside a recorded level over rows it never re-scored (bl3.B2)"
        )
        assert band in [
            record.view for record in ledger_records(ledger) if isinstance(record, PhaseRecord)
        ], "the band was not filed on its cycle's ledger"


@pytest.mark.usefixtures("optimizing_memory")
def test_a_round_0_with_replicates_files_one_band_over_k_rescores_of_its_recorded_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    offline.llm.says = _bench_says
    with _peer_cycle(
        workspace(tmp_path / "replicated"),
        _PEER_HOP,
        monkeypatch,
        "capo",
        {"capo_init": {"config": {"size": 2, "k_max": 0}}},
        lambda prompt, s: "OK" in prompt or s.id % 2 == 0,
        origin_replicates=2,
    ) as (cycle, ledger):
        stores, peer = cycle.session.store, minted_campaign(_PEER_HOP)
        stores.campaigns.create_campaign(peer)
        minted_dataset(stores, peer)
        session = cycle.session
        session.state.clock = LaunchClock.now()
        # The origin walked in round 0's door, which the round file's cells name.
        ledger.append(RoundEnteredRecord(round=0))
        walked = list(session.state.scoring.partition.search)[:4]
        with scoped(round=0):
            asyncio.run(
                open_origin_round(cycle, walked, ledger, spend=SpendRollup(), resumed_ending=None)
            )
        bands = [
            record.view
            for record in ledger_records(ledger)
            if isinstance(record, PhaseRecord)
            and isinstance(record.view, VerificationGradedView)
            and record.view.verification.kind is MeasurementRole.NOISE_FLOOR
        ]
    assert len(bands) == 1, "round 0 filed no band, or one per replicate"
    (band,) = bands
    assert isinstance(band.reading, NoiseBand) and len(band.reading.raw) == 2
    rows = band.verification.sample_keys
    assert sorted(rows) == sorted(s.key for s in walked), (
        "the band re-scored rows round 0 never recorded"
    )
    assert len(band.verification.cells) == 2 * len(rows), "a replicate re-scored a partial set"


@pytest.mark.usefixtures("optimizing_memory")
def test_a_rescore_that_raises_ends_the_bands_mark_at_its_brackets_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    with _peer_bench(tmp_path, monkeypatch, offline) as bench:
        ledger = bench.ledger
        _, session, origin_sp = _closed_origin(bench)

        async def _outage(*_: object, **__: object) -> NoReturn:
            raise RuntimeError("the rescore raised")

        failed_from = ledger.next_offset
        with monkeypatch.context() as patched, scoped(ledger=ledger):
            patched.setattr(held_out, "score_search_point", _outage)
            with pytest.raises(RuntimeError):
                asyncio.run(held_out.measure_noise_floor(session, origin_sp, 2))
        dashboard = LiveDashboardProjection()
        dashboard.feed(
            RunWiringRecord(
                round_cells_ceiling=1,
                display_metric="fitness",
                elects_on="fitness",
                max_cells_in_flight=1,
                measured_unit="sample",
                measurement_node="solve",
                run_limits=RunLimits(),
                defaults_drift=None,
                backend_revision=None,
                tree_hash="tree",
            )
        )
        for at, record in enumerate(ledger_records(ledger)):
            if at >= failed_from:
                dashboard.feed(record)
        served = dashboard.value()
        assert served is not None
        assert [(m.state, m.failed_as) for m in served.verification if m.kind == "noise_floor"] == [
            ("failed", ReadingState.PASS_STOPPED)
        ], "a noise floor that raised left its mark running"


@pytest.mark.usefixtures("optimizing_memory")
def test_a_spent_budget_stops_the_bench_pass_and_the_held_passes_beside_it_stand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    with _bench_cycle(tmp_path, monkeypatch, offline) as bench:
        origin_pass, head, graded = bench.origin_pass, bench.head, bench.graded
        earlier = origin_pass.model_copy(
            update={"arm": ArmName(key=ArmKey("an earlier pick"), round=7, label="C7.1")}
        )
        book = require_spend_book("the bench journey")
        unspent, book.share = book.share, Budget(usd=0.0)
        short = graded(earlier)
        book.share = unspent
        stop = short.selections[head.individual.id].stop
        assert stop is not None and stop.cause is StopReason.SPEND_BUDGET, (
            "a spent budget stops the pass and names the bound"
        )
        assert short.selections[earlier.arm.key] is earlier, "a held pass was dropped"


# The refused pass holds the cells the paused pass banked.
@pytest.mark.usefixtures("optimizing_memory")
def test_a_refusal_mid_walk_banks_the_cells_the_pass_held_never_zero_over_paid_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    with _bench_cycle(tmp_path, monkeypatch, offline) as bench:
        head, graded = bench.head, bench.graded
        paused_pass = _pause_mid_pass(bench, monkeypatch)[2].verification

        async def _refused_mid_pass(*_: Any) -> MeasuredCell:
            raise SendRefusedError("no rate for this model", category=ErrorCategory.NO_RATE)

        with monkeypatch.context() as patched:
            patched.setattr(query_loop, "measure_sample", _refused_mid_pass)
            no_rate = graded().selections[head.individual.id]
        assert no_rate.stop is not None and no_rate.stop.cause is StopReason.NO_RATE, (
            "a refused pass did not name its refusal"
        )
        assert {key for key, _, _ in paused_pass.cells} <= {key for key, _, _ in no_rate.cells}, (
            "a refused pass banked none of the cells it held (bl3.B4)"
        )


# A pass after the stop replays the cells the paused pass banked, and each regrade reads the last.
@pytest.mark.usefixtures("optimizing_memory")
def test_the_bench_grades_the_pick_the_optimizer_declared_over_a_higher_composite_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    with _bench_cycle(tmp_path, monkeypatch, offline) as picked:
        cycle, session, head, graded = picked.cycle, picked.session, picked.head, picked.graded
        paused_pass = _pause_mid_pass(picked, monkeypatch)[2].verification
        passes = graded()
        [taken] = passes.selections.values()
        assert (taken.arm.key, taken.arm.round, taken.stop) == (head.individual.id, 1, None), (
            "the bench grades the pick the optimizer declared"
        )
        assert {key for key, _, _ in paused_pass.cells} <= {
            key for key, _, replayed in taken.cells if replayed
        }, "the pass after a stop bought again a cell the stopped pass had banked"
        assert graded(taken).selections[taken.arm.key] is taken, "a held pass was sent again"
        left = taken.model_copy(update={"sp_hash": "a pick the line left"})
        resent = graded(left).selections[taken.arm.key]
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
        assert (selected_accuracy.value, origin_fitness.value) == (1.0, 0.0)
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
        )
        assert result.result_round == bench.selected.round == 1, "the result names the round graded"


@pytest.mark.usefixtures("optimizing_memory")
def test_a_bench_line_reads_the_pick_it_holds_or_says_why_it_reads_nothing(
    tmp_path: Path,
) -> None:
    line_stores = workspace(tmp_path / "line")
    pick = prompted(instruction="the declared pick")
    spent = PassStop(cause=StopReason.SPEND_BUDGET, warning=None)
    refused = PassStop(cause=WalkEnd.CLIENT_ERROR, warning=None)
    line_scorer = scorer_of(_H2H_FORMULA)
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
        # A campaign its pass's admission refuses: no grade is offered.
        (
            None,
            {"refused": CampaignRefusal.NOT_ACTIVE},
            ReadingState.NOT_ASKED,
            False,
            None,
            (None, []),
            None,
        ),
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
    )
    for banked, run, state, can_run, lift, series, stopped in line_cases:
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
            line_passes = bench_passes(line_origin, *held, tolerance=banked.get("tolerance", 0))
        chosen = "origin" if banked and banked.get("pick") == "origin" else pick.id
        line = bench_line(
            ArmName(key=ArmKey(chosen), round=1, label="C1.1"),
            on_line="held_by" not in run,
            **run,
        )
        line_bench = read_bench(line_stores, line_passes, line_scorer, line=line)
        assert (line_bench.status.state, line_bench.status.can_run) == (state, can_run), case
        headline = line_bench.vs_origin.headline
        assert (None if headline is None else headline.estimate.value) == lift, case
        assert line_bench.status.subject == stopped or not stopped, f"{case}: names who stopped"
        pick_arm = arm_state("C1.1", slot=ArmSlot(round=1, idx=0, total=1), individual=pick)
        on_line = round_result(1, arms={ArmKey(pick.id): pick_arm}, selected=pick.id, cells={})
        assert bench_series(line_stores, line_passes, line_scorer, rounds=[on_line]) == series, case


# 4. An authored round — the operator's arm in the seat a proposer fills


@pytest.mark.usefixtures("optimizing_memory")
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
        return CommandDispatcher(authored).dispatch(CommandCall(payload, key, door=Door.HOST))

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
            control=RunControl(commands=authored.commands, cycle=authored.subject(hop)),
            arm_gate="author",
        ) as (cycle, ledger):
            offline.llm.says = lambda _body: "ok"
            at_origin = RoundContext(cycle=cycle, round_num=0, ledger=ledger)
            origin_read = PotterView(
                NodeContext(at_origin, optimizers.NODE_MEMBERS.get("l1_critique")), Ladder()
            )
            assert [b.text for b in origin_read.block_window().get("persona", ())[:1]] == [
                "Answer tersely."
            ], "round 0's critique composed before the block pool was seeded"
            l1 = cast("node_contract.Proposer", optimizers.NODE_MEMBERS.get("l1_generate"))
            seed = cast("node_contract.Proposer", optimizers.NODE_MEMBERS.get("mating"))
            seeded = node_contract.RoundWalk("default", (seed,), (l1,), "author")
            blank = node_contract.RoundOpening("", "", None, "", replayed=False)
            stated = l1.opening(NodeContext(at_origin, l1), blank)
            assert announce_opening(at_origin, seeded) == stated != blank, (
                "a seed walking ahead of L1 stated the round's standing and arms in L1's place"
            )
            measured.clear()

            async def at_the_hold() -> RoundResult:
                with pytest.raises(ConflictError):
                    await submit("arms-before-the-hold")

                held = asyncio.Event()

                async def gate_declared(*args: Any, **kwargs: Any) -> Any:
                    held.set()
                    return await hold_for_operator(*args, **kwargs)

                monkeypatch.setattr(
                    "promptpotter.application.runner.round.hold_for_operator", gate_declared
                )

                async def answer() -> None:
                    await held.wait()
                    assert authored.run_state(hop).gate == "arms", (
                        "the hold never reached the ledger"
                    )
                    await submit("arms-at-the-hold")

                (held_round, held_cut, _), _ = await asyncio.gather(
                    execute_round(cycle, 1, ledger), answer()
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
            resumed = PotterView(
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
            n_samples=28,
            ruler=None,
            parent=parent,
        )
        field = {
            ArmKey("winner"): sheet(measurements([1.0] * n)),
            parent: sheet(measurements([1.0] * parent_cells)),
            ArmKey("arm"): sheet(measurements([0.0] * misses + [1.0] * (n - misses))),
        }
        return check.check(field, [ArmKey("winner"), ArmKey("arm")]).get(ArmKey("arm"))

    return _Trailing(cfg, parent, behind_perfect_prior)


@pytest.mark.usefixtures("optimizing_memory")
def test_epsilon_ramps_in_from_its_floor(trailing: _Trailing) -> None:
    cfg, parent, _ = trailing
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
    shipped = PoBBCheck(
        pobb_knobs(),
        n_min=6,
        n_samples=28,
        ruler=None,
        parent=parent,
    )
    assert shipped.epsilon_at(shipped.n_min) == pytest.approx(shipped.epsilon), (
        "the manifest sits floor and ε on the same value, so an untouched campaign never grades"
    )


@pytest.mark.usefixtures("optimizing_memory")
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


@pytest.mark.usefixtures("optimizing_memory")
def test_an_arm_is_never_cut_on_cells_its_rival_never_sat(trailing: _Trailing) -> None:
    _, parent, _ = trailing
    unpaired = PoBBCheck(
        pobb_knobs(epsilon=0.05),
        n_min=4,
        n_samples=20,
        ruler=None,
        parent=parent,
    )
    field = {
        ArmKey("lucky_leader"): sheet(measurements([1.0] * 8, sample_ids=list(range(8)))),
        parent: sheet(measurements([0.0] * 20)),
        ArmKey("challenger"): sheet(measurements([0.0] * 5, sample_ids=[9, 12, 13, 14, 8])),
    }
    assert not unpaired.check(field, [ArmKey("challenger")]), (
        "an arm was cut on cells its rival never sat"
    )


@pytest.mark.usefixtures("optimizing_memory")
def test_a_cost_terms_tie_breaks_never_pin_p_best_at_a_coin(trailing: _Trailing) -> None:
    cfg, parent, _ = trailing
    longer, terse_parent = _charged_by_length()
    standings: list[node_contract.RaceSnapshot] = []
    charged = PoBBCheck(
        cfg, n_min=6, n_samples=34, ruler=None, parent=parent, on_snapshot=standings.append
    )
    charged.check({parent: terse_parent, ArmKey("l"): longer}, [ArmKey("l")])
    assert standings[-1].p_best > 0.5, "a cost term's tie-breaks pinned p_best at a coin"


@pytest.mark.usefixtures("optimizing_memory")
def test_an_origin_round_serves_its_lift_over_the_constant_answer_on_the_cells_it_scored(
    tmp_path: Path, offline: Router
) -> None:
    bank = [
        Sample(id=i, query=f"q{i}", ground_truth="TRUE" if i < 9 else "FALSE") for i in range(12)
    ]
    origin = jumping_clock(run_init_round(workspace(tmp_path), offline, bank)).cycle.origin_round
    floor = origin.health.constant_floor
    assert floor is not None and (floor.label, floor.floor) == ("TRUE", 0.75), (
        f"the floor is not the modal truth's share: {floor}"
    )
    fitness = origin.results.column(ROW_GRADES["fitness"])
    truth = {cell.key: cell.facts.ground_truth for cell in origin.results.scoreable}
    paired = fmean(fitness[key] - (truth[key] == "TRUE") for key in fitness)
    assert floor.lift.value == pytest.approx(paired), "the lift is not the origin paired per cell"
    served = RoundHealth.of(0, origin.health).floor
    assert served is not None and (served.key == "floor_rewards_collapse") is (paired < 0), (
        "the served verdict disagrees with the floor's side of the origin"
    )


@pytest.mark.usefixtures("optimizing_memory")
def test_the_collapse_refusal_reads_the_answer_never_the_labels(trailing: _Trailing) -> None:
    # A verifier proves an answer wrong by an unsolved cell, and alike-and-all-solved is NOT
    # collapsed.
    _, parent, _ = trailing

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

    constant = answered([1.0, 0.0, 0.0, 1.0, 0.0, 0.0])
    unpaired_check = PoBBCheck(
        pobb_knobs(),
        n_min=6,
        n_samples=28,
        ruler=None,
        parent=parent,
    )
    assert not unpaired_check.check({ArmKey("c"): sheet(constant)}, [ArmKey("c")]), (
        "one label on an n_min prefix, read against no parent, cut an arm the panel never judged"
    )
    # Collapse is read against the parent's answers on the same cells.
    apart = sheet(
        GradedCell.from_wire({**cell.wire(), "predicted": "done" if i % 2 else "other"})
        for i, cell in enumerate(constant)
    )
    assert is_answer_collapsed(constant, apart), (
        "a constant answerer is refused with no labels to read"
    )
    assert not is_answer_collapsed(constant, sheet(constant)), (
        "one answer the parent gave on every same cell is the draw's, read by θ, not a collapse"
    )
    assert not is_answer_collapsed(constant, NO_CELLS), "with no parent answer there is no contrast"
    assert not is_answer_collapsed(answered([1.0] * 6), apart), (
        "one answer that solves every cell is not a collapse"
    )
    assert not is_answer_collapsed(
        answered([1.0, 0.0, 0.0, 1.0, 0.0, 0.0], predicted="NO_RESULT"), apart
    ), "an agent that wrote no answer file answered nothing, which is not one answer"
    assert not is_answer_collapsed(
        answered([0.0] * 6, error_category=ErrorCategory.PIPELINE), apart
    ), "a backend that broke on every cell is not a verdict on the idea"
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
    truthful = sheet(
        GradedCell.from_wire({**cell.wire(), "predicted": cell.facts.ground_truth})
        for cell in labelled
    )
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
    spanless = [
        GradedCell.from_wire({**cell.wire(), "span": "" if i == 5 else "Uncertain"})
        for i, cell in enumerate(labelled)
    ]
    assert not is_answer_collapsed(spanless, truthful), (
        "a reply with no graded span was counted as the answer"
    )


@pytest.mark.usefixtures("optimizing_memory")
def test_the_fields_p_best_sums_to_one_discriminates_graded_rows_and_reads_no_slot() -> None:
    def uncharged(*values: float) -> CellSheet:
        return sheet(measurement(i, v, objective=v) for i, v in enumerate(values))

    cells = [str(i) for i in range(12)]
    strong, weak = uncharged(*[0.66] * 12), uncharged(*[0.30] * 12)
    field = {ArmKey("a"): weak, ArmKey("b"): strong, ArmKey("c"): weak}
    p_best, _ = field_p_best(field, cells)
    assert sum(p_best.values()) == pytest.approx(1.0), "the field's shares do not sum to 1"
    assert p_best[ArmKey("b")] > 0.8, f"graded rows failed to discriminate: {p_best}"
    reordered, _ = field_p_best(dict(reversed(field.items())), cells)
    assert reordered == p_best, "the order the field was walked in moved an arm's p_best"
    ties, _ = field_p_best({ArmKey("a"): weak, ArmKey("c"): weak}, cells)
    assert ties[ArmKey("a")] == pytest.approx(0.5, abs=0.03), "a tie reads as a coin"


@pytest.mark.usefixtures("optimizing_memory")
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


@pytest.mark.usefixtures("optimizing_memory")
def test_a_rung_the_endpoint_refuses_reads_refused_on_its_arm_never_broken(
    tmp_path: Path, offline: Router
) -> None:
    said = {"detail": "Unsupported value: 'reasoning_effort' does not support 'high' here."}
    offline.backend.faults["/matches"] = [httpx.Response(400, json=said) for _ in range(12)]
    origin = jumping_clock(
        run_init_round(
            workspace(tmp_path), offline, samples(12, "TRUE"), optimization={"origin_gate": "off"}
        )
    ).cycle.origin_round
    arm = origin.origin
    assert isinstance(arm, Cut) and arm.reason == Refused(parameter="reasoning_effort"), (
        f"a refused rung read {arm.state} {getattr(arm, 'reason', None)}, not REFUSED"
    )
    (reading,) = origin.arm_readings()
    assert (reading.end, reading.refused_parameter) == ("refused", "reasoning_effort"), (
        "the served arm does not say REFUSED or name the parameter"
    )


@pytest.mark.usefixtures("optimizing_memory")
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


@pytest.mark.usefixtures("optimizing_memory")
def test_a_cell_a_declared_bound_cut_is_settled_and_never_re_measured() -> None:
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


@pytest.mark.usefixtures("optimizing_memory")
def test_gepas_gate_tests_each_arm_against_the_minibatch_scores_of_its_own_parent(
    built_stores: Stores,
) -> None:
    bars = MinibatchBars(
        bars={
            "weak_parent_child": MinibatchBar(parent_id="p1", objectives={"0": 0.1, "1": 0.1}),
            "strong_parent_child": MinibatchBar(parent_id="p2", objectives={"0": 0.9, "1": 0.9}),
        }
    )
    weak_child, strong_child = (ArmKey(cid) for cid in bars.bars)
    gated = seat(built_stores, optimization={"optimizer": "gepa"})
    gated.cycle.emit(bars, gated.round_num)
    gate = MinibatchGate()
    minibatch = samples(2)

    race = gate.race(
        NodeContext(gated, gate), Panel(cells=minibatch, order=minibatch, block_size=2)
    )
    assert race.priors == {}, "the gate paired a prior, so the bench would catch it up"
    level = [measurement(i, 1.0, objective=0.5) for i in range(2)]
    stops = race.close({weak_child: level, strong_child: level})
    assert (
        list(stops) == [strong_child] and stops[strong_child].check_result["parent_id"] == "p2"
    ), "one arm's parent bar decided another arm's gate"


# 6. Escalation and stop — live, and folded back on resume


_TODAYS_BAR = CrowningBar(statistic="posterior", admission=BAR_ADMISSION)


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
            measurands=grade_measurands(dataclasses.replace(scorer_of(), id="s")),
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
        bar=_TODAYS_BAR,
    )


# Eight panel cells C0 misses and one it takes: a pick clean on all nine clears zero over it.
_C0_PANEL: Sequence[float | None] = [0.0] * 8 + [1.0]
_CLEAN_PANEL: Sequence[float | None] = [1.0] * 9
_TIED_PANEL: Sequence[float | None] = [1.0, 0.0] + [0.0] * 6 + [1.0]


def _high_water(comp: float | None, thetas: list[float], se: float) -> HighWater:
    """Round i read thetas[i] on one series; the peak is the FIRST round at the max."""
    origin = thetas[0] if thetas else None
    rest = thetas[1:]
    return HighWater(
        composite_fitness=comp,
        series=level_series(origin, rest, origin_se=se, ses=[se] * len(rest)),
        theta_round=max(range(len(thetas)), key=thetas.__getitem__) if thetas else None,
    )


@pytest.mark.usefixtures("optimizing_memory")
def test_a_theta_stall_verdict_must_clear_its_own_error() -> None:
    # θ advances once for real (+0.467), then by noise.
    stall_rounds = [
        (0.4853, -0.1296, 0.216),
        (0.6248, 0.3376, 0.202),
        (0.6248, 0.3498, 0.198),
        (0.6248, 0.3498, 0.198),
    ]

    def climb() -> list[str]:
        ladder, actions = Ladder(), []
        for upto, (comp, _, se) in enumerate(stall_rounds):
            asked = ask_l2_escalation(
                ladder,
                best=_high_water(comp, [t for _, t, _ in stall_rounds[: upto + 1]], se),
                escalation_ladder=EscalationLadder.FULL,
                l2_patience=2,
                l3_patience=1,
                admission=0.85,
            )
            actions.append(str(asked.next_action))
            if asked.next_action != NextAction.FIRE_L2:
                break
            ladder = l2_fired(ladder, asked.l2)
        return actions

    assert climb() == ["fire_l2", "fire_l2", "fire_l2", "fire_l3"], (
        "a θ step inside the election's bar is a stall"
    )


@pytest.mark.usefixtures("optimizing_memory")
def test_the_bar_applies_on_the_theta_scale_only() -> None:
    # A composite-scale verdict has no error term.
    series = level_series(0.34, [0.35, 0.60, 0.80], ses=[0.20, 0.20, 0.20])

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
            admission=0.85,
        )
        return asked.l2.stall_count, asked.l2.comparator

    assert l2_read(0.0, 0.0, 1, 0) == (1, "theta"), "a θ step inside its SE cleared the stall"
    assert l2_read(0.0, 0.0, 2, 0) == (1, "theta"), (
        "a step past the lead's own SE but inside the lift's cleared the stall"
    )
    assert l2_read(0.0, 0.0, 3, 0) == (0, "theta"), "a θ step past the election's bar stalled"
    assert l2_read(0.7, 0.6, None, None) == (0, "composite"), (
        "with no θ on either side the composite lift is the verdict"
    )


@pytest.mark.usefixtures("optimizing_memory")
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
@pytest.mark.usefixtures("optimizing_memory")
def test_a_heal_fire_spends_no_l3_patience() -> None:
    healed = Ladder()
    seen: list[float] = []

    def stretch(theta: float, *, heal_first: bool) -> LadderAsk:
        nonlocal healed
        asks: list[LadderAsk] = []
        while not asks or asks[-1].next_action is NextAction.FIRE_L2:
            seen.append(theta)
            asked = ask_l2_escalation(
                healed,
                best=_high_water(0.5, seen, 0.2),
                escalation_ladder=EscalationLadder.FULL,
                l2_patience=2,
                l3_patience=1,
                admission=0.85,
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


# Each fire lands on the ladder the fire before it left.
@pytest.mark.usefixtures("optimizing_memory")
def test_a_landed_fire_moves_the_ladder_and_an_unlanded_one_moves_nothing() -> None:
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
            best=_high_water(fitness, [], 0.2),
            escalation_ladder=EscalationLadder.FULL,
            l2_patience=3,
            l3_patience=2,
            admission=0.85,
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


class _StallReads(NamedTuple):
    close: Callable[..., RoundResult]
    observed: Callable[[list[RoundAdvance]], Ladder]


@pytest.fixture
def stall_reads() -> _StallReads:
    """A round closed by whether it improved, and the L1 stall a ladder banks over advances."""

    def close(rnd: int, *, improved: bool, electable: int = 2) -> RoundResult:
        return round_result(rnd, improved=improved, voted={"electable": electable}, cells={})

    def observed(advances: list[RoundAdvance]) -> Ladder:
        ladder = Ladder()
        for advance in advances:
            ladder, _ = bank_and_route(
                ladder, advance=advance, l1_patience=99, escalation_ladder=EscalationLadder.FULL
            )
        return ladder

    return _StallReads(close, observed)


@pytest.mark.usefixtures("optimizing_memory")
def test_the_l1_stall_and_the_lives_bank_read_the_same_rounds(stall_reads: _StallReads) -> None:
    close, observed = stall_reads
    sequence = [(True, 2), (True, 2), (True, 2), (True, 2), (False, 0), (False, 2), (False, 2)]
    closes = [close(i, improved=up, electable=n) for i, (up, n) in enumerate(sequence, start=1)]
    assert observed([c.overlap.advance for c in closes]).l1_stall_count == 3, (
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
    assert observed([*(line.advance for line in lines), RoundAdvance.HELD]).l1_stall_count == 1, (
        "a crown is progress for pacing, separated or not; only the held round stalls"
    )


@pytest.mark.usefixtures("optimizing_memory")
def test_a_reading_that_failed_moves_the_stall_clock_neither_way(
    stall_reads: _StallReads,
) -> None:
    close, observed = stall_reads
    won = _line_read(1, True, ("C1.1", _CLEAN_PANEL), c0=_C0_PANEL)
    origin_close = close(0, improved=False, electable=0)
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
        bar=None,
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
    unread_close = round_result(3, improved=True, overlap=stopped, voted={"electable": 2})
    assert stalls_left([origin_close, unread_close], (2, 4)) == 2, (
        "the life bank reads the stall column: an unread pick banks no life"
    )


@pytest.mark.usefixtures("optimizing_memory")
def test_a_pick_one_panel_cell_holed_reads_on_the_cells_both_scored() -> None:
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


@pytest.mark.usefixtures("optimizing_memory")
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
        assert round_advance(1, True, pair, (), _TODAYS_BAR) is advance, state
        assert round_advance(1, False, pair, (), _TODAYS_BAR) is RoundAdvance.HELD, state
        assert round_advance(0, True, pair, (), _TODAYS_BAR) is RoundAdvance.ORIGIN, state


def _lived_peer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AbstractContextManager[tuple[Cycle, CycleEventLog]]:
    """A CAPO peer on a life bank of 2 capped at 4."""
    return _peer_cycle(
        workspace(tmp_path / "peer"),
        _PEER_HOP,
        monkeypatch,
        "capo",
        {},
        lambda _p, _s: True,
        lives={"start": 2, "cap": 4},
    )


@pytest.mark.usefixtures("optimizing_memory", "offline")
def test_the_bench_stops_a_peer_on_spent_lives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _lived_peer(tmp_path, monkeypatch) as (cycle, _):
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
            paid = LineOutlay(search_usd=None, worked_s=None)
            set_cycle(
                cycle, line=line, standing=RunStanding.after(line, lives=lives.bank, outlay=paid)
            )
            assert cycle.standing is not None
            return stalls_left(line, lives.bank), standing_tripped(
                cycle.standing, cycle.config.optimization
            )

        held, promoted, uncompared = (False, 2), (True, 2), (False, 0)
        assert stop_after(held) == (1, None)
        assert stop_after(held, held) == (0, StopReason.LIVES_EXHAUSTED)
        assert stop_after(held, uncompared) == (1, None), (
            "a round no arm reached the election of is evidence about the proposer, no life"
        )
        assert stop_after(promoted, promoted, promoted, held, held) == (2, None), (
            "the bank fills to its cap"
        )


@pytest.mark.usefixtures("optimizing_memory", "offline")
def test_the_campaign_ends_only_where_the_objective_is_spent_and_the_round_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _lived_peer(tmp_path, monkeypatch) as (cycle, _):
        readings = {
            RoundAdvance.ADVANCED: _line_read(1, True, ("C1.1", _CLEAN_PANEL), c0=_C0_PANEL),
            RoundAdvance.NOT_SEPARATED: _line_read(1, True, ("C1.1", _TIED_PANEL), c0=_C0_PANEL),
            RoundAdvance.ADVANCED_UNPAIRED: OverlapReading.unpaired(ReadingState.NOT_HELD, 1, True),
            RoundAdvance.UNREAD: OverlapReading.unpaired(ReadingState.PASS_STOPPED, 1, True),
        }
        assert all(reading.advance is advance for advance, reading in readings.items())

        # One ceiling stop: the pick's own level on the origin panel at its one-sided anytime-valid
        # lower bound, `halt_at_fitness` its target; no pair, no stop.
        def outcome(reading: OverlapReading, target: float) -> StopReason | None:
            closing = round_result(1, improved=True, overlap=reading)
            folds = {key: score_summary(composite_fitness=1.0) for key in closing.folds}
            line = [round_result(0, improved=False), closing.model_copy(update={"folds": folds})]
            set_cycle(
                cycle,
                line=line,
                standing=RunStanding.after(
                    line, lives=None, outlay=LineOutlay(search_usd=None, worked_s=None)
                ),
            )
            assert cycle.standing is not None
            optimization = cycle.config.optimization.model_copy(update={"halt_at_fitness": target})
            return standing_tripped(cycle.standing, optimization)

        for advance in (RoundAdvance.ADVANCED_UNPAIRED, RoundAdvance.UNREAD):
            assert outcome(readings[advance], 0.0) is None, f"{advance} met a ceiling unpaired"
        swept = _line_read(1, True, ("C1.1", [1.0] * 9), c0=[0.0] * 9)
        assert outcome(swept, 0.55) is StopReason.TARGET_HIT, (
            "a pick whose anytime lower bound clears the target ran on"
        )
        assert outcome(swept, 0.6) is None, (
            "9 of 9 stopped at a 0.6 ceiling: a short draw's rate is no level at every look"
        )


@pytest.mark.usefixtures("optimizing_memory")
def test_a_round_of_no_op_variants_measures_no_candidate_and_books_zero_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    with _peer_cycle(
        workspace(tmp_path / "restated"),
        _PEER_HOP,
        monkeypatch,
        "potter",
        {},
        lambda _p, _s: True,
    ) as (cycle, ledger):
        restated = {"solve": dict(cycle.parent.prompts["solve"].fields)}
        variant = {
            "evidence_grounding": {"field": "critique", "citation": "the parent"},
            "targets_rows": [],
            "prompt_fields_updates": restated,
            "changes_description": "restate the parent",
        }
        offline.llm.says = lambda _body: json.dumps({"variants": [variant, variant]})
        offline.llm.sent.clear()
        generating = NodeContext(
            RoundContext(cycle=cycle, round_num=1, ledger=ledger),
            optimizers.NODE_MEMBERS.get("l1_generate"),
        )
        no_cells = Panel(cells=[], order=[], block_size=0)
        member = optimizers.NODE_MEMBERS.get("l1_generate")
        assert isinstance(member, L1Generate), "l1_generate is potter's proposer"
        proposed = asyncio.run(member.propose(generating, no_cells, node_contract.Proposals([])))
        readout = cycle.output(L1Readout, 1)
    assert proposed.proposals == [], "a round of no-op variants measured a candidate"
    assert len(offline.llm.sent) == 1, "a batch the invariants read as no-ops bought a repair"
    assert (readout.l1_parse_failure, readout.l1_proposed, readout.l1_rejected) == (
        PARSE_FAILURE_ALL_NO_OP,
        0,
        {},
    ), "a round of no-op variants was booked as proposer wounds, not as zero candidates"


@pytest.mark.usefixtures("optimizing_memory")
def test_an_l1_write_to_answer_format_is_refused_before_it_costs_a_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    # The scorer parses answer_format.
    member = cast("L1Generate", optimizers.NODE_MEMBERS.get("l1_generate"))
    no_cells = Panel(cells=[], order=[], block_size=0)
    with _peer_cycle(
        workspace(tmp_path / "held_format"),
        _PEER_HOP,
        monkeypatch,
        "potter",
        {},
        lambda _p, _s: True,
    ) as (cycle, ledger):
        writes = [{"answer_format": "Say anything."}, {"instruction": "Check each premise."}]
        offline.llm.says = lambda _body: json.dumps(
            {
                "variants": [
                    {"prompt_fields_updates": {"solve": fields}, "changes_description": "edit"}
                    for fields in writes
                ]
            }
        )
        generating = NodeContext(
            RoundContext(cycle=cycle, round_num=1, ledger=ledger),
            optimizers.NODE_MEMBERS.get("l1_generate"),
        )
        proposed = asyncio.run(member.propose(generating, no_cells, node_contract.Proposals([])))
    fatal = [
        [vf.reason for vf in fatal_validation_failures(cp.validation_failures)]
        for cp in proposed.proposals
    ]
    assert fatal == [["forbidden_axis"], []], "an L1 edit of answer_format reached the backend"


# Round 3's fork offer reads the rounds the disk-faulted closes landed.
@pytest.mark.usefixtures("optimizing_memory", "offline")
def test_a_layer_forking_on_an_arm_its_round_left_unelected_re_elects_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The round the fork hangs from is restated electing that arm, so the line continues from it.
    with _peer_cycle(
        workspace(tmp_path / "reelect"),
        _PEER_HOP,
        monkeypatch,
        "potter",
        {},
        lambda _p, _s: True,
    ) as (cycle, ledger):
        origin_key = ArmKey(cycle.parent.id)
        origin_arm = arm_state("C0", slot=ArmSlot(round=0, idx=0, total=1), individual=cycle.parent)
        # Walked on no cell this cycle's bank draws, so no panel renders a row the bank lacks.
        unwalked = {ArmKey(f"c{i}"): sheet(()) for i in range(3)}
        line = [
            round_result(0, arms={origin_key: origin_arm}, selected=origin_key, cells={}),
            round_result(1, candidates_scored=3, optimizer_state=optimizer_state(), cells=unwalked),
        ]
        forking = NodeContext(
            RoundContext(cycle=cycle, round_num=2, ledger=ledger),
            optimizers.NODE_MEMBERS.get("l2_context"),
        )
        set_cycle(cycle, line=line)
        assert PotterView(forking, Ladder()).reelect_offer == {}, (
            "an improving line, which builds on its parent, was offered a fork"
        )
        # A round file the disk refuses is its ledger's one warning: the close lands, and so
        # does the next round's file, with no second warning.
        session = cycle.session
        round_files = session.store.campaigns.layout(session.hop)
        with LedgerIndex.of(ledger.path).projecting() as checkout:
            checkout.add(RoundFileProjection(session.store, session.hop, session.state))
            with monkeypatch.context() as unwritable:
                unwritable.setattr(closed_rounds, "write_json", Mock(side_effect=OSError("full")))
                close_round(ledger, line, cycle=cycle)
            line.append(
                round_result(
                    2,
                    improved=False,
                    candidates_scored=1,
                    optimizer_state=optimizer_state(),
                    cells={},
                )
            )
            set_cycle(cycle, line=line)
            close_round(ledger, line, cycle=cycle)
        warned = [r.kind for r in ledger_records(ledger) if isinstance(r, RoundWarningRecord)]
        assert warned == ["derived_file_stale"] and round_files.round_file(2).exists(), (
            "a round file's disk fault unwound its close, went unreported, or stopped the next"
        )
        forking = NodeContext(
            RoundContext(cycle=cycle, round_num=3, ledger=ledger),
            optimizers.NODE_MEMBERS.get("l2_context"),
        )
        stalled = PotterView(forking, Ladder())
        offer = stalled.reelect_offer
        # c0 sits on both rounds' panels: one entry, its first listing.
        assert offer == {
            "C1.1": RoundArm(round=1, arm=ArmKey("c0")),
            "C1.2": RoundArm(round=1, arm=ArmKey("c1")),
            "C1.3": RoundArm(round=1, arm=ArmKey("c2")),
        }, "a stalled layer may stand a fork on another arm than the picks and those they outranked"
        assert "ELECTIONS" in DispatchHub.render("rebase_capability", stalled), (
            "an arm two rounds list kept the layer's prompt from composing"
        )
        standing = fork_proposal_model(tuple(offer))
        refork = _rebase_request(
            forking, "l2_context", standing(reason="r", stand_on="C1.2"), offer
        )
        assert refork is not None and refork.fork_from_round == 2, (
            "a fork standing on a round-1 arm cuts at round 2"
        )
        hangs_from = refork.hangs_from
        assert hangs_from is not None and hangs_from.election is not None
        assert (hangs_from.election.selected, hangs_from.optimizer_state.population) == (
            ArmKey("c1"),
            [line[1].arms[ArmKey("c1")].individual],
        ), "the restated round elects another arm than the one the layer stood the fork on"
        assert line[1].election is not None and line[1].election.selected == ArmKey("c0"), "guard"
        assert (
            hangs_from.election.reelection
            == Reelection(by=Principal("l2_context/round_3", "l2_context"), reason="r")
            and hangs_from.election.verdict_reason == line[1].election.verdict_reason
        ), "a re-election is not told apart from the selector's own verdict"
        stood_on_pick = _rebase_request(
            forking, "l2_context", standing(reason="r", stand_on="C1.1"), offer
        )
        assert stood_on_pick is not None and stood_on_pick.hangs_from is None, (
            "a fork on the round's own pick restated an election it already holds"
        )
        with pytest.raises(ValidationError):
            standing.model_validate({"reason": "r", "stand_on": "C9.9"})


# Each restate banks the fires before it, and the resume seats what they banked.
@pytest.mark.usefixtures("optimizing_memory")
def test_a_resume_folds_back_the_fires_a_layer_made_after_the_round_it_read_closed(
    tmp_path: Path,
) -> None:
    # What a layer fires is on no close.
    cycle_dir = CycleDir(tmp_path / "fired")
    fired_ledger = CycleEventLog.open(cycle_dir)
    # Appended here, as the store appends a restate.
    carried = select_optimizer(OptimizerChoice(optimizer="potter")).slices
    live = SliceMap(carried)
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
        state = SliceMap(carried)
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


# Each press reads the inbox and the phase the press before it left.
@pytest.mark.usefixtures("optimizing_memory")
def test_a_loop_command_is_acted_on_by_the_loop_it_reached_or_refused_never_dropped(
    tmp_path: Path,
) -> None:
    commanded = workspace(tmp_path / "commands")
    command_hop, command_dir = walking_cycle(commanded)
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

    def intervened() -> bool:
        index = commanded.campaigns.load(command_hop, moment=None)
        assert index is not None
        return index.human_intervened

    skip = SkipSearchpointPayload(**address)
    try:
        untaken = press(skip, "skip-1")
        assert acks(untaken) == ["accepted"], "a press was acked as a cut"
        assert not intervened(), "the press alone marked the cycle babysat"
        # A second RUNNING record is a new launch: the press the last one never reached is not its.
        commands.append(RunPhaseRecord(run_phase=RunPhase.RUNNING))
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
            waiting = commanded.run_state(command_hop).pause
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
    assert paused.pause is not None and paused.pause.cause is PauseCause.COMMAND
    for late, key in ((skip, "skip-3"), (PauseCyclePayload(**address), "pause-2")):
        with pytest.raises(ConflictError):
            press(late, key)
    assert commanded.inbox(command_hop).skips == ()
    assert not control.pause_requested(), "a pause with no run to stop was left standing"


# 7. Paired readings over shared cells


def _reference_reading(
    stores: Stores,
    reference: Sequence[float | None],
    arm: Sequence[float | None],
    *,
    cut: CutKind | None,
) -> PairedReading:
    bank = samples(len(reference), "t")
    scorer = scorer_of()

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
    # The shipped optimizers default to different models, so every arm pins one on each llm node.
    manifest = yaml.safe_load((optimizers_root() / optimizer / "pipeline.yaml").read_text("utf-8"))
    model = proposer_model or "qwen/qwen3.7-flash"
    return defaulted_config(
        {
            "optimization": {
                "optimizer": optimizer,
                "degradation_threshold": 0.0,
                "nodes": {
                    name: {"config": {"model": model}}
                    for name, node in manifest["nodes"].items()
                    if node.get("type") == "llm"
                },
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
    tilt: float = 0.0,
    elects_on: DisplayMetric = "ability",
    fit: ArmFit | None = None,
) -> Subject:
    if stores.campaigns.load_campaign(cid) is not None:
        return Subject(cid)
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
    walked = filed(
        stores.archive,
        [cell.facts for cell in panel],
        filed_under([("", {"individual": f"panel_{cid}"})], prompt_fields_id="origin"),
    )
    # A rebase fork replays its own origin and redraws the one partition.
    ledgers: dict[CycleHop, CycleEventLog] = {}
    held = Bank.of(_H2H_BANK)
    stores.tenant_datasets.put_bank(held)
    for cycle in {root, hop}:
        stores.campaigns.mint_cycle(cycle)
        stores.campaigns.append(
            cycle, PartitionRecord.of(partition, dataset_hash=held.dataset_hash)
        )
        ledgers[cycle] = CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(cycle)))
        arm_walk(
            ledgers[cycle],
            ((s.id, cell) for cell, s in zip(walked, partition.search, strict=False)),
        )
    if rebased:
        stores.mark_superseded(root, hop.cycle_id)
    if graded:
        key, pick = (
            ArmKey(f"{cid}:selected"),
            arm_state("C1.1", slot=ArmSlot(round=1, idx=0, total=1)),
        )
        answered = sheet(
            cell.filed(dataclasses.replace(cell.facts, answer=answer))
            for cell, (_, answer, _) in zip(panel, walked, strict=True)
        )
        closed = round_result(
            1,
            arms={key: pick},
            selected=key,
            cells={key: answered},
            voted={"elects_on": elects_on, "fit": {} if fit is None else {key: fit}},
        )
        close_round(
            ledgers[hop],
            [round_result(0, cells={}), closed],
            cycle=seat(stores, bank=_H2H_BANK).cycle,
        )
    if steered:
        stores.campaigns.append(hop, InterventionRecord(kind="skip"))
    passes: dict[str, VerificationPass] = {}
    for role, level in (("origin", origin), ("selected", selected))[: 1 + graded]:
        # The origin is ONE individual every campaign sends; each pick is its own.
        individual = role if role == "origin" else f"{cid}:{role}"
        rows = [
            measurement(
                s.id,
                None,
                query=s.query,
                sample_key=s.key,
                pipeline_data={
                    "env_reward": level
                    + 0.1 * (s.id % 2)
                    + (role == "selected") * tilt * (s.id % 3)
                },
            )
            for s in partition.bench
        ]
        passes[role] = bench_pass(
            stores,
            individual,
            rows,
            [s.key for s in partition.bench],
            filed_as=f"bench_{individual}_{seed}_{level}",
            sp_hash=role,
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
    for kind, usd, replay in calls:
        ledgers[root if kind == "optimizer" else hop].append(
            billed_call(
                usd,
                kind=kind,
                node=kind,
                input_tokens=10,
                output_tokens=5,
                replay=replay,
                timestamp=started,
            )
        )
    for role, taken in passes.items():
        ledgers[hop].append(
            PhaseRecord(
                phase=CampaignPhase.BENCH,
                event="graded",
                view=VerificationGradedView(
                    verification=taken,
                    subject=role,  # type: ignore[arg-type]
                    tolerance=0,
                    reserve_usd=0.05 if role == "origin" else None,
                    reserve_tokens=0 if role == "origin" else None,
                    reading=None,
                    state=ReadingState.READ,
                    refusal=None,
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
    return Subject(cid)


_PAIRED_CAND = [0.90, 0.10, 0.85, 0.20, 0.75, 0.30, 0.95, 0.05]


@pytest.mark.usefixtures("optimizing_memory")
def test_the_paired_t_matches_ttest_rel_and_brackets_the_evidence_it_tests() -> None:
    cand = _PAIRED_CAND
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


@pytest.mark.usefixtures("optimizing_memory")
def test_a_level_is_bracketed_on_the_student_t_its_paired_lift_is_tested_on() -> None:
    cand = _PAIRED_CAND

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


@pytest.mark.usefixtures("optimizing_memory")
def test_a_pair_is_one_estimate_over_the_cells_both_members_scored() -> None:
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
        member_rows("a", measurements([0.0] * 34), sparse),
        member_rows("b", measurements([1.0] * 4 + [0.0] * 30), sparse),
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
        unit="score",
    )
    (beside,) = _read_pair(a, b, bank, measurands=[lift.measurand, composite]).beside
    assert beside.flips is None
    assert beside.estimate.value == pytest.approx(0.6 * estimate.value), (
        "a measurand beside the headline is read over the SAME scored cells"
    )
    clear = _read_pair(
        member_rows("a", measurements([0.0] * 6), bank),
        member_rows("b", measurements([1.0] * 6), bank),
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


@pytest.mark.usefixtures("optimizing_memory")
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
    assert cells_read("fitness", cells, (), None) == 4
    fold = fold_cells(cells, "rate")
    assert (fold.total, fold.fitness) == (4, 0.75)
    assert fold.composite_fitness == pytest.approx(sum(worth.values()) / 4)
    assert level_band(cells, ROW_GRADES["fitness"])[0] == fold.fitness
    assert {key: row.grade.fitness for key, row in twice.by_key().items()} == standing
    once = member_rows("once", measurements(list(by_slot.values())), four)
    unmoved = _read_pair(once, twice, four)
    lift, coverage = unmoved.headline, unmoved.coverage
    assert lift is not None and coverage is not None and lift.flips is not None
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
    assert level_band(ungraded.sheet, ROW_GRADES["fitness"])[0] == pytest.approx(2 / 3)
    short = _read_pair(once, ungraded, four).coverage
    assert short is not None and short.scored == 3, "an unscored cell left the pair"
    with pytest.raises(UngradedCellError):
        ROW_GRADES["fitness"].read(ungraded.sheet.cells[-1])


@pytest.mark.usefixtures("optimizing_memory")
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


@pytest.mark.usefixtures("optimizing_memory")
def test_an_arm_is_read_on_the_cells_its_reference_was_read_on(built_stores: Stores) -> None:
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


@pytest.mark.usefixtures("optimizing_memory")
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
        scope_of=dict(enumerate("aabb")),
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
        scorer=dataclasses.replace(scorer_of(), id="s"),
        instrument_id="i",
    ).lift("fitness")
    assert on_bench is not None and on_bench.estimate.value == pytest.approx(0.5), (
        "the lift lost its pairing"
    )


@pytest.mark.usefixtures("optimizing_memory")
def test_a_verify_reads_its_fresh_cells_alone_and_pairs_its_lift_over_the_origin() -> None:
    def banked(strategy: VerifyStrategy = "random", round_num: int = 3) -> VerificationPass:
        return VerificationPass(
            kind=MeasurementRole.VERIFY,
            arm=ArmName(key=ArmKey("c"), round=round_num, label="C3.1"),
            sp_hash="h",
            cells=[(f"k{sid}", f"r.{sid}", False) for sid in range(100, 110)],
            sample_keys=[f"k{sid}" for sid in range(100, 110)],
            stop=None,
            scorer_id="s",
            strategy=strategy,
            seed=1,
            reads_before=None,
        )

    def keyed(rows: list[GradedCell]) -> CellSheet:
        return sheet((_keyed(row, f"k{row.key}") for row in rows), "s")

    recorded = keyed(measurements([1.0] * 20))
    fresh = keyed(measurements([1.0, 0.0] * 5, range(100, 110)))
    # The origin: half the round's cells, none of the fresh ones.
    c0_rows = keyed([*measurements([0.0, 1.0] * 10), *measurements([0.0] * 10, range(100, 110))])
    hop = CycleHop(campaign_id="camp", cycle_id="cyc")
    c0 = ArmName(key=ArmKey("c0"), round=0, label="C0")

    def verified(pass_: VerificationPass, fresh_rows: CellSheet = fresh) -> Any:
        arm = c0 if pass_.arm.round == 0 else pass_.arm
        return verify_reading(
            pass_,
            scorer=dataclasses.replace(scorer_of(), id="s"),
            fresh=fresh_rows,
            recorded=recorded,
            measured=verify_member(
                hop, arm, recorded.merged(fresh_rows), bought=10, instrument_id="bank"
            ),
            origin=verify_member(hop, c0, c0_rows, bought=0, instrument_id="bank"),
        )

    verify = verified(banked())
    assert verify.fresh.fitness is not None and verify.recorded.fitness is not None
    assert verify.fresh.fitness.value == pytest.approx(0.5), "the fresh cells, never the pool"
    assert verify.recorded.fitness.value == pytest.approx(1.0)
    assert verify.fitness_increment == pytest.approx(-0.5)
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


@pytest.mark.usefixtures("optimizing_memory")
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


@pytest.mark.usefixtures("optimizing_memory")
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


@pytest.mark.usefixtures("optimizing_memory")
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
    assert (at_c0.candidates, standing_now.candidates) == (0, 5), (
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


@pytest.mark.usefixtures("optimizing_memory")
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
    assert at_c0.vs_parent is None
    assert held.vs_parent == held.vs_origin and held.climb is not None
    assert held.climb.vs_parent == held.climb.lift, "one pair read on two sheets at round 1"
    assert standing_now.vs_parent is not None
    assert standing_now.vs_parent == run[-1].overlap.vs_parent


class _Declared(NamedTuple):
    stores: Stores
    declared: Any


@pytest.fixture
def declared_head_to_heads(tmp_path: Path) -> _Declared:
    """Five declared head-to-heads, one over defaults its arms no longer run under."""
    h2h_stores = workspace(tmp_path / "h2h")
    declared = minted_campaign(
        CycleHop(campaign_id="arm", cycle_id="cycle_arm"),
        config=_h2h_config("potter", 0, _H2H_FORMULA, None),
        purpose=Compare(arm=Arm(head_to_head_id="any", arm_key="any", replicate=1)),
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
    return _Declared(h2h_stores, declared)


@pytest.mark.usefixtures("optimizing_memory")
def test_a_head_to_head_pairs_two_optimizers_only_on_one_bench_under_one_grader(
    declared_head_to_heads: _Declared,
) -> None:
    h2h_stores = declared_head_to_heads.stores
    potter_a, capo_b = Subject("potter_a"), Subject("capo_b")
    potter_course = Subject("potter_a", "cycle_potter_a_fork_r1")
    _evidence_campaign(h2h_stores, "potter_a", 1, selected=0.5, rebased=True)
    _evidence_campaign(h2h_stores, "capo_b", 2, selected=0.7, incurred=0.3, loop=0.15, wall=50)
    shared = subject_evidence(h2h_stores, [potter_a, capo_b]).head_to_head
    assert shared is not None and shared.rows[0].bench_set is not None
    h2h_cases: tuple[
        tuple[tuple[Subject, ...], dict[str, Any] | None, bool | None, list[str], int, int],
        ...,
    ] = (
        ((potter_a, capo_b), None, True, [], 1, 1),
        ((potter_course, capo_b), None, True, [], 1, 1),
        ((potter_course, potter_a, capo_b), None, True, [], 1, 1),
        (
            (potter_a, capo_b),
            {"cid": "gepa_c", "n": 3, "seed": 1},
            False,
            ["bench_rows"],
            1,
            1,
        ),
        # A replicate read apart from its arm's origin puts the whole arm outside the family.
        (
            (potter_a, capo_b),
            {"cid": "capo_d", "n": 4, "origin": 0.6},
            False,
            ["origin_reading"],
            0,
            2,
        ),
        # capo_b and capo_f run one treatment: one arm, its two replicates.
        ((potter_a, capo_b), {"cid": "capo_f", "n": 6, "at": 1}, True, [], 1, 2),
        (
            (potter_a, capo_b),
            {"cid": "potter_g", "n": 7, "proposer_model": "other/model"},
            False,
            ["optimizer_models"],
            1,
            1,
        ),
        (
            (potter_a, capo_b),
            {"cid": "capo_s", "n": 9, "steered": True},
            False,
            ["human_intervened"],
            0,
            2,
        ),
    )
    for subjects, third, verdict, differs_on, family, replicates in h2h_cases:
        case = f"{subjects} beside {third}"
        roster = [*subjects]
        if third is not None:
            roster.append(_evidence_campaign(h2h_stores, **{"selected": 0.7, **third}))
        evidence = subject_evidence(h2h_stores, roster)
        h2h = evidence.head_to_head
        assert evidence.scorer_id == auto_scorer_id(_H2H_FORMULA, None, judge_instrument=None)
        assert h2h is not None and (h2h.verdict, h2h.guard.differs_on) == (verdict, differs_on), (
            case
        )
        tested = [c for c in h2h.contrasts if c.lift is not None and c.lift.family is not None]
        assert len(tested) == family, f"{case}: the tests one correction was taken across"
        contrast = next(
            c for c in h2h.contrasts if {c.a.optimizer, c.b.optimizer} == {"potter", "capo"}
        )
        potter_arm, capo_arm = sorted(
            (contrast.a, contrast.b), key=lambda arm: arm.optimizer != "potter"
        )
        lift = contrast.lift
        assert lift is not None and len(contrast.differences) == 6, case
        assert lift.estimate.value == pytest.approx(0.2 if contrast.a is potter_arm else -0.2)
        assert lift.estimate.side != "spans", case
        assert potter_arm.replicates == ["potter_a"], case
        assert len(capo_arm.replicates) == replicates, f"{case}: one treatment is one arm"
        assert (lift.family is None) is (contrast.guard.state is GuardState.DIFFERS), (
            f"{case}: a contrast that differs is read, labelled, and outside the correction"
        )
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


@pytest.mark.usefixtures("optimizing_memory")
def test_the_arms_of_one_declared_head_to_head_are_controlled_only_on_their_record(
    declared_head_to_heads: _Declared,
) -> None:
    h2h_stores = declared_head_to_heads.stores
    optimizer_hour = {
        "potter": ("potter", 10),
        "capo": ("capo", 11),
        "gepa": ("gepa", 12),
        "levi": ("levi", 13),
        "waiting": ("capo", 14),
        "capped": ("capo", 15),
        "rewound": ("capo", 16),
        "forked": ("capo", 17),
    }
    for h2h_id, arms, verdict, guards in (
        ("real4", ("potter", "capo"), True, ["controlled"] * 2),
        # An arm a launch rewound in place stays admitted, and leaves the comparison as that event.
        ("real4", ("potter", "rewound"), True, ["controlled", "uncontrolled"]),
        # A fork spends its campaign's ceiling: the arm leaves though the act landed on the fork.
        ("real4", ("potter", "forked"), True, ["controlled", "uncontrolled"]),
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
                    purpose=Compare(arm=Arm(head_to_head_id=record, arm_key=key, replicate=1)),
                    graded=key != "waiting",
                )
            )
        if "capped" in arms:
            capped = h2h_stores.campaigns.load_campaign(f"capo_capped_{h2h_id}")
            assert capped is not None
            h2h_stores.campaigns.append(
                capped.root_hop, CampaignCeilingRecord(budget=Budget(usd=1.0))
            )
        if "rewound" in arms:
            rewound = h2h_stores.campaigns.load_campaign(f"capo_rewound_{h2h_id}")
            assert rewound is not None
            h2h_stores.campaigns.leave_comparison(
                h2h_stores.campaigns.line_holder(rewound.root_hop), ComparisonExit.REWIND
            )
        if "forked" in arms:
            forked = h2h_stores.campaigns.load_campaign(f"capo_forked_{h2h_id}")
            assert forked is not None
            line = h2h_stores.campaigns.line_holder(forked.root_hop)
            parent = h2h_stores.campaigns.cycle_dir(line)
            fork = CycleHop(campaign_id=line.campaign_id, cycle_id=f"{line.cycle_id}_fork_r1")
            mint_fork(
                h2h_stores.campaigns,
                fork,
                parent,
                ForkSpec(
                    trigger=ForkTrigger.OPERATOR_REWIND,
                    reason="",
                    issued_by=SYSTEM_PRINCIPAL,
                    from_candidate=RoundArm(round=0, arm=ArmKey("origin")),
                ),
                cut=CycleEventLog.open(CycleDir(parent)).next_offset,
                cut_round=1,
            )
            h2h_stores.campaigns.leave_comparison(fork, ComparisonExit.FORK)
        h2h = subject_evidence(h2h_stores, arm_subjects).head_to_head
        assert h2h is not None and h2h.guard.head_to_head_id == h2h_id, case
        assert [r.guard.state.value for r in h2h.rows] == guards, (
            f"{case}: {[r.guard.differs_on for r in h2h.rows]}"
        )
        assert h2h.verdict is verdict, case
        tested_by_state = Counter(c.guard.state for c in h2h.contrasts if c.lift is not None)
        for contrast in h2h.contrasts:
            if contrast.lift is not None and contrast.lift.family is not None:
                assert contrast.lift.family.n_tests == tested_by_state[contrast.guard.state], (
                    f"{case}: a controlled and an uncontrolled contrast were corrected as one family"
                )
        assert (GuardDifference.DEFAULTS in h2h.guard.differs_on) is (h2h_id == "real8"), (
            f"{case}: arms run under defaults their record was not declared over, unsaid"
        )
        if verdict is None:
            assert h2h.verdict_absent is ReadingState.PENDING, f"{case}: a waiting arm is pending"


@pytest.mark.usefixtures("optimizing_memory")
def test_a_matrix_reads_each_arm_as_a_treatment_over_its_replicates_clustered_by_bench_row(
    declared_head_to_heads: _Declared,
) -> None:
    # Each bench row's arm means are differenced; Holm gates, and BH reads false discovery across
    # the arm × arm family.
    h2h_stores, declared = declared_head_to_heads
    h2h_stores.campaigns.declare_head_to_head(
        HeadToHeadRecord(
            head_to_head_id="seeds",
            created_at="",
            origin="origin",
            shared=declared.shared_part(),
            ruler_version=0,
        )
    )
    pinned = ArmFit(theta=0.31, theta_se=0.2, theta_caveat=ThetaCaveat.FLOOR_PINNED)
    replicated: dict[str, tuple[float, float, DisplayMetric, ArmFit | None]] = {
        "potter_s1": (0.1, 0.0, "ability", ArmFit(theta=0.42, theta_se=0.1)),
        "potter_s2": (0.2, 0.0, "ability", pinned),
        "capo_s1": (0.1, 0.4, "fitness", None),
        "capo_s2": (0.0, 0.4, "fitness", None),
        "gepa_s1": (0.2, 0.2, "composite", None),
    }
    replicate_arms = [
        _evidence_campaign(
            h2h_stores,
            cid,
            17 + i,
            selected=selected,
            tilt=tilt,
            elects_on=elects_on,
            fit=fit,
            purpose=Compare(arm=Arm(head_to_head_id="seeds", arm_key=cid, replicate=1)),
        )
        for i, (cid, (selected, tilt, elects_on, fit)) in enumerate(replicated.items())
    ]
    matrix = subject_evidence(h2h_stores, replicate_arms).head_to_head
    assert matrix is not None and matrix.verdict is True
    held_out = partition_bank(_H2H_BANK, DatasetSplit(bench=6), seed=0).bench

    def seeded(cid: str, row: Sample) -> float:
        selected, tilt, _, _ = replicated[cid]
        return selected + 0.1 * (row.id % 2) + tilt * (row.id % 3)

    by_arm = {
        "potter": ["potter_s1", "potter_s2"],
        "capo": ["capo_s1", "capo_s2"],
        "gepa": ["gepa_s1"],
    }
    assert [(c.a.optimizer, c.b.optimizer) for c in matrix.contrasts] == [
        ("potter", "capo"),
        ("potter", "gepa"),
        ("capo", "gepa"),
    ], "each treatment is one arm, the older arm `a`"
    for contrast in matrix.contrasts:
        older, newer = by_arm[contrast.a.optimizer], by_arm[contrast.b.optimizer]
        assert (contrast.a.replicates, contrast.b.replicates) == (older, newer)
        known = {
            row.key: fmean(seeded(cid, row) for cid in newer)
            - fmean(seeded(cid, row) for cid in older)
            for row in held_out
        }
        assert contrast.differences == pytest.approx(known), "each row: b's mean less a's"
        lift = contrast.lift
        assert lift is not None and lift.family is not None
        assert lift.estimate.value == pytest.approx(fmean(known.values()))
        pairs = [seeded(y, row) - seeded(x, row) for row in held_out for x in older for y in newer]
        clustered = stdev(known.values()) / sqrt(len(known))
        naive = stdev(pairs) / sqrt(len(pairs))
        assert (contrast.se_clustered, contrast.se_naive) == pytest.approx((clustered, naive)), (
            "the row is the cluster: its replicate pairings are never independent rows"
        )
        assert contrast.design_effect == pytest.approx((clustered / naive) ** 2)
        assert (lift.family.id, lift.family.n_tests) == ("seeds", 3)
        assert contrast.q_value is not None and contrast.q_value <= lift.family.p_adjusted, (
            "BH never gates harder than Holm"
        )
    ranked = sorted(
        (c for c in matrix.contrasts if c.lift is not None and c.lift.family is not None),
        key=lambda c: c.lift.estimate.p_value if c.lift is not None else 1.0,
    )
    p = [c.lift.estimate.p_value for c in ranked if c.lift is not None]
    lowest = ranked[0].lift
    assert lowest is not None and lowest.family is not None
    assert lowest.family.p_adjusted == pytest.approx(min(1.0, 3 * p[0]))
    assert ranked[-1].q_value == pytest.approx(p[-1])
    elections = {r.campaign_id: r.election for r in matrix.rows}
    assert {cid: e and (e.elects_on, e.reads_on) for cid, e in elections.items()} == {
        "potter_s1": ("ability", "ability"),
        "potter_s2": ("ability", "fitness"),
        "capo_s1": ("fitness", "fitness"),
        "capo_s2": ("fitness", "fitness"),
        "gepa_s1": ("composite", "composite"),
    }, "an ability pick is quoted on θ unless a caveat is served on its θ"
    quoted = elections["potter_s1"]
    assert quoted is not None and quoted.ability is not None and quoted.ability.theta == 0.42
    assert any(note.startswith("potter_s1, gepa_s1 elected") for note in matrix.notes)


# 8. A bare-answer backend — the n8n eval harness


def test_an_n8n_cell_delivers_the_candidate_and_grades_only_an_answer_it_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    monkeypatch.setattr(settings, "N8N_HARNESS_URL", FakeHarness.URL)
    monkeypatch.setattr(settings, "N8N_HARNESS_TOKEN", SecretStr("harness-secret"))
    stores = workspace(tmp_path)
    harness = n8n.CONNECTOR
    assert harness.pipeline_declaration is not None
    graph = harness.pipeline_declaration(stores, None)
    session = loop_session(
        stores,
        parse_pipeline_response(graph),
        [],
        client=BackendClient(harness, workload=PROBE_WORKLOAD, jobs_dir=stores.jobs_dir),
    )
    gold = '{"requires_action": ["true"]}'
    row = Sample(id=0, query='{"subject": "Invoice 1023"}', ground_truth=gold)
    contract = {
        "output_schema": {"type": "object", "properties": {"requires_action": {"type": "string"}}},
        "answer_fields": ["requires_action"],
    }
    candidate = {"prompt": "## requires_action\nTrue when the sender asks for a reply.", **contract}

    def cell(params: dict[str, Any], book_usd: float | None = None) -> MeasuredCell:
        async def measured() -> MeasuredCell:
            with (
                scoped(ledger=CycleEventLog(tmp_path / "harness.jsonl")),
                scoped(round=1),
                scoped(book=spend_book(book_usd, usd_reserve=None)),
            ):
                return await measure_sample(row, session, PipelineParams.model_validate(params))

        return asyncio.run(measured())

    read = cell({"harness": {**candidate, "model": "another/model"}})
    headers, sent = offline.harness.asked[-1]
    rode = (headers.get("x-eval-secret"), headers.get("authorization"))
    assert rode == ("harness-secret", None), (
        "the secret rode another header than the operator named"
    )
    assert sent == {
        "case_id": row.key,
        "input": json.loads(row.query),
        "expected": json.loads(gold),
        "prompt": candidate["prompt"],
        "params": contract,
    }, "the harness was sent another candidate, or a model a dataset aimed it at"
    assert (read.predicted, read.error_category) == ("true", None), (
        "the harness's answer was not what the cell read"
    )
    assert read.cost_s is not None, "a harness cell's round trip went unclocked"

    offline.harness.says = lambda body: {"case_id": body["case_id"], "answer": {"a": "x"}}
    assert json.loads(cell({"harness": candidate}).predicted) == {"a": "x"}, (
        "an object answer was graded as anything but its fields"
    )

    cell({"harness": {}})
    assert offline.harness.asked[-1][1]["prompt"] == "", (
        "an unprompted cell asked the harness for anything but its production text"
    )

    replies: tuple[Callable[[dict[str, Any]], dict[str, Any] | httpx.Response], ...] = (
        lambda body: {"case_id": body["case_id"], "answer": "  ", "raw": {}},
        lambda _body: httpx.Response(403, json={"message": "Authorization data is wrong!"}),
    )
    for reply in replies:
        offline.harness.says = reply
        hole = cell({"harness": candidate})
        assert hole.errored and hole.predicted != NO_RESULT, (
            "a harness that answered nothing was graded as a wrong answer"
        )

    asked = len(offline.harness.asked)
    with pytest.raises(SendRefusedError):
        cell({"harness": candidate}, book_usd=0.5)
    assert len(offline.harness.asked) == asked, (
        "a cell no rate prices was sent under a dollar ceiling it cannot be held to"
    )
