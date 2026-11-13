"""Money — what is billed, and what a ceiling holds.

Owns `infrastructure/llm/` (pricing, the spend book, wire cost), `application/jobs/quota.py`,
`account_spend.py`, the runner's budget gate and judge billing. Spend that reads $0, a cell billed
twice, a ceiling nothing enforces, a delete that hands the money back.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import inspect
import json
import random
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Coroutine, Iterator, Sequence
from pathlib import Path
from typing import Any, NamedTuple, cast, get_args
from unittest import mock

import httpx
import pytest
from factories import (
    PROMPT_INFO,
    SANDBOX_CAMPAIGN,
    archive_row,
    arm_state,
    arm_walk,
    backend_client,
    billed_call,
    cycle_result,
    filed,
    filed_under,
    inner_cells,
    inner_sandbox,
    jumping_clock,
    loop_session,
    minted_campaign,
    minted_dataset,
    on_jumping_clock,
    pipeline_schema,
    run_wiring,
    samples,
    score_summary,
    scorer_of,
    spend_book,
    stored_campaign,
    workspace,
)
from offline_world import NOT_A_KEY, PROVIDER_KEYS, Hang, Router, Said, completion
from pydantic import BaseModel, Field, SecretStr, ValidationError

from promptpotter.application import free_tier
from promptpotter.application.account import account_of, local_stores
from promptpotter.application.bench import llm_call as call_mod
from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.bench.task_context import checkin_template
from promptpotter.application.commands.dispatcher import (
    Applier,
    CommandCall,
    CommandDispatcher,
    DispatchedToCycle,
)
from promptpotter.application.commands.limits_and_queue import change_run_limits
from promptpotter.application.datasets.authored import ConfigLayers
from promptpotter.application.datasets.draft_campaign import new_draft
from promptpotter.application.datasets.loaders import archive_entry
from promptpotter.application.datasets.startability import offered_dataset
from promptpotter.application.head_to_head_price import (
    optimizer_round_price,
    priced_head_to_head,
)
from promptpotter.application.initialization.session import Session
from promptpotter.application.jobs import interlock, mint, quota
from promptpotter.application.jobs import registry as registry_module
from promptpotter.application.jobs.account_activity import account_activity
from promptpotter.application.jobs.coupon import (
    AllowanceState,
    BackendCells,
    ProviderSend,
    keyed_client,
    paying_key,
    provider_send,
)
from promptpotter.application.jobs.launcher import run_job as run_job_mod
from promptpotter.application.jobs.launcher.admission import (
    RunStart,
    admit_and_share,
    open_spend_book,
    paid_call,
    paid_pass,
    slotless_share,
)
from promptpotter.application.jobs.launcher.run_job import HeldRun, JobSpec
from promptpotter.application.jobs.machine_status import machine_status
from promptpotter.application.jobs.quota import (
    QuotaExceededError,
    account_wallet,
    admit_launch,
    change_budget,
    coupon_status,
    cycle_share,
    hold_moved,
    hold_run,
)
from promptpotter.application.jobs.reaper import reclaim_orphan_sandboxes
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.maintenance.archive_maintenance import purge_cold_store
from promptpotter.application.maintenance.reset import apply_reset, plan_reset
from promptpotter.application.optimizer_manifest import checkin_manifest, select_optimizer
from promptpotter.application.optimizers.nodes import Race
from promptpotter.application.pipeline_resolve import cycle_config, laid_config
from promptpotter.application.run_phase_control import RunControl, budget_stop
from promptpotter.application.runner import round as round_module
from promptpotter.application.runner.bench import cycle_bench, line_run
from promptpotter.application.runner.inner import spawn
from promptpotter.application.runner.inner.tasks import InnerSpawnContext
from promptpotter.application.runner.loop import round_bounds
from promptpotter.application.runner.round import CatchUpPool
from promptpotter.application.runner.termination import run_stop_reason
from promptpotter.application.scoring import query_loop
from promptpotter.application.scoring.cell_envelope import CellEnvelope
from promptpotter.application.scoring.formula.compiler import cell_channels_of
from promptpotter.application.scoring.judges import call as judge_call
from promptpotter.application.scoring.judges.registry import build_evaluators
from promptpotter.application.scoring.passes import grade_line_bench, measure_noise_floor
from promptpotter.application.scoring.probe_reasoning import probe_reasoning
from promptpotter.application.scoring.sample_measurement import (
    cell_billing,
    emit_replayed_step_tokens,
    measure_sample,
    measured_cell_price,
)
from promptpotter.application.scoring.search_point_scorer import (
    open_walk,
    replayable_on,
    score_search_point,
)
from promptpotter.application.scoring.seed_screen import screen_inner_seeds
from promptpotter.application.served_dashboard import ServedDashboard, served_dashboard
from promptpotter.config.settings import settings
from promptpotter.connectors.protocol import InProcessWorkload, cell_envelope_of
from promptpotter.connectors.termnorm import TermNormSession
from promptpotter.domain.arms import CutKind
from promptpotter.domain.bench import BenchRefusal
from promptpotter.domain.campaign import Optimize, RunDeclaration
from promptpotter.domain.campaign_config import (
    CampaignConfig,
    CycleConfig,
    OptimizationConfig,
    merge_config_layers,
)
from promptpotter.domain.cells import PinnedCell
from promptpotter.domain.checkin_draft import DatasetBasis
from promptpotter.domain.command_kinds import (
    ArmRequest,
    ChangeRunLimitsPayload,
    DiscardDraftPayload,
    EditDraftCampaignPayload,
    HeadToHeadRequest,
    MintHeadToHeadPayload,
    StartCheckinPayload,
)
from promptpotter.domain.cycle_paths import CycleDir, CycleHop, WorkspaceDir
from promptpotter.domain.draft_edit import EditDraftPatch, OptimizationOverrides
from promptpotter.domain.head_to_head_plan import HeadToHeadPlan
from promptpotter.domain.job import UNRESOLVED_HOP, CampaignLaunch, EmbeddedRun, PaidCall
from promptpotter.domain.judges import JudgeSpec, JudgeStage
from promptpotter.domain.l4.proxies import (
    OuterSampleProxies,
    inner_cell_facts,
    inner_cell_report,
)
from promptpotter.domain.measurement_provenance import REUSABLE_MIN_GRADE
from promptpotter.domain.paired_reading import ArmKey, RoundArm
from promptpotter.domain.phases import (
    ERROR_CATEGORY_INFO,
    CampaignPhase,
    PauseCause,
    RunPhase,
    StopLoop,
    StopReason,
    WalkEnd,
)
from promptpotter.domain.pipeline_params import NodeCall, PipelineParams
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import (
    PipelineNode,
    PipelineSchema,
    WebSpendBound,
)
from promptpotter.domain.results import CycleResult
from promptpotter.domain.ruler import RulerReach
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.run_records import (
    ArmRecord,
    Bill,
    CallPrice,
    ConfigOverrides,
    CycleSeed,
    CycleSeedRecord,
    CycleShareRecord,
    ForkRemainder,
    ForkSpec,
    ForkTrigger,
    Hold,
    Lapse,
    LaunchAnswer,
    PhaseRecord,
    Release,
    RoundEnteredRecord,
    RoundWarningRecord,
    RunPhaseRecord,
    SampleScoredRecord,
    SampleStartedRecord,
    SpendTombstoneRecord,
    StandingBudgetRecord,
    TokenUsageRecord,
)
from promptpotter.domain.sample import Bank, Sample
from promptpotter.domain.scoring import CellSheet, GradedCell, MeasuredCell, PipelineData, Scorer
from promptpotter.domain.search_point import JobSearchPoint, NodeConfig
from promptpotter.domain.spend import (
    CYCLE_AXES,
    MONEY_AXES,
    Budget,
    CeilingMeter,
    HeldBudget,
    KeySource,
    SpendRollup,
    StepUsage,
    TokenAccount,
    TokenUsageKind,
)
from promptpotter.domain.validators import StopSignal
from promptpotter.domain.verification import RescoreCount
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.identity.default_claim import is_host
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm import base, pricing, telemetry
from promptpotter.infrastructure.llm.openai_compat import ChatReply, billed_cost
from promptpotter.infrastructure.llm.pricing import (
    PriceTier,
    Rate,
    RateCeiling,
)
from promptpotter.infrastructure.llm.request import ChatMessage, ChatRequest
from promptpotter.infrastructure.llm.send_pacing import (
    SEND_ATTEMPTS,
    SendBudget,
)
from promptpotter.infrastructure.llm.spend_book import (
    Admission,
    Billed,
    CallLabel,
    SendBound,
    SpendBook,
    admitted,
    bound_account,
    replied,
    reserved,
)
from promptpotter.infrastructure.producer_lock import hold_cycle, release_cycle
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.store.account_spend import (
    account_bill,
    bank_spend,
    billed_spend,
    campaign_spend,
    ledger_cost,
    unreported_under,
)
from promptpotter.infrastructure.store.campaign_store import store as campaign_store_module
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_last,
    scan_ledger_priced_keys,
    scan_ledger_spend,
    scan_ledger_spend_by_round,
    scan_standing_rounds,
)
from promptpotter.infrastructure.store.dataset_access import DEFINITION_FILE, resolve_dataset
from promptpotter.infrastructure.store.io import read_lines, rmtree_robust, write_jsonl, write_yaml
from promptpotter.infrastructure.store.layout import (
    CampaignLayout,
    CycleLayout,
    dataset_id_prefix,
    draft_dirs,
)
from promptpotter.infrastructure.store.llm_reuse_cache import LLMReuseCache
from promptpotter.infrastructure.store.measurement_archive import ReplayFeed
from promptpotter.infrastructure.store.read_model import (
    LedgerIndex,
    LedgerSpan,
    money_on,
)
from promptpotter.infrastructure.store.session_pointer import cleanup_stub_fork_if_empty
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.infrastructure.store.user_store import User
from promptpotter.infrastructure.store.wallet_store import ChargeBook, CouponGrant
from promptpotter.presentation.api.routers import commands as commands_api
from promptpotter.presentation.cli import campaign_runner
from promptpotter.shared.errors import (
    BudgetUnenforceableError,
    CellHaltedError,
    CellInfrastructureError,
    CellSendRefusedError,
    CellUnscoreableError,
    ConflictError,
    CycleBusyError,
    ErrorCategory,
    HostAllowanceExhaustedError,
    LaunchRefusalReason,
    LedgerUnreadableError,
    NoApiKeyError,
    NotFoundError,
    PayloadInvalidError,
    SendRefusedError,
    ServiceUnavailableError,
)
from promptpotter.shared.identity import (
    SYSTEM_PRINCIPAL,
    IdentityContext,
    Issuer,
    TenantId,
    UserId,
    default_identity,
)
from promptpotter.shared.measurement_context import (
    MeasuredPass,
    MeasurementRole,
    measured_pass,
)
from promptpotter.shared.run_scope import scoped


@pytest.fixture
def run_bound(tmp_path: Path) -> Iterator[None]:
    with scoped(ledger=CycleEventLog(tmp_path / "bound.jsonl")), telemetry.at_round(1):
        yield


def _admitted(stores: Stores, registry: JobRegistry, job_id: str, reserve: Budget) -> None:
    """Admitted as `admit_and_share` admits it: its hold on the account, then the stamp."""
    hold_run(stores, job_id, reserve, parent=None)
    registry.mark_admitted(job_id)


def _reserve_of(stores: Stores, job_id: str) -> Budget:
    return money_on(stores.charges.path).holds[job_id].bound


def _held(
    stores: Stores,
    registry: JobRegistry,
    admitted: Budget,
    *,
    reserve: Budget | None = None,
    standing: Budget | None = None,
    step_rounds: int | None = None,
) -> HeldBudget:
    """Admitted alone, on a job of its own: the cycle's share is the whole of its money axes."""
    job = registry.create(
        user_id="solo",
        principal_id="solo",
        workspace=WorkspaceDir(registry.jobs_dir),
        hop=UNRESOLVED_HOP,
        holder=EmbeddedRun("ds1"),
    )
    _admitted(stores, registry, job.job_id, Budget() if reserve is None else reserve)
    return HeldBudget(
        admitted=admitted,
        standing=Budget() if standing is None else standing,
        job_id=job.job_id,
        share=admitted.only(MONEY_AXES),
        step_rounds=step_rounds,
    )


def _armed(stores: Stores, hop: CycleHop | None, held: HeldBudget) -> SpendBook:
    """A run's book, live on its job and its cycle's records where it has one; its share lands
    there first, as admission lands it, unless one already stands."""
    if hop is not None and stores.campaigns.last(hop, CycleShareRecord) is None:
        hold_moved(stores, hop, held.share)
    return open_spend_book(
        held,
        stores=stores,
        hop=hop,
        meters="bill",
        opened=time.monotonic(),
        live=hop is not None,
    )


def _budgeted(budget: Budget) -> CampaignConfig:
    config = CampaignConfig.model_validate({"optimization": {"degradation_threshold": 0.05}})
    optimization = config.optimization.model_copy(update={"budget": budget})
    return config.model_copy(update={"optimization": optimization})


def _change_run_limits(
    dispatcher: CommandDispatcher, hop: CycleHop, budget: Budget, *, overshoot: bool | None = None
) -> Budget:
    """The ceiling the change armed: the campaign's money and the cycle's own axes."""
    payload = ChangeRunLimitsPayload(
        campaign_id=hop.campaign_id, cycle_id=hop.cycle_id, budget=budget, overshoot=overshoot
    )
    dispatched = DispatchedToCycle(dispatcher, payload, "limits", minted_campaign(hop), hop)

    async def apply() -> Applier[object]:
        applier = await change_run_limits(dispatched)
        await applier.run()
        return applier

    asyncio.run(apply())
    campaigns = dispatcher.stores.campaigns
    return campaigns.read_campaign_ceiling(hop).over(campaigns.read_standing_budget(hop).budget)


# 1. What a call costs


async def _grade_twice(
    tmp_path: Path, offline: Router, *, reply: str
) -> tuple[int, list[TokenUsageRecord], Any]:
    # A provider reporting no `cached_tokens` cannot catch the metering dropping the discount.
    usage = {
        "prompt_tokens": 11,
        "completion_tokens": 1,
        "total_tokens": 12,
        "prompt_tokens_details": {"cached_tokens": 8},
    }
    offline.llm.says = lambda body: completion(reply, model=body["model"], usage=usage)
    offline.llm.sent.clear()
    cache = LLMReuseCache(tmp_path, "judge_reuse")
    judge = JudgeStage(model="grader-1", provider="openai")
    (ev,) = build_evaluators({"answer": JudgeSpec(name="sealqa", stages=[judge])}, beside=())
    graded = CycleEventLog(tmp_path / "graded.jsonl")
    with _billing_on(graded, spend_book()), scoped(judge_cache=cache):
        for _ in range(2):
            row = MeasuredCell(sample_key="k0", query="who?", predicted="Ada", ground_truth="Ada")
            computed = ev.compute(result=row, schema=None)
            assert inspect.isawaitable(computed)
            last = await computed
    return len(offline.llm.sent), _token_bills(graded), last


class _Reply(BaseModel):
    answer: str = Field(description="The answer.")


_MESSAGES: list[ChatMessage] = [{"role": "user", "content": "q"}]
_MAX_PRICE = {"max_price": {"prompt": 500000.0, "completion": 250000.0}}
_EVERY_FIELD: dict[str, Any] = {
    "temperature": 0.3,
    "max_tokens": 64,
    "response_model": _Reply,
    "reasoning_effort": "low",
    "top_p": 0.9,
    "seed": 7,
    "route_order": ["Alibaba"],
}


@pytest.mark.usefixtures("run_bound", "offline")
def test_a_rate_belongs_to_its_provider_model_pair_and_a_cache_read_reprices_inside_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def row(input_usd: float, output_usd: float, lister: str, **tiers: float) -> dict[str, Any]:
        return {
            "input_cost_per_token": input_usd,
            "output_cost_per_token": output_usd,
            "litellm_provider": lister,
            **tiers,
        }

    listed = {
        "models": {
            # DeepSeek's first-party key, character-for-character OpenRouter's model id.
            "deepseek/deepseek-v4-flash": row(0.00000014, 0.00000028, "deepseek"),
            "openrouter/openai/gpt-oss-20b": row(0.00000004, 0.00000015, "openrouter"),
            "groq/openai/gpt-oss-120b": row(0.00000015, 0.0000006, "groq"),
            "gpt-4o": row(
                0.0000025,
                0.00001,
                "openai",
                cache_creation_input_token_cost=0.000003125,
                cache_read_input_token_cost=0.00000025,
            ),
            # A bare id whose own colon is part of the model, not a route selector.
            "anthropic.claude-haiku-4-5-v1:0": row(0.000001, 0.000005, "bedrock"),
            "openrouter/openrouter/auto": row(0.000002, 0.000004, "openrouter"),
            "orphan-model": {"input_cost_per_token": 0.000001},
            "openai/half-listed": {"input_cost_per_token": 0.000001},
        }
    }
    (tmp_path / "rates.json").write_text(json.dumps(listed), encoding="utf-8")
    with monkeypatch.context() as patched:
        patched.setattr(pricing, "BUNDLED_PATH", tmp_path / "rates.json")
        lookup, usd = pricing.lookup_rate, pricing.compute_usd
        for model, provider, rate in (
            ("deepseek-v4-flash", "deepseek", Rate(0.00000014, 0.00000028)),
            ("deepseek/deepseek-v4-flash", "openrouter", None),
            ("deepseek-v4-flash", None, None),
            # A routing suffix selects another upstream host with its own rate.
            ("openai/gpt-oss-20b:nitro", "openrouter", None),
            ("openai/gpt-oss-20b", "openrouter", Rate(0.00000004, 0.00000015)),
            ("openai/gpt-oss-120b", "groq", Rate(0.00000015, 0.0000006)),
            ("gpt-4o", "openai", Rate(0.0000025, 0.00001, 0.000003125, 0.00000025)),
            # A row listing one side is unpriced, never zero on the other.
            ("half-listed", "openai", None),
        ):
            assert lookup(model, provider) == rate, (
                f"{provider}/{model}: a rate belongs to the provider-model pair, not the model alone"
            )
        for key, expected in (
            ("anthropic.claude-haiku-4-5-v1:0", 10 * 0.000001 + 10 * 0.000005),
            ("openrouter/openrouter/auto", 10 * 0.000002 + 10 * 0.000004),
            ("deepseek/deepseek-v4-flash", 10 * 0.00000014 + 10 * 0.00000028),
            ("gpt-4o", 10 * 0.0000025 + 10 * 0.00001),
        ):
            routed = pricing.priced_pair(key, None, names_provider=True)
            assert usd(routed[0], 10, 10, provider=routed[1]) == pytest.approx(expected), (
                f"{key}: a key prices on the route it names inline"
            )
        assert pricing.priced_pair("orphan-model", None, names_provider=True) == (
            "orphan-model",
            None,
        ), "a row naming no lister invented a provider"
        assert usd("deepseek/deepseek-v4-flash", 10, 10, provider="openrouter") is None, (
            "a call no row prices read as priced"
        )
        cold = usd("gpt-4o", 1000, 0, provider="openai")
        hit = usd("gpt-4o", 1000, 0, provider="openai", cache_read_tokens=800)
        assert (cold, hit) == pytest.approx(
            (1000 * 0.0000025, 200 * 0.0000025 + 800 * 0.00000025)
        ), "a cache read is a SUBSET of the input count: re-priced out of it, never added on top"
        assert usd(
            "openai/gpt-oss-20b", 1000, 0, provider="openrouter", cache_read_tokens=800
        ) == pytest.approx(usd("openai/gpt-oss-20b", 1000, 0, provider="openrouter")), (
            "no cache tier on the row: the read bills at the INPUT price, never a rate nobody sourced"
        )


@pytest.mark.usefixtures("run_bound", "offline")
def test_a_wire_cost_reaches_the_reply_and_a_bill_sums_the_parts_that_named_one() -> None:
    # OpenRouter's real shape: `cost` rides as an EXTRA on the usage object.
    priced = {
        "prompt_tokens": 263,
        "completion_tokens": 152,
        "total_tokens": 415,
        "cost": 7.938e-05,
        "cost_details": {"upstream_inference_cost": 0},
        "is_byok": False,
    }

    def wired(usage: dict[str, Any] | None) -> ChatReply:
        return ChatReply.model_validate(completion("{}", model="m", usage=usage))

    assert wired(priced).cost == 7.938e-05, (
        "wire cost never reached the response, so nothing prices the optimizer"
    )
    for usage in ({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}, None):
        assert wired(usage).cost is None, (
            f"{usage}: None, not 0.0 — 0.0 is a measurement, and satisfies the cap that should "
            "escalate to the table"
        )
    for reported, billed in (
        ((1e-05, 2e-05), 3e-05),
        ((None, 2e-05), 2e-05),
        ((1e-05, None), 1e-05),
        ((None, None), None),
    ):
        assert billed_cost(*reported) == (None if billed is None else pytest.approx(billed)), (
            f"{reported}: a bill is the sum of the parts that named one, absent only when none did"
        )


@pytest.mark.usefixtures("run_bound")
async def test_a_send_carries_only_what_its_client_sends_under_the_gateways_price_cap(
    monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    async def ceiling(*_: Any, **__: Any) -> RateCeiling:
        return RateCeiling(tiers=(PriceTier(0, 0.5, 0.25),))

    async def send(host: str, asked: dict[str, Any]) -> None:
        with scoped(book=spend_book(), send_budget=SendBudget(None, attempts=1)):
            await keyed_client(host).chat(
                ChatRequest(_MESSAGES, "m", **asked), label=CallLabel("l1", "optimizer")
            )

    offline.llm.says = lambda _body: '{"answer": "a"}'
    with monkeypatch.context() as patched:
        patched.setattr(base, "rate_ceiling", ceiling)
        for host, asked, sent in (
            ("groq", {"route_order": ["Alibaba"]}, False),
            ("openrouter", {}, True),
            ("openrouter", _EVERY_FIELD, True),
        ):
            offline.llm.sent.clear()
            sending = asyncio.create_task(send(host, asked))
            if sent:
                await sending
            else:
                with pytest.raises(ValueError):
                    await sending
            assert [body["provider"]["max_price"] for body in offline.llm.sent] == (
                [_MAX_PRICE["max_price"]] if sent else []
            ), (
                f"{host} {sorted(asked)}: a field its client cannot send reached the provider, "
                "or the gateway sent no price ceiling"
            )


@pytest.mark.usefixtures("run_bound", "offline")
async def test_an_ungraded_cell_bills_what_it_spent_and_a_replay_states_its_rows_price(
    tmp_path: Path,
) -> None:
    async def ran_ungraded(*_args: Any) -> dict[str, Any]:
        spent = {"agent": {"input": 1200, "output": 300, "estimated": False, "cost_usd": 0.0076}}
        raise CellUnscoreableError("verifier timed out", spent=spent)

    cells = CycleEventLog(tmp_path / "cells.jsonl")
    unbounded = spend_book()
    session = loop_session(
        workspace(tmp_path / "ungraded"),
        pipeline_schema([]),
        [],
        client=backend_client(
            tmp_path / "jobs", execution="in_process", in_process_run=ran_ungraded
        ),
        scorer=scorer_of("max(0.0, min(1.0, env_reward))", verifier_graded=True),
    )
    with scoped(ledger=cells):
        with scoped(book=unbounded):
            row_ = await measure_sample(
                Sample(id=0, query="10452", ground_truth=None), session, PipelineParams()
            )
        banked = {"agent": StepUsage(input=1200, output=300, rate_priced_usd=0.25)}
        emit_replayed_step_tokens(banked, {})
    *ran, replayed = [r for _, r in cells.iter() if isinstance(r, TokenUsageRecord)]
    assert row_.error_category == ErrorCategory.UNSCOREABLE, "an ungraded cell read as graded"
    assert [(r.price.cost_usd, r.input_tokens, r.bill is None) for r in ran] == [
        (0.0076, 1200, False)
    ], "a cell that ran without a grade billed nothing of what it spent"
    assert (replayed.price.cost_usd, replayed.price.rate_priced_usd, replayed.bill) == (
        None,
        0.25,
        None,
    ), (
        "a replay states the price its row recorded: re-priced, incurred cost moves with each refresh"
    )


@pytest.mark.usefixtures("run_bound")
async def test_a_replayed_grading_is_metered_uncharged_and_an_empty_reply_never_cached(
    tmp_path: Path, offline: Router
) -> None:
    calls, metered, score = await _grade_twice(tmp_path / "judge", offline, reply="A")
    blank_calls, _, blank = await _grade_twice(tmp_path / "blank", offline, reply="   ")
    assert score.score == 1.0, "the replayed reply must grade identically, not merely cheaply"
    assert calls == 1, f"an identical comparison re-billed the provider: {calls}x"
    assert [m.bill is None for m in metered] == [False, True], "a served grading went unmetered"
    assert {m.price.kind for m in metered} == {"judge"}, "grading spend landed outside its bucket"
    # No charge: OUR cache served the reply. `cache_read`: the PROVIDER discounted a sent call.
    assert (metered[0].cache_read_tokens, metered[0].input_tokens) == (8, 11), (
        "cache_read is a SUBSET of input, never a deduction from it"
    )
    assert blank.score is None, "an unreadable grading is an absent verdict, never a zero"
    assert blank_calls == 2, "an empty reply was cached and replayed as if it were a verdict"


# 2. What a run is billed


class _OrderedFakeBackend:
    """Finishes LATER samples FIRST: under uniform latency the loop need order nothing to pass."""

    def __init__(self, n: int, *, slowest_last: bool = False) -> None:
        self.slowest_last = slowest_last
        self.n = n
        self.calls: list[int] = []
        self._inflight = 0
        # In flight as each call began. Read its PEAK only: the tail measures the scheduler.
        self.entries: list[int] = []

    async def measure(self, sample: Sample) -> Any:
        self.calls.append(sample.id)
        return await self.hold(sample)

    async def hold(self, sample: Sample, pace: float = 0.01) -> Any:
        self._inflight += 1
        self.entries.append(self._inflight)
        rank = sample.id if self.slowest_last else (self.n - sample.id + 1)
        try:
            await asyncio.sleep(rank * pace)
        finally:
            self._inflight -= 1
        return MeasuredCell(
            sample_key=sample.key,
            query=sample.query,
            ground_truth=sample.ground_truth or "",
            predicted=sample.ground_truth or "",
            pipeline=PipelineData(total_time=0.5),
        )


class _CutAfter:
    """Stands in for the PoBB gate on the same seam, firing where the test picks."""

    name = "cut_after"

    def __init__(self, n: int) -> None:
        self.n = n

    def earliest_stop(
        self, results: Sequence[GradedCell], upcoming: Sequence[tuple[Sample, GradedCell | None]]
    ) -> int | None:
        m = max(self.n, len(results) + 1)
        return m if m <= len(results) + len(upcoming) else None

    def check(self, results: Sequence[GradedCell]) -> StopSignal | None:
        if len(results) < self.n:
            return None
        return StopSignal(self.name, "eliminated", {"queries_scored": len(results)})


_HIT = Scorer(
    id="hit",
    per_cell=None,
    matcher=None,
    span=None,
    ranking=None,
    fields=None,
    fitness_unit="score",
    terms=frozenset(),
    fitness=lambda _row, _grade: 1.0,
    objective=None,
)


def _searching(usd_per_query: float) -> Any:
    bound = WebSpendBound(kind="web", queries=1, usd_per_query=usd_per_query)
    return pipeline_schema([PipelineNode(name="search", tunes_llm=False, spend_bound=bound)])


def _filed(stores: Stores, sp: JobSearchPoint, session: Any) -> ReplayFeed:
    entry = archive_entry(
        sp, dataset_hash=session.bank.dataset_hash, pipeline_schema=session.pipeline_schema
    )
    return ReplayFeed(stores.archive, entry.node_configs)


def _control_pausing(pressed: Callable[[], bool], **bound: Any) -> RunControl:
    class _Pressed(RunControl):
        def pause_requested(self) -> bool:
            return pressed()

    return _Pressed(**bound)


def _ended_on(walk: query_loop.Walk) -> WalkEnd | None:
    assert walk.outcome is not None, "a walk the phase ran ended without an outcome"
    return walk.outcome.ended_on


async def _stopped_by_operator(phase: Any) -> str | None:
    try:
        await phase
    except KeyboardInterrupt as stop:
        return str(stop)
    except StopLoop as stop:
        return stop.reason.value
    except SendRefusedError as refused:
        return str(ERROR_CATEGORY_INFO[refused.category].stops)
    return None


async def _walk(
    stores: Stores,
    dataset: list[Sample],
    *,
    armed: int,
    cut_at: int | None,
    max_cells: int = 2,
    parent_lacks_cells: bool = False,
    backfill_pace: float = 0.01,
    pause_after_call: int | None = None,
    resumes: JobSearchPoint | None = None,
    stall: int | None = None,
    skip_at: int | None = None,
    cap_usd: float | None = None,
) -> dict[str, Any]:
    backend = _OrderedFakeBackend(len(dataset))
    telemetry.bind_priced(set())
    # An individual of its own, so a walk replays only the cells of the one it resumes.
    sp = resumes or JobSearchPoint(
        pipeline_params=PipelineParams().with_nodes({"search": {"walk": uuid.uuid4().hex}})
    )
    committed: list[int] = []
    returned: list[int] = []
    book = spend_book(cap_usd)
    dollar = SendBound(input_tokens=0, output_tokens=0, usd=1.0)

    async def _measure(sample: Sample, _session: Any, _pipeline_params: Any = None) -> Any:
        with admitted(
            CallLabel("cell", "backend"), dollar, model=None, provider=None, paid_by="host"
        ) as bill:
            if sample.id == stall:
                await asyncio.sleep(0.3)
            row = await backend.measure(sample)
            returned.append(sample.id)
            bill.settle(Billed(TokenAccount(), 1.0))
        return row

    def _backfill(_cycle: Any, _caught: Any, _sp: Any, sample: Sample, _prior: ArmKey) -> Any:
        call = asyncio.ensure_future(backend.hold(sample, backfill_pace))

        def _commit() -> list[Any]:
            committed.append(sample.id)
            return [_HIT.grade(call.result())]

        return call, _commit, call.cancel

    class _ParentPrior:
        """A parent measured on none of the round's cells, so every cell owes it one catch-up call."""

        def __init__(self) -> None:
            self.priors = {ArmKey("parent"): CellSheet("", ())}

        def admit(self, key: ArmKey, results: Sequence[GradedCell]) -> None:
            self.priors[key] = self.priors[key].merged(CellSheet("", tuple(results)))

    parent = ArmKey("parent")
    priors = CatchUpPool(
        cast("Race", _ParentPrior()), cast("Cycle", None), "elimination", {parent: sp}
    )

    # The walk's own ledger: what each launch recorded of the look-ahead it was sent under.
    started = CycleEventLog.open(CycleDir(stores.projects_root / "walks" / uuid.uuid4().hex))
    with (
        scoped(ledger=started),
        mock.patch.object(query_loop, "measure_sample", _measure),
        mock.patch.object(round_module, "_catch_up", _backfill),
        scoped(book=book),
    ):
        session = loop_session(
            stores,
            # One node the backend bounds at the cell's dollar, so the scheduler counts in them.
            _searching(1.0),
            dataset,
            scorer=_HIT,
            control=_control_pausing(
                lambda: pause_after_call is not None and len(returned) >= pause_after_call,
                held_lookahead=armed,
            ),
            client=backend_client(stores.jobs_dir, max_cells_in_flight=max_cells),
        )
        walk = open_walk(
            sp,
            dataset,
            session,
            label=MeasurementRole.PANEL,
            checks=[_CutAfter(cut_at)] if cut_at else [],
            measured=MeasuredPass(individual_id="arm"),
            skip_at=skip_at,
        )
        stopped = await _stopped_by_operator(
            query_loop.run_walks(
                {"arm": walk},
                session,
                keep_cut=False,
                backfills=priors if parent_lacks_cells else None,
            )
        )
    depths = [r.sample_lookahead for _, r in started.iter() if isinstance(r, SampleStartedRecord)]
    feed = _filed(stores, sp, session)
    taken = {cell.key for cell in walk.results}
    priced = scan_ledger_priced_keys([started.path])
    return {
        "sp": sp,
        # Each row's answer is its address in this walk's own archive: the record is the facts.
        "rows": [cell.filed(dataclasses.replace(cell.facts, answer=None)) for cell in walk.results],
        "stop_reason": stopped or _ended_on(walk),
        "calls": list(backend.calls),
        "entries": list(backend.entries),
        "committed": committed,
        "banked": {s.id for s in dataset if s.key not in taken}
        & set(replayable_on(dataset, feed.advance())),
        "priced": {s.id for s in dataset if feed.cell_key(s.key) in priced},
        "returned": returned,
        "billed": book.spent.sent_usd,
        "max_depth": max(depths) if depths else 0,
        "ledger": started.path,
    }


async def _round(
    stores: Stores,
    dataset: list[Sample],
    *,
    armed: int,
    cuts: list[int | None],
    slowest_last: bool = False,
    pause_after_call: int | None = None,
) -> dict[str, Any]:
    backend = _OrderedFakeBackend(len(dataset), slowest_last=slowest_last)
    flag = {"pause": False}
    ledger = CycleEventLog.open(CycleDir(stores.projects_root / "rounds" / uuid.uuid4().hex))

    def absorbed() -> list[tuple[int, int]]:
        return [
            (int(r.individual_id[1:]), r.position)
            for _, r in ledger.iter()
            if isinstance(r, SampleScoredRecord)
        ]

    # Each call beside how many cells the round had absorbed when it went out.
    asked: list[tuple[int, int, int]] = []

    async def _measure(sample: Sample, _session: Any, _pipeline_params: Any = None) -> Any:
        call = len(backend.calls) + 1
        walk = int(cast("MeasuredPass", measured_pass()).individual_id[1:])
        asked.append((walk, sample.id, len(absorbed())))
        row = await backend.measure(sample)
        if call == pause_after_call:
            flag["pause"] = True
        return row

    session = loop_session(
        stores,
        pipeline_schema([]),
        dataset,
        scorer=_HIT,
        control=_control_pausing(lambda: flag["pause"], held_lookahead=armed),
        client=backend_client(stores.jobs_dir, max_cells_in_flight=armed, holds_own_sends=True),
    )
    walks = [
        open_walk(
            JobSearchPoint(),
            dataset,
            session,
            label=MeasurementRole.PANEL,
            checks=[_CutAfter(cut)] if cut else [],
            measured=MeasuredPass(individual_id=f"c{w}"),
        )
        for w, cut in enumerate(cuts)
    ]
    with scoped(ledger=ledger), mock.patch.object(query_loop, "measure_sample", _measure):
        stopped = await _stopped_by_operator(
            query_loop.run_walks(dict(enumerate(walks)), session, keep_cut=False)
        )
    return {
        "rows": [
            [cell.filed(dataclasses.replace(cell.facts, answer=None)) for cell in walk.results]
            for walk in walks
        ],
        "stops": [stopped or _ended_on(walk) for walk in walks],
        "absorbed": absorbed(),
        "asked": asked,
        "calls": list(backend.calls),
        "peak": max(backend.entries),
    }


def _dataset() -> list[Sample]:
    return [Sample(id=i, query=f"q{i}", ground_truth=str(i % 2)) for i in range(1, 9)]


@pytest.mark.usefixtures("run_bound", "offline")
def test_budget_equality_is_over_the_axes_each_names() -> None:
    # A lift is no bound, and an omitted axis no lift.
    for one, other, same in (
        (Budget(usd=1.0), Budget(usd=1.0), True),
        (Budget(usd=None), Budget(), False),
        (Budget(usd=1.0, tokens=None), Budget(usd=1.0), False),
        (Budget(usd=1.0), Budget(usd=2.0), False),
    ):
        assert (one == other) is same and (hash(one) == hash(other)) is same, (
            f"{one!r} and {other!r} read {'apart' if same else 'as one'}: equality saw another "
            "set of axes than the ones each names"
        )
    assert Budget(usd=1.0).only(MONEY_AXES).named == {"usd"}, "only() named an axis self did not"


@pytest.mark.usefixtures("run_bound", "offline")
def test_a_spawned_run_arms_the_book_its_launch_held_on_its_own_tenants_key(
    tmp_path: Path,
) -> None:
    # An identity rebuilt from the tenant alone carries no issuer, and no issuer IS the operator.
    signup = IdentityContext(
        user_id=UserId("sub-9"),
        tenant_id=TenantId("sub-9"),
        issuer=Issuer("https://accounts.google.com"),
        email="a@example.com",
        capabilities=frozenset({"campaign.run"}),
    )
    stores = workspace(tmp_path, signup)
    spawned = JobRegistry(tmp_path / "jobs", capacity=lambda _live: 1)
    held = _held(
        stores,
        spawned,
        Budget(usd=0.25, tokens=40_000, rounds=None, wall_clock_s=900.0),
        reserve=Budget(usd=0.5, tokens=80_000),
        # The operator named the USD axis and LIFTED the rounds cap: a lift stands as a lift.
        standing=Budget(usd=0.25, rounds=None),
        step_rounds=2,
    )
    hop = CycleHop(campaign_id="ds__000001", cycle_id="cycle_root")
    mode = RunMode(from_round=2, diag=True)
    launched_config = CampaignConfig(optimization=OptimizationConfig(degradation_threshold=0.05))
    held_job = spawned.get(held.job_id)
    assert held_job is not None
    launched = HeldRun(
        start=RunStart(stores, spawned, held_job),
        session=loop_session(stores, pipeline_schema([]), [], hop=hop),
        cycle=CycleConfig.laid(launched_config, []),
        budget=held,
        optimizer=select_optimizer(launched_config.optimization),
    )
    spec = launched.job_spec(mode)
    book = _armed(stores, None, spec.budget)
    assert (book.budget, book.allotment, book.reserve) == (
        held.admitted.only(CYCLE_AXES),
        Budget(usd=0.25, tokens=40_000),
        Budget(usd=0.5, tokens=80_000),
    ), "the book the spawned run armed is not the budget its launch held"
    rebuilt = build_stores(
        spec.identity,
        projects_root=spec.projects_root,
        install_datasets_root=spec.install_datasets_root,
        shared_root=spec.shared_root,
    )
    assert is_host(rebuilt.identity) is is_host(stores.identity) is False, (
        "a signup's run, rebuilt in its own process, spent the host's own key"
    )
    assert rebuilt.base_dir == stores.base_dir, "the spawned run billed another tenant's tree"


@pytest.mark.usefixtures("run_bound", "offline")
@on_jumping_clock
async def test_a_lookahead_window_changes_the_bill_and_never_the_record(
    built_stores: Stores,
) -> None:
    dataset = _dataset()
    d1 = await _walk(built_stores, dataset, armed=1, cut_at=None)
    d2 = await _walk(built_stores, dataset, armed=2, cut_at=None)
    assert (d1["max_depth"], d2["max_depth"]) == (1, 2), (
        "arming did not open the window — the rest proves nothing"
    )
    assert d1["rows"] == d2["rows"] and d1["calls"] == d2["calls"], "a window moved the record"
    assert [r.key for r in d2["rows"]] == [s.key for s in dataset], "a window reordered rows"
    c1 = await _walk(built_stores, dataset, armed=1, cut_at=4)
    c2 = await _walk(built_stores, dataset, armed=2, cut_at=4)
    assert c2["max_depth"] == 2, "window never opened on the cut walk"
    assert c1["stop_reason"] == c2["stop_reason"] == "stop_rule", "a window moved the cut"
    assert c1["rows"] == c2["rows"], "a window moved what a cut walk recorded"
    assert len(c1["calls"]) == 4, "an unarmed walk launched before the cell ahead was decided"
    # AT MOST one extra call: equality would pin a scheduling accident, two an overgrown window.
    assert 0 <= len(c2["calls"]) - len(c1["calls"]) <= 1, "a window bought more than one call"
    for cells in (1, 2):
        wide = await _walk(built_stores, dataset, armed=4, cut_at=None, max_cells=cells)
        assert wide["max_depth"] == cells, "the window outgrew the cells its backend holds"
    deep = await _walk(built_stores, dataset, armed=8, cut_at=4, max_cells=8)
    assert deep["stop_reason"] == "stop_rule" and deep["rows"] == c1["rows"], (
        "a deep window moved the cut walk's record"
    )
    assert 0 <= len(deep["calls"]) - len(c1["calls"]) <= 1, (
        "what a cut discards is bounded by the stop rule's HORIZON, not by the depth"
    )
    assert sorted(deep["returned"]) == sorted(deep["calls"]), "a discarded remote call vanished"
    assert deep["billed"] == len(deep["calls"]), (
        "a discarded remote call still bills: the provider charges whether or not anyone waits"
    )


@pytest.mark.usefixtures("run_bound", "offline")
@on_jumping_clock
async def test_a_catch_up_holds_a_cells_slot_and_is_archived_only_for_a_cell_absorbed(
    built_stores: Stores,
) -> None:
    dataset = _dataset()
    # The unarmed cut walk, whose record no catch-up may move.
    c1 = await _walk(built_stores, dataset, armed=1, cut_at=4)
    lone = await _walk(built_stores, dataset, armed=1, cut_at=4, parent_lacks_cells=True)
    wide = await _walk(
        built_stores, dataset, armed=3, cut_at=4, max_cells=3, parent_lacks_cells=True
    )
    # Catch-up far faster than a cell, so the one started past the cut is back before the cut.
    quick = await _walk(
        built_stores,
        dataset,
        armed=8,
        cut_at=4,
        max_cells=8,
        parent_lacks_cells=True,
        backfill_pace=0.0001,
    )
    assert max(lone["entries"]) == 1 and max(wide["entries"]) <= 3, (
        "a catch-up ran past the slots a cell would hold"
    )
    absorbed = [s.id for s in dataset[:4]]
    assert lone["committed"] == wide["committed"] == quick["committed"] == absorbed, (
        "a catch-up was archived for a cell the walk never absorbed"
    )
    assert lone["rows"] == wide["rows"] == quick["rows"] == c1["rows"], "a catch-up moved a record"


@pytest.mark.usefixtures("run_bound", "offline")
@on_jumping_clock
async def test_a_rounds_walks_measure_at_once_decide_in_turn_and_share_one_depth(
    built_stores: Stores,
) -> None:
    dataset = _dataset()
    serial = await _round(built_stores, dataset, armed=1, cuts=[4, None, 6])
    fanned = await _round(built_stores, dataset, armed=4, cuts=[4, None, 6])
    assert serial["peak"] == 1 and fanned["peak"] <= 4, "the depth did not bound the round"
    assert fanned["rows"] == serial["rows"], "measuring walks at once moved a record"
    assert fanned["stops"] == serial["stops"] == ["stop_rule", None, "stop_rule"], (
        "measuring walks at once moved a cut"
    )
    assert fanned["absorbed"] == serial["absorbed"], "walks decided out of turn"
    # First cells fastest: the second walk's calls go out while the first still has cells to take.
    ahead = await _round(built_stores, dataset, armed=4, cuts=[None, None], slowest_last=True)
    assert ahead["absorbed"] == [(w, s.id) for w in (0, 1) for s in dataset], (
        "a later candidate's cell was absorbed before an earlier one's"
    )
    last_of_first = max(i for i, (w, _) in enumerate(ahead["absorbed"]) if w == 0)
    assert any(w == 1 and before <= last_of_first for w, _, before in ahead["asked"]), (
        "no later candidate measured ahead — the identity check below proves nothing"
    )
    for w in (0, 1):
        calls = sorted(sid for who, sid, _ in ahead["asked"] if who == w)
        assert calls == [s.id for s in dataset], f"walk {w}'s calls ran under another identity"


@pytest.mark.usefixtures("run_bound", "offline")
@on_jumping_clock
async def test_the_call_back_as_a_pause_is_pressed_is_absorbed_and_never_paid_again(
    built_stores: Stores,
) -> None:
    dataset = _dataset()
    paused = await _round(built_stores, dataset, armed=1, cuts=[None], pause_after_call=3)
    assert paused["stops"] == ["graceful"], "a pause read as another stop"
    assert len(paused["rows"][0]) == len(paused["calls"]) == 3, (
        "the call that returned as the pause was pressed is absorbed, not paid again on resume"
    )


@pytest.mark.usefixtures("run_bound", "offline")
@on_jumping_clock
async def test_a_pause_starts_no_catch_up_and_a_catch_up_out_at_it_launches_no_cell(
    built_stores: Stores,
) -> None:
    dataset = _dataset()
    for armed_, pace, after, taken in ((1, 0.01, 2, 2), (2, 0.02, 1, 1)):
        owing = await _walk(
            built_stores,
            dataset,
            armed=armed_,
            cut_at=None,
            parent_lacks_cells=True,
            backfill_pace=pace,
            pause_after_call=after,
        )
        assert owing["stop_reason"] == "graceful", f"armed {armed_}: a pause read as another stop"
        assert owing["committed"] == [dataset[0].id], f"armed {armed_}: a pause started a catch-up"
        assert len(owing["rows"]) == len(owing["calls"]) == taken, (
            f"armed {armed_}: a catch-up out at the pause launched a cell"
        )


# The resumed walk replays the cells the stopped one banked.
@pytest.mark.usefixtures("run_bound", "offline")
@on_jumping_clock
async def test_what_comes_back_behind_a_stalled_head_is_banked_priced_and_never_paid_again(
    built_stores: Stores,
) -> None:
    dataset = _dataset()
    head = dataset[0].id
    stopped = await _walk(
        built_stores, dataset, armed=4, max_cells=4, cut_at=None, pause_after_call=3, stall=head
    )
    assert stopped["rows"] == [] and set(stopped["banked"]) == set(stopped["returned"]), (
        "what came back behind a head still out was not banked"
    )
    assert stopped["priced"] == stopped["banked"], (
        "a banked cell's price was not recorded against its key"
    )
    resumed = await _walk(
        built_stores, dataset, armed=4, max_cells=4, cut_at=None, resumes=stopped["sp"]
    )
    assert not set(resumed["calls"]) & set(stopped["banked"]), "a banked cell was paid again"
    assert [r.key for r in resumed["rows"]] == [s.key for s in dataset], (
        "the resumed walk did not replay what it banked"
    )


@pytest.mark.usefixtures("run_bound", "offline")
@on_jumping_clock
async def test_a_cell_past_where_a_rule_could_cut_is_never_banked(built_stores: Stores) -> None:
    dataset = _dataset()
    head = dataset[0].id
    capped = await _walk(
        built_stores, dataset, armed=4, max_cells=4, cut_at=2, pause_after_call=2, stall=head
    )
    assert set(capped["banked"]) == {head, dataset[1].id}, (
        "a cell past where a rule could cut was banked: the serial walk would not have measured it"
    )


@pytest.mark.usefixtures("run_bound", "offline")
@on_jumping_clock
async def test_a_walks_spend_ceiling_binds_before_a_call(built_stores: Stores) -> None:
    dataset = _dataset()
    ceiling = await _walk(built_stores, dataset, armed=4, max_cells=4, cut_at=None, cap_usd=5.0)
    assert (ceiling["stop_reason"], ceiling["billed"]) == ("spend_budget", 5.0), (
        "a spend ceiling binds BEFORE a call: every cell out is counted at its bound"
    )


@pytest.mark.usefixtures("run_bound", "offline")
def test_the_last_skip_on_record_per_candidate_outlives_a_stop_and_a_rewind_drops_it(
    tmp_path: Path,
) -> None:
    skips = CycleEventLog(tmp_path / "skips.jsonl")
    superseding: tuple[tuple[str, int, CutKind | None], ...] = (
        ("a", 3, "skipped"),
        ("b", 2, "skipped"),
        ("b", 8, None),
        ("c", 4, "skipped"),
    )
    with telemetry.at_round(2):
        for cid, n, cut in superseding:
            arm_walk(
                skips,
                [(n, (f"k{n}", "a", False))],
                rnd=2,
                individual=cid,
                role=MeasurementRole.PANEL,
                running=score_summary(walked=n),
            )
            skips.append(ArmRecord(round=2, arm=ArmKey(cid), state=arm_state(cid, cut=cut)))

    def standing_skips() -> dict[tuple[int, ArmKey], int]:
        return scan_standing_rounds([LedgerSpan(skips.path)]).skipped

    assert standing_skips() == {(2, "a"): 3, (2, "c"): 4}, "a superseded skip replayed"
    skips.append(RoundEnteredRecord(round=2))
    assert standing_skips() == {(2, "a"): 3, (2, "c"): 4}, "a resume re-entering lost a skip"
    skips.append(RoundEnteredRecord(round=2, rewound=True))
    assert standing_skips() == {}, "a regenerated round inherited a displaced skip"


# The second walk resumes the first's skipped individual.
@pytest.mark.usefixtures("run_bound", "offline")
@on_jumping_clock
async def test_a_skip_holds_and_its_replay_records_the_depth_it_skipped_at(
    built_stores: Stores,
) -> None:
    dataset = _dataset()
    replayed = await _walk(built_stores, dataset, armed=4, max_cells=4, cut_at=None, skip_at=3)
    assert replayed["stop_reason"] == "skip" and len(replayed["rows"]) == 3, "a skip did not hold"
    assert sorted(replayed["calls"]) == [s.id for s in dataset[:3]], "a skipped cell was paid"
    # A second resume replays the skipped walk: its depth is what the replay records, never zero.
    again = await _walk(
        built_stores, dataset, armed=4, max_cells=4, cut_at=None, skip_at=3, resumes=replayed["sp"]
    )
    depth = [
        r.running.walked
        for _, r in CycleEventLog(again["ledger"]).iter()
        if isinstance(r, SampleScoredRecord) and r.running is not None
    ]
    assert not again["calls"] and depth[-1:] == [3], (
        "a replayed skip recorded no depth, so the next resume skips at zero cells"
    )


@pytest.mark.usefixtures("run_bound", "offline")
@on_jumping_clock
async def test_a_cells_envelope_cancels_the_inner_campaign_it_spawned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, built_stores: Stores
) -> None:
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def _hanging_inner(_cell: Any, _minted: list[Any], _spawned_by: dict[str, Any]) -> Any:
        """SWALLOWS the cancellation and returns normally: a stub that re-raises cannot catch that."""
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
        return cycle_result([], 0.0, [])

    monkeypatch.setattr(spawn, "_run_inner_campaign", _hanging_inner)
    seats = inner_cells("potter")
    owner = CycleHop(campaign_id="ppself__aaaaaa", cycle_id="cycle_deadbeef0000")
    spawning = InnerSpawnContext(
        inner_sandbox_root=inner_sandbox(built_stores, owner.campaign_id, owner.cycle_id),
        identity=built_stores.identity,
        shared_root=built_stores.shared_root,
        install_datasets_root=built_stores.install_datasets_root,
        research_datasets_root=None,
        inside=(owner,),
        enclosing=(),
        acting=(),
        cells=seats,
        purpose=Optimize(),
        ruler=RulerReach(version=0, own=None),
    )
    (seat,) = seats.cells
    query = seat.id
    pin = PinnedCell(cell=seat, instrument="i").pin()
    cell = Sample(id=0, query=query, ground_truth=None, source_pin=pin)
    with scoped(ledger=CycleEventLog(tmp_path / "outer.jsonl"), spawn=spawning):
        # The connector DECLARES the seconds, the scoring seam enforces them, as in `measure_sample`.
        envelope = CellEnvelope(spawn.inner_cell_envelope_s(cell, {}), attempts=1, label=query)
        with pytest.raises(CellUnscoreableError):
            async with envelope:
                await spawn.run_inner_cycle(cell, {})
    assert started.is_set(), "the inner campaign never started — the envelope proved nothing"
    assert cancelled.is_set(), "the inner campaign outlived its envelope and kept spending"


@pytest.mark.usefixtures("run_bound")
@on_jumping_clock
async def test_a_resumed_arm_rereads_its_cells_unbilled_and_its_bench_runs_past_the_ceiling(
    tmp_path: Path, built_stores: Stores, offline: Router
) -> None:
    billed_cent = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost": 0.01}
    offline.llm.says = lambda body: completion("ok", model=body["model"], usage=billed_cent)
    loop_reuse = LLMReuseCache(tmp_path / "reuse", "optimizer_reuse")
    judge_reuse = LLMReuseCache(tmp_path / "reuse", "judge_reuse")

    async def round_calls() -> None:
        await call_mod.llm_call(
            [{"role": "user", "content": "q"}],
            context=call_mod.LLMCallContext(
                node="checkin",
                config=NodeConfig.model_validate(
                    checkin_manifest().config.model_dump(exclude_unset=True)
                    | {"provider": "openai", "model": "m"}
                ),
                template=checkin_template(),
                cache=loop_reuse,
                seed=None,
            ),
        )
        with scoped(judge_cache=judge_reuse):
            await judge_call.ask(JudgeStage(model="m", provider="openai"), "grade", judge="j")

    # Another campaign's run banks both replies; this arm only ever replays them.
    telemetry.bind_priced(set())
    with _billing_on(CycleEventLog(tmp_path / "other.jsonl"), spend_book()):
        await round_calls()

    reread = samples(4)
    step = {"solve": StepUsage(input=10, output=5, cost_usd=0.01)}
    banked = {
        s.id: MeasuredCell(
            sample_key=s.key,
            query=s.query,
            ground_truth="a",
            predicted="a",
            pipeline=PipelineData(step_tokens=step),
        )
        for s in reread
    }
    # The ceiling already spent: no cell fits, and the control reads it as reached.
    ledger = CycleEventLog.open(CycleDir(tmp_path / "cycle"))
    ledger.append(billed_call(0.02, kind="optimizer", node="spent"))
    arm = spend_book(0.02, meters="search_incurred", metered_on=ledger.path)
    session = loop_session(
        built_stores,
        _searching(0.01),
        reread,
        scorer=_HIT,
        client=backend_client(built_stores.jobs_dir, max_cells_in_flight=1),
    )
    # The arm's individual and the one the bench grades, every cell of each already filed.
    picked, graded = (
        JobSearchPoint(pipeline_params=PipelineParams().with_nodes({"search": {"sp": n}}))
        for n in "pg"
    )
    for sp in (picked, graded):
        filed(
            built_stores.archive,
            banked.values(),
            archive_entry(
                sp, dataset_hash=session.bank.dataset_hash, pipeline_schema=session.pipeline_schema
            ),
            grade=REUSABLE_MIN_GRADE,
            created_at="2026-01-01T00:00:00Z",
        )
    feed = _filed(built_stores, picked, session)
    with scoped(ledger=ledger):
        # The stopped launch priced the cells it took.
        telemetry.bind_priced({feed.cell_key(s.key) for s in reread[:3]})
        with scoped(book=arm):
            walk = open_walk(picked, reread, session, label=MeasurementRole.PANEL, measured=None)
            halted = await _stopped_by_operator(
                query_loop.run_walks({"arm": walk}, session, keep_cut=False)
            )
            bench = await score_search_point(
                graded, reread, session, label=MeasurementRole.BENCH, measured=None
            )
            # The stopped launch made the round's calls; the resumed one learns them off the ledger.
            telemetry.bind_priced(set())
            await round_calls()
            telemetry.bind_priced(scan_ledger_priced_keys([ledger.path]))
            await round_calls()
    assert [c.key for c in walk.results] == [s.key for s in reread[:3]], (
        "the ceiling held back a re-read"
    )
    assert halted == "spend_budget", "a spent ceiling did not stop the arm"
    assert len(bench.cells) == len(reread), "the search's ceiling stopped the bench pass"
    kinds = [r.price.kind for _, r in ledger.iter() if isinstance(r, TokenUsageRecord)]
    assert kinds == ["optimizer"] + ["bench"] * len(reread) + ["optimizer", "judge"], (
        f"a re-read was metered a second time: {kinds}"
    )
    assert arm.spent.sent_usd == pytest.approx(0.04), (
        "the arm's book read another bill than metered"
    )
    priced = scan_ledger_priced_keys([ledger.path])
    benched = _filed(built_stores, graded, session)
    benched_keys = {benched.cell_key(s.key) for s in reread}
    assert benched_keys <= priced and len(priced) == len(reread) + 2, (
        "the ledger names another set of priced calls than the arm paid for"
    )


@pytest.mark.usefixtures("run_bound", "offline")
def test_a_ledger_index_serves_the_file_as_it_stands_grown_compacted_raced_and_shared(
    tmp_path: Path,
) -> None:
    index = CycleEventLog.open(CycleDir(tmp_path / "index"))

    def bill(n: int, round: int | None = None, log: CycleEventLog = index) -> None:
        for _ in range(n):
            log.append(
                billed_call(
                    1.0,
                    kind="backend",
                    node="llm_only",
                    model="openai/gpt-oss-20b",
                    provider="openrouter",
                    output_tokens=1,
                    duration_s=1.0,
                    round=round,
                )
            )

    def seed(model: str) -> None:
        overlay = {"llm_only": NodeCall(model=model)}
        index.append(CycleSeedRecord(seed=CycleSeed(pipeline_overlay=overlay)))

    def tailed() -> tuple[float, int, CycleSeed | None]:
        spend, calls, _worked_s = scan_ledger_spend([LedgerSpan(index.path)])
        seeded = scan_last([LedgerSpan(index.path)], CycleSeedRecord)
        return spend.total_used_usd, calls, None if seeded is None else seeded.seed

    def cold() -> tuple[float, int, CycleSeed | None]:
        rows = [json.loads(line) for line in read_lines(index.path)]
        bills = [
            row["money"]["price"]["cost_usd"] for row in rows if row["record_type"] == "token_usage"
        ]
        seeds = [row["seed"] for row in rows if row["record_type"] == "cycle_seed"]
        seeded = CycleSeed.model_validate(seeds[-1]) if seeds else None
        return sum(bills), len(bills), seeded

    bill(3)
    seed("first")
    assert tailed() == cold() and cold()[1] == 3, "the index served another bill than the file"
    bill(2, round=2)
    seed("second")
    assert tailed() == cold() and cold()[1] == 3 + 2, "the index missed rows appended past its tail"
    by_round = scan_ledger_spend_by_round([LedgerSpan(index.path)])
    assert {r: spent.total_used_usd for r, spent in by_round.items()} == {0: 3.0, 2: 2.0}, (
        "the per-round split is the same bill: a call that carries no round banks at the origin's"
    )
    write_jsonl(index.path, [json.loads(line) for line in read_lines(index.path)[:2]])
    assert tailed() == cold() == (2.0, 2, None), (
        "a compaction swaps the file for a shorter one; the index kept its old rows"
    )
    seen: list[tuple[float, int]] = []
    written = threading.Event()

    def read() -> None:
        racing = True
        while racing:
            racing = not written.is_set()
            seen.append(tailed()[:2])

    reader = threading.Thread(target=read)
    reader.start()
    bill(10)
    seed("third")
    written.set()
    reader.join()
    assert all(usd == calls for usd, calls in seen), (
        f"a reader racing the writer saw a torn bill: {[s for s in seen if s[0] != s[1]][:3]}"
    )
    assert tailed() == cold() and cold()[1] == 2 + 10, "the index lost a row written under a race"
    start = threading.Barrier(2)

    def flood() -> None:
        own = CycleEventLog(index.path)
        start.wait()
        bill(10, log=own)

    writers = [threading.Thread(target=flood) for _ in range(2)]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join()
    assert tailed() == cold() and cold()[1] == 2 + 10 + 2 * 10, (
        "writers sharing one file — the server's command beside the run's records — lost a bill"
    )


# The refused purge reads the cell the named home carried.
@pytest.mark.usefixtures("run_bound", "offline")
def test_a_cell_one_account_paid_replays_free_to_another_only_from_a_named_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    homes = tmp_path / "homes"

    def account(sub: str) -> Stores:
        signed_in = IdentityContext(
            user_id=UserId(sub), tenant_id=TenantId(sub), issuer=Issuer(_WEB)
        )
        return workspace(homes, signed_in)

    cell_entry = filed_under([("llm_only", {"model": "m"})])
    paid = MeasuredCell.from_wire(
        {
            "sample_key": "k",
            "pipeline_data": {"step_tokens": {"llm_only": {"input": 900, "rate_priced_usd": 0.3}}},
        }
    )

    def replayed_by_bob() -> list[str]:
        return [
            answer.cell.sample_key for answer in account("bob-id").archive.population(cell_entry)
        ]

    def bank_and_replay(shared: str) -> tuple[list[str], float]:
        monkeypatch.setattr(settings, "SHARED_ARCHIVE", shared)
        filed(account("ada-id").archive, [paid], cell_entry, created_at=shared)
        ledger = CycleEventLog(homes / f"bob-{shared}.jsonl")
        book = spend_book(1.0, meters="bill", metered_on=ledger.path)
        with _billing_on(ledger, book):
            for answer in account("bob-id").archive.population(cell_entry):
                emit_replayed_step_tokens(answer.cell.pipeline.step_tokens, {})
        return replayed_by_bob(), book.spent.sent_usd

    assert bank_and_replay("") == ([], 0), "one account replayed a cell another paid for"
    assert bank_and_replay("lab") == (["k"], 0), (
        "a named home did not carry one account's cell to another, or its replay billed him"
    )
    # Only the host purges the home every account's cells live in.
    with pytest.raises(PayloadInvalidError):
        purge_cold_store(account("ada-id"), apply=True)
    assert replayed_by_bob() == ["k"], "an account's refused purge still took the shared home"
    purge_cold_store(workspace(homes, default_identity()), apply=True)


# 3. A campaign's ceiling


async def _no_wait(*_: Any) -> None:
    return None


@dataclasses.dataclass
class _Provider:
    """What the world's provider does as the journey flips it: an unpriced reply, at a random
    lag, sometimes timed out, hung, cut by a TLS fault once, unbilled, or down."""

    rng: random.Random = dataclasses.field(default_factory=lambda: random.Random(7))
    flaky: bool = False
    hang: bool = False
    broken_once: bool = False
    unbilled: bool = False
    down: bool = False

    async def __call__(self, body: dict[str, Any]) -> Said:
        if self.down:
            return httpx.Response(500, json={"error": "down"})
        if self.broken_once:
            self.broken_once = False
            raise ssl.SSLError("bad record mac")
        await asyncio.sleep(self.rng.random() * 0.02)
        if self.flaky and self.rng.random() < 0.2:
            raise httpx.ReadTimeout("late")
        if self.hang:
            await asyncio.Event().wait()
        prompt = self.rng.randint(1, len(body["messages"][0]["content"]))
        said = self.rng.randint(0, body["max_tokens"])
        usage = {"prompt_tokens": prompt, "completion_tokens": said, "total_tokens": prompt + said}
        return completion("ok", model="gpt-x", usage=None if self.unbilled else usage)


def _priced_wire(offline: Router) -> _Provider:
    offline.rates = {"openai/gpt-x": {"input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6}}
    assert pricing.refresh_rates(force=True), "the world's price list was not fetched"
    offline.llm.says = provider = _Provider()
    return provider


async def _owned_chat(node: str, content: str = "x", *, max_tokens: int = 10) -> Any:
    """One optimizer send through the registry's client, owned as an entry owns it."""
    with scoped(send_budget=SendBudget(None, attempts=SEND_ATTEMPTS)):
        return await keyed_client("openai").chat(
            ChatRequest([{"role": "user", "content": content}], "gpt-x", max_tokens=max_tokens),
            label=CallLabel(node, "optimizer"),
        )


@contextlib.contextmanager
def _billing_on(ledger: CycleEventLog, book: Any) -> Iterator[None]:
    with scoped(ledger=ledger), scoped(book=book):
        yield


def _token_bills(ledger: CycleEventLog) -> list[TokenUsageRecord]:
    return [r for _, r in ledger.iter() if isinstance(r, TokenUsageRecord)]


def _charges() -> ChargeBook:
    return bound_account("the test").charges


_CELL = CallLabel("cell", "backend")
_BENCH = CallLabel("b", "bench")
_OPTIMIZER = CallLabel("o", "optimizer")


def _bill(hold_id: str, usd: float, *, key_source: KeySource = "host") -> Bill:
    price = CallPrice(kind="backend", model=None, provider=None, cost_usd=usd, rate_priced_usd=None)
    return Bill(hold_id=hold_id, key_source=key_source, price=price, tokens=0)


def _drawn(book: SpendBook, usd: float, *, key_source: KeySource = "host", lapsed: bool) -> None:
    """A call under *book*'s job that billed *usd*, or lapsed at it, as its admission writes it."""
    call = Hold(
        hold_id=uuid.uuid4().hex,
        parent=book.hold_id,
        key_source=key_source,
        bound=Budget(usd=usd, tokens=0),
        resource="backend",
        node="cell",
        ledger="drawn.jsonl",
    )
    book.charges.append(call)
    closed = Lapse(hold_id=call.hold_id, bound=call.bound) if lapsed else None
    book.charges.append(closed or _bill(call.hold_id, usd, key_source=key_source))


def _unreported_sends(ledger: CycleEventLog) -> int:
    """The account's sends on *ledger* left unreported (`Money.unreported_holds`)."""
    charges = _charges()
    on = charges.held_on(ledger.path)
    whole, unpriced = money_on(charges.path).unreported_holds()
    return sum(hold.ledger == on for hold in (*whole, *unpriced))


@pytest.mark.usefixtures("offline")
def test_a_mid_run_ceiling_reaches_the_running_book_and_the_loop(
    tmp_path: Path, built_stores: Stores
) -> None:
    # The declared ceiling lives on the ledger alone, so a launch's flag sweep cannot take it.
    campaigns = built_stores.campaigns
    hop = CycleHop(campaign_id="camp", cycle_id="cyc")
    campaigns.mint_cycle(hop)
    cycle_dir = campaigns.cycle_dir(hop)
    jobs = JobRegistry(tmp_path / "jobs", capacity=lambda _live: 1)
    declared = _held(built_stores, jobs, Budget())

    def declare(budget: Budget) -> None:
        # Money binds on the cycle's share, the rest on its own axes: both records, both live.
        campaigns.append(hop, StandingBudgetRecord(budget=budget.only(CYCLE_AXES)))
        hold_moved(built_stores, hop, budget.only(MONEY_AXES))

    CycleEventLog.open(CycleDir(cycle_dir)).append(billed_call(1.0, input_tokens=9_000))
    book = _armed(built_stores, hop, declared)
    assert budget_stop(book) is None, (
        "a run that declared NOTHING still holds a book, which stays silent until a ceiling exists"
    )
    declare(Budget(usd=0.50))
    assert budget_stop(book) == StopReason.SPEND_BUDGET, "a mid-run ceiling reached no book"
    CycleEventLog.open(CycleDir(cycle_dir)).append(RunPhaseRecord(run_phase=RunPhase.RUNNING))
    assert budget_stop(book) == StopReason.SPEND_BUDGET, "a launch swept the ceiling"
    declare(Budget(tokens=5_000))
    assert budget_stop(book) == StopReason.TOKEN_BUDGET, "a token ceiling bound nothing"
    session = loop_session(built_stores, pipeline_schema([]), [], hop=hop)
    config = _budgeted(Budget(usd=0.025, rounds=50))
    declare(Budget(rounds=3))
    bounded, pause_at = round_bounds(session, config)
    assert (bounded.optimization.budget.rounds, pause_at) == (3, None), (
        "a mid-run round cap reached no loop"
    )
    assert budget_stop(book) is None, "a lifted ceiling still governed the run"
    # Another PROCESS's reshare lands on the cycle's ledger, behind this process's own write of it.
    CycleEventLog.open(CycleDir(cycle_dir)).append(CycleShareRecord(allotment=Budget(usd=0.50)))
    assert budget_stop(book) == StopReason.SPEND_BUDGET, (
        "a share another process moved was hidden from the running book"
    )
    declare(Budget(rounds=None))
    bounded, _ = round_bounds(session, config)
    assert bounded.optimization.budget.rounds is None, (
        "a LIFT of the round cap reads as no cap, never a fall back to the config's"
    )


@pytest.mark.usefixtures("offline")
def test_moving_one_ceiling_leaves_the_other_at_its_launch_cap(
    tmp_path: Path, built_stores: Stores
) -> None:
    moved = CycleHop(campaign_id="camp-4", cycle_id="cycle_budget0001")
    built_stores.campaigns.create_campaign(minted_campaign(moved))
    built_stores.campaigns.mint_cycle(moved)
    CycleEventLog.open(CycleDir(built_stores.campaigns.cycle_dir(moved))).append(
        billed_call(0.10, input_tokens=210_000)
    )
    registry = JobRegistry(tmp_path / "jobs-4", capacity=lambda _live: 1)
    job = registry.request_slot(
        user_id="default",
        principal_id="default",
        workspace=built_stores.campaigns.workspace,
        holder=CampaignLaunch("ds1"),
        hop=moved,
    )
    _admitted(built_stores, registry, job.job_id, Budget(usd=0.30, tokens=5_000_000))
    caps = HeldBudget(
        admitted=Budget(usd=0.30, tokens=210_000),
        standing=Budget(),
        job_id=job.job_id,
        share=Budget(usd=0.30, tokens=210_000),
        step_rounds=None,
    )
    moved_book = _armed(built_stores, moved, caps)
    assert budget_stop(moved_book) == StopReason.TOKEN_BUDGET, "the token ceiling bound nothing"
    dispatcher = CommandDispatcher(built_stores, registry)
    armed = _change_run_limits(dispatcher, moved, Budget(usd=0.50))
    assert armed.usd == pytest.approx(0.50), (
        "the change armed no ceiling a replay could stop the run at"
    )
    assert budget_stop(moved_book) == StopReason.TOKEN_BUDGET, (
        "a USD raise lifted the token ceiling"
    )
    assert moved_book.allotment.usd == pytest.approx(0.50), (
        "$0.50 ceiling, $0.10 of it this cycle's own: the running book binds another share"
    )
    _change_run_limits(dispatcher, moved, Budget(usd=None))
    assert moved_book.allotment.usd is None, "a lifted ceiling still bounds the running share"


@pytest.mark.usefixtures("offline")
def test_an_offshoot_takes_the_rounds_its_parent_left_and_names_no_money_of_its_own() -> None:
    # A cap the fork's seed names is the operator's.
    left = ForkRemainder.of(rounds_closed=3, rounds=5)
    for overrides, expected in (
        (ConfigOverrides(), Budget(rounds=2)),
        (
            ConfigOverrides(budget=Budget(rounds=9, wall_clock_s=60.0)),
            Budget(rounds=9, wall_clock_s=60.0),
        ),
        (ConfigOverrides(budget=Budget(rounds=None)), Budget(rounds=None)),
    ):
        assert left.under(overrides).budget == expected, (
            f"{overrides}: an offshoot ran past the rounds its parent left, or over its operator's"
        )
    # A fork's money is its campaign's share, so no entry point's seed may name its own.
    for money in (Budget(usd=1.0), Budget(tokens=1_000)):
        with pytest.raises(ValidationError):
            ConfigOverrides(budget=money)
    for remainder, rounds_left in (
        (ForkRemainder.of(rounds_closed=7, rounds=5), 1),
        (ForkRemainder.of(rounds_closed=1, rounds=None), None),
    ):
        assert remainder.rounds == rounds_left, (
            "a parent run past its cap left its offshoot no round, or an uncapped one invented a cap"
        )


class _SharedCeiling(NamedTuple):
    shared: JobRegistry
    launch: Callable[[CycleHop, float | None], tuple[Any, HeldBudget]]
    share: Callable[[HeldBudget], float | None]
    bill: Callable[[CycleHop, float], None]


@pytest.fixture
def shared_ceiling(tmp_path: Path, built_stores: Stores) -> _SharedCeiling:
    """The jobs dir every reader attaches to, with room for every fork, and launches on it."""
    shared = JobRegistry(tmp_path / "jobs", capacity=lambda _live: 4)
    payer = account_of(built_stores)

    def bill(at: CycleHop, usd: float) -> None:
        CycleEventLog.open(CycleDir(built_stores.campaigns.cycle_dir(at))).append(billed_call(usd))

    def launch(fork: CycleHop, usd: float | None) -> tuple[Any, HeldBudget]:
        job = shared.request_slot(
            user_id=payer.user_id,
            principal_id=payer.user_id,
            workspace=built_stores.campaigns.workspace,
            holder=CampaignLaunch("ds1"),
            hop=fork,
        )
        try:
            held = admit_and_share(
                Budget(usd=usd),
                Budget(usd=usd),
                stores=built_stores,
                job_registry=shared,
                job=job,
                hop=fork,
                step_rounds=None,
                sends=_CELLS,
            )
        except QuotaExceededError as exc:
            shared.release(job.job_id, exc)
            raise
        return job, held

    def share(held: HeldBudget) -> float | None:
        job = shared.get(held.job_id)
        assert job is not None
        return cycle_share(built_stores, job).usd

    return _SharedCeiling(shared, launch, share, bill)


# Each fork is admitted what the forks launched before it left of the one ceiling.
@pytest.mark.usefixtures("offline")
def test_forks_launched_side_by_side_hold_one_ceiling_between_them(
    built_stores: Stores, shared_ceiling: _SharedCeiling
) -> None:
    shared, launch, share, bill = shared_ceiling
    root = CycleHop(campaign_id="camp-5", cycle_id="cycle_share0005")
    forks = [
        CycleHop(campaign_id="camp-5", cycle_id=f"cycle_share0005_fork_{name}")
        for name in ("a", "b", "c")
    ]
    built_stores.campaigns.create_campaign(minted_campaign(root))
    built_stores.campaigns.mint_cycle(root)
    root_dir = built_stores.campaigns.cycle_dir(root)
    bill(root, 0.25)
    for fork in forks:
        built_stores.campaigns.mint_fork_cycle(
            fork,
            root_dir,
            ForkSpec(
                trigger=ForkTrigger.OPERATOR_REWIND,
                reason="",
                issued_by=SYSTEM_PRINCIPAL,
                from_candidate=RoundArm(round=0, arm=ArmKey("origin")),
            ),
            cut=CycleEventLog.open(CycleDir(root_dir)).next_offset,
            cut_round=1,
        )
    fork_a, fork_b, fork_c = forks
    _, held_a = launch(fork_a, 0.5)
    assert share(held_a) == pytest.approx(0.25), (
        "fork a was not admitted its ceiling less the spent"
    )
    bill(fork_a, 0.125)
    job_b, held_b = launch(fork_b, 1.0)
    assert share(held_b) == pytest.approx(0.5), (
        "$1.00 ceiling, $0.375 spent, fork a holding $0.125 more: fork b may reach $0.50 — no "
        "more, and its own spend counted once"
    )
    assert share(held_a) == pytest.approx(0.25), (
        "fork b's launch raised the campaign's ceiling and handed fork a fork b's money"
    )
    beside = _armed(built_stores, fork_b, held_b)
    eighth = SendBound(input_tokens=0, output_tokens=0, usd=0.125)
    assert beside.fits(eighth, paid_by="host") == 4, (
        "fork b's book spends what fork a still holds: two forks, two ceilings"
    )
    # A third fork is refused: its siblings hold the ceiling whole.
    with pytest.raises(QuotaExceededError):
        launch(fork_c, 1.0)
    # A fork arms its campaign's ceiling, which the launch that moved it wrote on the root.
    armed = _change_run_limits(CommandDispatcher(built_stores, shared), fork_b, Budget(rounds=4))
    assert armed.usd == pytest.approx(1.0), (
        "a fork armed its own ledger's ceiling, not the campaign's"
    )

    # An embedded run holds a share through the same admission, and never a machine slot.
    async def held_embedded(held: HeldBudget) -> tuple[None, CycleResult]:
        assert held.admitted.usd == pytest.approx(1.0)
        run = shared.running_job_for(built_stores.campaigns.workspace, fork_c)
        assert run is not None and cycle_share(built_stores, run).usd == pytest.approx(0.5), (
            "an embedded run held no share, or another one"
        )
        assert fork_c not in {j.hop for j in shared.list_running()}, (
            "an embedded run took a machine slot to carry its share"
        )
        return None, cycle_result([], None, [])

    async def embedded() -> None:
        await slotless_share(
            _budgeted(Budget(usd=1.0)),
            stores=built_stores,
            holder=EmbeddedRun("ds1"),
            hop=fork_c,
            requested=Budget(),
            step_rounds=None,
            sends=_CELLS,
            run=held_embedded,
        )

    with pytest.raises(QuotaExceededError):
        asyncio.run(embedded())
    shared.release(job_b.job_id, StopReason.CONVERGED)
    asyncio.run(embedded())
    assert shared.running_job_for(built_stores.campaigns.workspace, fork_c) is None, (
        "an embedded run kept its share past its exit"
    )


# The money-axis refusals read the pair's cycles while they live.
@pytest.mark.usefixtures("offline")
def test_a_raised_ceiling_is_reshared_over_every_live_cycle_never_handed_whole_to_one(
    built_stores: Stores, shared_ceiling: _SharedCeiling
) -> None:
    shared, launch, share, _ = shared_ceiling
    # A runs at $1, B is admitted at $3 beside it ($2), and the raise to $3 leaves A its $1.
    eighth = SendBound(input_tokens=0, output_tokens=0, usd=0.125)
    pair = [CycleHop(campaign_id="camp-6", cycle_id=f"cycle_pair000{n}") for n in (1, 2)]
    built_stores.campaigns.create_campaign(minted_campaign(pair[0]))
    for cycle in pair:
        built_stores.campaigns.mint_cycle(cycle)
    job_a, held_a = launch(pair[0], 1.0)
    book_a = _armed(built_stores, pair[0], held_a)
    job_b6, held_b6 = launch(pair[1], 3.0)
    assert (share(held_a), share(held_b6)) == (1.0, 2.0), (
        "two live cycles were admitted more than their campaign's ceiling between them"
    )
    _change_run_limits(CommandDispatcher(built_stores, shared), pair[0], Budget(usd=3.0))
    assert (book_a.allotment.usd, share(held_a), share(held_b6)) == (1.0, 1.0, 2.0), (
        "a raised ceiling handed one cycle what its sibling holds: the two may spend past it"
    )
    assert book_a.fits(eighth, paid_by="host") == 8, (
        "A's live book binds on another share than it holds"
    )
    # One campaign's money axis binds every live cycle or none, whichever arrived first.
    with pytest.raises(QuotaExceededError):
        launch(CycleHop(campaign_id="camp-6", cycle_id="cycle_pair0003"), None)
    for job in (job_a, job_b6):
        shared.release(job.job_id, StopReason.CONVERGED)
    open_c, _ = launch(CycleHop(campaign_id="camp-6", cycle_id="cycle_pair0004"), None)
    with pytest.raises(QuotaExceededError):
        launch(CycleHop(campaign_id="camp-6", cycle_id="cycle_pair0005"), 1.0)
    shared.release(open_c.job_id, StopReason.CONVERGED)


@pytest.mark.usefixtures("offline")
def test_a_launch_landing_mid_reshare_waits_it_out(
    monkeypatch: pytest.MonkeyPatch, built_stores: Stores, shared_ceiling: _SharedCeiling
) -> None:
    shared, launch, _, _ = shared_ceiling
    # A holds $1 of $1 while a raise to $3 re-shares; B, launching at $3 meanwhile, is admitted
    # on the shares as moved, never on a torn split.
    race = [CycleHop(campaign_id="camp-8", cycle_id=f"cycle_race000{n}") for n in (1, 2)]
    built_stores.campaigns.create_campaign(minted_campaign(race[0]))
    for cycle in race:
        built_stores.campaigns.mint_cycle(cycle)
    launch(race[0], 1.0)
    resharing, b_at_gate, go = threading.Event(), threading.Event(), threading.Event()
    metered_spend, gate = built_stores.campaigns.metered_spend, shared.admission_gate
    landed: list[object] = []

    def paused_metered(campaign_id: str, meter: Any) -> Budget:
        if threading.current_thread() is raiser and not resharing.is_set():
            resharing.set()
            go.wait(timeout=60)
        return metered_spend(campaign_id, meter)

    def watched_gate() -> Any:
        if threading.current_thread() is launcher:
            b_at_gate.set()
        return gate()

    def raise_to_three() -> None:
        change_budget(
            Budget(usd=3.0), stores=built_stores, job_registry=shared, hop=race[0], sends=()
        )

    def launch_b() -> None:
        try:
            landed.append(launch(race[1], 3.0))
        except QuotaExceededError as exc:
            landed.append(exc)

    raiser = threading.Thread(target=raise_to_three)
    launcher = threading.Thread(target=launch_b)
    with monkeypatch.context() as patched:
        patched.setattr(built_stores.campaigns, "metered_spend", paused_metered)
        patched.setattr(shared, "admission_gate", watched_gate)
        raiser.start()
        assert resharing.wait(timeout=60), "the raise never reached its re-share"
        launcher.start()
        assert b_at_gate.wait(timeout=60), "the launch never reached the admission gate"
        go.set()
        raiser.join(timeout=60)
        launcher.join(timeout=60)
    racing = [
        cycle_share(built_stores, job).usd or 0.0
        for job in shared.list_holding_spend()
        if job.on_campaign(built_stores.campaigns.workspace, "camp-8")
    ]
    assert landed and sum(racing) <= 3.0 + 1e-9, (
        f"shares {racing} of a $3 ceiling: a launch admitted mid-reshare overcommitted it"
    )
    for job in shared.list_holding_spend():
        if job.on_campaign(built_stores.campaigns.workspace, "camp-8"):
            shared.release(job.job_id, StopReason.CONVERGED)


@pytest.mark.usefixtures("offline")
def test_a_ceiling_its_campaign_has_spent_admits_nothing(
    built_stores: Stores, shared_ceiling: _SharedCeiling
) -> None:
    _, launch, _, bill = shared_ceiling
    race = [CycleHop(campaign_id="camp-8", cycle_id=f"cycle_race000{n}") for n in (1, 2)]
    built_stores.campaigns.create_campaign(minted_campaign(race[0]))
    for cycle in race:
        built_stores.campaigns.mint_cycle(cycle)
    bill(race[0], 3.0)
    with pytest.raises(QuotaExceededError):
        launch(race[1], 3.0)


@pytest.mark.usefixtures("offline")
def test_a_job_retargeted_mid_stamp_is_never_overwritten_by_the_runs_stale_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    built_stores: Stores,
    shared_ceiling: _SharedCeiling,
) -> None:
    # A job file has one writer at a time (bl5.B2).
    shared, _, _, _ = shared_ceiling
    race = [CycleHop(campaign_id="camp-8", cycle_id=f"cycle_race000{n}") for n in (1, 2)]
    stamped = shared.create(
        user_id="u-stamp",
        principal_id="u-stamp",
        workspace=built_stores.campaigns.workspace,
        hop=race[1],
        holder=CampaignLaunch("ds1"),
    )
    shared.mark_admitted(stamped.job_id)
    api = JobRegistry(tmp_path / "jobs", capacity=lambda _live: 4)
    interleaved = [False]
    read_job = shared.get

    def read_then_moved(job_id: str) -> Any:
        job = read_job(job_id)
        with contextlib.suppress(ServiceUnavailableError):
            api.update_target(job_id, hop=race[0])
            interleaved[0] = True
        return job

    with monkeypatch.context() as patched:
        patched.setattr(interlock, "LOCK_TIMEOUT", 0.0)
        patched.setattr(shared, "get", read_then_moved)
        shared.mark_started(stamped.job_id)
        if not interleaved[0]:
            api.update_target(stamped.job_id, hop=race[0])
    after = shared.get(stamped.job_id)
    assert after is not None and after.hop == race[0], (
        "a job retargeted mid-stamp was overwritten by the run's stale job file"
    )
    assert after.started_at is not None
    shared.release(stamped.job_id, StopReason.CONVERGED)


@pytest.mark.usefixtures("offline")
def test_sharing_divides_the_campaigns_ceiling_never_what_the_account_admits(
    tmp_path: Path, shared_ceiling: _SharedCeiling
) -> None:
    # A $10 campaign that has spent $3 shares the $7 left; its account binds through the reserve.
    shared, _, _, _ = shared_ceiling
    delegate = workspace(
        tmp_path,
        IdentityContext(
            user_id=UserId("sub-d"),
            tenant_id=TenantId("sub-d"),
            issuer=Issuer("https://accounts.google.com"),
            capabilities=frozenset({"campaign.run"}),
        ),
    )
    delegate.grant.save(CouponGrant(face=Budget(usd=50.0, tokens=50_000)))
    spender, claimed = (
        CycleHop(campaign_id="camp-9", cycle_id=f"cycle_claim000{n}") for n in (1, 2)
    )
    delegate.campaigns.create_campaign(minted_campaign(spender))
    delegate.campaigns.mint_cycle(spender)
    delegate.campaigns.mint_cycle(claimed)
    CycleEventLog.open(CycleDir(delegate.campaigns.cycle_dir(spender))).append(billed_call(3.0))
    claim = shared.request_slot(
        user_id="sub-d",
        principal_id="sub-d",
        workspace=delegate.campaigns.workspace,
        holder=CampaignLaunch("ds1"),
        hop=claimed,
    )
    admit_and_share(
        Budget(usd=10.0),
        Budget(usd=10.0),
        stores=delegate,
        job_registry=shared,
        job=claim,
        hop=claimed,
        step_rounds=None,
        sends=_CELLS,
    )
    claim_held = shared.get(claim.job_id)
    assert claim_held is not None and cycle_share(delegate, claim_held).usd == pytest.approx(7.0), (
        "a share was cut from its account's claim, not the campaign's $7 left"
    )
    shared.release(claim.job_id, StopReason.CONVERGED)


@pytest.mark.usefixtures("offline")
def test_a_planned_head_to_head_is_priced_at_its_archived_cells_and_optimizer_rounds(
    built_stores: Stores,
) -> None:
    bank = loop_session(built_stores, pipeline_schema([]), samples(6, "g"))
    on = [("llm_only", {"model": "X"})]
    for model, usd, seconds, rows in (
        ("X", 0.001, 1.0, slice(0, 3)),
        ("X", 0.003, 3.0, slice(3, 6)),
        ("Y", 1.0, 99.0, slice(0, 3)),
    ):
        steps = {"step_tokens": {"llm_only": {"input": 1, "output": 1, "cost_usd": usd}}}
        filed(
            built_stores.archive,
            [
                archive_row(s.query, pipeline_data={**steps, "step_timings": {"llm_only": seconds}})
                for s in bank.bank.samples[rows]
            ],
            filed_under([("llm_only", {"model": model})], bank.bank.dataset_hash),
        )
    cell = measured_cell_price(
        bank.store, bank.bank.dataset_hash, PipelineParams().with_nodes(dict(on))
    )
    assert cell is not None and (cell.usd, cell.seconds, cell.cells) == pytest.approx(
        (0.002, 2.0, 6)
    ), "a cell was priced off another model's rows, or off fewer than the archive holds"
    priced = CycleHop(campaign_id="camp-10", cycle_id="cycle_priced0001")
    built_stores.campaigns.create_campaign(minted_campaign(priced))
    built_stores.campaigns.mint_cycle(priced)
    for usd, at in ((0.5, None), (0.02, 1), (0.04, 2), (0.0, 3)):
        CycleEventLog.open(CycleDir(built_stores.campaigns.cycle_dir(priced))).append(
            billed_call(usd, round=at)
        )
    held = optimizer_round_price(built_stores, "potter")
    assert held is not None and (held.usd, held.rounds) == pytest.approx((0.03, 2)), (
        "a round's optimizer price counted the origin's init, or a round that called it for free"
    )
    write_yaml(
        built_stores.tenant_datasets.dataset_dir("ds1") / DEFINITION_FILE,
        {
            "pipeline": {"backend_type": "termnorm"},
            "campaign": {"sp_budget_origin": 20, "optimization": {"degradation_threshold": 0.4}},
        },
    )
    plan = HeadToHeadPlan.model_validate(
        {
            "head_to_head_id": "plan",
            "dataset_name": "ds1",
            "arms": {"potter": {"optimizer": "potter"}, "capo": {"optimizer": "capo"}},
            "replicates": 2,
            "budgets": {"under": {"usd": 0.05}, "over": {"usd": 1.2, "rounds": 2}},
        }
    )
    heads = plan.heads()
    on_disk = ConfigLayers.of(offered_dataset(built_stores, "ds1"), None)
    configs = [
        laid_config(
            built_stores,
            on_disk.over(head.head_to_head.declared_optimization(arm)),
            connector="termnorm",
            schema=None,
        ).config
        for head in heads
        for arm in head.head_to_head.arms
    ]
    points = [point for point in plan.budgets.values() for _ in range(4)]
    assert all(
        c.optimization.budget.only(point.named) == point
        for c, point in zip(configs, points, strict=True)
    ), "an arm was minted on another budget than its point's"
    price = priced_head_to_head(
        heads,
        configs,
        dataset_hash=bank.bank.dataset_hash,
        search=30,
        bench=10,
        cell=cell,
        optimizers={"potter": held, "capo": None},
        cells_in_flight=4,
    )
    for arm, config in zip(price.arms, configs, strict=True):
        point = config.optimization.budget
        assert point.usd is not None
        per_round = select_optimizer(config.optimization).round_cells_ceiling(30)
        searched = arm.usd - arm.bench_cells * cell.usd
        if point.rounds is not None and arm.rounds == point.rounds:
            assert arm.search_cells == 20 + point.rounds * per_round, "a round cap bought another"
        else:
            # Within one send of the ceiling: a cell, or the optimizer call opening a round.
            send = max(cell.usd, (price.optimizers[arm.optimizer] or cell).usd)
            assert point.usd - send - 1e-9 <= searched <= point.usd + 1e-9, (
                f"{arm.optimizer} at ${point.usd}: priced ${searched} of search, where its meter "
                "stops the arm within a send of its ceiling"
            )
        assert (arm.refused is None) == (point.usd >= per_round * cell.usd), (
            f"{arm.optimizer} at ${point.usd}: priced as if its mint ran, or refused one it runs"
        )
        assert arm.serial_s is not None and arm.wall_clock_s is not None and cell.seconds
        assert arm.serial_s - arm.wall_clock_s == pytest.approx(
            (arm.search_cells + arm.bench_cells) * cell.seconds * (1 - 1 / 4)
        ), "the wall clock read the cells at another concurrency than the connector's"
    assert price.usd == pytest.approx(sum(arm.usd for arm in price.arms)), "an arm went unpriced"
    potter_over, capo_over = price.arms[4], price.arms[6]
    assert (potter_over.rounds, capo_over.rounds) == (2, 1), (
        "$1.20 buys potter its two capped rounds and capo one 450-cell round, cut where it ends"
    )


# 4. What a send is held at


@pytest.mark.usefixtures("run_bound")
def test_a_remote_cell_open_at_the_runs_wall_clock_is_cut_on_it(
    tmp_path: Path, offline: Router
) -> None:
    offline.backend.faults = {"/matches": [Hang()]}
    jobs = tmp_path / "jobs"
    remote = backend_client(jobs, cell_envelope_s=cell_envelope_of(600.0))
    clock = spend_book(None, usd_reserve=None, wall_clock_s=30.0)
    cut_session = loop_session(
        workspace(tmp_path / "cut"),
        pipeline_schema([]),
        [],
        scorer=_HIT,
        client=remote,
    )

    async def cut_cell() -> tuple[MeasuredCell, float]:
        loop = asyncio.get_running_loop()
        began = loop.time()
        with scoped(book=clock):
            row = await measure_sample(
                Sample(id=0, query="q", ground_truth="a"), cut_session, PipelineParams()
            )
        return row, loop.time() - began

    row, waited = jumping_clock(cut_cell())
    assert row.error_category == ErrorCategory.HALTED, (
        "a cell open at the wall clock's edge ended as anything but a HALTED cut"
    )
    assert waited == pytest.approx(30.0, abs=1.0), (
        f"the cell ran {waited:.0f}s: its envelope, not the run's clock, bounded it"
    )
    clock.opened -= 31.0
    assert budget_stop(clock) == StopReason.WALL_CLOCK, (
        "a run past its wall clock was not stopped on it"
    )


@pytest.mark.usefixtures("run_bound", "offline")
def test_every_level_holds_a_sends_whole_bound_whatever_such_sends_billed_before() -> None:
    worst = SendBound(input_tokens=0, output_tokens=1000, usd=0.125)
    for reserve in (0.5, None, 1.0):
        own = spend_book(0.5, usd_reserve=reserve)
        assert own.fits(worst, paid_by="host") == 4, f"reserve {reserve}: the bound holds"
        held = [
            own.hold(worst, _CELL, what="cell", paid_by="host", ledger="cells.jsonl")
            for _ in range(4)
        ]
        with pytest.raises(SendRefusedError):
            own.hold(worst, _CELL, what="cell", paid_by="host", ledger="cells.jsonl")
        billed = 0
        while own.exhausted() is None:
            own.charges.append(_bill(held.pop(), worst.usd or 0.0))
            billed += 1
        assert billed == 4, f"reserve {reserve}: the run stops on reaching its ceiling"


# Each call's hold is read after the bills before it: the mean moves with every one.
@pytest.mark.usefixtures("run_bound", "offline")
def test_with_overshoot_on_a_call_holds_its_nodes_mean_bill_in_the_run_after_the_first(
    tmp_path: Path, built_stores: Stores
) -> None:
    hop = CycleHop(campaign_id="camp", cycle_id="cyc")
    built_stores.campaigns.create_campaign(minted_campaign(hop))
    built_stores.campaigns.mint_cycle(hop)
    jobs = JobRegistry(tmp_path / "jobs", capacity=lambda _live: 1)
    book = _armed(built_stores, hop, _held(built_stores, jobs, Budget(usd=1.0)))
    worst = SendBound(input_tokens=0, output_tokens=1000, usd=0.10)

    def held(node: str, billed: float) -> Budget:
        """What a call of *node* is held at, which then bills *billed* on 100 tokens."""
        with _billing_on(CycleEventLog(tmp_path / "calls.jsonl"), book):
            call = Admission(
                book, CallLabel(node, "optimizer"), worst, model=None, provider=None, paid_by="host"
            )
            bound = list(money_on(built_stores.charges.path).holds.values())[-1].bound
            call.settle(Billed(TokenAccount(input=60, output=40), billed))
        return bound

    _change_run_limits(CommandDispatcher(built_stores, jobs), hop, Budget(), overshoot=True)
    assert held("judge", 0.02) == worst.budget, "a node's first call held less than its worst case"
    assert held("judge", 0.04) == Budget(usd=0.02, tokens=100), (
        "a node's second call held another figure than its first call's bill"
    )
    assert held("judge", 0.05).usd == pytest.approx(0.03), "the hold is not the node's mean bill"
    assert held("other", 0.01) == worst.budget, "another node's bills sized a node's first call"
    _change_run_limits(CommandDispatcher(built_stores, jobs), hop, Budget(), overshoot=False)
    assert held("judge", 0.01) == worst.budget, "overshoot off still held a call below its worst"


@pytest.mark.usefixtures("run_bound", "offline")
def test_a_bill_past_its_overshoot_hold_draws_on_the_run_and_the_dashboard_serves_it(
    tmp_path: Path, built_stores: Stores
) -> None:
    hop = CycleHop(campaign_id="camp", cycle_id="cyc")
    campaign = minted_campaign(hop)
    stored_campaign(built_stores, campaign)
    minted_dataset(built_stores, campaign)
    built_stores.campaigns.mint_cycle(hop)
    ledger = CycleEventLog.open(CycleDir(built_stores.campaigns.cycle_dir(hop)))
    ledger.append(run_wiring())
    jobs = JobRegistry(tmp_path / "jobs", capacity=lambda _live: 1)
    book = _armed(built_stores, hop, _held(built_stores, jobs, Budget(usd=1.0)))
    _change_run_limits(CommandDispatcher(built_stores, jobs), hop, Budget(), overshoot=True)
    worst = SendBound(input_tokens=0, output_tokens=1000, usd=0.10)
    with _billing_on(ledger, book):
        for billed in (0.02, 0.05):
            Admission(
                book,
                CallLabel("judge", "optimizer"),
                worst,
                model=None,
                provider=None,
                paid_by="host",
            ).settle(Billed(TokenAccount(input=60, output=40), billed))
    money = money_on(built_stores.charges.path)
    assert money.drawn[book.hold_id].total.sent_usd == pytest.approx(0.07), (
        "a bill past its hold drew less than its whole bill on the run's hold"
    )
    assert book.money_left().usd == pytest.approx(0.93), "the share counted only what was held"
    dash = served_dashboard(built_stores, hop)
    assert isinstance(dash, ServedDashboard) and dash.overshot is not None
    assert dash.overshot.calls == 1 and dash.overshot.usd == pytest.approx(0.03), (
        "the dashboard served another overshoot than the one call billed past its mean"
    )
    assert dash.run_limits.overshoot, "the dashboard served the switch off"


@pytest.mark.usefixtures("run_bound", "offline")
def test_a_sends_room_is_the_ceiling_less_bills_unpriced_sends_and_the_set_aside() -> None:
    worst = SendBound(input_tokens=0, output_tokens=1000, usd=0.125)
    owed = spend_book(0.5)
    _drawn(owed, 0.125, lapsed=True)
    assert owed.fits(worst, paid_by="host") == 3, "a send no bill priced left its room open"
    owed.set_aside(0.25, 0)
    assert owed.fits(worst, paid_by="host") == 1, (
        "the search could spend what the bench was set aside"
    )
    assert owed.exhausted() is None, "a set-aside read as spent"
    _drawn(owed, 0.125, lapsed=False)
    assert owed.fits(worst, paid_by="host") == 0 and owed.exhausted() is not None, (
        "a send's room is the ceiling less EVERYTHING owed: bills, unpriced sends, the set-aside"
    )


@pytest.mark.usefixtures("run_bound", "offline")
def test_a_price_list_that_could_not_be_fetched_is_an_outage_never_an_unpriced_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetches: list[str] = []

    def down(url: str, **_kw: Any) -> Any:
        fetches.append(url)
        raise urllib.error.URLError("no route to host")

    async def held_twice() -> BaseException:
        with scoped(book=spend_book(1.0, usd_reserve=None)):
            for _ in range(2):
                with pytest.raises(pricing.PriceListUnreachableError) as outage:
                    await base.hold_ceiling("vendor/model-x", "openrouter")
        with scoped(book=spend_book()):
            assert await base.hold_ceiling("vendor/model-x", "openrouter") is None, (
                "an unbounded book refused a send over a list it never needs"
            )
        return outage.value

    # A model no other call in the suite names, so neither route memo holds it yet.
    with monkeypatch.context() as patched:
        patched.setattr(urllib.request, "urlopen", down)
        outage = asyncio.run(held_twice())
    assert len(fetches) == 1, f"{len(fetches)} fetches — one per send, each a full timeout"
    assert run_stop_reason(outage) is StopReason.PRICE_LIST_UNREACHABLE, "an outage read as no rate"


def _replay(usd: float) -> TokenUsageRecord:
    return billed_call(usd, kind="backend", node="n", input_tokens=10, output_tokens=5, replay=True)


@pytest.mark.usefixtures("run_bound", "offline")
def test_a_controlled_arms_ceiling_is_its_searchs_incurred_cost_a_replay_spends_no_bill(
    tmp_path: Path,
) -> None:
    metered: dict[CeilingMeter, CycleEventLog] = {}
    for meter in get_args(CeilingMeter):
        metered[meter] = CycleEventLog(tmp_path / meter / "ledger.jsonl")
        metered[meter].append(_replay(0.06))
        metered[meter].append(_replay(0.05))
    arm = spend_book(0.10, meters="search_incurred", metered_on=metered["search_incurred"].path)
    ordinary = spend_book(0.10, metered_on=metered["bill"].path)
    assert ordinary.exhausted() is None, "a replay billed nothing, so it spends no bill"
    assert arm.exhausted() == ErrorCategory.SPEND_CEILING, (
        "arms replay each other's cells: a ceiling on the BILL hands the second arm a bigger search"
    )


# The replays that spend the share read on from the bench pass's bill.
@pytest.mark.usefixtures("run_bound", "offline")
def test_a_bench_pass_eats_none_of_an_arms_search_budget_and_replays_spend_it(
    tmp_path: Path,
) -> None:
    searched = CycleEventLog(tmp_path / "fresh.jsonl")
    fresh = spend_book(0.10, meters="search_incurred", metered_on=searched.path)
    searched.append(_replay(0.09))
    pass_bound = SendBound(input_tokens=100, output_tokens=100, usd=5.0)
    # On the account's own key: one on the box's would meet the reserve (B2).
    benched = fresh.hold(pass_bound, _BENCH, what="bench pass", paid_by="user", ledger="b")
    fresh.charges.append(Release(hold_id=benched))
    searched.append(billed_call(5.0, kind="bench", node="b", output_tokens=1, key_source="user"))
    assert fresh.exhausted() is None, "the bench pass ate into the arm's search budget"
    # The pass's cells are backend cells filed as bench: admitted in the bucket they bill to.
    with (
        scoped(book=fresh),
        telemetry.filed_as("bench"),
        reserved(CallLabel("cell", "backend"), pass_bound, paid_by="user"),
    ):
        pass
    # A replay draws no coupon: the search's share refuses once replays spend it.
    searched.append(_replay(0.02))
    small = SendBound(input_tokens=1, output_tokens=1, usd=0.02)
    with pytest.raises(SendRefusedError):
        fresh.hold(small, _OPTIMIZER, what="o", paid_by="host", ledger="o")


# The killed send's hold is read beside the sends the burst left unreported.
@pytest.mark.usefixtures("run_bound")
def test_no_burst_of_sends_records_spend_past_its_ceiling_and_a_killed_hold_stays_out(
    tmp_path: Path, offline: Router
) -> None:
    wire = _priced_wire(offline)
    wire.flaky = True
    rng = wire.rng
    burst_ledger = CycleEventLog(tmp_path / "burst.jsonl")
    ceiling_usd = 0.05
    book = spend_book(ceiling_usd)

    async def burst() -> list[Any]:
        async def one(i: int) -> Any:
            return await _owned_chat(f"n{i}", "x" * rng.randint(10, 400), max_tokens=1500)

        tasks = [asyncio.ensure_future(one(i)) for i in range(40)]
        await asyncio.sleep(0.005)
        for task in tasks[::5]:
            task.cancel()
        return await asyncio.gather(*tasks, return_exceptions=True)

    with _billing_on(burst_ledger, book):
        outcomes = jumping_clock(burst())
    wire.flaky = False
    records = _token_bills(burst_ledger)
    assert all(r.price.cost_usd is None for r in records), (
        "a reply that reported no cost read as one"
    )
    recorded = sum(r.price.rate_priced_usd or 0.0 for r in records)
    assert recorded <= ceiling_usd + 1e-12, (
        f"recorded ${recorded:.6f} past a ${ceiling_usd} ceiling"
    )
    assert recorded == pytest.approx(book.spent.sent_usd), (
        "no reply reported a cost: each record is priced at our rate, and the ceiling binds on that"
    )
    assert any(isinstance(o, SendRefusedError) for o in outcomes), "the ceiling never bound"
    unreported = sum(isinstance(o, asyncio.CancelledError | httpx.ReadTimeout) for o in outcomes)
    charges = _charges()
    left_out = unreported_under(charges, burst_ledger.path).host.unreported_usd
    assert unreported and _unreported_sends(burst_ledger) == unreported, (
        "a send that left unbilled lapsed nowhere"
    )
    assert len(records) == sum(not isinstance(o, BaseException) for o in outcomes), (
        "a reply left no bill, or a failed send left one"
    )
    assert book.spent.unreported_usd == pytest.approx(left_out), "the book forgot a lapsed send"
    assert book.spent.at_most_usd <= ceiling_usd + 1e-12, (
        "bills and lapsed sends together ran past the ceiling"
    )
    # A hard exit runs no `finally`: the hold written ahead of the call is held while its job
    # holds, and unreported at its bound once the job's hold closes.
    hold_usd = 0.0036
    killed = Hold(
        hold_id="killed",
        parent="reaped",
        key_source="host",
        bound=Budget(usd=hold_usd, tokens=2_100),
        resource="optimizer",
        node="o",
        ledger=charges.held_on(burst_ledger.path),
    )
    reaped = {"hold_id": "reaped", "parent": None, "resource": None, "node": None, "ledger": None}
    charges.append(killed.model_copy(update=reaped))
    charges.append(killed)
    assert _unreported_sends(burst_ledger) == unreported, "a send out under a live job lapsed"
    charges.append(Release(hold_id="reaped"))
    account = (
        billed_spend([burst_ledger.path]).plus(unreported_under(charges, burst_ledger.path)).host
    )
    assert account.used_usd == 0.0, "a price off our rate table was read as spent"
    assert account.rate_priced_usd == pytest.approx(recorded), "the rate-priced sum moved"
    assert account.unreported_usd == pytest.approx(left_out + hold_usd), (
        "a killed send's hold was not counted as out"
    )
    assert _unreported_sends(burst_ledger) == unreported + 1, "a killed send's hold went unread"


@pytest.mark.usefixtures("run_bound", "offline")
def test_a_bill_naming_no_price_closes_its_hold_in_tokens_and_keeps_its_money_held(
    tmp_path: Path,
) -> None:
    hold_usd = 0.0036
    charges = _charges()
    bare = CycleEventLog(tmp_path / "bare" / "ledger.jsonl")
    on_bare = charges.held_on(bare.path)
    charges.append(
        Hold(
            hold_id="bare",
            parent=None,
            key_source="host",
            bound=Budget(usd=hold_usd, tokens=2_100),
            resource="optimizer",
            node="o",
            ledger=on_bare,
        )
    )
    priceless = billed_call(node="bare", input_tokens=40, output_tokens=9, hold_id="bare").bill
    assert priceless is not None
    charges.append(priceless.model_copy(update={"ledger": on_bare}))
    assert unreported_under(charges, bare.path).host.unreported_usd == pytest.approx(hold_usd), (
        "a priceless bill freed its hold"
    )


@pytest.mark.usefixtures("run_bound")
def test_a_nested_runs_send_is_admitted_by_both_books_and_billed_on_its_roots_ledger(
    tmp_path: Path, offline: Router
) -> None:
    _priced_wire(offline)
    hold_usd = 0.0036
    root = CycleEventLog(tmp_path / "root.jsonl")
    book = spend_book(0.05)
    book.ledger = root
    nested = CycleEventLog(tmp_path / "nested.jsonl")
    inner = spend_book(5.0, enclosing=book)
    inner.ledger = nested
    with _billing_on(nested, inner):
        jumping_clock(_owned_chat("inner", "nested"))
    assert [(r.node, r.mirrored) for r in _token_bills(root)] == [("inner:inner", False)], (
        "a nested send was not billed on its root's ledger"
    )
    assert [r.mirrored for r in _token_bills(nested)] == [True], "the nested copy bills twice"
    assert _unreported_sends(root) == 0, "a billed nested send stayed held"
    assert billed_spend([nested.path]).total.sent_usd == 0.0, "the nested ledger billed its mirror"
    assert ledger_cost([nested.path]).total.sent_usd == pytest.approx(
        billed_spend([root.path]).total.sent_usd
    ), "the nested run's own cost read another figure than the bill its root carries"
    metered_usd = book.spent.sent_usd
    assert 0.0 < metered_usd == pytest.approx(billed_spend([root.path]).total.sent_usd), (
        "the root's book read another bill than its ledger holds"
    )
    assert inner.spent.sent_usd == pytest.approx(metered_usd), (
        "the nested book counted another bill than its root's"
    )
    room = (inner.money_left(), book.money_left())
    past_root = SendBound(input_tokens=0, output_tokens=0, usd=0.06)
    with pytest.raises(SendRefusedError):
        inner.hold(past_root, _OPTIMIZER, what="past the root", paid_by="host", ledger="r")
    assert (inner.money_left(), book.money_left()) == room, (
        "a nested send past its ROOT's ceiling was admitted on its own book's room, or held "
        "half-way"
    )
    cut = SendBound(input_tokens=600, output_tokens=1500, usd=hold_usd)
    with _billing_on(nested, inner):
        Admission(
            inner,
            CallLabel("cut", "optimizer"),
            cut,
            model="gpt-x",
            provider="openai",
            paid_by="host",
        ).unreported()
    assert _unreported_sends(root) == 1, "a nested send cut short lapsed nowhere"
    assert book.spent.unreported_usd == pytest.approx(hold_usd), (
        "a nested send cut short stayed held on its own book and freed its root's"
    )


@pytest.mark.usefixtures("run_bound", "offline")
def test_an_inner_campaign_whose_provider_reports_no_bill_is_not_a_free_cell() -> None:
    silent = SpendRollup()
    silent.bank(billed_call(node="n", input_tokens=10, output_tokens=5, rate_priced_usd=0.02))
    facts = inner_cell_facts(cycle_result([0.4], 0.3, []).model_copy(update={"spend": silent}))
    assert silent.total_used_usd == 0.0, "an unbilled inner cell vanished"
    inner_row = MeasuredCell(
        sample_key="k0",
        query="q",
        predicted="",
        ground_truth="",
        pipeline=PipelineData(
            cell_report=inner_cell_report(facts, OuterSampleProxies(floored=0, held_out_lift=0.0))
        ),
    )
    assert cell_channels_of(inner_row, None)["cost"] == pytest.approx(0.02), (
        "an inner campaign whose provider reported no bill read as a free cell"
    )


# Each send's count reads on from the sends held before it.
@pytest.mark.usefixtures("run_bound")
def test_a_send_that_left_and_named_no_price_stays_held_at_its_bound(
    tmp_path: Path, offline: Router
) -> None:
    wire = _priced_wire(offline)
    hold_usd = 0.0036
    cut = SendBound(input_tokens=600, output_tokens=1500, usd=hold_usd)
    charges = _charges()
    held_ledger = CycleEventLog(tmp_path / "held.jsonl")
    book = spend_book(0.05)
    wire.broken_once = True
    with _billing_on(held_ledger, book):
        jumping_clock(_owned_chat("tls"))
    assert _unreported_sends(held_ledger) == 1, (
        "a TLS record fault after the request left is resent like a 5xx, and the broken send held"
    )
    wire.unbilled = True
    bills, spent_usd = len(_token_bills(held_ledger)), book.spent.sent_usd
    with _billing_on(held_ledger, book):
        jumping_clock(_owned_chat("unbilled"))
    assert _unreported_sends(held_ledger) == 2 and book.spent.sent_usd == spent_usd, (
        "a reply reporting no usage is a bill that never came: held, never settled at 0"
    )
    assert len(_token_bills(held_ledger)) == bills, "a reply with no usage wrote a bill"
    wire.unbilled = False
    nitro = CallLabel("nitro", "optimizer")
    sends_before, before, held_usd, spent_usd = (
        _unreported_sends(held_ledger),
        unreported_under(charges, held_ledger.path).host,
        book.spent.unreported_usd,
        book.spent.sent_usd,
    )
    with _billing_on(held_ledger, book):
        Admission(
            book,
            nitro,
            cut,
            model="m:nitro",
            provider="openrouter",
            paid_by="host",
        ).settle(Billed(TokenAccount(input=600, output=100), None))
    assert book.spent.sent_usd == spent_usd, "a bill naming tokens and no price read as free"
    assert book.spent.unreported_usd == pytest.approx(held_usd + hold_usd), (
        "it was not held at its bound"
    )
    after = unreported_under(charges, held_ledger.path).host
    assert (_unreported_sends(held_ledger), after.unreported_tokens) == (
        sends_before + 1,
        before.unreported_tokens,
    ), "a priceless bill's hold was not counted as out"
    assert after.unreported_usd == pytest.approx(before.unreported_usd + hold_usd), (
        "a priceless hold left its bound"
    )


@pytest.mark.usefixtures("run_bound")
def test_one_unit_of_work_resends_from_one_budget_whichever_loop_asks(offline: Router) -> None:
    wire = _priced_wire(offline)

    async def two_rungs() -> None:
        with scoped(send_budget=SendBudget(None, attempts=SEND_ATTEMPTS)):
            for _rung in range(2):
                with pytest.raises(httpx.HTTPStatusError):
                    await keyed_client("openai").chat(
                        ChatRequest([{"role": "user", "content": "x"}], "gpt-x", max_tokens=10),
                        label=CallLabel("down", "optimizer"),
                    )

    wire.down = True
    with scoped(book=spend_book()):
        offline.llm.sent.clear()
        jumping_clock(two_rungs())
        attempts = len(offline.llm.sent)
        assert attempts == SEND_ATTEMPTS + 1, f"{attempts} attempts in one unit of work"
        offline.llm.sent.clear()
        _reply_text, error = jumping_clock(
            judge_call.ask(JudgeStage(model="gpt-x", provider="openai"), "grade", judge="j")
        )
    attempts = len(offline.llm.sent)
    assert error and attempts == SEND_ATTEMPTS, (
        f"{attempts} attempts on one grading: a grading opens its own budget, and spends that"
    )
    wire.down = False


@pytest.mark.usefixtures("run_bound", "offline")
def test_a_repaired_nodes_resend_is_in_its_sums_never_held_again() -> None:
    # Holding it again halted runs short of their cap.
    repaired = {"step_tokens": {"n": {"attempts": 2, "input": 10, "output": 5, "cost_usd": 0.001}}}
    bill, _ = cell_billing(pipeline_schema([]), {}, {"n": SendBound(40, 20, 0.004)})(repaired)
    assert bill is not None and bill.unknown is None, "an answered attempt was held as unreported"


@pytest.mark.usefixtures("run_bound")
def test_a_backend_cell_is_never_sent_again_while_it_may_still_bill(
    tmp_path: Path, offline: Router
) -> None:
    jobs = tmp_path / "jobs"
    cell_ledger = CycleEventLog(tmp_path / "termnorm.jsonl")
    billed_step = {
        "step_tokens": {
            "n": {
                "attempts": 3,
                "unreported_attempts": 2,
                "input": 10,
                "output": 5,
                "cost_usd": 0.001,
            }
        }
    }
    attempt = SendBound(input_tokens=40, output_tokens=20, usd=0.004)
    deadline = {
        "detail": {"error_code": "llm_timeout", "retryable": False, "message": "no reply in 164s"},
        "data": {"step_tokens": None},
    }
    offline.backend.faults = {
        "/status": [httpx.ConnectError("still restarting")],
        "/matches": [
            httpx.ConnectError("refused"),
            httpx.Response(500, json={"detail": "upstream", "data": billed_step}),
            httpx.ReadTimeout("slow"),
            httpx.Response(504, json=deadline),
        ],
    }
    offline.backend.asked.clear()
    remote = backend_client(jobs, session_factory=TermNormSession)
    whole_cell = SendBound(input_tokens=100, output_tokens=50, usd=0.01)
    wallet = spend_book()

    async def remote_cell() -> Any:
        async with CellEnvelope(600.0, attempts=remote.connector.cell_attempts, label="q"):
            return await remote.run_query(
                Sample(id=0, query="q", ground_truth=None),
                PipelineParams(),
                bound=whole_cell,
                billed=cell_billing(pipeline_schema([]), {}, {"n": attempt}),
            )

    for ends in (httpx.ReadTimeout, CellHaltedError):
        with _billing_on(cell_ledger, wallet), pytest.raises(ends):
            jumping_clock(remote_cell())
    posts = offline.backend.asked.count("/matches")
    assert posts == 4, (
        f"{posts} POSTs — a read timeout is NEVER resent: the backend still works it, and a "
        "second POST bills twice"
    )
    assert [r.price.cost_usd for r in _token_bills(cell_ledger)] == [0.001], (
        "a failed POST's reported step spend went unbilled"
    )
    assert wallet.spent.unreported_usd == pytest.approx(2 * 0.01 + 2 * 0.004), (
        "a cell that may bill was freed"
    )
    assert wallet.spent.unreported_tokens == 2 * 150 + 2 * 60, (
        "a cell that may bill freed its tokens"
    )


@pytest.mark.usefixtures("run_bound", "offline")
def test_a_relays_422_on_an_answer_that_failed_validation_may_have_billed() -> None:
    assert replied(422, headers=None, said="", answered_on=frozenset({422})).may_have_billed, (
        "the relay's 422 on an answer that failed validation is a generation: it may have billed"
    )
    assert not replied(422, headers=None, said="").may_have_billed, "a plain 422 read as billed"


@pytest.mark.usefixtures("run_bound", "offline")
def test_a_container_trial_reruns_on_the_budget_its_connector_sized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from promptpotter.connectors import harbor  # extras: harbor

    trials: list[int] = []

    async def registry_down(_episode: Any) -> Any:
        trials.append(1)
        raise CellInfrastructureError("no such host", spent={})

    monkeypatch.setattr(harbor, "_attempt", registry_down)
    run_trial = harbor.CONNECTOR.in_process_run
    assert run_trial is not None

    async def container_cell() -> Any:
        async with CellEnvelope(600.0, attempts=harbor.CONNECTOR.cell_attempts, label="task"):
            return await run_trial(
                InProcessWorkload(experiment={}, experiment_dir=tmp_path, program=None),
                Sample(id=0, query="task", ground_truth=None, source_pin={}),
                {harbor.SKILL_DELIVERY_KEY: "agent_skill"},
            )

    with pytest.raises(CellInfrastructureError):
        jumping_clock(container_cell())
    assert len(trials) == harbor.CONNECTOR.cell_attempts, (
        f"{len(trials)} trials: a container cell reran past the budget its connector sized"
    )


class _DspyCells(NamedTuple):
    cell: Callable[..., dict[str, Any]]
    lm_bills: Callable[[], list[TokenUsageRecord]]
    sends: list[int]
    rows: list[PipelineData]
    purse: SpendBook


@pytest.fixture
def dspy_cells(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router) -> _DspyCells:
    """A DSPy program's cells on the priced wire, each billed on one purse's ledger."""
    import dspy  # extras: dspy
    from dspy import lm15

    from promptpotter.connectors import dspy_module  # extras: dspy

    _priced_wire(offline)
    jobs = tmp_path / "jobs"
    monkeypatch.setattr(dspy_module, "held_wait", _no_wait)
    sends: list[int] = []
    script: list[Exception] = []

    class _Engine:
        def complete(self, _request: Any) -> Any:
            sends.append(1)
            if script:
                raise script.pop(0)
            said = lm15.TextPart("[[ ## answer ## ]]\n4\n\n[[ ## completed ## ]]")
            return lm15.Response(
                id=None,
                model="gpt-x",
                message=lm15.Message.assistant([said]),
                finish_reason="stop",
                usage=lm15.Usage(input_tokens=11, output_tokens=3),
            )

    class _Calls(dspy.Module):  # type: ignore[misc]
        def __init__(self, calls: int) -> None:
            super().__init__()
            self.steps = [dspy.Predict("question -> answer") for _ in range(calls)]
            for step in self.steps:
                step.lm = dspy.LM("openai/gpt-x", engine=_Engine(), cache=False)

        def forward(self, question: str) -> Any:
            for step in self.steps:
                answer = step(question=question)
            return answer

    dspy_ledger = CycleEventLog(tmp_path / "dspy.jsonl")
    purse = spend_book(1.0)
    example = dspy.Example(question="2+2?", answer="4").with_inputs("question")
    rows: list[PipelineData] = []

    def dspy_cell(calls: int, *fails: Exception) -> dict[str, Any]:
        sends.clear()
        script[:] = fails
        program = dspy_module.DspyProgram(
            student=_Calls(calls),
            metric=lambda ex, pred: pred.answer == ex.answer,
            examples=[example],
        )
        client = BackendClient(
            dspy_module.CONNECTOR,
            workload=InProcessWorkload(experiment=None, experiment_dir=None, program=program),
            jobs_dir=jobs,
        )
        config = {"prompt": "Answer.", "model": "openai/gpt-x", "max_calls": 2, "max_tokens": 50}
        pairs = {dspy_module.PROGRAM_NODE: client.priced_as(NodeCall.model_validate(config))}

        async def one_cell() -> Any:
            async with CellEnvelope(600.0, attempts=client.connector.cell_attempts, label="q"):
                return await client.run_query(
                    Sample(id=0, query="q", ground_truth="4"),
                    PipelineParams().with_nodes({dspy_module.PROGRAM_NODE: config}),
                    bound=SendBound(input_tokens=20_000, output_tokens=2_000, usd=0.5),
                    billed=cell_billing(pipeline_schema([]), pairs, {}),
                )

        # A real clock: the student runs in a worker thread, which a jumping clock outruns.
        with _billing_on(dspy_ledger, purse):
            data, spent = asyncio.run(one_cell())
        rows.append(spent)
        return cast("dict[str, Any]", data)

    def lm_bills() -> list[TokenUsageRecord]:
        return [r for r in _token_bills(dspy_ledger) if r.input_tokens]

    return _DspyCells(dspy_cell, lm_bills, sends, rows, purse)


@pytest.mark.usefixtures("run_bound")
def test_a_dspy_cell_bills_each_lm_call_and_its_row_states_the_price_a_replay_reads(
    tmp_path: Path, dspy_cells: _DspyCells
) -> None:
    from promptpotter.connectors import dspy_module  # extras: dspy

    dspy_cell, lm_bills, _, rows, purse = dspy_cells
    answered_cell = dspy_cell(2)
    assert answered_cell[dspy_module.SCORE_KEY] == 1.0, "a DSPy cell that answered scored another"
    assert answered_cell["step_tokens"][dspy_module.PROGRAM_NODE]["input"] == 2 * 11, (
        "a DSPy cell's row lost a call's tokens"
    )
    assert [(r.input_tokens, r.output_tokens) for r in lm_bills()] == [(11, 3)] * 2, (
        "a student's call left no bill of its own"
    )
    call_usd = 11 * 1e-6 + 3 * 2e-6
    priced = lm_bills()
    assert [r.price.cost_usd for r in priced] == [None, None] and [
        r.price.rate_priced_usd for r in priced
    ] == pytest.approx([call_usd] * 2), (
        "the engine reports tokens and no cost: each call is priced at our rate"
    )
    assert purse.spent.sent_usd == pytest.approx(2 * call_usd), "the ceiling bound another price"
    banked_step = rows[-1].step_tokens[dspy_module.PROGRAM_NODE]
    assert (banked_step.model, banked_step.provider) == ("gpt-x", "openai"), (
        "the ROW names no route its price could be read on"
    )
    assert banked_step.rate_priced_usd == pytest.approx(2 * call_usd), (
        "the ROW states another price: the fitness cost term and a replay's ceiling read it"
    )
    replays = CycleEventLog(tmp_path / "replays.jsonl")
    replay_arm = spend_book(1.0, meters="search_incurred", metered_on=replays.path)
    with _billing_on(replays, replay_arm):
        emit_replayed_step_tokens(rows[-1].step_tokens, {})
    assert replay_arm.spent.sent_usd == pytest.approx(2 * call_usd), (
        "a replayed DSPy cell read as free"
    )


@pytest.mark.usefixtures("run_bound")
def test_a_dspy_cell_past_its_bound_halts_and_bills_the_calls_it_made(
    dspy_cells: _DspyCells,
) -> None:
    dspy_cell, lm_bills, sends, _, _ = dspy_cells
    # An answered cell first, whose two bills the halted cell's two join.
    dspy_cell(2)
    with pytest.raises(CellHaltedError):
        dspy_cell(3)
    assert len(sends) == 2, f"{len(sends)} calls sent under a bound sized on two"
    assert len(lm_bills()) == 4, "the calls a halted cell had made went unbilled"


@pytest.mark.usefixtures("run_bound")
def test_a_dspy_cell_resends_a_5xx_once_and_reads_a_providers_failure_as_a_hole(
    dspy_cells: _DspyCells,
) -> None:
    from dspy import lm15  # extras: dspy

    from promptpotter.connectors import dspy_module  # extras: dspy

    dspy_cell, _, sends, _, _ = dspy_cells
    resent = dspy_cell(1, lm15.ServerError("boom", status=500))
    assert resent[dspy_module.SCORE_KEY] == 1.0 and len(sends) == 2, "a 5xx was not resent once"
    for fail, raised, category in (
        (lm15.TransportError("refused"), CellInfrastructureError, ErrorCategory.CONNECTION),
        (
            lm15.BillingError("Insufficient credits", status=402),
            CellSendRefusedError,
            ErrorCategory.PROVIDER_CREDIT,
        ),
    ):
        with pytest.raises(raised) as hole:
            dspy_cell(1, fail)
        assert hole.value.category is category, f"{fail!r}: a provider's failure scored a cell"


class _AgentCells(NamedTuple):
    agent_cell: Callable[[int], Coroutine[Any, Any, Any]]
    seams: list[Any]
    cut_short: list[bool]
    three_answered: asyncio.Event
    whole: SendBound


@pytest.fixture
def agent_cells(tmp_path: Path, offline: Router) -> _AgentCells:
    """A cell billed send by send through the agent's model seam; its fourth turn hangs while
    `cut_short` holds."""
    from promptpotter.connectors import harbor  # extras: harbor

    wire = _priced_wire(offline)
    jobs = tmp_path / "jobs"
    turns: list[Any] = []
    seams: list[Any] = []
    cut_short = [True]
    three_answered = asyncio.Event()

    async def episode_run(_workload: Any, _sample: Any, _payload: dict[str, Any]) -> dict[str, Any]:
        seams.append(
            seam := harbor._AgentModel(
                "gpt-x", "openai", {"max_tokens": 100, "max_input_tokens": 1000}
            )
        )
        for n in range(4):
            wire.hang = cut_short[0] and n == 3
            turns.append(await seam.call(f"turn {n}"))
            if len(turns) == 3:
                three_answered.set()
        return {"data": {"terminal_node": "agent"}}

    agent = backend_client(
        jobs, execution="in_process", in_process_run=episode_run, holds_own_sends=True
    )
    whole = SendBound(input_tokens=100_000, output_tokens=50_000, usd=0.5)

    async def agent_cell(sample_id: int) -> Any:
        async with CellEnvelope(600.0, attempts=agent.connector.cell_attempts, label="q"):
            return await agent.run_query(
                Sample(id=sample_id, query="q", ground_truth=None),
                PipelineParams(),
                bound=whole,
                billed=lambda _data: (None, None),
            )

    return _AgentCells(agent_cell, seams, cut_short, three_answered, whole)


@pytest.mark.usefixtures("run_bound")
def test_a_cell_cancelled_mid_episode_reserves_its_bound_and_holds_only_what_is_out(
    tmp_path: Path, agent_cells: _AgentCells
) -> None:
    agent_cell, _, _, three_answered, whole = agent_cells
    turns_ledger = CycleEventLog(tmp_path / "turns.jsonl")
    purse = spend_book(1.0)

    async def cancel_mid_episode() -> None:
        running = asyncio.ensure_future(agent_cell(0))
        await three_answered.wait()
        await asyncio.sleep(0.05)
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    with _billing_on(turns_ledger, purse):
        jumping_clock(cancel_mid_episode())
    cut_bills = _token_bills(turns_ledger)
    assert [r.node for r in cut_bills] == ["agent"] * 3, "a turn that answered left no bill"
    cut_usd = sum(r.price.bill_or_rate_usd or 0.0 for r in cut_bills)
    assert purse.spent.sent_usd == pytest.approx(cut_usd), (
        "the book read another bill than the turns wrote"
    )
    out = unreported_under(_charges(), turns_ledger.path).host.unreported_usd
    assert whole.usd is not None
    assert _unreported_sends(turns_ledger) == 1 and out < whole.usd / 100, (
        f"a cancel left ${out} unreported"
    )
    assert purse.spent.unreported_usd == pytest.approx(out), "the book held another sum than is out"
    assert purse.fits(whole, paid_by="host") == 1, "a cell's bound was not reserved whole"


@pytest.mark.usefixtures("run_bound")
def test_an_episodes_row_counts_the_tokens_its_sends_billed_and_no_reported_price(
    tmp_path: Path, agent_cells: _AgentCells
) -> None:
    agent_cell, seams, cut_short, _, _ = agent_cells
    turns_ledger = CycleEventLog(tmp_path / "turns.jsonl")
    purse = spend_book(1.0)
    cut_short[0] = False
    with _billing_on(turns_ledger, purse):
        jumping_clock(agent_cell(1))
    episode_bills = _token_bills(turns_ledger)
    assert seams[-1].spent.input == sum(r.input_tokens for r in episode_bills), (
        "the cell's row counts other tokens than its sends billed"
    )
    assert seams[-1].cost_usd is None, (
        "sends that reported no cost summed to a REPORTED price: unknown, never a smaller one"
    )


@pytest.mark.usefixtures("run_bound", "offline")
def test_a_resume_counts_each_prior_bill_as_its_send_did(tmp_path: Path) -> None:
    # An own key's off the coupon, a kind its meter skips on the box's key.
    prior = CycleEventLog(tmp_path / "prior.jsonl")
    prior_bills: tuple[tuple[TokenUsageKind, KeySource], ...] = (
        ("optimizer", "user"),
        ("bench", "host"),
    )
    for kind, paid_by in prior_bills:
        prior.append(
            billed_call(
                0.02, kind=kind, node=kind, input_tokens=4, output_tokens=2, key_source=paid_by
            )
        )
    resumed = spend_book(1.0, meters="search_incurred", metered_on=prior.path)
    assert (resumed.spent.sent_usd, resumed.coupon_left().usd) == pytest.approx((0.02, 0.98)), (
        "a resume read an own key's bill as the box's, or lost a bill its meter skips"
    )


@pytest.mark.usefixtures("run_bound")
def test_an_agent_whose_sends_died_on_a_5xx_never_measured_the_prompt(offline: Router) -> None:
    from promptpotter.connectors import harbor  # extras: harbor

    wire = _priced_wire(offline)
    wire.down = True

    async def downed_turn() -> Any:
        seam = harbor._AgentModel("gpt-x", "openai", {"max_tokens": 100, "max_input_tokens": 1000})
        with (
            scoped(send_budget=SendBudget(None, attempts=SEND_ATTEMPTS)),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await seam.call("turn")
        return seam

    with scoped(book=spend_book()):
        downed = jumping_clock(downed_turn())
    wire.down = False
    # Read before the trial's result: a send's fault decides alone.
    ended = downed.failure(cast("Any", None))
    assert ended is not None and ended[1] is ErrorCategory.SERVER, (
        f"an episode the provider's 5xx ended scored as the prompt's failure: {ended!r}"
    )


# 5. The host coupon


_WEB = "https://accounts.google.com"
_FACE = Budget.amount(settings.HOST_COUPON.usd or 0.0, settings.HOST_COUPON.tokens or 0)
_HELD_HOP = CycleHop(campaign_id="camp", cycle_id="cycle_x")
# A backend's cells, which go out on the box's keys whatever the account holds.
_CELLS = (BackendCells("termnorm", "host", priced=True),)
# Cells no rate bounds: a dollar ceiling on them is refused at the mint.
_UNPRICED_CELLS = (BackendCells("n8n", "host", priced=False),)
_OWN_ONLY = (ProviderSend("openrouter", "user"),)


def _account_stores(
    tmp_path: Path,
    *,
    issuer: str | None,
    user: User,
    spent: dict[str, Any] | None,
    face: Budget | None = None,
    own_key: str | None = None,
) -> Stores:
    """`issuer` set makes this a WEB identity; the box operator has none and is unmetered. Its one
    campaign's cycle is `_HELD_HOP`; its charges hold what the box's keys *spent* for it."""
    stores = workspace(
        tmp_path,
        IdentityContext(
            user_id=UserId(user.user_id),
            tenant_id=TenantId(user.tenant_id),
            issuer=None if issuer is None else Issuer(issuer),
            email="",
            host=issuer is None,
        ),
    )
    stores.users.save(user)
    if face is not None:
        stores.grant.save(CouponGrant(face=face))
    if own_key is not None:
        stores.api_keys.put(own_key, SecretStr("sk-own"))
    held = minted_campaign(_HELD_HOP, config=_budgeted(Budget()))
    stores.campaigns.create_campaign(held)
    minted_dataset(stores, held)
    stores.campaigns.mint_cycle(_HELD_HOP)
    if spent is not None:
        stores.charges.append(
            Bill(
                hold_id="spent",
                key_source="host",
                price=CallPrice(
                    kind="optimizer",
                    model="gpt-x",
                    provider="openai",
                    cost_usd=spent.get("cost_usd"),
                    rate_priced_usd=None,
                ),
                tokens=spent["input_tokens"] + spent["output_tokens"],
            )
        )
    return stores


def _running(stores: Stores, registry: JobRegistry, *, admitted: bool, reserve: Budget) -> None:
    """Another run of the account holding *reserve*, admitted or still at its gate."""
    held = registry.create(
        user_id="sub-9",
        principal_id="sub-9",
        workspace=WorkspaceDir(registry.jobs_dir),
        hop=UNRESOLVED_HOP,
        holder=CampaignLaunch(dataset_name="d"),
        stage=RunPhase.RUNNING,
    )
    if admitted:
        _admitted(stores, registry, held.job_id, reserve)


async def _paid_door(door: str, stores: Any) -> None:
    match door:
        case "turn":
            async with paid_call(stores=stores, bucket="turn", provider="openrouter"):
                keyed_client("openrouter")
        case "bench":
            await grade_line_bench(stores=stores, hop=_HELD_HOP, acting="bench")
        case "noise-floor":
            await measure_noise_floor(stores=stores, hop=_HELD_HOP, k=RescoreCount(2))
        case "seed-screen":
            write_yaml(
                stores.tenant_datasets.dataset_dir("d") / DEFINITION_FILE,
                {
                    "pipeline": {"backend_type": "termnorm"},
                    "campaign": {"optimization": {"degradation_threshold": 0.4}},
                },
            )
            await screen_inner_seeds(
                stores=stores, identity=stores.identity, dataset_name="d", seeds=[1], n_samples=1
            )
        case "probe-reasoning":
            await probe_reasoning("openai/gpt-4o", stores=stores)


_SPENT_THE_COUPON = {
    "model": "openai/gpt-4o",
    "input_tokens": 1_000,
    "output_tokens": 500,
    "cost_usd": _FACE.usd_amount,
}
_UNPRICEABLE = {
    "model": "openai/gpt-oss-20b:nitro",
    "input_tokens": 400_000,
    "output_tokens": 100_000,
}
_UNBOUNDED = (None, None)


@pytest.mark.usefixtures("run_bound")
def test_a_price_off_our_rate_table_is_recorded_beside_spend_never_as_spend(
    tmp_path: Path, offline: Router
) -> None:
    """TWO units, because a price needs a rate on file and a token count never does (decisions.md § 0003)."""
    offline.rates = {
        "openrouter/openai/gpt-4o": {"input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6}
    }
    assert pricing.refresh_rates(force=True), "the world's price list was not fetched"
    usage = TokenAccount(input=400_000, output=100_000)
    price = telemetry.call_price(
        "optimizer",
        usage,
        model="openai/gpt-4o",
        provider="openrouter",
        cost_usd=None,
        recorded=None,
    )
    telemetry.append_record(
        billed_call(
            price.cost_usd,
            rate_priced_usd=price.rate_priced_usd,
            input_tokens=usage.input,
            output_tokens=usage.output,
        )
    )
    priced = billed_spend([tmp_path / "bound.jsonl"]).host
    assert (priced.used_usd, priced.unpriced_tokens) == (0.0, 0), (
        "a price off our rate table was recorded as spend, or its tokens as unpriced"
    )
    assert priced.rate_priced_usd == pytest.approx(400_000 * 1e-6 + 100_000 * 2e-6), (
        "a price off our rate table was not recorded beside spend"
    )


def _doors() -> tuple[tuple[Any, ...], ...]:
    """Each paid door's admission per account: the coupon bounds only what the box's keys pay."""
    full = Budget(usd=_FACE.usd, tokens=_FACE.tokens)
    hand_set = Budget(usd=50.0, tokens=50_000)
    rows: tuple[tuple[Any, ...], ...] = (
        # door, issuer, face, sends (None: a backend's cells), declared, running, spent,
        # admitted, reserve
        # A hand-set grant of the default face: off the free tier, whose ceiling binds below.
        ("launch", _WEB, full, None, None, None, None, _UNBOUNDED, (0.30, _FACE.tokens)),
        # A USD ceiling holds no tokens: every send under it is priced, so the dollars bound it.
        ("launch", _WEB, full, None, 0.15, None, None, (0.15, None), (0.30, None)),
        ("launch", _WEB, full, None, 10.0, None, None, HostAllowanceExhaustedError, None),
        # The free tier's ceiling bounds what a launch holds, a lift's included (bl2.B5).
        ("launch", _WEB, None, None, None, None, None, (0.03, None), (0.06, None)),
        # A run none of whose sends the box's keys pay is refused nothing and reserves nothing.
        ("launch", _WEB, None, _OWN_ONLY, 10.0, None, None, (10.0, None), (0.0, 0)),
        (
            "launch",
            _WEB,
            None,
            _OWN_ONLY,
            None,
            {"admitted": False, "reserve": Budget()},
            None,
            _UNBOUNDED,
            (0.0, 0),
        ),
        (
            "launch",
            _WEB,
            None,
            None,
            None,
            {"admitted": True, "reserve": full},
            None,
            HostAllowanceExhaustedError,
            None,
        ),
        (
            "launch",
            _WEB,
            None,
            None,
            None,
            {"admitted": False, "reserve": Budget()},
            None,
            QuotaExceededError,
            None,
        ),
        (
            "launch",
            _WEB,
            full,
            None,
            None,
            None,
            _UNPRICEABLE,
            _UNBOUNDED,
            (settings.UNPRICED_GRACE_USD, _FACE.token_amount - 500_000),
        ),
        ("launch", None, None, None, None, None, None, _UNBOUNDED, _UNBOUNDED),
        ("launch", _WEB, hand_set, None, 10.0, None, None, (10.0, None), (20.0, None)),
        (
            "launch",
            _WEB,
            None,
            None,
            None,
            None,
            _SPENT_THE_COUPON,
            HostAllowanceExhaustedError,
            None,
        ),
        ("launch", _WEB, None, _OWN_ONLY, None, None, _SPENT_THE_COUPON, _UNBOUNDED, (0, 0)),
        # A provider no key pays is refused first, on any account, before anything is held.
        (
            "launch",
            None,
            None,
            (*_CELLS, ProviderSend("groq", None)),
            None,
            None,
            None,
            NoApiKeyError,
            None,
        ),
        # Cells no rate bounds: refused under the run's dollar cap or the coupon's, never in tokens.
        ("launch", None, None, _UNPRICED_CELLS, 10.0, None, None, BudgetUnenforceableError, None),
        ("launch", _WEB, None, _UNPRICED_CELLS, None, None, None, BudgetUnenforceableError, None),
        ("launch", None, None, _UNPRICED_CELLS, None, None, None, _UNBOUNDED, _UNBOUNDED),
        # An own key frees its provider's sends, never a backend's cells.
        (
            "launch",
            _WEB,
            None,
            (*_CELLS, *_OWN_ONLY),
            None,
            None,
            _SPENT_THE_COUPON,
            HostAllowanceExhaustedError,
            None,
        ),
        *(
            (
                door,
                _WEB,
                None,
                None,
                None,
                None,
                _SPENT_THE_COUPON,
                HostAllowanceExhaustedError,
                None,
            )
            for door in ("turn", "bench", "noise-floor", "seed-screen", "probe-reasoning")
        ),
    )
    return rows


@pytest.mark.usefixtures("run_bound", "offline")
def test_every_paid_door_admits_its_declared_ceiling_and_holds_what_the_coupon_has_left(
    tmp_path: Path,
) -> None:
    for n, (
        door,
        issuer,
        face,
        sends,
        declared,
        running,
        spent,
        admits,
        reserves,
    ) in enumerate(_doors()):
        at = tmp_path / f"door-{n}"
        at.mkdir()
        user = User(user_id="sub-9", tenant_id="sub-9", created_at="2026-01-01")
        stores = _account_stores(at, issuer=issuer, user=user, spent=spent, face=face)
        registry = JobRegistry(stores.jobs_dir, capacity=lambda _live: 1)
        if running is not None:
            _running(stores, registry, **cast("dict[str, Any]", running))
        if isinstance(admits, type):
            with pytest.raises(admits) as refused:
                if door == "launch":
                    admit_launch(
                        declared=Budget() if declared is None else Budget(usd=declared),
                        user=user,
                        stores=stores,
                        job_registry=registry,
                        job_id="job-a",
                        hop=None,
                        sends=_CELLS if sends is None else sends,
                    )
                else:
                    asyncio.run(_paid_door(door, stores))
            # The refusal names the ceiling that binds: only the run's own is the run's to drop.
            if isinstance(refused.value, BudgetUnenforceableError):
                binds = refused.value.details["binds"]
                assert binds == ("allowance" if issuer else "run"), f"door {n}: names {binds}"
            continue
        knocked, reserve = admit_launch(
            declared=Budget() if declared is None else Budget(usd=declared),
            user=user,
            stores=stores,
            job_registry=registry,
            job_id="job-a",
            hop=None,
            sends=_CELLS if sends is None else sends,
        )
        assert (knocked.usd, knocked.tokens) == admits, (
            f"door {n}: a launch was admitted under another ceiling than its campaign declared"
        )
        assert (reserve.usd, reserve.tokens) == pytest.approx(reserves), (
            f"door {n}: the coupon held back another amount than it has left for the run"
        )


@pytest.mark.usefixtures("run_bound", "offline")
def test_a_cycle_retaken_after_a_killed_bench_pass_closes_its_dead_bracket(
    tmp_path: Path,
) -> None:
    user = User(user_id="sub-9", tenant_id="sub-9", created_at="2026-01-01")
    killed = _account_stores(tmp_path / "killed", issuer=_WEB, user=user, spent=None)
    CycleEventLog.open(CycleDir(killed.campaigns.cycle_dir(_HELD_HOP))).append(
        PhaseRecord(phase=CampaignPhase.BENCH, event="enter")
    )

    async def _retaken() -> bool:
        async with paid_pass(
            stores=killed, verb="bench", hop=_HELD_HOP, declared=Budget(), sends=_CELLS
        ):
            return line_run(killed, _HELD_HOP).pass_open

    assert not asyncio.run(_retaken()), (
        "a cycle retaken after a killed bench pass read the dead bracket as a pass in flight"
    )


@pytest.mark.usefixtures("run_bound", "offline")
def test_an_accounts_own_key_wins_and_the_boxs_key_sends_only_on_a_live_coupon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Per provider; the box's refusal names the provider an own key would free.
    user = User(user_id="sub-9", tenant_id="sub-9", created_at="2026-01-01")
    spent_out = _account_stores(
        tmp_path / "keys", issuer=_WEB, user=user, spent=_SPENT_THE_COUPON, own_key="openrouter"
    )
    own = paying_key(spent_out.api_keys.load(), "openrouter")
    assert own is not None and (own.paid_by, own.secret.get_secret_value()) == ("user", "sk-own"), (
        "an account's own key lost to the box's"
    )
    asyncio.run(_paid_door("turn", spent_out))

    async def _box_paid() -> None:
        async with paid_call(stores=spent_out, bucket="turn", provider="openai"):
            keyed_client("openai")

    with pytest.raises(HostAllowanceExhaustedError) as box_paid:
        asyncio.run(_box_paid())
    assert (box_paid.value.details["reason"], box_paid.value.details["provider"]) == (
        LaunchRefusalReason.USED_UP,
        "openai",
    ), "a send past the coupon was refused for another cause, or named no provider to key"
    spent_out.charges.append(
        Bill(
            hold_id="own",
            key_source="user",
            price=CallPrice(
                kind="optimizer",
                model="gpt-x",
                provider="openrouter",
                cost_usd=5.0,
                rate_priced_usd=None,
            ),
            tokens=9_000_000,
        )
    )
    wallet = account_wallet(spent_out)
    assert (wallet.headroom.usd, wallet.state) == (0.0, AllowanceState.USED_UP), (
        "the coupon read another remainder than the box's keys left it"
    )
    status = coupon_status(spent_out)
    assert status.own_key_spend.billed_usd == pytest.approx(5.0), "the own key's spend went unread"
    payers = {route.provider: route.payer for route in status.routes}
    assert (payers["openrouter"], payers["openai"]) == ("user", "host"), (
        "the served payer of a provider is not the one its sends go out on"
    )
    fresh = _account_stores(tmp_path / "fresh", issuer=_WEB, user=user, spent=None)
    with monkeypatch.context() as patched:
        patched.setattr(settings, "GROQ_API_KEY", SecretStr(""))
        assert provider_send(fresh.api_keys.load(), "groq").payer is None, (
            "a provider no key holds read as paid"
        )


@pytest.mark.usefixtures("run_bound", "offline")
def test_in_flight_the_reserve_binds_only_a_send_on_the_boxs_key() -> None:
    book = spend_book(None, usd_reserve=0.05)
    send = SendBound(input_tokens=1, output_tokens=1, usd=0.04)
    _drawn(book, 1.0, key_source="user", lapsed=False)
    assert book.exhausted() is None, "a run spending its own key stopped at the host's coupon"
    book.hold(send, _OPTIMIZER, what="o", paid_by="host", ledger="o")
    with pytest.raises(HostAllowanceExhaustedError) as past:
        book.hold(send, _OPTIMIZER, what="o", paid_by="host", ledger="o")
    assert run_stop_reason(past.value) is StopReason.HOST_ALLOWANCE, (
        "a run stopped past the coupon read as another stop"
    )
    book.hold(send, _OPTIMIZER, what="o", paid_by="user", ledger="o")


@pytest.mark.usefixtures("run_bound", "offline")
def test_a_compare_arms_uncounted_bench_sends_still_draw_the_coupons_reserve(
    tmp_path: Path,
) -> None:
    send = SendBound(input_tokens=1, output_tokens=1, usd=0.04)
    compared = CycleEventLog(tmp_path / "compared.jsonl")
    compared.append(billed_call(0.03, kind="bench"))
    graded = spend_book(None, usd_reserve=0.05, meters="search_incurred", metered_on=compared.path)
    with pytest.raises(HostAllowanceExhaustedError):
        graded.hold(send, _BENCH, what="b", paid_by="host", ledger="b")
    with pytest.raises(SendRefusedError) as unpriced:
        graded.hold(
            SendBound(input_tokens=1, output_tokens=1, usd=None),
            CallLabel("d", "diagnostic"),
            what="d",
            paid_by="host",
            ledger="d",
        )
    assert unpriced.value.category is ErrorCategory.NO_RATE, "an unpriced bench send went out"


@pytest.mark.usefixtures("run_bound", "offline")
def test_the_free_tier_holds_an_ungranted_web_account_and_no_other(tmp_path: Path) -> None:
    user = User(user_id="sub-9", tenant_id="sub-9", created_at="2026-01-01")
    hand_set = Budget(usd=50.0, tokens=50_000)
    for n, (issuer, face, on_tier) in enumerate(
        ((_WEB, None, True), (None, None, False), (_WEB, hand_set, False))
    ):
        at = tmp_path / f"tier-{n}"
        at.mkdir()
        stores = _account_stores(at, issuer=issuer, user=user, spent=None, face=face)
        assert free_tier.on_free_tier(stores) is on_tier, (
            f"account {n} sits on the wrong side of the tier"
        )


class _FreeFence(NamedTuple):
    boxed: free_tier.FreeFence
    keyed: free_tier.FreeFence
    oss: str
    config: CampaignConfig
    schema: PipelineSchema
    layered: CampaignConfig


@pytest.fixture
def free_fence(tmp_path: Path) -> _FreeFence:
    """A web account's fence on the box's keys and on its own key, and a check-in laid under it."""
    user = User(user_id="sub-9", tenant_id="sub-9", created_at="2026-01-01")
    boxed = free_tier.free_fence(
        _account_stores(tmp_path / "boxed", issuer=_WEB, user=user, spent=None), "termnorm"
    )
    keyed = free_tier.free_fence(
        _account_stores(
            tmp_path / "keyed", issuer=_WEB, user=user, spent=None, own_key="openrouter"
        ),
        "termnorm",
    )
    assert boxed is not None and keyed is not None and boxed.task and keyed.task, (
        "a backend's cells, paid by the box whatever the account holds, left the tier's fence"
    )
    oss = "openai/gpt-oss-20b:nitro"
    config = _budgeted(Budget(usd=None))
    schema = parse_pipeline_response(
        {
            "nodes": {
                "llm_only": {
                    "config": {"model": oss},
                    "optimizer": {
                        "param_keys": ["reasoning_effort"],
                        "observation_mappings": [{"pipeline_key": "answer", "is_llm": True}],
                    },
                }
            },
            "pipelines": {"default": ["llm_only"]},
        }
    )
    layered = free_tier.with_free_tier_layer(config, schema, boxed)
    return _FreeFence(boxed, keyed, oss, config, schema, layered)


@pytest.mark.usefixtures("run_bound", "offline")
def test_the_tier_fences_a_provider_only_where_the_boxs_key_pays_it(
    free_fence: _FreeFence,
) -> None:
    boxed, keyed, _, _, _, _ = free_fence
    assert "openrouter" in boxed.providers and "openrouter" not in keyed.providers, (
        "the tier fenced a provider the account's own key pays, or freed one the box pays"
    )


@pytest.mark.usefixtures("run_bound")
def test_a_dollar_ceiling_admits_only_a_route_a_rate_prices(offline: Router) -> None:
    offline.rates = {"openai/dear": {"input_cost_per_token": 5e-8, "output_cost_per_token": 6e-7}}
    assert pricing.refresh_rates(force=True), "the world's price list was not fetched"
    assert (pricing.route_priced("x", "openai"), pricing.route_priced("dear", "openai")) == (
        False,
        True,
    ), "a dollar ceiling would admit a route no rate prices"


@pytest.mark.usefixtures("run_bound", "offline")
def test_a_free_check_in_measures_on_twenty_cells_at_its_models_lowest_rung(
    free_fence: _FreeFence,
) -> None:
    boxed, _, _, _, schema, layered = free_fence
    assert (
        layered.sp_budget_origin,
        layered.optimization.nodes["adaptive_queue"].config["sp_budget_round"],
        free_tier.free_tier_overlay(layered, schema, boxed),
    ) == (20, 20, {"llm_only": {"reasoning_effort": "low"}}), (
        "a free check-in measured on more cells, or launched above its model's lowest rung"
    )


@pytest.mark.usefixtures("run_bound", "offline")
def test_a_searched_model_axis_offers_and_starts_the_tiers_models_alone(
    free_fence: _FreeFence,
) -> None:
    boxed, _, oss, config, _, _ = free_fence
    searched = parse_pipeline_response(
        {
            "nodes": {
                "llm_only": {
                    "config": {"model": oss},
                    "optimizer": {
                        "param_keys": ["model", "reasoning_effort"],
                        "observation_mappings": [{"pipeline_key": "answer", "is_llm": True}],
                    },
                }
            },
            "pipelines": {"default": ["llm_only"]},
            "available_models": ["openai/dear", *free_tier.free_tier().task_models],
        }
    )
    tiered = free_tier.with_free_tier_layer(config, searched, boxed)
    offered = searched.narrow(tiered.optimizer_narrowing)
    llm_only = offered.get_node("llm_only")
    assert llm_only is not None and offered.model_options(llm_only) == list(
        free_tier.free_tier().task_models
    ), "a free check-in offers a model the tier does not list"
    launched = PipelineParams.model_validate({"steps": ["llm_only"], "llm_only": {"model": oss}})
    refused_models = [
        what
        for run in (config, tiered)
        for what in free_tier.outside_free_tier(
            run, select_optimizer(run.optimization), launched, searched, boxed
        )
        if what.startswith("the task model")
    ]
    assert refused_models == ["the task model openai/dear"], (
        "Start refused a model the free check-in offered, or ran one the tier does not list"
    )


@pytest.mark.usefixtures("run_bound", "offline")
def test_start_refuses_on_the_free_tier_what_its_fence_leaves_outside(
    free_fence: _FreeFence,
) -> None:
    boxed, keyed, oss, config, schema, layered = free_fence

    def optimizer_on(effort: str) -> CampaignConfig:
        luna = {"model": "openai/gpt-6-luna", "reasoning_effort": effort}
        overlay = {"optimization": {"nodes": {"l1_generate": {"config": luna}}}}
        return CampaignConfig.model_validate(
            merge_config_layers(layered.model_dump(mode="json", exclude_unset=True), overlay)
        )

    for run, effort, steps, fence, outside in (
        (layered, "low", ["llm_only"], boxed, 0),
        (layered, "none", ["llm_only"], boxed, 1),
        # The axis left open: the optimizer could search any rung, so Start refuses it.
        (config, "low", ["llm_only"], boxed, 1),
        (layered, "low", ["web_search", "llm_only"], boxed, 1),
        (optimizer_on("medium"), "low", ["llm_only"], boxed, 0),
        (optimizer_on("high"), "low", ["llm_only"], boxed, 1),
        (optimizer_on("high"), "low", ["llm_only"], keyed, 0),
        (layered, "none", ["llm_only"], keyed, 1),
    ):
        params = {"steps": steps, "llm_only": {"model": oss, "reasoning_effort": effort}}
        selected = select_optimizer(run.optimization)
        off = free_tier.outside_free_tier(
            run, selected, PipelineParams.model_validate(params), schema, fence
        )
        assert len(off) == outside, f"Start ran {off or params} on the free tier, or refused it"
    with pytest.raises(PayloadInvalidError):
        free_tier.refuse_outside_free_tier(off)


@pytest.mark.usefixtures("run_bound", "offline")
def test_a_later_layer_naming_another_model_drops_the_earlier_layers_route() -> None:
    routed: dict[str, object] = {
        "pipeline_overlay": {"llm_only": {"model": "a/x", "route_order": ["Groq"]}}
    }
    moved = merge_config_layers(routed, {"pipeline_overlay": {"llm_only": {"model": "b/y"}}})
    assert moved["pipeline_overlay"] == {"llm_only": {"model": "b/y"}}, (
        "a campaign layer moved the model under a route naming the old model's hosts"
    )


@pytest.mark.usefixtures("run_bound", "offline")
def test_the_fence_walks_every_call_a_run_sends_a_judges_model_included(
    free_fence: _FreeFence,
) -> None:
    boxed, _, oss, _, schema, layered = free_fence
    judge = {"name": "j", "stages": [{"model": "openai/dear", "provider": "openrouter"}]}
    judged = CampaignConfig.model_validate(
        merge_config_layers(
            layered.model_dump(mode="json", exclude_unset=True), {"judges": {"j": judge}}
        )
    )
    tiered_params = PipelineParams.model_validate(
        {"steps": ["llm_only"], "llm_only": {"model": oss, "reasoning_effort": "low"}}
    )
    assert free_tier.outside_free_tier(
        judged, select_optimizer(judged.optimization), tiered_params, schema, boxed
    ) == ["the judge model openai/dear"], "a judge on a model the tier does not list ran free"


@pytest.mark.usefixtures("run_bound", "offline")
def test_a_paid_verb_never_bills_a_cycle_its_producer_still_holds(tmp_path: Path) -> None:
    operator = User(user_id="op", tenant_id="op", created_at="")
    held_stores = _account_stores(tmp_path / "held", issuer=None, user=operator, spent=None)
    cycle_dir = held_stores.campaigns.cycle_dir(_HELD_HOP)
    hold_cycle(cycle_dir)

    async def bill_held() -> None:
        async with paid_pass(
            stores=held_stores, verb="bench", hop=_HELD_HOP, declared=Budget(), sends=_CELLS
        ):
            pass

    with pytest.raises(CycleBusyError):
        asyncio.run(bill_held())
    CycleEventLog.open(CycleDir(cycle_dir)).append(
        RunPhaseRecord.stop(StopReason.PAUSED, cause=PauseCause.INTERRUPT)
    )
    with pytest.raises(CycleBusyError):
        asyncio.run(bill_held())
    release_cycle(cycle_dir)
    asyncio.run(bill_held())


def _web_account(tmp_path: Path, sub: str) -> Stores:
    return workspace(
        tmp_path,
        IdentityContext(
            user_id=UserId(sub),
            tenant_id=TenantId(sub),
            issuer=Issuer(_WEB),
            claims={},
            capabilities=frozenset(),
        ),
    )


@pytest.mark.usefixtures("run_bound", "offline")
def test_a_scoring_pass_holds_a_slot_and_its_reservation_against_a_launch_beside_it(
    tmp_path: Path,
) -> None:
    passing = _web_account(tmp_path, "sub-8")
    passing.grant.save(CouponGrant(face=Budget(usd=1.0)))
    payer = account_of(passing)
    slots = JobRegistry.attach(passing.jobs_dir)

    def launch_beside() -> Budget:
        launch = slots.request_slot(
            user_id="sub-8",
            principal_id="sub-8",
            workspace=passing.campaigns.workspace,
            holder=CampaignLaunch("ds1"),
        )
        try:
            return admit_launch(
                declared=Budget(),
                user=payer,
                stores=passing,
                job_registry=slots,
                job_id=launch.job_id,
                hop=None,
                sends=_CELLS,
            )[1]
        finally:
            slots.release(launch.job_id, StopReason.CONVERGED)

    async def pass_beside() -> None:
        async with paid_pass(
            stores=passing, verb="verify", hop=None, declared=Budget(), sends=_CELLS
        ) as pass_book:
            assert pass_book.reserve.usd == pytest.approx(1.0), (
                "a pass declared nothing and did not hold the coupon's headroom"
            )
            assert pass_book.allotment.usd is None, "a pass that declared nothing took a ceiling"
            assert len(machine_status(slots, identity=passing.identity).running) == 1, (
                "a paid pass ran beside the machine's slot count"
            )
            with pytest.raises(HostAllowanceExhaustedError):
                launch_beside()

    asyncio.run(pass_beside())
    assert not machine_status(slots, identity=passing.identity).running, (
        "a pass kept its slot past its exit"
    )
    assert launch_beside().usd == pytest.approx(1.0), "the pass kept its reservation past its exit"


# The uncap arms over the round cap and token ceiling the changes before it moved.
@pytest.mark.usefixtures("run_bound", "offline")
def test_a_budget_change_leaves_the_axis_it_did_not_touch_alone(tmp_path: Path) -> None:
    metered = _web_account(tmp_path, "sub-9")
    hop = CycleHop(campaign_id="camp-3", cycle_id="cycle_budget0000")
    campaign = minted_campaign(hop)
    metered.campaigns.create_campaign(campaign)
    registry = JobRegistry(tmp_path / "jobs-3", capacity=lambda _live: 1)
    job = registry.request_slot(
        user_id="sub-9",
        principal_id="sub-9",
        workspace=metered.campaigns.workspace,
        holder=CampaignLaunch("ds1"),
        hop=hop,
    )
    assert job.holds_slot, "an empty box must hand out a slot, not a place in line"
    _admitted(metered, registry, job.job_id, Budget(usd=0.30, tokens=5_000_000))
    hold_moved(metered, hop, Budget(usd=0.15, tokens=2_500_000))
    dispatcher = CommandDispatcher(metered, registry)
    _change_run_limits(dispatcher, hop, Budget(rounds=3))
    _change_run_limits(dispatcher, hop, Budget(tokens=1_000))
    held = registry.get(job.job_id)
    assert held is not None and _reserve_of(metered, job.job_id) == Budget(
        usd=0.30, tokens=2_000
    ), (
        "the untouched USD reservation was released, or the token one did not move with its "
        "ceiling: as much again for the calls out"
    )
    assert cycle_share(metered, held) == Budget(usd=0.15, tokens=1_000), (
        "the moved token ceiling was not re-shared, or the untouched USD share moved with it"
    )
    standing = metered.campaigns.read_standing_budget(hop)
    assert standing.budget == Budget(rounds=3), (
        "the standing record is written WHOLE: a spend move must carry the round cap it did not touch"
    )
    assert metered.campaigns.read_campaign_ceiling(campaign.root_hop) == Budget(tokens=1_000), (
        "a money ceiling landed on one cycle, not the campaign every cycle shares"
    )
    # An UNCAP lifts the campaign's ceiling; the account still binds it through the reservation.
    armed = _change_run_limits(dispatcher, hop, Budget(usd=None))
    assert armed == Budget(usd=None, tokens=1_000, rounds=3), (
        "the armed ceiling is not what a stop fires at: the campaign's money and the cycle's own"
    )
    lifted, held = metered.campaigns.read_campaign_ceiling(hop), registry.get(job.job_id)
    assert lifted == Budget(usd=None, tokens=1_000), (
        "an uncapped campaign ceiling was stored as the account's headroom"
    )
    assert held is not None and cycle_share(metered, held).usd is None, (
        "an uncap became a bounded share"
    )
    assert _reserve_of(metered, job.job_id).usd is not None, (
        "an uncap left the account nothing held back for the run"
    )


@pytest.mark.usefixtures("run_bound", "offline")
def test_a_moved_ceiling_counts_every_cycles_spend_against_the_campaign_once(
    tmp_path: Path,
) -> None:
    # A fork's walked prefix not twice, and what the parent billed past the cut counts too.
    forker = _web_account(tmp_path, "sub-7")
    root = CycleHop(campaign_id="camp-7", cycle_id="cycle_budget0007")
    fork = CycleHop(campaign_id="camp-7", cycle_id="cycle_budget0007_fork_r1")
    forker.campaigns.create_campaign(minted_campaign(root))
    forker.campaigns.mint_cycle(root)

    def bill(at: CycleHop, usd: float) -> None:
        with scoped(account=forker):
            ledger = CycleEventLog.open(CycleDir(forker.campaigns.cycle_dir(at)))
            _pay(ledger, usd, input_tokens=1_000, output_tokens=0)

    bill(root, 0.1)
    root_dir = forker.campaigns.cycle_dir(root)
    forker.campaigns.mint_fork_cycle(
        fork,
        root_dir,
        ForkSpec(
            trigger=ForkTrigger.OPERATOR_REWIND,
            reason="",
            issued_by=SYSTEM_PRINCIPAL,
            from_candidate=RoundArm(round=0, arm=ArmKey("origin")),
        ),
        cut=CycleEventLog.open(CycleDir(root_dir)).next_offset,
        cut_round=1,
    )
    bill(root, 0.1)
    bill(fork, 0.3)
    registry = JobRegistry(tmp_path / "jobs-7", capacity=lambda _live: 1)
    job = registry.request_slot(
        user_id="sub-7",
        principal_id="sub-7",
        workspace=forker.campaigns.workspace,
        holder=CampaignLaunch("ds1"),
        hop=fork,
    )
    _admitted(forker, registry, job.job_id, Budget(usd=0.8))
    hold_moved(forker, fork, Budget(usd=0.8))
    forker.grant.save(CouponGrant(face=Budget(usd=1.0)))
    allowance = account_of(forker)
    # A changed ceiling is admitted as a launch is: dollars its cells cannot be held to refuse.
    with pytest.raises(BudgetUnenforceableError):
        change_budget(
            Budget(usd=5.0), stores=forker, job_registry=registry, hop=fork, sends=_UNPRICED_CELLS
        )
    change_budget(Budget(usd=5.0), stores=forker, job_registry=registry, hop=fork, sends=_CELLS)
    caps = forker.campaigns.read_campaign_ceiling(fork)
    _, relaunched = admit_launch(
        declared=Budget(),
        user=allowance,
        stores=forker,
        job_registry=registry,
        job_id=job.job_id,
        hop=fork,
        sends=_CELLS,
    )
    assert caps.usd == relaunched.usd == pytest.approx(1.0), (
        "$1.00 allowance, $0.50 of it the campaign's own: its ceiling is the whole $1.00, which "
        "already counts that $0.50 — not $1.10 off a fork prefix counted twice"
    )
    assert _reserve_of(forker, job.job_id).usd == pytest.approx(1.0), (
        "the reservation moved off the ceiling it backs"
    )


@pytest.mark.usefixtures("run_bound", "offline")
def test_of_two_set_limits_raises_the_second_reads_the_wallet_after_the_firsts_reserve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Set-limits is an admission, so the reserves stand within the face.
    racer = _web_account(tmp_path, "sub-8")
    racer.grant.save(CouponGrant(face=Budget(usd=0.30)))
    racing = JobRegistry(tmp_path / "jobs-8", capacity=lambda _live: 2)
    raced = [CycleHop(campaign_id=f"camp-8{n}", cycle_id=f"cycle_race80{n}") for n in (1, 2)]
    for racing_hop in raced:
        racer.campaigns.create_campaign(minted_campaign(racing_hop))
        racer.campaigns.mint_cycle(racing_hop)
        held_job = racing.request_slot(
            user_id="sub-8",
            principal_id="sub-8",
            workspace=racer.campaigns.workspace,
            holder=CampaignLaunch("ds1"),
            hop=racing_hop,
        )
        _admitted(racer, racing, held_job.job_id, Budget(usd=0.0))
        hold_moved(racer, racing_hop, Budget(usd=0.0))
    first_read, second_at_gate, go = threading.Event(), threading.Event(), threading.Event()
    wallet_of, gate_of = quota.read_account_wallet, racing.admission_gate
    raises: list[BaseException] = []

    def paused_wallet(**kwargs: Any) -> Any:
        read = wallet_of(**kwargs)
        if threading.current_thread() is first and not first_read.is_set():
            first_read.set()
            go.wait(timeout=60)
        return read

    def watched_racing_gate() -> Any:
        if threading.current_thread() is second:
            second_at_gate.set()
        return gate_of()

    def raise_on(at: CycleHop) -> None:
        try:
            change_budget(Budget(usd=0.20), stores=racer, job_registry=racing, hop=at, sends=_CELLS)
        except QuotaExceededError as exc:
            raises.append(exc)

    first = threading.Thread(target=raise_on, args=(raced[0],))
    second = threading.Thread(target=raise_on, args=(raced[1],))
    with monkeypatch.context() as patched:
        patched.setattr(quota, "read_account_wallet", paused_wallet)
        patched.setattr(racing, "admission_gate", watched_racing_gate)
        first.start()
        assert first_read.wait(timeout=60), "the first raise never read the wallet"
        second.start()
        assert second_at_gate.wait(timeout=60), "the second raise never reached the gate"
        go.set()
        first.join(timeout=60)
        second.join(timeout=60)
    reserved = sum(
        _reserve_of(racer, job.job_id).usd or 0.0
        for job in racing.list_holding_spend(user_id="sub-8")
    )
    assert reserved <= 0.30 + 1e-9, (
        f"two set-limits reserved ${reserved:.2f} on a $0.30 coupon: the second read the wallet "
        "before the first's reserve landed"
    )


# Each headroom reads the holds and bills the steps before it left on the account.
@pytest.mark.usefixtures("run_bound", "offline")
def test_a_live_run_holds_its_reserve_less_what_it_billed_until_its_hold_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Its spend counts once, so a launch beside it is quoted what the coupon can still pay.
    biller = _web_account(tmp_path, "sub-6")
    biller.grant.save(CouponGrant(face=Budget(usd=0.30)))
    billed_hop = CycleHop(campaign_id="camp-6b", cycle_id="cycle_billed01")
    biller.campaigns.create_campaign(minted_campaign(billed_hop))
    biller.campaigns.mint_cycle(billed_hop)
    billing = JobRegistry(biller.jobs_dir, capacity=lambda _live: 2)
    live = billing.request_slot(
        user_id="sub-6",
        principal_id="sub-6",
        workspace=biller.campaigns.workspace,
        holder=CampaignLaunch("ds1"),
        hop=billed_hop,
    )
    _admitted(biller, billing, live.job_id, Budget(usd=0.20))
    live_book = SpendBook(
        declared=Budget(),
        share=Budget(),
        charges=biller.charges,
        hold_id=live.job_id,
        headroom=Budget(),
        metered_on=None,
        meters="bill",
        opened=time.monotonic(),
    )
    with scoped(account=biller):
        billed_ledger = CycleEventLog.open(CycleDir(biller.campaigns.cycle_dir(billed_hop)))
        _pay(billed_ledger, 0.08, input_tokens=1_000, output_tokens=0, book=live_book)
    # A fork rebases the run onto another cycle: its bills draw its hold by the hold tree, never by
    # the ledger they landed on.
    biller.charges.append(
        Hold(
            hold_id="forked",
            parent=live.job_id,
            key_source="host",
            bound=Budget(usd=0.02),
            resource="optimizer",
            node="o",
            ledger="campaigns/camp-6b/cycles/cycle_forked01/events.jsonl",
        )
    )
    biller.charges.append(
        Bill(
            hold_id="forked",
            key_source="host",
            price=CallPrice(
                kind="optimizer",
                model="gpt-x",
                provider="openai",
                cost_usd=0.02,
                rate_priced_usd=None,
            ),
            tokens=10,
        )
    )
    assert quota.account_wallet(biller).headroom.usd == pytest.approx(0.10), (
        "a live run's billed $0.10 was held twice: once billed, once inside its $0.20 reserve"
    )
    # Liveness is the book's: the run's end closes its hold, and a send it left open is then
    # unreported at its bound; a call on no run holds the coupon for every other launch.
    biller.charges.append(
        Hold(
            hold_id="left",
            parent=live.job_id,
            key_source="host",
            bound=Budget(usd=0.05),
            resource="optimizer",
            node="o",
            ledger="campaigns/camp-6b/cycles/cycle_forked01/events.jsonl",
        )
    )
    assert quota.account_wallet(biller).headroom.usd == pytest.approx(0.10), (
        "a send open under a live run's reserve was held twice"
    )
    stamped = billing.get(live.job_id)
    assert stamped is not None
    RunStart(biller, billing, stamped).release(RuntimeError("the run raised"))
    assert quota.account_wallet(biller).headroom.usd == pytest.approx(0.15), (
        "an ended run kept its reserve, or the send it left open went unread"
    )
    # A call on no run is held under its own job, which holds what its calls out hold.
    chat = billing.create(
        user_id="sub-6",
        principal_id="sub-6",
        workspace=biller.campaigns.workspace,
        hop=UNRESOLVED_HOP,
        holder=PaidCall("turn"),
    )
    _admitted(biller, billing, chat.job_id, Budget())
    biller.charges.append(
        Hold(
            hold_id="turn",
            parent=chat.job_id,
            key_source="host",
            bound=Budget(usd=0.04),
            resource="optimizer",
            node="o",
            ledger="chat/events.jsonl",
        )
    )
    assert quota.account_wallet(biller).headroom.usd == pytest.approx(0.11), (
        "a call on no run held nothing of the coupon"
    )
    # A dead producer's hold closes at the next wallet read, on a box no sweep runs on.
    crashed = billing.request_slot(
        user_id="sub-6",
        principal_id="sub-6",
        workspace=biller.campaigns.workspace,
        holder=CampaignLaunch("ds1"),
        hop=billed_hop,
    )
    _admitted(biller, billing, crashed.job_id, Budget(usd=0.05))
    assert quota.account_wallet(biller).headroom.usd == pytest.approx(0.06)
    unreported = money_on(biller.charges.path).unreported.host.unreported_usd
    with monkeypatch.context() as dead:
        dead.setattr(registry_module, "producer_alive", lambda *_: False)
        assert quota.account_wallet(biller).headroom.usd == pytest.approx(0.11), (
            "a crashed run held the coupon until a sweep"
        )
    money = money_on(biller.charges.path)
    assert chat.job_id not in money.open and money.unreported.host.unreported_usd == (
        pytest.approx(unreported + 0.04)
    ), "a dead chat turn's open send stayed held, never read as unreported"


# Each act counts the campaigns and launches the acts before it minted.
@pytest.mark.usefixtures("run_bound", "offline")
def test_a_head_to_head_is_one_door_that_prices_admits_and_answers_its_arms_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PROMPTPOTTER_HOME", str(tmp_path / "duel"))
    monkeypatch.setenv(PROVIDER_KEYS[0], NOT_A_KEY)
    stores = local_stores(tenant=None)
    write_yaml(
        stores.tenant_datasets.dataset_dir("duel") / DEFINITION_FILE,
        {
            "pipeline": {
                "backend_type": "termnorm",
                "nodes": {
                    "llm_only": {"config": {"provider": "groq", "model": "openai/gpt-oss-120b"}}
                },
            },
            "campaign": {"optimization": {"degradation_threshold": 0.4}},
        },
    )
    bank = Bank.of(samples(60, "TRUE"))
    stores.tenant_datasets.put_bank(bank)
    stores.tenant_datasets.relabel("duel", bank.dataset_hash)
    detached: list[JobSpec] = []

    async def ran_elsewhere(held: HeldRun, mode: RunMode) -> asyncio.Task[None]:
        """The arm's own process, run and exited: its slot handed back, nothing spent."""
        spec = held.job_spec(mode)
        detached.append(spec)
        held.start.job_registry.release(spec.job_id, StopReason.CONVERGED)
        return asyncio.create_task(asyncio.sleep(0))

    monkeypatch.setattr(run_job_mod, "spawn_job", ran_elsewhere)

    def campaigns() -> list[str]:
        return sorted(c.campaign_id for c in stores.campaigns.list_campaigns(lifecycle="all"))

    def asked(h2h: str, arms: dict[str, str], budget: Budget) -> MintHeadToHeadPayload:
        return MintHeadToHeadPayload(
            dataset_name="duel",
            head_to_head=HeadToHeadRequest(
                head_to_head_id=h2h,
                arms=[
                    ArmRequest(arm_key=key, optimization=OptimizationOverrides(optimizer=opt))
                    for key, opt in arms.items()
                ],
                budget=budget,
            ),
        )

    # A dollar cap is priced before any arm takes a key, on the cell's bound where no run has
    # billed one yet: a cap one round outspends mints nothing.
    pair = {"a": "capo", "b": "potter"}
    with pytest.raises(PayloadInvalidError) as short:
        cheap = asked("duel-2", pair, Budget(usd=0.0001, rounds=1))
        asyncio.run(CommandDispatcher(stores).dispatch(CommandCall(cheap)))
    assert short.value.code == "spend_cap_below_round", "guard: refused for its round"
    assert not campaigns(), "an arm whose round outspends its cap took a key"

    # The terminal's verb: one optimizer twice, apart by a knob, each arm bound by the one budget.
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "promptpotter",
            "--json",
            "head-to-head",
            "duel",
            "--head-to-head",
            "duel-1",
            "--arm",
            "capo=capo",
            "--arm",
            "capo-b3=capo,nodes.blocks.config.max_blocks=3",
            "--max-usd",
            "50",
            "--max-rounds",
            "1",
        ],
    )
    campaign_runner.main()
    answered = LaunchAnswer.model_validate_json(capsys.readouterr().out).launches
    arms = mint.standing_arms(stores, "duel-1")
    assert [launch.hop for launch in answered] == [arms[k].root_hop for k in ("capo", "capo-b3")], (
        "the terminal answered other arms than the head-to-head records"
    )
    assert [spec.hop for spec in detached] == [launch.hop for launch in answered], (
        "an arm ran in no process of its own, or in another arm's cycle"
    )
    assert len({c.root_content_hash for c in arms.values()}) == 1, (
        "two arms of one head-to-head searched from different origins"
    )
    ran = {k: cycle_config(stores, c, c.root_hop).config.optimization for k, c in arms.items()}
    assert {k: (o.budget.usd, o.budget.rounds) for k, o in ran.items()} == {
        "capo": (50.0, 1),
        "capo-b3": (50.0, 1),
    }, "an arm ran another budget than the one every arm declared"
    blocks = {k: o.nodes["blocks"].config for k, o in ran.items() if "blocks" in o.nodes}
    assert blocks == {"capo-b3": {"max_blocks": 3}}, (
        "an arm's knob reached its sibling, or never its own arm"
    )

    # Past the account's concurrent ceiling, no arm is minted or keeps a slot: none or all.
    minted = campaigns()
    trio = asked("duel-3", {"a": "capo", "b": "capo", "c": "capo"}, Budget(usd=50.0, rounds=1))
    with pytest.raises(QuotaExceededError):
        asyncio.run(CommandDispatcher(stores).dispatch(CommandCall(trio)))
    assert campaigns() == minted, "an arm past the account's ceiling minted a campaign"
    assert not JobRegistry.attach(stores.jobs_dir).list_running(), "a refused arm kept its slot"

    # A retry under the act's key, at the REST door, answers the arms its act minted.
    registry = JobRegistry.attach(stores.jobs_dir)
    funded = asked("duel-2", pair, Budget(usd=50.0, rounds=1))
    route = next(r for r in commands_api.commands_router.routes if r.name == "head-to-head")  # type: ignore[attr-defined]
    answers = [
        asyncio.run(
            route.endpoint(job_registry=registry, stores=stores, payload=funded, key="duel-2-once")  # type: ignore[attr-defined]
        )
        for _ in range(2)
    ]
    assert answers[0] == answers[1], "a retry under the act's key answered other arms"
    assert len(campaigns()) == len(minted) + 2 and len(detached) == 4, (
        "a retry under the act's key minted or launched its arms again"
    )


# 6. Spend outlives what spent it


def _pay(
    ledger: CycleEventLog,
    cost_usd: float,
    *,
    input_tokens: int,
    output_tokens: int,
    book: SpendBook | None = None,
) -> None:
    """One send through the one admission every paid call takes, on *ledger*, under *book*."""
    bound_send = SendBound(input_tokens=input_tokens, output_tokens=output_tokens, usd=cost_usd)
    with (
        scoped(ledger=ledger),
        scoped(book=spend_book() if book is None else book),
        admitted(
            CallLabel("backend", "backend"),
            bound_send,
            model="openai/gpt-4o",
            provider="openrouter",
            paid_by="host",
        ) as send,
    ):
        send.settle(Billed(TokenAccount(input=input_tokens, output=output_tokens), cost_usd))


@pytest.fixture
def account_bound(built_stores: Stores) -> Iterator[None]:
    with scoped(account=built_stores):
        yield


class _SpentCampaign(NamedTuple):
    root: str
    stub: str
    retried: str
    stub_ledger: Path
    retried_ledger: Path
    spent_cycle: Callable[..., Path]
    account_usd: Callable[[], float]
    cleanup: Callable[[str], bool]
    ceiling_counts_now: Callable[[], tuple[float, int]]
    tombstones: Callable[[], list[SpendTombstoneRecord]]


@pytest.fixture
def spent_campaign(built_stores: Stores) -> _SpentCampaign:
    """camp-2's root and two forks off it, each spent on the box's key, and readers of that spend."""
    stores = built_stores
    root = "cycle_root0000"
    stub, retried = f"{root}_fork_aaaa", f"{root}_fork_bbbb"
    stores.campaigns.create_campaign(minted_campaign(CycleHop(campaign_id="camp-2", cycle_id=root)))

    def spent_cycle(cycle_id: str, *, cost_usd: float, campaign_id: str = "camp-2") -> Path:
        hop = CycleHop(campaign_id=campaign_id, cycle_id=cycle_id)
        stores.campaigns.mint_cycle(hop)
        cycle_dir = stores.campaigns.cycle_dir(hop)
        _pay(
            CycleEventLog.open(CycleDir(cycle_dir)), cost_usd, input_tokens=2_000, output_tokens=800
        )
        return CycleLayout(cycle_dir).ledger

    spent_cycle(root, cost_usd=0.07)
    stub_ledger = spent_cycle(stub, cost_usd=0.11)
    retried_ledger = spent_cycle(retried, cost_usd=0.05)

    def account_usd() -> float:
        return account_bill(stores.charges).host.used_usd

    def cleanup(cycle_id: str) -> bool:
        return cleanup_stub_fork_if_empty(
            campaign_store=stores.campaigns, hop=CycleHop(campaign_id="camp-2", cycle_id=cycle_id)
        )[0]

    def tombstones() -> list[SpendTombstoneRecord]:
        workspace = CycleEventLog(CycleEventLog.workspace_path(stores.campaigns.workspace))
        return [r for _, r in workspace.iter() if isinstance(r, SpendTombstoneRecord)]

    def ceiling_counts_now() -> tuple[float, int]:
        counted = stores.campaigns.metered_spend("camp-2", "bill")
        return counted.usd_amount, counted.token_amount

    return _SpentCampaign(
        root,
        stub,
        retried,
        stub_ledger,
        retried_ledger,
        spent_cycle,
        account_usd,
        cleanup,
        ceiling_counts_now,
        tombstones,
    )


@pytest.mark.usefixtures("account_bound")
def test_the_account_and_the_campaigns_ceiling_read_what_its_cycles_billed(
    spent_campaign: _SpentCampaign,
) -> None:
    _, _, _, _, _, _, account_usd, _, ceiling_counts_now, _ = spent_campaign
    before = account_usd()
    assert before == pytest.approx(0.07 + 0.11 + 0.05), (
        "the account read another sum than its cycles billed"
    )
    ceiling_counts = ceiling_counts_now()
    assert ceiling_counts == pytest.approx((before, 3 * 2_800)), (
        "the campaign's ceiling read another sum than its cycles metered"
    )


@pytest.mark.usefixtures("account_bound")
def test_an_admission_whose_hold_cannot_be_written_frees_its_room_and_charges_nothing(
    built_stores: Stores, monkeypatch: pytest.MonkeyPatch, spent_campaign: _SpentCampaign
) -> None:
    stores = built_stores
    root, _, _, _, _, _, account_usd, cleanup, _, _ = spent_campaign
    before = account_usd()

    def unwritable_line(_book: ChargeBook, _line: object) -> None:
        raise OSError("the account's disk is full")

    root_hop = CycleHop(campaign_id="camp-2", cycle_id=root)
    bill, book = account_bill(stores.charges), spend_book(1.0)
    room = book.money_left()
    with (
        scoped(ledger=CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(root_hop)))),
        monkeypatch.context() as patched,
        scoped(book=book),
        pytest.raises(OSError),
    ):
        patched.setattr(ChargeBook, "append", unwritable_line)
        with admitted(
            CallLabel("backend", "backend"),
            SendBound(input_tokens=2_000, output_tokens=800, usd=0.5),
            model="openai/gpt-4o",
            provider="openrouter",
            paid_by="host",
        ):
            pass
    assert account_bill(stores.charges) == bill, "a send never made left its hold on the bill"
    assert book.money_left() == room, "a send never made kept its room held"
    assert not cleanup(root) and account_usd() == pytest.approx(before), (
        "a cycle the delete REFUSES must not be banked: it keeps its rows, the same money twice"
    )


@pytest.mark.usefixtures("account_bound")
def test_a_ledger_refusing_a_sends_first_part_keeps_its_later_parts_on_the_account(
    built_stores: Stores, monkeypatch: pytest.MonkeyPatch, spent_campaign: _SpentCampaign
) -> None:
    stores = built_stores
    root, _, _, _, _, _, account_usd, _, _, _ = spent_campaign
    root_hop = CycleHop(campaign_id="camp-2", cycle_id=root)
    before = account_usd()

    def unappendable(*_: object) -> None:
        raise OSError("the ledger's disk is full")

    with (
        scoped(ledger=CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(root_hop)))),
        monkeypatch.context() as patched,
        scoped(book=spend_book(1.0)),
        pytest.raises(OSError),
    ):
        patched.setattr("promptpotter.infrastructure.llm.spend_book.append_record", unappendable)
        with admitted(
            CallLabel("backend", "backend"),
            SendBound(input_tokens=2_000, output_tokens=800, usd=0.5),
            model="openai/gpt-4o",
            provider="openrouter",
            paid_by="host",
        ) as parts:
            parts.settle(
                Billed(TokenAccount(input=10, output=5), 0.02),
                Billed(TokenAccount(input=10, output=5), 0.03),
            )
    before += 0.05
    assert account_usd() == pytest.approx(before), (
        "a ledger that refused a send's first part left its later parts off the account"
    )


@pytest.mark.usefixtures("account_bound")
def test_deleting_a_spent_stub_fork_keeps_its_money_on_the_account_and_the_ceiling(
    spent_campaign: _SpentCampaign,
) -> None:
    _, stub, _, stub_ledger, _, _, account_usd, cleanup, ceiling_counts_now, _ = spent_campaign
    before, ceiling_counts = account_usd(), ceiling_counts_now()
    assert cleanup(stub) and not stub_ledger.exists(), "a spent stub fork survived its delete"
    assert account_usd() == pytest.approx(before), "deleting a spent stub fork un-spent it"
    assert ceiling_counts_now() == pytest.approx(ceiling_counts), (
        "deleting a spent stub fork handed its money back to the campaign's ceiling"
    )


@pytest.mark.usefixtures("account_bound")
def test_a_crash_retried_between_bank_and_delete_counts_the_fork_once(
    built_stores: Stores, spent_campaign: _SpentCampaign
) -> None:
    stores = built_stores
    _, _, retried, _, retried_ledger, _, account_usd, cleanup, ceiling_counts_now, _ = (
        spent_campaign
    )
    before, ceiling_counts = account_usd(), ceiling_counts_now()
    retried_hop = CycleHop(campaign_id="camp-2", cycle_id=retried)
    for _crashed_attempt in range(2):
        bank_spend(
            workspace=stores.campaigns.workspace,
            cycle_dirs=[stores.campaigns.cycle_dir(retried_hop)],
            subject=stores.subject(retried_hop),
            metered={
                meter: stores.campaigns.cycle_metered(retried_hop, meter)
                for meter in get_args(CeilingMeter)
            },
        )
    assert cleanup(retried) and not retried_ledger.exists(), "a banked fork survived its delete"
    assert account_usd() == pytest.approx(before), (
        "banking precedes the delete: a retry after a crash in between must find the tombstone"
    )
    assert ceiling_counts_now() == pytest.approx(ceiling_counts), (
        "a crash retried between bank and delete counted the cycle's metered spend twice, or none"
    )


# The pass and the manifest-less delete meet the campaign the torn delete left.
@pytest.mark.usefixtures("account_bound")
def test_a_delete_torn_at_its_tree_commits_at_its_tombstone_and_admits_nothing_after(
    built_stores: Stores, monkeypatch: pytest.MonkeyPatch, spent_campaign: _SpentCampaign
) -> None:
    stores = built_stores
    root, _, _, _, _, _, account_usd, _, ceiling_counts_now, _ = spent_campaign
    root_hop = CycleHop(campaign_id="camp-2", cycle_id=root)
    before, ceiling_counts = account_usd(), ceiling_counts_now()
    camp_dir = stores.campaigns.campaign_root_dir("camp-2")
    spent_before = campaign_spend(stores.campaigns, stores.charges, "camp-2")
    owned_sandbox = inner_sandbox(stores, "camp-2", root)
    with monkeypatch.context() as patched:

        def torn(path: Path) -> None:
            if path == camp_dir:
                raise OSError(f"killed while removing {path}")
            rmtree_robust(path)

        patched.setattr(campaign_store_module, "rmtree_robust", torn)
        with pytest.raises(OSError):
            stores.campaigns.delete_campaign(
                "camp-2",
                keep_results=False,
                changed_at="2026-01-02T00:00:00Z",
                inner_sandbox_root=owned_sandbox.parent,
            )
    assert camp_dir.is_dir() and not owned_sandbox.exists(), (
        "a delete torn at its tree stranded the campaign's inner sandbox, which no retry reaches"
    )
    assert campaign_spend(stores.campaigns, stores.charges, "camp-2") == spent_before, (
        "a torn delete's rows counted beside its tombstone: the campaign's spend read twice"
    )
    torn_read = stores.campaigns.load_campaign("camp-2")
    assert torn_read is not None and torn_read.lifecycle_status == "deleted", (
        "a torn delete's detail read the manifest's own status past its tombstone"
    )
    assert ceiling_counts_now() == pytest.approx(ceiling_counts), (
        "a torn delete's rows metered beside its tombstone: the ceiling refuses at half its room"
    )

    # The slot door refuses a run or a pass on it: nothing it would spend past its tombstone
    # reaches a sum, and no job of it holds a slot.
    async def bench_pass() -> None:
        async with paid_pass(
            stores=stores, verb="bench", hop=root_hop, declared=Budget(usd=1.0), sends=()
        ):
            raise AssertionError("a pass on a deleted campaign was admitted")

    with pytest.raises(ConflictError) as retired:
        asyncio.run(bench_pass())
    assert retired.value.code == "campaign_not_active", "a deleted campaign admitted a pass"
    assert not JobRegistry.attach(stores.jobs_dir).list_running(), (
        "a refused pass left its job holding a slot"
    )
    graded_under = _budgeted(Budget()).model_copy(
        update={"scoring": {"per_sample": "label_match(predicted, ground_truth)"}}
    )
    served = cycle_bench(stores, torn_read, root_hop, graded_under).status
    assert (served.can_run, served.refusal) == (False, BenchRefusal.NOT_ACTIVE), (
        "the served bench offers a grade the pass's admission refuses"
    )
    # A campaign whose manifest is gone still deletes, and banks: nothing reads its rows as one.
    CampaignLayout(camp_dir).manifest.unlink()
    assert stores.campaigns.delete_campaign(
        "camp-2", keep_results=True, changed_at="2026-01-02T00:00:00Z"
    )
    assert not camp_dir.exists(), "a manifest-less campaign kept rows nothing marks deleted"
    assert account_usd() == pytest.approx(before), (
        "banked by the destroyer itself, under BOTH `keep_results` arms, so no caller can skip it"
    )


@pytest.mark.usefixtures("account_bound")
def test_a_reclaimed_orphan_sandbox_banks_only_the_spend_it_did_not_carry_out(
    built_stores: Stores, spent_campaign: _SpentCampaign
) -> None:
    stores = built_stores
    tombstones = spent_campaign.tombstones
    # An inner sandbox is a SIBLING of the tenant tree, so no account-wide walk reaches it.
    sandbox = inner_sandbox(stores, SANDBOX_CAMPAIGN, "orphaned-outer-cycle")
    inner_cycle = sandbox / "tenant" / "campaigns" / "innerds__20260101-000000" / "cycles"
    inner_ledger = CycleEventLog.open(CycleDir(inner_cycle / "inner-cycle-0"))
    # One call carried out, one not: only the second is still this sandbox's to bank.
    for input_tokens, cost_usd, mirrored in ((400, 0.10, True), (600, 0.15, False)):
        inner_ledger.append(
            billed_call(
                cost_usd,
                kind="backend",
                node="llm_only",
                model="openai/gpt-oss-20b",
                provider="openrouter",
                input_tokens=input_tokens,
                output_tokens=100,
                duration_s=1.0,
                mirrored=mirrored,
            )
        )
    banked_before = len(tombstones())
    assert reclaim_orphan_sandboxes(stores.projects_root) == 1, (
        "an orphan sandbox was not reclaimed"
    )
    assert not sandbox.exists(), "a reclaimed sandbox survived"
    banked = tombstones()[banked_before:]
    assert [b.spent["host"].used_tokens for b in banked] == [600 + 100] and [
        b.spent["host"].used_usd for b in banked
    ] == pytest.approx([0.15]), (
        "a forwarded inner cycle was banked twice, or its own spend not at all"
    )


@pytest.mark.usefixtures("account_bound")
def test_a_drafts_sends_are_charged_and_a_starts_claim_holds_off_a_discard(
    built_stores: Stores, spent_campaign: _SpentCampaign
) -> None:
    stores = built_stores
    account_usd = spent_campaign.account_usd

    def spent_draft(draft_id: str, cost_usd: float) -> Path:
        draft_dir = stores.drafts.draft_dir(draft_id)
        _pay(CycleEventLog.open(CycleDir(draft_dir)), cost_usd, input_tokens=300, output_tokens=100)
        return draft_dir

    draft = new_draft(
        slug="drafted",
        basis=DatasetBasis(dataset="drafted"),
        n_samples=1,
        sample_preview=[],
        headers=[],
    )
    stores.drafts.write_draft(draft)
    undrafted = account_usd()
    draft_dir = spent_draft(draft.draft_id, 0.04)
    with_draft = account_usd()
    assert with_draft == pytest.approx(undrafted + 0.04), "a draft's check-in calls went unbilled"

    def command(
        payload: DiscardDraftPayload | StartCheckinPayload | EditDraftCampaignPayload,
    ) -> object:
        return asyncio.run(CommandDispatcher(stores).dispatch(CommandCall(payload)))

    # An edit racing a Start's mint is refused while the claim holds, and finds nothing once the
    # draft closed: it never lands inside the mint nor writes the closed draft's dir back.
    edit = EditDraftCampaignPayload(
        draft_id=draft.draft_id, patch=EditDraftPatch(raw_task_description="raced")
    )
    held_ledger, held_draft = stores.drafts.ledger(draft.draft_id), draft_dir / "draft.json"
    ledger_before, draft_before = held_ledger.read_bytes(), held_draft.read_bytes()
    claim = stores.drafts.claim(draft.draft_id)
    for refused in (
        DiscardDraftPayload(draft_id=draft.draft_id),
        StartCheckinPayload(draft_id=draft.draft_id),
        edit,
    ):
        with pytest.raises(ConflictError):
            command(refused)
    assert draft_dir.is_dir(), "a discard refused under a Start's claim still took the draft"
    assert held_ledger.read_bytes() == ledger_before, "an edit under a Start's claim was recorded"
    assert held_draft.read_bytes() == draft_before, "an edit landed on a draft a Start holds"
    claim.release()
    command(DiscardDraftPayload(draft_id=draft.draft_id))
    assert not draft_dir.exists(), "a discard left the draft's dir, or wrote it back"
    with pytest.raises(NotFoundError):
        command(edit)
    assert not draft_dir.exists(), "an edit on a closed draft wrote its dir back"
    assert account_usd() == pytest.approx(with_draft), "discarding a spent draft un-spent it"
    with pytest.raises(NotFoundError):
        command(StartCheckinPayload(draft_id=draft.draft_id))
    with pytest.raises(NotFoundError):
        stores.drafts.claim(draft.draft_id)
    assert not draft_dir.exists(), "a Start on a discarded draft wrote its dir back"


class _Framing(NamedTuple):
    framing: Session
    declared: RunDeclaration
    rows: list[Sample]


@pytest.fixture
def unframed_session(built_stores: Stores) -> _Framing:
    """A session on the drafted dataset, declared with no task framing."""
    stores = built_stores
    unframed = {"optimization": {"degradation_threshold": 0.4}}
    write_yaml(
        stores.tenant_datasets.dataset_dir("drafted") / DEFINITION_FILE,
        {"pipeline": {"backend_type": "termnorm"}, "campaign": unframed},
    )
    rows = samples(30)
    solve = PipelineNode(name="solve", tunes_llm=False, prompt_info=PROMPT_INFO)
    framing = loop_session(
        stores,
        pipeline_schema([solve]),
        rows,
        dataset=resolve_dataset(stores, "drafted"),
    )
    declared = mint.declare(framing, CampaignConfig.model_validate(unframed), purpose=Optimize())
    return _Framing(framing, declared, rows)


@pytest.mark.usefixtures("account_bound")
def test_a_framing_paid_before_its_cycle_exists_stays_billed_past_its_refused_mint(
    built_stores: Stores,
    monkeypatch: pytest.MonkeyPatch,
    spent_campaign: _SpentCampaign,
    unframed_session: _Framing,
) -> None:
    stores = built_stores
    account_usd = spent_campaign.account_usd
    framing, declared, rows = unframed_session
    # The account before the framing bills.
    with_draft = account_usd()

    async def framed(*_: object, ledger: CycleEventLog, **__: object) -> None:
        _pay(ledger, 0.03, input_tokens=500, output_tokens=200)
        raise PayloadInvalidError("refused after the framing", code="refused")

    monkeypatch.setattr(mint, "commit_task_framing", framed)
    with pytest.raises(PayloadInvalidError):
        asyncio.run(mint.mint_framed_cycle(framing, declared, rows, task_text="frame this"))
    assert not draft_dirs(stores.campaigns.workspace), "an unminted framing left its ledger"
    assert account_usd() == pytest.approx(with_draft + 0.03), "a refused mint un-spent its framing"


@pytest.mark.usefixtures("account_bound")
def test_a_mint_that_dies_before_its_commit_leaves_no_campaign_to_list_run_or_bill(
    built_stores: Stores, monkeypatch: pytest.MonkeyPatch, unframed_session: _Framing
) -> None:
    stores = built_stores
    framing, declared, rows = unframed_session

    def _dies(*_: object) -> None:
        raise OSError("torn")

    torn_plan = mint.resolve_cycle_plan(framing, declared, rows)
    with monkeypatch.context() as tearing, pytest.raises(OSError, match="torn"):
        tearing.setattr(type(stores.campaigns), "mint_cycle", _dies)
        mint.mint(torn_plan, campaign_id="torn__000001", adopting=None)
    assert stores.campaigns.load_campaign("torn__000001") is None, (
        "a mint that died before its cycle left a campaign with none"
    )


@pytest.mark.usefixtures("account_bound")
def test_a_reset_over_a_reshaped_ledger_drops_it_and_keeps_the_accounts_money(
    built_stores: Stores, spent_campaign: _SpentCampaign
) -> None:
    stores = built_stores
    root, _, _, _, _, spent_cycle, account_usd, _, _, _ = spent_campaign
    root_ledger = CycleEventLog.open(
        CycleDir(stores.campaigns.cycle_dir(CycleHop(campaign_id="camp-2", cycle_id=root)))
    )
    # The sends the Activity pane sums beside the cycles': a two-part send, a draft's, a framing's.
    with (
        scoped(ledger=root_ledger),
        scoped(book=spend_book(1.0)),
        admitted(
            CallLabel("backend", "backend"),
            SendBound(input_tokens=2_000, output_tokens=800, usd=0.5),
            model="openai/gpt-4o",
            provider="openrouter",
            paid_by="host",
        ) as parts,
    ):
        parts.settle(
            Billed(TokenAccount(input=10, output=5), 0.02),
            Billed(TokenAccount(input=10, output=5), 0.03),
        )
    _pay(root_ledger, 0.04, input_tokens=300, output_tokens=100)
    _pay(root_ledger, 0.03, input_tokens=500, output_tokens=200)
    framed_total = account_usd()
    stores.campaigns.create_campaign(minted_campaign(CycleHop(campaign_id="camp-3", cycle_id=root)))
    reshaped = spent_cycle(root, cost_usd=0.13, campaign_id="camp-3")
    lines = [json.loads(line) for line in reshaped.read_text(encoding="utf-8").splitlines()]
    for line in lines:
        line.pop("money", None)
    write_jsonl(reshaped, lines)
    with pytest.raises(LedgerUnreadableError):
        billed_spend([reshaped])
    apply_reset(plan_reset(stores.projects_root, tenant_id=None, staged={}))
    assert not stores.campaigns.iter_campaign_dirs(), "a reset kept a campaign it cannot read"
    assert account_usd() == pytest.approx(framed_total + 0.13), (
        "a reset over a reshaped ledger dropped the account's spend with it"
    )
    activity = account_activity(stores, window="1d", group_by="model")
    assert activity.total_spend_usd == pytest.approx(account_usd()), (
        "the Activity pane read another bill than the account's charges"
    )
    assert activity.total_tokens == 4 * 2_800 + 400 + 700 + 2 * 15, (
        "the Activity pane lost the sends of what was deleted, reset or never minted"
    )


@pytest.mark.usefixtures("account_bound")
def test_a_settle_whose_ledger_write_fails_still_closes_its_book_whole(
    built_stores: Stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = built_stores
    root = "cycle_root0000"

    # Its part billed and the remainder held, never the whole bound held again on top of the bill.
    def unwritable(_record: object, _ledger: object) -> None:
        raise OSError("the ledger's disk is full")

    after_reset = CycleHop(campaign_id="camp-4", cycle_id=root)
    stores.campaigns.create_campaign(minted_campaign(after_reset))
    stores.campaigns.mint_cycle(after_reset)
    book = spend_book(1.0)
    with (
        scoped(ledger=CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(after_reset)))),
        monkeypatch.context() as patched,
        scoped(book=book),
        pytest.raises(OSError),
        admitted(
            CallLabel("backend", "backend"),
            SendBound(input_tokens=2_000, output_tokens=800, usd=0.5),
            model="openai/gpt-4o",
            provider="openrouter",
            paid_by="host",
        ) as send,
    ):
        patched.setattr("promptpotter.infrastructure.llm.spend_book.append_record", unwritable)
        send.settle(
            Billed(TokenAccount(input=2_000, output=400), 0.2),
            unknown=SendBound(input_tokens=0, output_tokens=400, usd=0.1),
        )
    assert book.money_left().usd == pytest.approx(1.0 - 0.2 - 0.1), (
        "a settle that raised left its book holding another sum than it billed and kept held"
    )


@pytest.mark.usefixtures("account_bound")
def test_a_projection_raising_on_a_landed_bill_is_its_ledgers_one_warning(
    built_stores: Stores,
) -> None:
    stores = built_stores
    root = "cycle_root0000"

    # The part counts once, and the next bill lands with no second warning.
    class _Unwritable(Projection):
        def _handle_token_usage(self, record: TokenUsageRecord) -> None:  # noqa: ARG002 -- routed by its annotation
            raise OSError("the readout's disk is full")

    faulted = CycleHop(campaign_id="camp-5", cycle_id=root)
    stores.campaigns.create_campaign(minted_campaign(faulted))
    stores.campaigns.mint_cycle(faulted)
    faulted_ledger = CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(faulted)))
    book = spend_book(1.0)
    with (
        LedgerIndex.of(faulted_ledger.path).projecting() as held,
        scoped(ledger=faulted_ledger),
        scoped(book=book),
    ):
        held.add(_Unwritable())
        for _ in range(2):
            with admitted(
                CallLabel("backend", "backend"),
                SendBound(input_tokens=2_000, output_tokens=800, usd=0.5),
                model="openai/gpt-4o",
                provider="openrouter",
                paid_by="host",
            ) as send:
                send.settle(Billed(TokenAccount(input=2_000, output=400), 0.2))
    warned = [r.kind for _, r in faulted_ledger.iter() if isinstance(r, RoundWarningRecord)]
    assert book.money_left().usd == pytest.approx(1.0 - 2 * 0.2), (
        "a projection's raise on a landed bill counted its part twice, or unwound the next send"
    )
    assert warned == ["derived_file_stale"], "a projection's fault went unreported, or stormed"


@pytest.mark.usefixtures("account_bound")
def test_a_campaign_id_is_drawn_again_over_a_live_dir_a_tombstone_and_a_charged_id(
    built_stores: Stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A charged id: one a landing wiped whose charges still name its ledger.
    campaigns = built_stores.campaigns
    prefix = dataset_id_prefix("ds1")
    live = campaigns.campaign_root_dir(f"{prefix}__aaaaaa") / "cycles"
    live.mkdir(parents=True)
    (live / "mark").write_text("live", encoding="utf-8")
    gone = CycleHop(campaign_id=f"{prefix}__bbbbbb", cycle_id="cycle-0")
    CycleEventLog.open(CycleDir(campaigns.cycle_dir(gone))).append(billed_call(0.1))
    assert campaigns.delete_campaign(
        f"{prefix}__bbbbbb", keep_results=True, changed_at="2026-01-01T00:00:00Z"
    )
    wiped = CycleHop(campaign_id=f"{prefix}__dddddd", cycle_id="cycle-0")
    built_stores.charges.append(
        Hold(
            hold_id="wiped",
            parent=None,
            key_source="host",
            bound=Budget(usd=0.1),
            resource="backend",
            node="cell",
            ledger=built_stores.charges.held_on(campaigns.ledger(wiped)),
        )
    )
    drawn = iter(["aaaaaa", "bbbbbb", "dddddd", "cccccc"])
    monkeypatch.setattr("secrets.token_hex", lambda _n: next(drawn))
    assert campaigns.claim_campaign_id("ds1", built_stores.charges) == f"{prefix}__cccccc", (
        "a draw took a live campaign's, a deleted one's or a charged one's id"
    )
    assert [p.name for p in live.iterdir()] == ["mark"], "the draw wrote into a live campaign"
    assert (live / "mark").read_text(encoding="utf-8") == "live"
