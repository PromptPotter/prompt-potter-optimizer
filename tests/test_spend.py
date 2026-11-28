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
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Coroutine, Iterator
from pathlib import Path
from typing import Any, NamedTuple, NoReturn, cast, get_args

import httpx
import offline_world
import pytest
from factories import (
    LAUNCHED_DATASET,
    SANDBOX_CAMPAIGN,
    archive_row,
    arm_state,
    arm_walk,
    backend_client,
    billed_call,
    cycle_result,
    declaration_of,
    defaulted_config,
    fake_connector,
    filed,
    filed_under,
    inner_cells,
    inner_sandbox,
    jumping_clock,
    laid_ledger,
    launched_bank,
    ledger_records,
    measurement,
    on_jumping_clock,
    pipeline_schema,
    run_holding,
    run_wiring,
    samples,
    spend_book,
    workspace,
)
from fastapi import Response
from offline_world import (
    NOT_A_KEY,
    PROVIDER_KEYS,
    Hang,
    Router,
    Said,
    completion,
)
from pydantic import BaseModel, Field, SecretStr, ValidationError
from world import Shared, World, shared_run

from promptpotter import connectors
from promptpotter.application import free_tier
from promptpotter.application.account import account_of, local_stores, set_coupon
from promptpotter.application.bench.line import cycle_bench, live_cycle_run
from promptpotter.application.bench.resume_and_fork.fork_siblings import cut_operator_fork
from promptpotter.application.commands.dispatcher import (
    Applier,
    CommandCall,
    CommandDispatcher,
    DispatchedToCycle,
)
from promptpotter.application.commands.limits_and_queue import change_run_limits
from promptpotter.application.datasets.authored import ConfigLayers, effective_config
from promptpotter.application.datasets.draft_campaign import new_draft
from promptpotter.application.datasets.startability import offered_dataset
from promptpotter.application.head_to_head_price import (
    optimizer_round_price,
    priced_head_to_head,
)
from promptpotter.application.initialization.session import Session
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs import interlock, mint, quota
from promptpotter.application.jobs import registry as registry_mod
from promptpotter.application.jobs.account_activity import account_activity
from promptpotter.application.jobs.launcher import run_job as run_job_mod
from promptpotter.application.jobs.launcher.admission import (
    admit_and_hold,
    admit_and_share,
    admit_arms,
    paid_call,
    paid_pass,
    request_jobs,
    slotless_opening,
)
from promptpotter.application.jobs.launcher.launch import Inline, admit
from promptpotter.application.jobs.launcher.mint_and_start import ForkLaunch
from promptpotter.application.jobs.launcher.opening import Opening, claim_cycle, open_spend_book
from promptpotter.application.jobs.launcher.run_job import HeldLaunch, HeldRun, JobSpec
from promptpotter.application.jobs.machine_status import machine_status
from promptpotter.application.jobs.quota import (
    QuotaExceededError,
    RateLimitedError,
    account_wallet,
    admit_launch,
    coupon_status,
    declare_run_budget,
    sends_on,
)
from promptpotter.application.jobs.reaper import reclaim_orphan_sandboxes, sweep
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.jobs.shares import (
    change_budget,
    cycle_share,
    declare_round_allowance,
    hold_moved,
    hold_run,
    stand_held_budget,
)
from promptpotter.application.maintenance.archive_maintenance import purge_cold_store
from promptpotter.application.maintenance.landing import complete_registries
from promptpotter.application.maintenance.reset import apply_reset, plan_reset
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.pipeline_resolve import (
    Resolution,
    cycle_config,
    laid_config,
    laid_session,
)
from promptpotter.application.run_phase_control import budget_stop
from promptpotter.application.run_stop import StopLoop, panel_gate_tripped, run_stop_reason
from promptpotter.application.runner import entry as run_entry
from promptpotter.application.runner import loop
from promptpotter.application.runner.inner import spawn
from promptpotter.application.runner.inner.tasks import enter_inner_cell
from promptpotter.application.runner.loop import round_bounds
from promptpotter.application.scoring.cell_envelope import CellEnvelope
from promptpotter.application.scoring.judges import call as judge_call
from promptpotter.application.scoring.judges.registry import build_evaluators
from promptpotter.application.scoring.passes import grade_line_bench
from promptpotter.application.scoring.sample_measurement import (
    cell_billing,
    emit_replayed_step_tokens,
    measure_sample,
    measured_cell_price,
)
from promptpotter.application.served_dashboard import (
    ServedDashboard,
    current_caps,
    served_dashboard,
)
from promptpotter.config.paths import default_projects_root, jobs_dir
from promptpotter.config.settings import settings
from promptpotter.connectors.protocol import Connector, InProcessWorkload
from promptpotter.connectors.termnorm import TermNormSession
from promptpotter.domain.arms import CutKind
from promptpotter.domain.bench import PassRefusal
from promptpotter.domain.campaign import CampaignCycle, Optimize, RunDeclaration, ceiling_meter
from promptpotter.domain.campaign_config import (
    CAMPAIGN_RULE,
    CampaignConfig,
)
from promptpotter.domain.cells import PinnedCell
from promptpotter.domain.checkin_draft import DatasetBasis
from promptpotter.domain.command_kinds import (
    ArmRequest,
    ChangeRunLimitsPayload,
    DiscardDraftPayload,
    Door,
    EditDraftCampaignPayload,
    ForkCyclePayload,
    HeadToHeadRequest,
    MintHeadToHeadPayload,
    StartCheckinPayload,
)
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.cycle_result import CycleResult
from promptpotter.domain.dashboard_rows import ArmedCaps
from promptpotter.domain.draft_edit import EditDraftPatch, OptimizationOverrides
from promptpotter.domain.fitness_fold import cell_channels_of
from promptpotter.domain.head_to_head_plan import HeadToHeadPlan
from promptpotter.domain.job import (
    UNRESOLVED_HOP,
    CampaignLaunch,
    EmbeddedRun,
    LaunchMove,
    PaidCall,
)
from promptpotter.domain.judges import JudgeSpec, JudgeStage
from promptpotter.domain.l4.cells import InnerCells
from promptpotter.domain.l4.proxies import (
    OuterSampleProxies,
    inner_cell_facts,
    inner_cell_report,
)
from promptpotter.domain.layer_fold import UNBOUNDED, Layer, fold_layers
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.phases import CampaignPhase, PauseCause, StopReason
from promptpotter.domain.pipeline_params import NodeCall, PipelineParams
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import NodeKind, PipelineNode, PipelineSchema
from promptpotter.domain.records.run_records import (
    ArmRecord,
    Bill,
    CallPrice,
    ConfigOverrides,
    CycleMintedRecord,
    CycleShareRecord,
    CycleStart,
    ForkRemainder,
    ForkTrigger,
    Hold,
    Lapse,
    LaunchAnswer,
    LLMCallRecord,
    PhaseRecord,
    PricedKeyRecord,
    RebaseRequest,
    Release,
    RoundEnteredRecord,
    RoundWarningRecord,
    Running,
    RunPhaseRecord,
    SpendTombstoneRecord,
    StandingBudgetRecord,
    TokenUsageRecord,
)
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.sample import Bank, Sample
from promptpotter.domain.scoring import MeasuredCell, PipelineData
from promptpotter.domain.spend import (
    CYCLE_AXES,
    MONEY_AXES,
    Budget,
    CeilingMeter,
    HeldBudget,
    KeySource,
    SpendCount,
    SpendRollup,
    StepUsage,
    TokenAccount,
    TokenUsageKind,
)
from promptpotter.infrastructure import docker_host, holders
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm import base, pricing, telemetry
from promptpotter.infrastructure.llm.base import ChatMessage, ChatRequest
from promptpotter.infrastructure.llm.capabilities import (
    CAPABILITY_FILE,
    ensure_model_capabilities,
    resolve_model_capabilities,
    resolve_schema_menu,
)
from promptpotter.infrastructure.llm.money_fold import money_on
from promptpotter.infrastructure.llm.openai_compat import ChatReply, ReportedUsage
from promptpotter.infrastructure.llm.pricing import (
    PriceTier,
    Rate,
    RateCeiling,
)
from promptpotter.infrastructure.llm.send_failure import FailedSendError, failed_send
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
    booked_sum,
    bound_account,
    replied,
    reserved,
)
from promptpotter.infrastructure.llm.wallet import (
    AllowanceState,
    BackendCells,
    ProviderSend,
    Send,
    keyed_client,
    paying_key,
    provider_send,
    sends_draw_coupon,
)
from promptpotter.infrastructure.producer_lock import hold_cycle, release_cycle
from promptpotter.infrastructure.run_scope import (
    InnerSpawnContext,
    rescope,
    run_scope,
    scoped,
)
from promptpotter.infrastructure.store.account_spend import (
    account_bill,
    bank_spend,
    billed_spend,
    campaign_spend,
    ledger_cost,
    unreported_under,
    workspace_bank,
)
from promptpotter.infrastructure.store.campaign_store import store as campaign_store_module
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    LEDGER_SPEND,
    SPEND_BY_ROUND,
    STANDING_ROUNDS,
)
from promptpotter.infrastructure.store.dataset_access import DEFINITION_FILE
from promptpotter.infrastructure.store.io import (
    read_lines,
    rmtree_robust,
    take_lock,
    write_jsonl,
    write_yaml,
)
from promptpotter.infrastructure.store.layout import (
    CampaignLayout,
    CycleLayout,
    campaign_cycles_dir,
    dataset_id_prefix,
    draft_dirs,
)
from promptpotter.infrastructure.store.llm_reuse_cache import LLMReuseCache
from promptpotter.infrastructure.store.read_model import (
    LedgerIndex,
    LedgerSpan,
    Projection,
    last,
    read_line,
)
from promptpotter.infrastructure.store.session_pointer import cleanup_stub_fork_if_empty
from promptpotter.infrastructure.store.stores import Stores, build_stores, sandbox_store
from promptpotter.infrastructure.store.user_store import User
from promptpotter.infrastructure.store.wallet_store import ChargeBook
from promptpotter.presentation.api.routers import commands as commands_api
from promptpotter.presentation.cli import main as cli_main
from promptpotter.shared.errors import (
    BudgetUnenforceableError,
    CellHaltedError,
    CellInfrastructureError,
    CellSendRefusedError,
    CellUnscoreableError,
    ConflictError,
    CycleBusyError,
    ErrorCategory,
    FileUnreadableError,
    HostAllowanceExhaustedError,
    LaunchRefusalReason,
    MachineBusyError,
    NoApiKeyError,
    NotFoundError,
    PayloadInvalidError,
    SendRefusedError,
    ServiceUnavailableError,
)
from promptpotter.shared.identity import (
    SYSTEM_PRINCIPAL,
    IdentityContext,
    TenantId,
    UserId,
    acting_principal,
    default_identity,
)
from promptpotter.shared.measurement_context import MeasurementRole


@pytest.fixture
def run_bound(tmp_path: Path) -> Iterator[None]:
    with scoped(ledger=CycleEventLog(tmp_path / "bound.jsonl")), scoped(round=1):
        yield


def _admitted(stores: Stores, registry: JobRegistry, job_id: str, reserve: Budget) -> None:
    """Admitted as `admit_and_share` admits it: its hold on the account, then the stamp."""
    hold_run(stores, job_id, reserve, parent=None)
    registry.move(job_id, LaunchMove.ADMIT)


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
        user_id=str(stores.identity.user_id),
        principal_id=acting_principal(stores.identity).id,
        workspace=stores.campaigns.workspace,
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
    config = defaulted_config({"optimization": {"degradation_threshold": 0.05}})
    optimization = config.optimization.model_copy(update={"budget": budget})
    return config.model_copy(update={"optimization": optimization})


def _change_run_limits(
    dispatcher: CommandDispatcher, hop: CycleHop, budget: Budget, *, overshoot: bool | None = None
) -> Budget:
    """The ceiling the change armed: the campaign's money and the cycle's own axes."""
    payload = ChangeRunLimitsPayload(
        campaign_id=hop.campaign_id, cycle_id=hop.cycle_id, budget=budget, overshoot=overshoot
    )
    at = _at(dispatcher.stores, hop)
    dispatched = DispatchedToCycle(dispatcher, payload, "limits", at, door=Door.HOST)

    async def apply() -> Applier[ArmedCaps]:
        applier = await change_run_limits(dispatched)
        await applier.run()
        return applier

    asyncio.run(apply())
    return dispatcher.stores.campaigns.armed_budget(at)


def _at(stores: Stores, hop: CycleHop) -> CampaignCycle:
    """*hop* with its campaign as a launch's door loads it."""
    return stores.campaigns.campaign_cycle(hop)


def _opened(stores: Stores, net: Router) -> tuple[Session, CampaignConfig]:
    """*stores*' account's `LAUNCHED_DATASET` opened as every door opens it, its config laid."""
    launched_bank(stores, net, samples(90, "TRUE"))

    async def opening() -> tuple[Session, CampaignConfig]:
        async with init_services(
            dataset_name=LAUNCHED_DATASET, identity=stores.identity, stores=stores
        ) as session:
            assert session.dataset is not None, "a dataset session resolved its dataset"
            return laid_session(session, ConfigLayers.of(session.dataset, None))

    return asyncio.run(opening())


def _minted(stores: Stores, net: Router, layer: dict[str, Any] | None = None) -> Session:
    """A campaign of *stores*' account minted through the real door, nothing run: the opened
    session, *layer* laid over its config, the plan resolved and minted. The session is bound to
    its root cycle."""
    session, config = _opened(stores, net)
    if layer is not None:
        config = config.overlaid(Layer("campaign", layer))
    plan = mint.resolve_cycle_plan(session, config, Optimize(), session.bank.samples)
    return mint.mint(plan, campaign_id=mint.fresh_campaign_id(session), adopting=None).session


@pytest.fixture(scope="module")
def ran_24(tmp_path_factory: pytest.TempPathFactory) -> Shared:
    """One campaign on 24 rows, run once for every fact of this file that reads a run's line
    (~0.5 s once, ~25 ms a fact that adopts it, none one that reads it in place)."""

    async def act(world: World) -> CycleHop:
        return (await world.campaign(rows=24)).hop

    return shared_run(tmp_path_factory, act)


def _line(world: World, forks: int, ran: Shared | None = None) -> list[CycleHop]:
    """A campaign the world ran (*ran*'s copy, else its origin alone: another account mints its
    own), then *forks* cut off its origin by the operator's fork door."""
    if ran is None:
        root = asyncio.run(world.campaign(rounds=0, rows=24)).hop
    else:
        root, world = ran.made, world.adopt(ran)
    asked = ForkCyclePayload(campaign_id=root.campaign_id, cycle_id=root.cycle_id)
    cut = (cut_operator_fork(world.stores, asked, CycleStart()) for _ in range(forks))
    return [root, *(root.model_copy(update={"cycle_id": c.take(world.stores)}) for c in cut)]


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
    judge = JudgeStage(role="grade", temperature=0.0, model="grader-1", provider="openai")
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


def _listed(input_usd: float, output_usd: float, lister: str, **tiers: float) -> dict[str, Any]:
    return {
        "input_cost_per_token": input_usd,
        "output_cost_per_token": output_usd,
        "litellm_provider": lister,
        **tiers,
    }


# Upstream's rows, as its fetch strips them.
_LISTED_RATES: dict[str, dict[str, Any]] = {
    # DeepSeek's first-party key, character-for-character OpenRouter's model id.
    "deepseek/deepseek-v4-flash": _listed(0.00000014, 0.00000028, "deepseek"),
    "openrouter/openai/gpt-oss-20b": _listed(0.00000004, 0.00000015, "openrouter"),
    "groq/openai/gpt-oss-120b": _listed(0.00000015, 0.0000006, "groq"),
    "gpt-4o": _listed(
        0.0000025,
        0.00001,
        "openai",
        cache_creation_input_token_cost=0.000003125,
        cache_read_input_token_cost=0.00000025,
    ),
    # A bare id whose own colon is part of the model, not a route selector.
    "anthropic.claude-haiku-4-5-v1:0": _listed(0.000001, 0.000005, "bedrock"),
    "openrouter/openrouter/auto": _listed(0.000002, 0.000004, "openrouter"),
    "orphan-model": {"input_cost_per_token": 0.000001},
    "openai/half-listed": {"input_cost_per_token": 0.000001},
}


@pytest.fixture
def listed_rates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> Iterator[None]:
    """The world's price list, fetched over an empty floor."""
    offline.rates = dict(_LISTED_RATES)
    (tmp_path / "floor.json").write_text('{"models": {}}', encoding="utf-8")
    with monkeypatch.context() as patched:
        patched.setattr(pricing, "BUNDLED_PATH", tmp_path / "floor.json")
        assert pricing.refresh_rates(force=True).fault is None, (
            "the world's price list was not fetched"
        )
        yield


@pytest.mark.usefixtures("run_bound", "listed_rates")
def test_a_rate_belongs_to_its_provider_model_pair_never_the_model_alone() -> None:
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
        assert pricing.lookup_rate(model, provider) == rate, (
            f"{provider}/{model}: a rate belongs to the provider-model pair, not the model alone"
        )


@pytest.mark.usefixtures("run_bound", "listed_rates")
def test_a_key_prices_on_the_route_it_names_inline_and_a_call_no_row_prices_stays_unpriced() -> (
    None
):
    usd = pricing.compute_usd
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


@pytest.mark.usefixtures("run_bound", "listed_rates")
def test_a_cache_read_reprices_inside_input_and_a_row_with_no_cache_tier_bills_it_at_input() -> (
    None
):
    usd = pricing.compute_usd
    cold = usd("gpt-4o", 1000, 0, provider="openai")
    hit = usd("gpt-4o", 1000, 0, provider="openai", cache_read_tokens=800)
    assert (cold, hit) == pytest.approx((1000 * 0.0000025, 200 * 0.0000025 + 800 * 0.00000025)), (
        "a cache read is a SUBSET of the input count: re-priced out of it, never added on top"
    )
    assert usd(
        "openai/gpt-oss-20b", 1000, 0, provider="openrouter", cache_read_tokens=800
    ) == pytest.approx(usd("openai/gpt-oss-20b", 1000, 0, provider="openrouter")), (
        "no cache tier on the row: the read bills at the INPUT price, never a rate nobody sourced"
    )


@pytest.mark.usefixtures("run_bound")
def test_a_wire_cost_reaches_the_reply_and_a_bill_sums_the_parts_that_named_one() -> None:
    # OpenRouter's real shape: `cost` rides as an EXTRA on the usage object.
    def wired(usage: dict[str, Any] | None, usd: float | None = None) -> ChatReply:
        return ChatReply.model_validate(completion("{}", model="m", usage=usage, usd=usd))

    assert wired(None, usd=7.938e-05).cost == 7.938e-05, (
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
        assert booked_sum(*reported) == (None if billed is None else pytest.approx(billed)), (
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


@pytest.mark.usefixtures("run_bound")
async def test_a_truncated_attempt_keeps_its_token_split_past_the_reask_that_heals_it(
    offline: Router,
) -> None:
    # The healed answer is all the call returns: only the attempt's entry tells reasoning that
    # spent `max_tokens` from a runaway answer, and that evidence was paid for.
    spent = {"prompt_tokens": 9, "completion_tokens": 16000, "total_tokens": 16009}
    cut = completion('{"plan": "so the', model="m", usage=spent)
    cut["usage"]["completion_tokens_details"] = {"reasoning_tokens": 15000}
    cut["choices"][0]["finish_reason"] = "length"
    replies: Iterator[Said] = iter([cut, '{"answer": "a"}'])
    offline.llm.says = lambda _body: next(replies)
    with scoped(book=spend_book(), send_budget=SendBudget(None, attempts=2)):
        response = await keyed_client("groq").chat(
            ChatRequest(_MESSAGES, "m", response_model=_Reply), label=CallLabel("l1", "optimizer")
        )
    assert [
        (a.finish_reason, a.usage and (a.usage.output, a.usage.reasoning), a.chars)
        for a in response.schema_repair_errors
    ] == [("length", (16000, 15000), 16)], "a truncated attempt's split was lost to its re-ask"


@pytest.mark.usefixtures("run_bound")
async def test_an_ungraded_cell_bills_what_it_spent_and_a_replay_states_its_rows_price(
    tmp_path: Path, world: World
) -> None:
    async def ran_ungraded(*_args: Any) -> dict[str, Any]:
        spent = {"agent": {"input": 1200, "output": 300, "estimated": False, "cost_usd": 0.0076}}
        raise CellUnscoreableError("verifier timed out", spent=spent)

    cells = CycleEventLog(tmp_path / "cells.jsonl")
    unbounded = spend_book()
    in_process = fake_connector(
        "ungraded",
        execution="in_process",
        in_process_run=ran_ungraded,
        endpoint=None,
        pipeline_declaration=world.net.backend.declared,
    )
    async with world.opened(connector=in_process) as session:
        with scoped(ledger=cells), scoped(book=unbounded):
            row_ = await measure_sample(
                Sample(id=0, query="10452", ground_truth=None), session, PipelineParams()
            )
        with scoped(ledger=cells):
            emit_replayed_step_tokens(
                {"agent": StepUsage(input=1200, output=300, rate_priced_usd=0.25)}, {}
            )
    *ran, replayed = [r for r in ledger_records(cells) if isinstance(r, TokenUsageRecord)]
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


def test_budget_equality_is_over_the_axes_each_names() -> None:
    # A lift is no bound, and an omitted axis no lift.
    for one, other, same in (
        (Budget(usd=1.0), Budget(usd=1.0), True),
        (Budget(usd=UNBOUNDED), Budget(), False),
        (Budget(usd=1.0, tokens=UNBOUNDED), Budget(usd=1.0), False),
        (Budget(usd=1.0), Budget(usd=2.0), False),
    ):
        assert (one == other) is same and (hash(one) == hash(other)) is same, (
            f"{one!r} and {other!r} read {'apart' if same else 'as one'}: equality saw another "
            "set of axes than the ones each names"
        )
    assert Budget(usd=1.0).only(MONEY_AXES).named == {"usd"}, "only() named an axis self did not"


@pytest.mark.usefixtures("run_bound")
def test_a_spawned_run_arms_the_book_its_launch_held_on_its_own_tenants_key(
    offline: Router,
) -> None:
    # A spawned run is its launch's identity, never one rebuilt from the tenant alone: that is the host's.
    signup = IdentityContext(
        user_id=UserId("sub-9"),
        tenant_id=TenantId("sub-9"),
        email="a@example.com",
        capabilities=frozenset({"campaign.run"}),
    )
    stores = workspace(signup)
    spawned = JobRegistry(jobs_dir(default_projects_root()), capacity=lambda _live: 1)
    held = _held(
        stores,
        spawned,
        Budget(usd=0.25, tokens=40_000, rounds=UNBOUNDED, wall_clock_s=900.0),
        reserve=Budget(usd=0.5, tokens=80_000),
        # The operator named the USD axis and LIFTED the rounds cap: a lift stands as a lift.
        standing=Budget(usd=0.25, rounds=UNBOUNDED),
        step_rounds=2,
    )
    mode = RunMode(from_round=2)
    held_job = spawned.get(held.job_id)
    assert held_job is not None
    launched = HeldLaunch(
        opening=Opening(stores, spawned, held_job),
        hop=_minted(stores, offline).hop,
        budget=held,
        closing=contextlib.AsyncExitStack(),
        mode=mode,
    )
    spec = launched.job_spec()
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
    assert rebuilt.identity.host is stores.identity.host is False, (
        "a signup's run, rebuilt in its own process, spent the host's own key"
    )
    assert rebuilt.base_dir == stores.base_dir, "the spawned run billed another tenant's tree"


@pytest.mark.usefixtures("run_bound")
def test_a_ceiling_refuses_the_send_its_calls_out_would_carry_past_it() -> None:
    # A walk plans no more than the ceiling fits; a send past a wrong plan is refused at its hold.
    dollar = SendBound(input_tokens=0, output_tokens=0, usd=1.0)
    book = spend_book(5.0)

    def cell() -> Any:
        label = CallLabel("cell", "backend")
        return admitted(label, dollar, model=None, provider=None, paid_by="host")

    with scoped(book=book), contextlib.ExitStack() as out:
        held = [out.enter_context(cell()) for _ in range(5)]
        with pytest.raises(SendRefusedError) as refused:
            out.enter_context(cell())
        for bill in held:
            bill.settle(Billed(TokenAccount(), 1.0))
    assert (refused.value.category, book.spent.sent_usd) == (ErrorCategory.SPEND_CEILING, 5.0), (
        "a spend ceiling binds BEFORE a call: every cell out is counted at its bound"
    )


@pytest.mark.usefixtures("run_bound")
def test_a_search_ceiling_meters_its_search_and_prices_a_reread_once_per_campaign(
    tmp_path: Path,
) -> None:
    # The bench grades past the search's ceiling; a launch's re-read of a cell its campaign priced is free.
    metered: tuple[tuple[CeilingMeter, TokenUsageKind, bool], ...] = (
        ("search_incurred", "optimizer", True),
        ("search_incurred", "bench", False),
        ("bill", "bench", True),
    )
    for meters, kind, binds in metered:
        assert spend_book(1.0, meters=meters).binds(kind) is binds, f"{meters} binds {kind}"
    campaign = tmp_path / "campaign"
    laid_ledger(campaign_cycles_dir(campaign) / "stopped").append(PricedKeyRecord(priced_key="k"))
    rescope(priced=campaign)
    reread: tuple[tuple[CeilingMeter, str, bool], ...] = (
        ("search_incurred", "k", True),
        ("search_incurred", "j", False),
        ("bill", "j", True),
    )
    for meters, key, free in reread:
        assert spend_book(1.0, meters=meters).replays_free(key) is free, (
            f"under {meters}, a re-read of {key} is {'billed' if free else 'free'}"
        )


@pytest.mark.usefixtures("run_bound")
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
    with scoped(round=2):
        for cid, n, cut in superseding:
            arm_walk(
                skips,
                [(i, (f"k{i}", "a", False)) for i in range(n)],
                rnd=2,
                individual=cid,
                role=MeasurementRole.PANEL,
            )
            skips.append(ArmRecord(round=2, arm=ArmKey(cid), state=arm_state(cid, cut=cut)))

    def standing_skips() -> dict[tuple[int, ArmKey], int]:
        return read_line([LedgerSpan(skips.path)], STANDING_ROUNDS).skipped

    assert standing_skips() == {(2, "a"): 3, (2, "c"): 4}, "a superseded skip replayed"
    skips.append(RoundEnteredRecord(round=2))
    assert standing_skips() == {(2, "a"): 3, (2, "c"): 4}, "a resume re-entering lost a skip"
    skips.append(RoundEnteredRecord(round=2, rewound=True))
    assert standing_skips() == {}, "a regenerated round inherited a displaced skip"


def _inner_seat(stores: Stores, seats: InnerCells) -> tuple[InnerSpawnContext, Sample]:
    """An outer cycle's one seat, and the cell that asks it."""
    owner = CycleHop(campaign_id="ppself__aaaaaa", cycle_id="cycle_deadbeef0000")
    spawning = InnerSpawnContext(
        stores=stores,
        inside=(owner,),
        enclosing=(),
        acting=(),
        cells=seats,
        purpose=Optimize(),
        ruler=0,
    )
    (seat,) = seats.cells
    pin = PinnedCell(cell=seat, instrument="i").pin()
    return spawning, Sample(id=0, query=seat.id, ground_truth=None, source_pin=pin)


@pytest.mark.usefixtures("run_bound")
async def test_an_inner_runs_stop_reaches_its_outer_cell_off_the_cycle_it_ended_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, built_stores: Stores, offline: Any
) -> None:
    # A journey: the backend's hole is re-asked, and the re-ask rebases before the provider refuses.
    launched_bank(built_stores, offline, samples(40, "TRUE"))
    laid = defaulted_config({"optimization": {"optimizer": "potter", "degradation_threshold": 0.0}})
    spawning, cell = _inner_seat(
        built_stores, inner_cells("potter", dataset=LAUNCHED_DATASET, campaign=laid)
    )
    rounds: list[str] = []

    async def _round(cycle: Any, round_num: int, **_final: bool) -> Any:
        """Ask 1's round holed by the backend's 504; ask 2's rebases; its fork's, refused."""
        rounds.append(cycle.session.hop.cycle_id)
        if len(rounds) == 1:
            hole = measurement(0, None, error="504: no reply in 164s", error_category="SERVER")
            stop = panel_gate_tripped([hole], None, "strict")
            assert stop is not None
            raise stop
        if len(rounds) == 2:
            raise StopLoop(
                StopReason.REBASED,
                fork=RebaseRequest(
                    fork_from_round=round_num,
                    trigger=ForkTrigger.OPTIMIZER_REBASE,
                    reason="rebased",
                    issued_by=SYSTEM_PRINCIPAL,
                ),
            )
        raise SendRefusedError("Insufficient credits", category=ErrorCategory.PROVIDER_CREDIT)

    monkeypatch.setattr(loop, "execute_round", _round)
    with (
        scoped(account=built_stores),
        scoped(ledger=CycleEventLog(tmp_path / "outer.jsonl"), spawn=spawning),
        scoped(book=spend_book(1000.0), round=0),
        pytest.raises(CellSendRefusedError) as refused,
    ):
        await spawn.run_inner_cycle(cell, {})
    assert len(rounds) == 3 and rounds[1] != rounds[2], (
        f"no fork re-asked: {rounds}, {refused.value}"
    )
    assert "Insufficient credits" in str(refused.value), (
        "the outer cell read the stop off the cycle its run rebased from, not the one it ended on"
    )


@pytest.mark.usefixtures("run_bound")
async def test_a_seat_its_admission_refuses_mints_no_campaign_and_opens_no_observer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, built_stores: Stores, offline: Any
) -> None:
    launched_bank(built_stores, offline, samples(40, "TRUE"))
    laid = defaulted_config({"optimization": {"optimizer": "potter", "degradation_threshold": 0.0}})
    spawning, cell = _inner_seat(
        built_stores, inner_cells("potter", dataset=LAUNCHED_DATASET, campaign=laid)
    )
    opened: list[object] = []
    monkeypatch.setattr(run_entry, "enter_cycle", lambda *a, **_kw: opened.append(a))

    def _refused(*_a: object, **_kw: object) -> NoReturn:
        raise MachineBusyError(holding="", holder_user="", started_at=None)

    monkeypatch.setattr(spawn, "admit", _refused)
    with (
        scoped(account=built_stores),
        scoped(ledger=CycleEventLog(tmp_path / "outer.jsonl"), spawn=spawning),
        scoped(book=spend_book(1000.0), round=0),
        pytest.raises(MachineBusyError),
    ):
        await spawn.run_inner_cycle(cell, {})
    asked = spawn.inner_campaign_id(LAUNCHED_DATASET, "i", {})
    assert (
        sandbox_store(spawning.stores, spawning.inside[-1]).campaigns.load_campaign(asked) is None
    ), "a seat refused at its admission left its minted campaign"
    assert not opened, "a seat refused at its admission opened its observers"


@pytest.mark.usefixtures("run_bound")
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
    spawning, cell = _inner_seat(built_stores, inner_cells("potter"))
    with (
        scoped(ledger=CycleEventLog(tmp_path / "outer.jsonl"), spawn=spawning),
        scoped(round=0),
    ):
        # The connector DECLARES the seconds, the scoring seam enforces them, as in `measure_sample`.
        envelope = CellEnvelope(spawn.inner_cell_envelope_s(cell, {}), attempts=1, label=cell.query)
        with pytest.raises(CellUnscoreableError):
            async with envelope:
                await spawn.run_inner_cycle(cell, {})
    assert started.is_set(), "the inner campaign never started — the envelope proved nothing"
    assert cancelled.is_set(), "the inner campaign outlived its envelope and kept spending"


@pytest.fixture
def ledger_index(tmp_path: Path) -> CycleEventLog:
    return laid_ledger(tmp_path / "index")


def _bill_calls(log: CycleEventLog, n: int, round: int | None = None) -> None:
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


def _seed(log: CycleEventLog, model: str) -> None:
    overlay = {"llm_only": NodeCall(model=model)}
    log.append(CycleMintedRecord(start=CycleStart(pipeline_overlay=overlay)))


def _tailed(log: CycleEventLog) -> tuple[float, int, CycleStart | None]:
    spend, calls, _worked_s = read_line([LedgerSpan(log.path)], LEDGER_SPEND)
    seeded = read_line([LedgerSpan(log.path)], last(CycleMintedRecord))
    return spend.total_used_usd, calls, None if seeded is None else seeded.start


def _cold(log: CycleEventLog) -> tuple[float, int, CycleStart | None]:
    rows = [json.loads(line) for line in read_lines(log.path)]
    bills = [
        row["money"]["price"]["cost_usd"] for row in rows if row["record_type"] == "token_usage"
    ]
    seeds = [row["start"] for row in rows if row["record_type"] == "cycle_minted"]
    seeded = CycleStart.model_validate(seeds[-1]) if seeds else None
    return sum(bills), len(bills), seeded


# The compaction cuts the file the grown index has already read.
@pytest.mark.usefixtures("run_bound")
def test_a_ledger_index_serves_the_file_as_it_stands_grown_then_compacted(
    ledger_index: CycleEventLog,
) -> None:
    index = ledger_index
    _bill_calls(index, 3)
    _seed(index, "first")
    assert _tailed(index) == _cold(index) and _cold(index)[1] == 3, (
        "the index served another bill than the file"
    )
    _bill_calls(index, 2, round=2)
    _seed(index, "second")
    assert _tailed(index) == _cold(index) and _cold(index)[1] == 3 + 2, (
        "the index missed rows appended past its tail"
    )
    by_round = read_line([LedgerSpan(index.path)], SPEND_BY_ROUND)
    assert {r: spent.total_used_usd for r, spent in by_round.items()} == {0: 3.0, 2: 2.0}, (
        "the per-round split is the same bill: a call that carries no round banks at the origin's"
    )
    write_jsonl(index.path, [json.loads(line) for line in read_lines(index.path)[:2]])
    assert _tailed(index) == _cold(index) == (2.0, 2, None), (
        "a compaction swaps the file for a shorter one; the index kept its old rows"
    )


@pytest.mark.usefixtures("run_bound")
def test_a_ledger_index_read_in_a_race_with_its_writer_sees_no_torn_bill(
    ledger_index: CycleEventLog,
) -> None:
    index = ledger_index
    _bill_calls(index, 2)
    seen: list[tuple[float, int]] = []
    written = threading.Event()

    def read() -> None:
        racing = True
        while racing:
            racing = not written.is_set()
            seen.append(_tailed(index)[:2])

    # A spinning reader holds the GIL a whole switch interval each time the writer's I/O lets it
    # go, so the writer waits ~5 ms per syscall (3 s on Windows): it takes the GIL back at once.
    switch = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        reader = threading.Thread(target=read)
        reader.start()
        _bill_calls(index, 10)
        _seed(index, "third")
        written.set()
        reader.join()
    finally:
        sys.setswitchinterval(switch)
    assert all(usd == calls for usd, calls in seen), (
        f"a reader racing the writer saw a torn bill: {[s for s in seen if s[0] != s[1]][:3]}"
    )
    assert _tailed(index) == _cold(index) and _cold(index)[1] == 2 + 10, (
        "the index lost a row written under a race"
    )


@pytest.mark.usefixtures("run_bound")
def test_a_ledger_file_shared_by_two_writers_loses_no_bill_to_the_index(
    ledger_index: CycleEventLog,
) -> None:
    index = ledger_index
    _bill_calls(index, 2 + 10)
    assert _tailed(index) == _cold(index), "the index served another bill than the file"
    start = threading.Barrier(2)

    def flood() -> None:
        own = CycleEventLog(index.path)
        start.wait()
        _bill_calls(own, 10)

    writers = [threading.Thread(target=flood) for _ in range(2)]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join()
    assert _tailed(index) == _cold(index) and _cold(index)[1] == 2 + 10 + 2 * 10, (
        "writers sharing one file — the server's command beside the run's records — lost a bill"
    )


# The refused purge reads the cell the named home carried.
@pytest.mark.usefixtures("run_bound")
def test_a_cell_one_account_paid_replays_free_to_another_only_from_a_named_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def account(sub: str) -> Stores:
        signed_in = IdentityContext(user_id=UserId(sub), tenant_id=TenantId(sub))
        return workspace(signed_in)

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
        ledger = CycleEventLog(tmp_path / f"bob-{shared}.jsonl")
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
    purge_cold_store(workspace(default_identity()), apply=True)


# 3. A campaign's ceiling


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
        said = self.rng.randint(0, body["max_completion_tokens"])
        usage = {"prompt_tokens": prompt, "completion_tokens": said, "total_tokens": prompt + said}
        return completion("ok", model="gpt-x", usage=None if self.unbilled else usage)


# A cut of 600 input and 1500 output tokens at `_priced_wire`'s rates.
_HOLD_USD = 600 * 1e-6 + 1500 * 2e-6


def _priced_wire(offline: Router) -> _Provider:
    offline.rates = {"openai/gpt-x": {"input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6}}
    assert pricing.refresh_rates(force=True).fault is None, "the world's price list was not fetched"
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
    return [r for r in ledger_records(ledger) if isinstance(r, TokenUsageRecord)]


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


def _stopped_on(book: SpendBook) -> StopReason | None:
    return None if (stop := budget_stop(book)) is None else stop.reason


def test_a_mid_run_ceiling_reaches_the_running_book_and_the_loop(
    built_stores: Stores, offline: Router
) -> None:
    # The declared ceiling lives on the ledger alone, so a launch's flag sweep cannot take it.
    campaigns = built_stores.campaigns
    session = _minted(built_stores, offline)
    hop = session.hop
    cycle_dir = campaigns.cycle_dir(hop)
    jobs = JobRegistry(jobs_dir(default_projects_root()), capacity=lambda _live: 1)
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
    assert _stopped_on(book) is StopReason.SPEND_BUDGET, "a mid-run ceiling reached no book"
    CycleEventLog.open(CycleDir(cycle_dir)).append(RunPhaseRecord(state=Running()))
    assert _stopped_on(book) is StopReason.SPEND_BUDGET, "a launch swept the ceiling"
    declare(Budget(tokens=5_000))
    assert _stopped_on(book) is StopReason.TOKEN_BUDGET, "a token ceiling bound nothing"
    config = _budgeted(Budget(usd=0.025, rounds=50))
    declare(Budget(rounds=3))
    bounded, pause_at = round_bounds(session, config)
    assert (bounded.optimization.budget.rounds, pause_at) == (3, None), (
        "a mid-run round cap reached no loop"
    )
    assert budget_stop(book) is None, "a lifted ceiling still governed the run"
    # Another PROCESS's reshare lands on the cycle's ledger, behind this process's own write of it.
    CycleEventLog.open(CycleDir(cycle_dir)).append(CycleShareRecord(allotment=Budget(usd=0.50)))
    assert _stopped_on(book) is StopReason.SPEND_BUDGET, (
        "a share another process moved was hidden from the running book"
    )
    declare(Budget(rounds=UNBOUNDED))
    bounded, _ = round_bounds(session, config)
    assert bounded.optimization.budget.rounds is None, (
        "a LIFT of the round cap reads as no cap, never a fall back to the config's"
    )


def test_moving_one_ceiling_leaves_the_other_at_its_launch_cap(
    tmp_path: Path, built_stores: Stores, offline: Router
) -> None:
    moved = _minted(built_stores, offline).hop
    CycleEventLog.open(CycleDir(built_stores.campaigns.cycle_dir(moved))).append(
        billed_call(0.10, input_tokens=210_000)
    )
    registry = JobRegistry(tmp_path / "4" / "jobs", capacity=lambda _live: 1)
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
    assert _stopped_on(moved_book) is StopReason.TOKEN_BUDGET, "the token ceiling bound nothing"
    dispatcher = CommandDispatcher(built_stores, registry)
    armed = _change_run_limits(dispatcher, moved, Budget(usd=0.50))
    assert armed.usd == pytest.approx(0.50), (
        "the change armed no ceiling a replay could stop the run at"
    )
    assert _stopped_on(moved_book) is StopReason.TOKEN_BUDGET, (
        "a USD raise lifted the token ceiling"
    )
    assert moved_book.allotment.usd == pytest.approx(0.50), (
        "$0.50 ceiling, $0.10 of it this cycle's own: the running book binds another share"
    )
    _change_run_limits(dispatcher, moved, Budget(usd=UNBOUNDED))
    assert moved_book.allotment.usd is None, "a lifted ceiling still bounds the running share"


def test_an_offshoot_takes_the_rounds_its_parent_left_and_names_no_money_of_its_own() -> None:
    # A cap the fork's seed names is the operator's.
    left = ForkRemainder.of(rounds_closed=3, rounds=5)
    for overrides, expected in (
        (ConfigOverrides(), Budget(rounds=2)),
        (
            ConfigOverrides(budget=Budget(rounds=9, wall_clock_s=60.0)),
            Budget(rounds=9, wall_clock_s=60.0),
        ),
        (ConfigOverrides(budget=Budget(rounds=UNBOUNDED)), Budget(rounds=UNBOUNDED)),
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
def shared_ceiling(built_stores: Stores) -> _SharedCeiling:
    """The jobs dir every reader attaches to, with room for every fork, and launches on it."""
    shared = JobRegistry(jobs_dir(default_projects_root()), capacity=lambda _live: 4)
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
                campaign=_at(built_stores, fork).campaign,
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
def test_forks_launched_side_by_side_hold_one_ceiling_between_them(
    built_stores: Stores, world: World, ran_24: Shared, shared_ceiling: _SharedCeiling
) -> None:
    _, launch, share, bill = shared_ceiling
    root, fork_a, fork_b, _ = _line(world, 3, ran_24)
    bill(root, 0.25)
    _, held_a = launch(fork_a, 0.5)
    assert share(held_a) == pytest.approx(0.25), (
        "fork a was not admitted its ceiling less the spent"
    )
    bill(fork_a, 0.125)
    _, held_b = launch(fork_b, 1.0)
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


def _forks_a_and_b_admitted(
    world: World, ran_24: Shared, shared_ceiling: _SharedCeiling
) -> tuple[CycleHop, CycleHop, Any, HeldBudget]:
    """Fork a admitted $0.50 beside the root's $0.25, fork b $1.00 beside both: (b, c, b's job, b's hold)."""
    _, launch, _, bill = shared_ceiling
    root, fork_a, fork_b, fork_c = _line(world, 3, ran_24)
    bill(root, 0.25)
    launch(fork_a, 0.5)
    bill(fork_a, 0.125)
    job_b, held_b = launch(fork_b, 1.0)
    return fork_b, fork_c, job_b, held_b


def test_a_third_fork_is_refused_while_its_siblings_hold_the_ceiling_whole(
    world: World, ran_24: Shared, shared_ceiling: _SharedCeiling
) -> None:
    _, fork_c, _, _ = _forks_a_and_b_admitted(world, ran_24, shared_ceiling)
    with pytest.raises(QuotaExceededError):
        shared_ceiling.launch(fork_c, 1.0)


def test_an_admitted_fork_holds_its_share_against_a_sibling_admitted_before_its_run_opens(
    built_stores: Stores, world: World, ran_24: Shared, shared_ceiling: _SharedCeiling
) -> None:
    shared, launch, share, bill = shared_ceiling
    root, fork_a = _line(world, 1, ran_24)
    bill(root, 0.25)
    asked = ForkCyclePayload(campaign_id=root.campaign_id, cycle_id=root.cycle_id)
    request = ForkLaunch(_at(built_stores, root), asked, CycleStart(), Budget(usd=0.5))
    forking = admit(request, RunMode(), stores=built_stores, job_registry=shared, inline=Inline())
    held, _ = asyncio.run(admit_and_hold(request, forking.opening))
    _, sibling = launch(fork_a, 1.0)
    assert share(sibling) == pytest.approx(1.0 - 0.25 - held.share.usd_amount), (
        "a sibling admitted after a fork's admission spends the fork's share: two cycles, two ceilings"
    )


def test_a_fork_arms_its_campaigns_ceiling_which_the_launch_that_moved_it_wrote_on_the_root(
    built_stores: Stores, world: World, ran_24: Shared, shared_ceiling: _SharedCeiling
) -> None:
    fork_b, _, _, _ = _forks_a_and_b_admitted(world, ran_24, shared_ceiling)
    dispatcher = CommandDispatcher(built_stores, shared_ceiling.shared)
    armed = _change_run_limits(dispatcher, fork_b, Budget(rounds=4))
    assert armed.usd == pytest.approx(1.0), (
        "a fork armed its own ledger's ceiling, not the campaign's"
    )


# An embedded run holds a share through the same admission, and never a machine slot.
def test_an_embedded_run_holds_a_share_at_admission_and_gives_it_back_at_its_exit(
    built_stores: Stores, world: World, ran_24: Shared, shared_ceiling: _SharedCeiling
) -> None:
    shared = shared_ceiling.shared
    _, fork_c, job_b, _ = _forks_a_and_b_admitted(world, ran_24, shared_ceiling)

    async def held_embedded(held: HeldBudget) -> tuple[None, CycleResult]:
        assert held.admitted.usd == pytest.approx(1.0)
        holder = built_stores.campaigns.holder(fork_c)
        run = None if holder is None else holder.run
        assert run is not None and cycle_share(built_stores, run).usd == pytest.approx(0.5), (
            "an embedded run held no share, or another one"
        )
        assert fork_c not in {j.hop for j in shared.list_running()}, (
            "an embedded run took a machine slot to carry its share"
        )
        return None, cycle_result([], None, [])

    def admit_embedded(opening: Opening) -> HeldBudget:
        at = _at(built_stores, fork_c)
        declared, asked = declare_run_budget(
            _budgeted(Budget(usd=1.0)).optimization.budget,
            armed=built_stores.campaigns.armed_budget(at),
            requested=Budget(),
        )
        return admit_and_share(
            declared,
            asked,
            stores=built_stores,
            job_registry=opening.job_registry,
            job=opening.job,
            campaign=at.campaign,
            hop=at.hop,
            step_rounds=None,
            sends=_CELLS,
        )

    async def embedded() -> None:
        opening = await asyncio.to_thread(
            slotless_opening, built_stores, shared, EmbeddedRun("ds1"), fork_c
        )
        async with contextlib.AsyncExitStack() as closing:
            closing.push_async_exit(opening)
            held = await asyncio.to_thread(admit_embedded, opening)
            await held_embedded(held)

    with pytest.raises(QuotaExceededError):
        asyncio.run(embedded())
    shared.release(job_b.job_id)
    asyncio.run(embedded())
    assert built_stores.campaigns.holder(fork_c) is None, (
        "an embedded run kept its share past its exit"
    )


# The money-axis refusals read the pair's cycles while they live.
def test_a_raised_ceiling_is_reshared_over_every_live_cycle_never_handed_whole_to_one(
    built_stores: Stores, world: World, ran_24: Shared, shared_ceiling: _SharedCeiling
) -> None:
    shared, launch, share, _ = shared_ceiling
    # A runs at $1, B is admitted at $3 beside it ($2), and the raise to $3 leaves A its $1.
    eighth = SendBound(input_tokens=0, output_tokens=0, usd=0.125)
    *pair, pair_c, unminted = _line(world, 3, ran_24)
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
        launch(unminted, None)
    for job in (job_a, job_b6):
        shared.release(job.job_id)
    open_c, _ = launch(pair_c, None)
    with pytest.raises(QuotaExceededError):
        launch(unminted, 1.0)
    shared.release(open_c.job_id)


def _raise_ceiling(stores: Stores, registry: JobRegistry, at: CycleHop, usd: float) -> None:
    change_budget(
        Budget(usd=usd), stores=stores, job_registry=registry, at=_at(stores, at), sends=()
    )


def _campaign_jobs(stores: Stores, registry: JobRegistry, campaign_id: str) -> list[Any]:
    return [
        job
        for job in registry.list_holding_spend()
        if job.on_campaign(stores.campaigns.workspace, campaign_id)
    ]


def test_a_launch_landing_mid_reshare_waits_it_out(
    monkeypatch: pytest.MonkeyPatch,
    built_stores: Stores,
    world: World,
    ran_24: Shared,
    shared_ceiling: _SharedCeiling,
) -> None:
    shared, launch, _, _ = shared_ceiling
    # A holds $1 of $1 while a raise to $3 re-shares; B, launching at $3 meanwhile, is admitted
    # on the shares as moved, never on a torn split.
    race = _line(world, 1, ran_24)
    camp = race[0].campaign_id
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

    def launch_b() -> None:
        try:
            landed.append(launch(race[1], 3.0))
        except QuotaExceededError as exc:
            landed.append(exc)

    raiser = threading.Thread(target=_raise_ceiling, args=(built_stores, shared, race[0], 3.0))
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
        for job in _campaign_jobs(built_stores, shared, camp)
    ]
    assert landed and sum(racing) <= 3.0 + 1e-9, (
        f"shares {racing} of a $3 ceiling: a launch admitted mid-reshare overcommitted it"
    )
    for job in _campaign_jobs(built_stores, shared, camp):
        shared.release(job.job_id)


def test_a_ceiling_its_campaign_has_spent_admits_nothing(
    world: World, ran_24: Shared, shared_ceiling: _SharedCeiling
) -> None:
    _, launch, _, bill = shared_ceiling
    race = _line(world, 1, ran_24)
    bill(race[0], 3.0)
    with pytest.raises(QuotaExceededError):
        launch(race[1], 3.0)


def test_a_job_retargeted_mid_stamp_is_never_overwritten_by_the_runs_stale_copy(
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
    shared.move(stamped.job_id, LaunchMove.ADMIT)
    api = JobRegistry(jobs_dir(default_projects_root()), capacity=lambda _live: 4)
    read_job = shared.get
    retarget = threading.Thread(
        target=api.move, args=(stamped.job_id, LaunchMove.LAND), kwargs={"hop": race[0]}
    )

    def read_then_moved(job_id: str) -> Any:
        job = read_job(job_id)
        # Another process's stamp, asked mid-stamp: it waits on the gate, never refused.
        retarget.start()
        return job

    with monkeypatch.context() as patched:
        patched.setattr(shared, "get", read_then_moved)
        shared.move(stamped.job_id, LaunchMove.START)
    retarget.join()
    after = shared.get(stamped.job_id)
    assert after is not None and after.hop == race[0], (
        "a job retargeted mid-stamp was overwritten by the run's stale job file"
    )
    assert after.started_at is not None
    shared.release(stamped.job_id)


def test_a_busy_admission_gate_refuses_a_launch_and_never_a_running_jobs_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, built_stores: Stores
) -> None:
    # A paid cell 11 minutes in died on a gate four launches had made busy (gate-hold).
    jobs = tmp_path / "jobs"
    payer = account_of(built_stores)
    running = JobRegistry(jobs, capacity=lambda _live: 4).create(
        user_id=payer.user_id,
        principal_id=payer.user_id,
        workspace=built_stores.campaigns.workspace,
        hop=UNRESOLVED_HOP,
        holder=EmbeddedRun("ds1"),
    )
    JobRegistry(jobs, capacity=lambda _live: 4).move(running.job_id, LaunchMove.ADMIT)
    waiting = threading.Event()
    raised: list[BaseException] = []

    def asked_then_taken(path: Path, *, wait_s: float) -> Any:
        # The holder lets go only once the stamp has asked the held gate: refused, or waiting.
        lock = take_lock(path, wait_s=0.0)
        waiting.set()
        return take_lock(path, wait_s=wait_s) if lock is None and wait_s > 0.0 else lock

    def started() -> None:
        try:
            JobRegistry(jobs, capacity=lambda _live: 4).move(running.job_id, LaunchMove.START)
        except BaseException as exc:  # noqa: BLE001
            raised.append(exc)

    monkeypatch.setattr(interlock, "LOCK_TIMEOUT", 0.0)
    with JobRegistry(jobs, capacity=lambda _live: 4).admission_gate():
        with pytest.raises(ServiceUnavailableError) as refused:
            request_jobs(
                stores=built_stores,
                job_registry=JobRegistry(jobs, capacity=lambda _live: 4),
                holder=CampaignLaunch("ds1"),
            )
        assert refused.value.code == "admission_gate_stuck"
        monkeypatch.setattr(interlock, "take_lock", asked_then_taken)
        stamp = threading.Thread(target=started)
        stamp.start()
        waiting.wait()
    stamp.join()
    after = JobRegistry(jobs, capacity=lambda _live: 4).get(running.job_id)
    assert not raised and after is not None and after.started_at is not None, (
        f"a running job's stamp was refused by a busy gate: {raised}"
    )


def test_sharing_divides_the_campaigns_ceiling_never_what_the_account_admits(
    tmp_path: Path, offline: Router, shared_ceiling: _SharedCeiling
) -> None:
    # A $10 campaign that has spent $3 shares the $7 left; its account binds through the reserve.
    shared, _, _, _ = shared_ceiling
    delegate = workspace(
        IdentityContext(
            user_id=UserId("sub-d"),
            tenant_id=TenantId("sub-d"),
            capabilities=frozenset({"campaign.create", "campaign.run"}),
        ),
    )
    spender, claimed = _line(World(delegate, offline, tmp_path / "delegate"), 1)
    delegate.grant.set(Budget(usd=50.0, tokens=50_000), afresh=False, opened_at=_OPENED)
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
        campaign=_at(delegate, claimed).campaign,
        hop=claimed,
        step_rounds=None,
        sends=_CELLS,
    )
    claim_held = shared.get(claim.job_id)
    assert claim_held is not None and cycle_share(delegate, claim_held).usd == pytest.approx(7.0), (
        "a share was cut from its account's claim, not the campaign's $7 left"
    )
    shared.release(claim.job_id)


def _archived_cell(stores: Stores, bank: Any) -> Any:
    """X's cells archived at $0.001 and $0.003 beside Y's at $1.00: the price of X's pipeline."""
    for model, usd, seconds, rows in (
        ("X", 0.001, 1.0, slice(0, 3)),
        ("X", 0.003, 3.0, slice(3, 6)),
        ("Y", 1.0, 99.0, slice(0, 3)),
    ):
        steps = {"step_tokens": {"llm_only": {"input": 1, "output": 1, "cost_usd": usd}}}
        filed(
            stores.archive,
            [
                archive_row(s.query, pipeline_data={**steps, "step_timings": {"llm_only": seconds}})
                for s in bank.bank.samples[rows]
            ],
            filed_under([("llm_only", {"model": model})], bank.bank.dataset_hash),
        )
    on = [("llm_only", {"model": "X"})]
    return measured_cell_price(
        bank.store, bank.bank.dataset_hash, PipelineParams().with_nodes(dict(on))
    )


def _billed_optimizer(stores: Stores, hop: CycleHop) -> Any:
    """The origin's init at $0.50, two rounds at $0.02 and $0.04, one that called it for free."""
    for usd, at in ((0.5, None), (0.02, 1), (0.04, 2), (0.0, 3)):
        CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(hop))).append(
            billed_call(usd, round=at)
        )
    return optimizer_round_price(stores, "potter")


def _planned_arms(stores: Stores) -> tuple[HeadToHeadPlan, list[Any], list[Any]]:
    """The plan's heads, each arm's laid config, and the budget point each was minted on."""
    write_yaml(
        stores.tenant_datasets.dataset_dir("ds1") / DEFINITION_FILE,
        {
            "pipeline": {"connector": "termnorm"},
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
    on_disk = ConfigLayers.of(offered_dataset(stores, "ds1"), None)
    configs = [
        laid_config(
            stores,
            on_disk.over(head.head_to_head.declared_optimization(arm)),
            connector=connectors.CONNECTORS.get("termnorm"),
            schema=None,
        ).config
        for head in plan.heads()
        for arm in head.head_to_head.arms
    ]
    return plan, configs, [point for point in plan.budgets.values() for _ in range(4)]


def test_a_cell_is_priced_at_its_archived_rows_of_its_own_model(
    built_stores: Stores, offline: Router
) -> None:
    cell = _archived_cell(built_stores, _minted(built_stores, offline))
    assert cell is not None and (cell.usd, cell.seconds, cell.cells) == pytest.approx(
        (0.002, 2.0, 6)
    ), "a cell was priced off another model's rows, or off fewer than the archive holds"


def test_an_optimizers_round_is_priced_at_the_rounds_that_billed_it(
    built_stores: Stores, offline: Router
) -> None:
    held = _billed_optimizer(built_stores, _minted(built_stores, offline).hop)
    assert held is not None and (held.usd, held.rounds) == pytest.approx((0.03, 2)), (
        "a round's optimizer price counted the origin's init, or a round that called it for free"
    )


def test_a_planned_head_to_heads_arms_are_minted_on_their_budget_points(
    built_stores: Stores,
) -> None:
    _, configs, points = _planned_arms(built_stores)
    assert all(
        c.optimization.budget.only(point.named) == point
        for c, point in zip(configs, points, strict=True)
    ), "an arm was minted on another budget than its point's"


def test_a_planned_head_to_head_is_priced_to_within_a_send_of_each_arms_ceiling(
    built_stores: Stores, offline: Router
) -> None:
    bank = _minted(built_stores, offline)
    cell = _archived_cell(built_stores, bank)
    held = _billed_optimizer(built_stores, bank.hop)
    plan, configs, _ = _planned_arms(built_stores)
    price = priced_head_to_head(
        plan.heads(),
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
def test_a_remote_cell_open_at_the_runs_wall_clock_is_cut_on_it(world: World) -> None:
    clock = spend_book(None, usd_reserve=None, wall_clock_s=30.0)

    async def cut_cell() -> tuple[MeasuredCell, float]:
        loop = asyncio.get_running_loop()
        async with world.opened() as session:
            world.net.backend.faults = {"/matches": [Hang()]}
            began = loop.time()
            with scoped(book=clock):
                row = await measure_sample(
                    session.bank.samples[0], session, session.pipeline_params
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
    assert _stopped_on(clock) is StopReason.WALL_CLOCK, (
        "a run past its wall clock was not stopped on it"
    )


@pytest.mark.usefixtures("run_bound")
def test_a_cell_row_carries_the_category_its_sends_close_decided(world: World) -> None:
    async def downed_cell() -> MeasuredCell:
        async with world.opened() as session:
            down = httpx.Response(503, json={"detail": "down"})
            world.net.backend.faults = {"/matches": [down] * 12}
            with scoped(book=spend_book(None, usd_reserve=None)):
                return await measure_sample(
                    session.bank.samples[0], session, session.pipeline_params
                )

    row = jumping_clock(downed_cell())
    assert row.error_category == ErrorCategory.SERVER, (
        f"a 5xx past its resends reached the row as {row.error_category}, not the close's SERVER"
    )


def test_a_credit_refusal_is_the_providers_on_any_4xx() -> None:
    # Anthropic refuses for credit with a 400, OpenRouter with a 402 or 403, OpenAI with a 429: never
    # the prompt's, and never a throttle a backoff ends.
    request = httpx.Request("POST", "https://provider.invalid/v1/chat/completions")
    for status, said in (
        (400, "Your credit balance is too low"),
        (402, "This request requires more credits"),
        (403, "Key limit exceeded"),
        (429, '{"error": {"code": "insufficient_quota"}}'),
    ):
        reply = httpx.Response(status, text=said, request=request)
        refused = httpx.HTTPStatusError(str(status), request=request, response=reply)
        assert failed_send(refused).failure is ErrorCategory.PROVIDER_CREDIT, (
            f"a {status} credit refusal was charged to the prompt"
        )
    # Gemini's per-minute limit says "quota" too, and a backoff ends it: a throttle, never a stop.
    limited = httpx.Response(
        429,
        text="You exceeded your current quota. Retry in 20s. RESOURCE_EXHAUSTED",
        request=request,
    )
    throttled = httpx.HTTPStatusError("429", request=request, response=limited)
    assert failed_send(throttled).failure is ErrorCategory.PROVIDER_THROTTLED, (
        "a rate limit worded as quota stopped the run as a credit refusal"
    )


@pytest.mark.usefixtures("run_bound")
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
        # The stop reads the room the refused send read: the calls out fill the share.
        assert own.exhausted() is ErrorCategory.SPEND_CEILING, (
            f"reserve {reserve}: no stop at a full room"
        )
        for hold in held:
            own.charges.append(_bill(hold, worst.usd or 0.0))
        assert own.exhausted() is ErrorCategory.SPEND_CEILING, f"reserve {reserve}: billed, no stop"


# Each call's hold is read after the bills before it: the mean moves with every one.
@pytest.mark.usefixtures("run_bound")
def test_with_overshoot_on_a_call_holds_its_nodes_mean_bill_in_the_run_after_the_first(
    tmp_path: Path, built_stores: Stores, offline: Router
) -> None:
    hop = _minted(built_stores, offline).hop
    jobs = JobRegistry(jobs_dir(default_projects_root()), capacity=lambda _live: 1)
    book = _armed(built_stores, hop, _held(built_stores, jobs, Budget(usd=1.0)))
    worst = SendBound(input_tokens=0, output_tokens=1000, usd=0.10)

    def held(node: str, billed: float, on: SpendBook = book) -> Budget:
        """What a call of *node* is held at, which then bills *billed* on 100 tokens."""
        with _billing_on(CycleEventLog(tmp_path / "calls.jsonl"), on):
            call = Admission(
                on, CallLabel(node, "optimizer"), worst, model=None, provider=None, paid_by="host"
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
    # An inner run arms no cycle of its own: its root's switch sizes its calls (decision 23).
    _change_run_limits(CommandDispatcher(built_stores, jobs), hop, Budget(), overshoot=True)
    inner = spend_book(5.0, enclosing=book)
    held("judge", 0.02, inner)
    assert held("judge", 0.04, inner) == Budget(usd=0.02, tokens=100), (
        "an inner run held a call at its worst case under its root's overshoot switch"
    )


@pytest.mark.usefixtures("run_bound")
def test_a_bill_past_its_overshoot_hold_draws_on_the_run_and_the_dashboard_serves_it(
    built_stores: Stores, offline: Router
) -> None:
    hop = _minted(built_stores, offline).hop
    ledger = CycleEventLog.open(CycleDir(built_stores.campaigns.cycle_dir(hop)))
    jobs = JobRegistry(jobs_dir(default_projects_root()), capacity=lambda _live: 1)
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


def test_an_overshoot_switched_on_before_the_launch_wires_its_cycle_is_served_on(
    built_stores: Stores, offline: Router
) -> None:
    hop = _minted(built_stores, offline).hop
    jobs = JobRegistry(jobs_dir(default_projects_root()), capacity=lambda _live: 1)
    _change_run_limits(CommandDispatcher(built_stores, jobs), hop, Budget(), overshoot=True)
    built_stores.campaigns.append(hop, run_wiring())
    limits = built_stores.campaigns.run_limits(_at(built_stores, hop))
    assert limits is not None and limits.overshoot, (
        "a standing switch landing before the wiring was dropped from the served limits"
    )


def test_a_launch_standing_its_held_budget_keeps_the_pause_its_allowance_declared(
    built_stores: Stores, offline: Router
) -> None:
    hop = _minted(built_stores, offline).hop
    # A launch stepping four rounds from round 1 declares its pause at 4.
    declare_round_allowance(built_stores, hop, resumed_from_round=1, step_rounds=4)
    jobs = JobRegistry(jobs_dir(default_projects_root()), capacity=lambda _live: 1)
    held = _held(built_stores, jobs, Budget(usd=1.0), standing=Budget(rounds=2))
    stand_held_budget(built_stores, hop, held)
    standing = built_stores.campaigns.read_standing_budget(hop)
    assert standing.budget.rounds == 2 and standing.pause_at_round == 4, (
        "standing the held budget dropped the pause the launch's allowance declared"
    )


@pytest.mark.usefixtures("run_bound")
def test_a_sends_room_is_the_ceiling_less_bills_unpriced_sends_and_the_set_aside() -> None:
    worst = SendBound(input_tokens=0, output_tokens=1000, usd=0.125)
    owed = spend_book(0.5)
    _drawn(owed, 0.125, lapsed=True)
    assert owed.fits(worst, paid_by="host") == 3, "a send no bill priced left its room open"
    owed.set_aside(0.25, 0, paid_by="host")
    assert owed.fits(worst, paid_by="host") == 1, (
        "the search could spend what the bench was set aside"
    )
    assert owed.exhausted() is None, "a set-aside read as spent"
    _drawn(owed, 0.125, lapsed=False)
    assert owed.fits(worst, paid_by="host") == 0 and owed.exhausted() is not None, (
        "a send's room is the ceiling less EVERYTHING owed: bills, unpriced sends, the set-aside"
    )


@pytest.mark.usefixtures("run_bound")
def test_a_free_send_fits_at_exactly_nothing_left_and_never_once_overdrawn() -> None:
    free = SendBound(input_tokens=0, output_tokens=0, usd=0.0)
    book = spend_book(0.25)
    _drawn(book, 0.25, lapsed=False)
    assert book.fits(free, paid_by="host") > 0, "a send costing nothing was refused at nothing left"
    _drawn(book, 0.125, lapsed=False)
    assert (book.money_left().usd, book.coupon_left().usd) == pytest.approx((-0.125, -0.125)), (
        "the remainder lost the overdraft a bill past its bound drew"
    )
    assert book.fits(free, paid_by="host") == 0, "an overdrawn book admitted a free send"


_ASK_INSIDE_AN_OUTAGE = """
import sys
from promptpotter.infrastructure.llm import pricing
world.upstream_dead = True
try:
    pricing.route_ceiling(sys.argv[1], "openrouter")
except pricing.PriceListUnreachableError:
    sys.exit(f"{world.rate_fetches} fetches" if world.rate_fetches else 0)
sys.exit("no outage")
"""


@pytest.mark.usefixtures("run_bound")
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

    with monkeypatch.context() as patched:
        patched.setattr(urllib.request, "urlopen", down)
        outage = asyncio.run(held_twice())
    assert len(fetches) == 1, f"{len(fetches)} fetches — one per send, each a full timeout"
    assert run_stop_reason(outage) is StopReason.PRICE_LIST_UNREACHABLE, "an outage read as no rate"
    held_back = subprocess.run(
        offline_world.child(_ASK_INSIDE_AN_OUTAGE, "vendor/model-x"),
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert held_back.returncode == 0, f"a second process re-fetched inside the outage: {held_back}"


def _replay(usd: float) -> TokenUsageRecord:
    return billed_call(usd, kind="backend", node="n", input_tokens=10, output_tokens=5, replay=True)


@pytest.mark.usefixtures("run_bound")
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
@pytest.mark.usefixtures("run_bound")
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
        scoped(filed_as="bench"),
        reserved(CallLabel("cell", "backend"), pass_bound, paid_by="user"),
    ):
        pass
    # A replay draws no coupon: the search's share refuses once replays spend it.
    searched.append(_replay(0.02))
    small = SendBound(input_tokens=1, output_tokens=1, usd=0.02)
    with pytest.raises(SendRefusedError):
        fresh.hold(small, _OPTIMIZER, what="o", paid_by="host", ledger="o")


_BURST_CEILING_USD = 0.05


def _burst(tmp_path: Path, offline: Router) -> tuple[CycleEventLog, SpendBook, list[Any]]:
    """Forty sends over a flaky wire under a $0.05 ceiling, every fifth cancelled mid-flight."""
    wire = _priced_wire(offline)
    wire.flaky = True
    rng = wire.rng
    burst_ledger = CycleEventLog(tmp_path / "burst.jsonl")
    book = spend_book(_BURST_CEILING_USD)

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
    return burst_ledger, book, outcomes


@pytest.mark.usefixtures("run_bound")
def test_no_burst_of_sends_records_spend_past_its_ceiling(tmp_path: Path, offline: Router) -> None:
    burst_ledger, book, outcomes = _burst(tmp_path, offline)
    ceiling_usd = _BURST_CEILING_USD
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
    left_out = unreported_under(_charges(), burst_ledger.path).host.unreported_usd
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


# The killed send's hold is read beside the sends the burst left unreported.
@pytest.mark.usefixtures("run_bound")
def test_a_killed_hold_stays_out_of_a_bursts_spend_while_its_job_holds_and_is_unreported_after(
    tmp_path: Path, offline: Router
) -> None:
    burst_ledger, _, outcomes = _burst(tmp_path, offline)
    recorded = sum(r.price.rate_priced_usd or 0.0 for r in _token_bills(burst_ledger))
    unreported = sum(isinstance(o, asyncio.CancelledError | httpx.ReadTimeout) for o in outcomes)
    charges = _charges()
    left_out = unreported_under(charges, burst_ledger.path).host.unreported_usd
    # A hard exit runs no `finally`: the hold written ahead of the call is held while its job
    # holds, and unreported at its bound once the job's hold closes.
    killed = Hold(
        hold_id="killed",
        parent="reaped",
        key_source="host",
        bound=Budget(usd=_HOLD_USD, tokens=2_100),
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
    assert account.unreported_usd == pytest.approx(left_out + _HOLD_USD), (
        "a killed send's hold was not counted as out"
    )
    assert _unreported_sends(burst_ledger) == unreported + 1, "a killed send's hold went unread"


@pytest.mark.usefixtures("run_bound")
def test_a_bill_naming_no_price_closes_its_hold_in_tokens_and_keeps_its_money_held(
    tmp_path: Path,
) -> None:
    charges = _charges()
    bare = CycleEventLog(tmp_path / "bare" / "ledger.jsonl")
    on_bare = charges.held_on(bare.path)
    charges.append(
        Hold(
            hold_id="bare",
            parent=None,
            key_source="host",
            bound=Budget(usd=_HOLD_USD, tokens=2_100),
            resource="optimizer",
            node="o",
            ledger=on_bare,
        )
    )
    priceless = billed_call(node="bare", input_tokens=40, output_tokens=9, hold_id="bare").bill
    assert priceless is not None
    charges.append(priceless)
    assert unreported_under(charges, bare.path).host.unreported_usd == pytest.approx(_HOLD_USD), (
        "a priceless bill freed its hold"
    )


@pytest.mark.usefixtures("run_bound")
def test_a_settled_call_leaves_the_money_fold_at_the_next_line(tmp_path: Path) -> None:
    book = spend_book(1.0)
    bound = SendBound(input_tokens=0, output_tokens=100, usd=0.01)
    with _billing_on(CycleEventLog(tmp_path / "ledger.jsonl"), book):
        sends = [
            Admission(book, _OPTIMIZER, bound, model=None, provider=None, paid_by="host")
            for _ in range(3)
        ]
        for send in sends:
            send.settle(Billed(TokenAccount(input=10, output=5), 0.001))
        Admission(book, _OPTIMIZER, bound, model=None, provider=None, paid_by="host")
    money = money_on(book.charges.path)
    assert set(money.holds) == set(money.open) and len(money.open) == 2, (
        "a settled call stayed in the fold past its settle"
    )
    assert money.drawn[book.hold_id].total.sent_usd == pytest.approx(0.003), (
        "a call that left the fold took its bill off the job's hold"
    )


@pytest.mark.usefixtures("run_bound")
def test_a_nested_runs_replay_is_metered_on_its_roots_ledger(
    tmp_path: Path, built_stores: Stores
) -> None:
    root = CycleEventLog(tmp_path / "root.jsonl")
    book = spend_book(0.05, meters="search_incurred", metered_on=root.path)
    book.ledger = root
    inner = spend_book(5.0, enclosing=book)
    price = telemetry.call_price(
        "optimizer",
        TokenAccount(input=10, output=5),
        model=None,
        provider=None,
        cost_usd=0.01,
        recorded=None,
    )
    with _billing_on(CycleEventLog(tmp_path / "nested.jsonl"), inner):
        telemetry.emit_replay_usage(
            node="cell",
            usage=TokenAccount(input=10, output=5),
            duration_s=0.0,
            price=price,
            served_by=None,
        )
    assert money_on(root.path).metered["search_incurred"].usd_amount == pytest.approx(0.01), (
        "a nested run's replay reached its root's search-incurred meter at another price"
    )
    # Spawned by the root's bench pass: the bench's price on the root, the call's own kind inside.
    benched = CycleEventLog(tmp_path / "benched.jsonl")
    with scoped(filed_as="bench"), scoped():
        enter_inner_cell(
            _inner_seat(built_stores, inner_cells("potter"))[0], built_stores.campaigns, "c"
        )
        mode = run_scope().instrument
        assert mode is not None
        inner.enclosed_as = mode.enclosed_as or inner.enclosed_as  # as `arm_spend_book` arms it
        with _billing_on(benched, inner):
            telemetry.emit_replay_usage(
                node="cell",
                usage=TokenAccount(input=10, output=5),
                duration_s=0.0,
                price=telemetry.call_price(
                    "optimizer",
                    TokenAccount(input=10, output=5),
                    model=None,
                    provider=None,
                    cost_usd=0.01,
                    recorded=None,
                ),
                served_by=None,
            )
    assert [r.price.kind for r in _token_bills(benched)] == ["optimizer"], (
        "an inner run filed its own call as the bench pass that spawned it"
    )
    by_kind = read_line([LedgerSpan(root.path)], LEDGER_SPEND).spend.by_kind
    assert money_on(root.path).metered["search_incurred"].usd_amount == pytest.approx(0.01), (
        "a nested run a bench pass spawned was metered as the search's spend"
    )
    assert by_kind["bench"].incurred_usd == pytest.approx(0.01), (
        "a nested run a bench pass spawned did not read under the bench's kind"
    )


@pytest.mark.usefixtures("run_bound")
def test_a_key_two_cycles_of_one_campaign_read_is_priced_once(tmp_path: Path) -> None:
    campaign = tmp_path / "campaign"
    rescope(priced=campaign)
    claims = []
    for cycle in ("a", "b"):
        with scoped(ledger=laid_ledger(campaign_cycles_dir(campaign) / cycle)):
            claims.append(telemetry.claim_campaign_price("cell"))
    assert claims == [True, False], "a second cycle of the campaign priced a key again"


def test_a_malformed_capability_override_is_refused_never_read_past(tmp_path: Path) -> None:
    (tmp_path / CAPABILITY_FILE).write_text("gpt-x:\n  reasoning_effort: [low]\n", encoding="utf-8")
    with pytest.raises(FileUnreadableError):
        resolve_model_capabilities("gpt-x", "openai", workspace=tmp_path)


def test_a_schema_serves_each_model_once_per_provider_its_nodes_send_on(tmp_path: Path) -> None:
    # An optimizer's pool or election node names no provider: it sends on none.
    sends, pools = (
        PipelineNode(
            name=name,
            kind=kind,
            current_config=NodeCall(model=model, provider=provider),
            tunes_llm=False,
        )
        for name, kind, model, provider in (
            ("generate", NodeKind.LLM, "gpt-x", "openrouter"),
            ("pool", NodeKind.TOOL, None, None),
        )
    )
    menu = resolve_schema_menu(pipeline_schema([sends, pools]), workspace=tmp_path)
    assert {p: sorted(models) for p, models in menu.items()} == {"openrouter": ["gpt-x"]}, (
        f"{sorted(menu)}: a model is served under a provider no node sends on"
    )


async def test_a_fresh_workspaces_first_catalogue_fetch_lands_its_snapshot(
    offline: Router, tmp_path: Path
) -> None:
    offline.catalogue = [{"id": "openai/gpt-x"}]
    fresh = tmp_path / "projects" / "fresh"
    assert await ensure_model_capabilities(fresh) == 1, "a fetched catalogue wrote no snapshot"
    assert await ensure_model_capabilities(fresh) == 0, (
        "a fresh workspace's snapshot never landed, so every run refetches the catalogue"
    )


def test_a_rates_file_rewritten_under_a_live_reader_reprices_its_next_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rates = tmp_path / "rates.json"
    monkeypatch.setattr(pricing, "BUNDLED_PATH", rates)
    for usd in (0.000001, 0.0000025):
        row = {"input_cost_per_token": usd, "output_cost_per_token": usd}
        listed = {"models": {"gpt-x": row | {"lister": "openai"}}}
        rates.write_text(json.dumps(listed), encoding="utf-8")
        assert pricing.lookup_rate("gpt-x", "openai") == Rate(usd, usd), (
            "a rewritten rates file left the reader on the rates it read before"
        )


@pytest.mark.usefixtures("run_bound")
def test_a_nested_runs_send_is_admitted_by_both_books_and_billed_on_its_roots_ledger(
    tmp_path: Path, offline: Router
) -> None:
    _priced_wire(offline)
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
    cut = SendBound(input_tokens=600, output_tokens=1500, usd=_HOLD_USD)
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
    assert book.spent.unreported_usd == pytest.approx(_HOLD_USD), (
        "a nested send cut short stayed held on its own book and freed its root's"
    )


@pytest.mark.usefixtures("run_bound")
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
            cell_report=inner_cell_report(
                facts, OuterSampleProxies(floored=0, held_out_lift=0.0, held_out_lift_se=0.02)
            )
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
    cut = SendBound(input_tokens=600, output_tokens=1500, usd=_HOLD_USD)
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
    assert book.spent.unreported_usd == pytest.approx(held_usd + _HOLD_USD), (
        "it was not held at its bound"
    )
    after = unreported_under(charges, held_ledger.path).host
    assert (_unreported_sends(held_ledger), after.unreported_tokens) == (
        sends_before + 1,
        before.unreported_tokens,
    ), "a priceless bill's hold was not counted as out"
    assert after.unreported_usd == pytest.approx(before.unreported_usd + _HOLD_USD), (
        "a priceless hold left its bound"
    )


@pytest.mark.usefixtures("run_bound")
def test_a_resent_call_costs_what_its_admissions_booked(tmp_path: Path, offline: Router) -> None:
    _priced_wire(offline)
    sent: list[int] = []

    def blip_then_answer(_body: dict[str, Any]) -> Said:
        sent.append(1)
        if len(sent) == 1:
            return httpx.Response(500, json={"error": "blip"})
        return completion("ok", model="gpt-x", usd=0.0042)

    offline.llm.says = blip_then_answer
    book = spend_book(0.05)
    with _billing_on(CycleEventLog(tmp_path / "resent.jsonl"), book):
        reply = jumping_clock(_owned_chat("resent"))
    booked = book.spent.sent_usd + book.spent.unreported_usd
    assert len(sent) == 2 and book.spent.unreported_usd > 0, "the 5xx was not resent and held"
    assert reply.cost_usd == pytest.approx(booked), (
        "a call's cost read below what its resent sends booked"
    )


@pytest.mark.usefixtures("run_bound")
def test_the_offline_worlds_llm_resend_waits_no_wall_time(offline: Router) -> None:
    _priced_wire(offline)
    offline.llm.says = lambda _body: (
        completion("ok", model="gpt-x", usd=0.001)
        if offline.llm.sent[1:]
        else httpx.Response(500, json={"error": "blip"})
    )
    started = time.monotonic()
    # The loop's own clock, never a jumping one: what the offline run's legs run on.
    with scoped(book=spend_book()):
        asyncio.run(_owned_chat("resent"))
    waited = time.monotonic() - started
    assert len(offline.llm.sent) == 2 and waited < 0.5, (
        f"{waited:.2f}s for {len(offline.llm.sent)} sends: the world slept a resend's wait"
    )


@pytest.mark.usefixtures("run_bound")
def test_one_unit_of_work_resends_from_one_budget_whichever_loop_asks(offline: Router) -> None:
    wire = _priced_wire(offline)

    async def two_rungs() -> None:
        with scoped(send_budget=SendBudget(None, attempts=SEND_ATTEMPTS)):
            for _rung in range(2):
                with pytest.raises(FailedSendError):
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
            judge_call.ask(
                JudgeStage(role="grade", temperature=0.0, model="gpt-x", provider="openai"),
                "grade",
                judge="j",
            )
        )
    attempts = len(offline.llm.sent)
    assert error and attempts == SEND_ATTEMPTS, (
        f"{attempts} attempts on one grading: a grading opens its own budget, and spends that"
    )
    wire.down = False


@pytest.mark.usefixtures("run_bound")
def test_a_repaired_nodes_resend_is_in_its_sums_never_held_again() -> None:
    # Holding it again halted runs short of their cap.
    repaired = {"step_tokens": {"n": {"attempts": 2, "input": 10, "output": 5, "cost_usd": 0.001}}}
    bill, _ = cell_billing(pipeline_schema([]), {}, {"n": SendBound(40, 20, 0.004)})(repaired)
    assert bill is not None and bill.unknown is None, "an answered attempt was held as unreported"


_CELL_ATTEMPT = SendBound(input_tokens=40, output_tokens=20, usd=0.004)
_WHOLE_CELL = SendBound(input_tokens=100, output_tokens=50, usd=0.01)


def _restarting_backend_faults() -> dict[str, list[Any]]:
    """A restart, a refusal, a 500 that billed a step, a read timeout, and a 504 deadline."""
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
    deadline = {
        "detail": {"error_code": "llm_timeout", "retryable": False, "message": "no reply in 164s"},
        "data": {"step_tokens": None},
    }
    return {
        "/status": [httpx.ConnectError("still restarting")],
        "/matches": [
            httpx.ConnectError("refused"),
            httpx.Response(500, json={"detail": "upstream", "data": billed_step}),
            httpx.ReadTimeout("slow"),
            httpx.Response(504, json=deadline),
        ],
    }


async def _remote_cell(jobs: Path) -> Any:
    remote = backend_client(jobs, session_factory=TermNormSession)
    async with (
        contextlib.aclosing(remote),
        CellEnvelope(600.0, attempts=remote.connector.cell_attempts, label="q"),
    ):
        return await remote.run_query(
            Sample(id=0, query="q", ground_truth=None),
            PipelineParams(),
            bound=_WHOLE_CELL,
            pairs={},
            billed=cell_billing(pipeline_schema([]), {}, {"n": _CELL_ATTEMPT}),
        )


@pytest.mark.usefixtures("run_bound")
def test_a_backend_cell_is_never_sent_again_while_it_may_still_bill(
    tmp_path: Path, offline: Router
) -> None:
    cell_ledger = CycleEventLog(tmp_path / "termnorm.jsonl")
    offline.backend.faults = _restarting_backend_faults()
    offline.backend.asked.clear()
    wallet = spend_book()
    for ends in (FailedSendError, CellSendRefusedError):
        with _billing_on(cell_ledger, wallet), pytest.raises(ends) as ended:
            jumping_clock(_remote_cell(tmp_path / "jobs"))
        if isinstance(ended.value, FailedSendError):
            assert ended.value.outcome.failure is ErrorCategory.CONNECTION, (
                f"the read timeout ended the cell as {ended.value.outcome.failure}"
            )
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


def test_a_reply_names_its_category_the_parameter_it_refused_and_whether_it_may_have_billed() -> (
    None
):
    # The relay's 422 on an answer that failed validation is a generation: it may have billed.
    refused_effort = '{"error": "reasoning_effort: unsupported value"}'
    for status, said, answered_on, read in (
        (422, "", frozenset({422}), (True, None)),
        (422, "", frozenset(), (False, None)),
        (
            400,
            refused_effort,
            frozenset(),
            (False, (ErrorCategory.PARAMETER_REFUSED, "reasoning_effort")),
        ),
        (400, "bad request", frozenset(), (False, (ErrorCategory.CLIENT, None))),
    ):
        outcome = replied(status, headers=None, said=said, answered_on=answered_on)
        billed, named = read
        assert outcome.may_have_billed is billed, f"{status} {said!r}: read as billed, or not"
        if named is not None:
            assert (outcome.failure, outcome.refused_parameter) == named, (
                f"{said!r}: a refusal lost its category or the parameter it names"
            )


@pytest.mark.usefixtures("run_bound")
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
                InProcessWorkload(
                    experiment=harbor.HarborPanel(tasks=[], agent={"environment": "docker"}),
                    experiment_dir=tmp_path,
                    program=None,
                    workspace=None,
                ),
                Sample(id=0, query="task", ground_truth=None, source_pin={}),
                {"config": {harbor.SKILL_DELIVERY_KEY: "agent_skill"}},
                {},
            )

    with pytest.raises(CellInfrastructureError):
        jumping_clock(container_cell())
    assert len(trials) == harbor.CONNECTOR.cell_attempts, (
        f"{len(trials)} trials: a container cell reran past the budget its connector sized"
    )


@pytest.mark.usefixtures("run_bound")
def test_harbors_own_docker_fault_out_of_a_trial_ends_the_cell_on_the_machine_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harbor.trial.trial import Trial  # extras: harbor

    from promptpotter.connectors import harbor  # extras: harbor

    created: list[int] = []

    async def compose_down(*_args: Any, **_kw: Any) -> Any:
        created.append(1)
        # Harbor's own words (`DockerEnvironment._run_docker_compose_command`).
        raise RuntimeError("Docker compose command failed for environment task. Return code: 1.")

    monkeypatch.setattr(Trial, "create", compose_down)
    # The machine claimed off the loop, as a launch claims it, and the producer its trials are
    # labelled with: both restored after.
    monkeypatch.setattr(docker_host, "_claimed", None)
    monkeypatch.setenv(harbor._PRODUCER_ENV, "")
    docker_host.claim_machine(tmp_path / "jobs")
    client = BackendClient(
        harbor.CONNECTOR,
        endpoint=None,
        workload=InProcessWorkload(
            # Harbor's own agent, so no model of ours is bound before the trial is.
            experiment=harbor.HarborPanel(
                tasks=[], agent={"environment": "docker", "name": "oracle"}
            ),
            experiment_dir=tmp_path,
            program=None,
            workspace=None,
        ),
        jobs_dir=tmp_path / "jobs",
    )

    async def container_cell() -> Any:
        async with CellEnvelope(600.0, attempts=harbor.CONNECTOR.cell_attempts, label="task"):
            return await client.run_query(
                Sample(id=0, query="task", ground_truth=None, source_pin={"path": "task"}),
                PipelineParams().with_nodes(
                    {harbor.AGENT_NODE: {harbor.SKILL_DELIVERY_KEY: "agent_skill"}}
                ),
                bound=None,
                pairs={},
                billed=cell_billing(pipeline_schema([]), {}, {}),
            )

    with pytest.raises(CellInfrastructureError) as ended:
        jumping_clock(container_cell())
    assert (ended.value.category, len(created)) == (
        ErrorCategory.CONNECTION,
        harbor.CONNECTOR.cell_attempts,
    ), "Harbor's docker fault reached the cell raw, charged as the prompt's, or went unretried"


def test_a_machine_held_connector_arms_on_its_pool_never_the_process_count(tmp_path: Path) -> None:
    class Phase:
        def __init__(self, out: int) -> None:
            self.calls = out

        def out(self) -> int:
            return self.calls

    # held -> the room a phase with 1 out has, while another client's phase holds 2 on its machine.
    for held, room in ((False, 1), (True, 3)):
        mine, other = (
            backend_client(tmp_path, max_cells_in_flight=4, cells_hold_the_machine=held)
            for _ in range(2)
        )
        phase = Phase(1)
        with other.sending(Phase(2)), mine.sending(phase):
            assert mine.room(phase) == room, (
                f"cells_hold_the_machine={held}: room {mine.room(phase)}, so the machine's "
                "ceiling is counted twice or not shared"
            )


def dspy_billed(net: Router, out: Path) -> dict[str, Any]:
    """The dspy cells, run where dspy is imported: a child process in the offline world, never the
    suite's (its `extras` import costs the suite's process tens of MB it never returns). What each
    paid."""
    import dspy  # extras: dspy
    from dspy import lm15

    from promptpotter.connectors import dspy_module  # extras: dspy

    complete_registries()
    _priced_wire(net)
    jobs = out / "jobs"
    sends: list[int] = []
    script: list[Exception] = []
    keys: list[str | None] = []

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
            # Never called: DSPy connects it, so the cell hands it the wallet's key.
            self.connected = dspy.Predict("question -> answer")
            self.connected.lm = dspy.LM("openai/gpt-x", cache=False)

        def forward(self, question: str) -> Any:
            keys.append(self.connected.lm.kwargs.get("api_key"))
            for step in self.steps:
                answer = step(question=question)
            return answer

    dspy_ledger = CycleEventLog(out / "dspy.jsonl")
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
            endpoint=None,
            workload=InProcessWorkload(
                experiment=None, experiment_dir=None, program=program, workspace=None
            ),
            jobs_dir=jobs,
        )
        config = {"prompt": "Answer.", "model": "openai/gpt-x", "max_calls": 2, "max_tokens": 50}
        pairs = {
            dspy_module.PROGRAM_NODE: client.connector.priced_as(NodeCall.model_validate(config))
        }

        async def one_cell() -> Any:
            async with CellEnvelope(600.0, attempts=client.connector.cell_attempts, label="q"):
                return await client.run_query(
                    Sample(id=0, query="q", ground_truth="4"),
                    PipelineParams().with_nodes({dspy_module.PROGRAM_NODE: config}),
                    bound=SendBound(input_tokens=20_000, output_tokens=2_000, usd=0.5),
                    pairs=pairs,
                    billed=cell_billing(pipeline_schema([]), pairs, {}),
                )

        # A real clock: the student runs in a worker thread, which a jumping clock outruns.
        with _billing_on(dspy_ledger, purse):
            data, spent = asyncio.run(one_cell())
        rows.append(spent)
        return cast("dict[str, Any]", data)

    def lm_bills() -> list[tuple[int, int, float | None, float | None]]:
        return [
            (r.input_tokens, r.output_tokens, r.price.cost_usd, r.price.rate_priced_usd)
            for r in _token_bills(dspy_ledger)
            if r.input_tokens
        ]

    def ended(calls: int, *fails: Exception) -> list[Any]:
        """How the cell ended: answered, halted, a hole and its category, or the send's close."""
        try:
            dspy_cell(calls, *fails)
        except CellHaltedError:
            return ["halted", None, None]
        except CellSendRefusedError as refusal:
            return ["refused", refusal.category, None]
        except CellInfrastructureError as hole:
            return ["infrastructure", hole.category, None]
        except FailedSendError as refused:
            return ["closed", refused.outcome.failure, refused.outcome.status]
        return ["answered", None, None]

    account = workspace()
    with (
        scoped(account=account),
        scoped(ledger=CycleEventLog(out / "bound.jsonl")),
        scoped(round=1),
    ):
        # The one purse every cell bills, laid once the account it draws on is bound.
        purse = spend_book(1.0)
        answered = dspy_cell(2)
        replays = CycleEventLog(out / "replays.jsonl")
        replay_arm = spend_book(1.0, meters="search_incurred", metered_on=replays.path)
        with _billing_on(replays, replay_arm):
            emit_replayed_step_tokens(rows[-1].step_tokens, {})
        step = rows[-1].step_tokens[dspy_module.PROGRAM_NODE]
        paid = {
            "score": answered[dspy_module.SCORE_KEY],
            "row_input": answered["step_tokens"][dspy_module.PROGRAM_NODE]["input"],
            "bills": lm_bills(),
            "sent_usd": purse.spent.sent_usd,
            "row_route": [step.model, step.provider],
            "row_usd": step.rate_priced_usd,
            "replay_usd": replay_arm.spent.sent_usd,
        }
        paid["halted"] = [ended(3)[0], len(sends), len(lm_bills())]
        paid["holes"] = [
            ended(1, fail)
            for fail in (
                lm15.TransportError("refused"),
                lm15.BillingError("Insufficient credits", status=402),
                lm15.InvalidRequestError("Your credit balance is too low", status=400),
                lm15.InvalidRequestError("unknown field", status=400),
                lm15.StreamAssemblyError("the reply broke off"),
            )
        ]
        account.api_keys.put("openai", SecretStr("sk-own"))
        dspy_cell(1)
        paid["keys"] = [keys[0], keys[-1]]
    return paid


@pytest.fixture
def dspy_paid(dspy_child: dict[str, Any]) -> dict[str, Any]:
    """`dspy_billed`, as `conftest.py::dspy_child` ran it."""
    paid: dict[str, Any] = dspy_child["spend"]
    return paid


def test_a_dspy_cell_bills_each_lm_call_and_its_row_states_the_price_a_replay_reads(
    dspy_paid: dict[str, Any],
) -> None:
    call_usd = 11 * 1e-6 + 3 * 2e-6
    assert dspy_paid["score"] == 1.0, "a DSPy cell that answered scored another"
    assert dspy_paid["row_input"] == 2 * 11, "a DSPy cell's row lost a call's tokens"
    bills = dspy_paid["bills"][:2]
    assert [(i, o, cost) for i, o, cost, _ in bills] == [(11, 3, None)] * 2, (
        "a student's call left no bill of its own, or one at a cost the engine never reported"
    )
    assert [rated for *_, rated in bills] == pytest.approx([call_usd] * 2), (
        "the engine reports tokens and no cost: each call is priced at our rate"
    )
    assert dspy_paid["sent_usd"] == pytest.approx(2 * call_usd), "the ceiling bound another price"
    assert dspy_paid["row_route"] == ["gpt-x", "openai"], (
        "the ROW names no route its price could be read on"
    )
    assert dspy_paid["row_usd"] == pytest.approx(2 * call_usd), (
        "the ROW states another price: the fitness cost term and a replay's ceiling read it"
    )
    assert dspy_paid["replay_usd"] == pytest.approx(2 * call_usd), (
        "a replayed DSPy cell read as free"
    )


def test_a_dspy_cells_lm_sends_on_the_wallets_key_the_accounts_own_first(
    dspy_paid: dict[str, Any],
) -> None:
    assert dspy_paid["keys"] == [NOT_A_KEY, "sk-own"], (
        "a DSPy LM read the process environment's key, or the box's paid past the account's own"
    )


def test_a_dspy_cell_past_its_bound_halts_and_bills_the_calls_it_made(
    dspy_paid: dict[str, Any],
) -> None:
    # An answered cell first, whose two bills the halted cell's two join.
    halted, sends, bills = dspy_paid["halted"]
    assert halted == "halted", "a cell asking past its bound was not halted"
    assert sends == 2, f"{sends} calls sent under a bound sized on two"
    assert bills == 4, "the calls a halted cell had made went unbilled"


def test_a_dspy_cell_reads_a_providers_failure_as_a_hole_and_ends_on_its_sends_close(
    dspy_paid: dict[str, Any],
) -> None:
    # Refused, out of credit (402, and a 400 saying so), a 400 the close reads as ours, then a
    # status-less failure, which leaves the program typed as its close, never raw.
    assert dspy_paid["holes"] == [
        ["infrastructure", ErrorCategory.CONNECTION, None],
        ["refused", ErrorCategory.PROVIDER_CREDIT, None],
        ["refused", ErrorCategory.PROVIDER_CREDIT, None],
        ["closed", ErrorCategory.CLIENT, 400],
        ["closed", ErrorCategory.UNKNOWN, None],
    ], "a provider's failure scored a cell, or the cell re-read the raw error past its close"


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

    async def episode_run(*_args: Any) -> dict[str, Any]:
        seams.append(
            seam := harbor._AgentModel(
                NodeCall(model="gpt-x", provider="openai", max_tokens=100), 1000
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
                pairs={},
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


@pytest.mark.usefixtures("run_bound")
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
        seam = harbor._AgentModel(NodeCall(model="gpt-x", provider="openai", max_tokens=100), 1000)
        with (
            scoped(send_budget=SendBudget(None, attempts=SEND_ATTEMPTS)),
            pytest.raises(FailedSendError),
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


@pytest.mark.usefixtures("run_bound")
def test_an_agents_own_retries_resend_nothing_past_the_one_send_budget(offline: Router) -> None:
    from promptpotter.connectors import harbor  # extras: harbor

    wire = _priced_wire(offline)
    wire.down = True

    async def retried_turn() -> None:
        seam = harbor._AgentModel(NodeCall(model="gpt-x", provider="openai", max_tokens=100), 1000)
        with scoped(send_budget=SendBudget(None, attempts=SEND_ATTEMPTS)):
            # terminus-2's `_query_llm` tries a failed query three times.
            for _try in range(3):
                with pytest.raises(FailedSendError):
                    await seam.call("turn")

    with scoped(book=spend_book()):
        offline.llm.sent.clear()
        jumping_clock(retried_turn())
    wire.down = False
    assert len(offline.llm.sent) == SEND_ATTEMPTS, (
        f"{len(offline.llm.sent)} sends: the agent's retries resent past the one budget's "
        f"{SEND_ATTEMPTS}"
    )


# 5. The host coupon


_WEB = "https://accounts.google.com"
_FACE = Budget.amount(settings.HOST_COUPON.usd or 0.0, settings.HOST_COUPON.tokens or 0)
# A remote backend's cells, which go out on the box's keys.
_CELLS = (BackendCells("termnorm", "host", priced=True),)
# Cells no rate bounds: a dollar ceiling on them is refused at the mint.
_UNPRICED_CELLS = (BackendCells("n8n", "host", priced=False),)
_OWN_ONLY = (ProviderSend("openrouter", "user"),)
# A grant set on an account opened before any charge: its coupon draws on the whole bill.
_OPENED = "2000-01-01T00:00:00Z"


def _other(user: User, tenant: str) -> User:
    """*user* as an account of its own in the fact's home."""
    return user.model_copy(update={"user_id": tenant, "tenant_id": tenant})


def _account_stores(
    *,
    issuer: str | None,
    user: User,
    spent: dict[str, Any] | None,
    face: Budget | None = None,
    own_key: str | None = None,
) -> Stores:
    """`issuer` set makes this a WEB identity; the box operator has none and is unmetered. Its
    charges hold what the box's keys *spent* for it."""
    stores = workspace(
        IdentityContext(
            user_id=UserId(user.user_id),
            tenant_id=TenantId(user.tenant_id),
            email="",
            host=issuer is None,
        ),
    )
    stores.users.save(user)
    if face is not None:
        stores.grant.set(face, afresh=False, opened_at=_OPENED)
    if own_key is not None:
        stores.api_keys.put(own_key, SecretStr("sk-own"))
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
        user_id=str(stores.identity.user_id),
        principal_id=acting_principal(stores.identity).id,
        workspace=stores.campaigns.workspace,
        hop=UNRESOLVED_HOP,
        holder=CampaignLaunch(dataset_name="d"),
    )
    if admitted:
        _admitted(stores, registry, held.job_id, reserve)
        registry.move(held.job_id, LaunchMove.START)


async def _turn(stores: Stores, _net: Router) -> None:
    async with paid_call(stores=stores, bucket="turn", provider="openrouter"):
        keyed_client("openrouter")


async def _bench(stores: Stores, net: Router) -> None:
    hop = (await asyncio.to_thread(_minted, stores, net)).hop
    await grade_line_bench(stores=stores, at=_at(stores, hop), acting="bench")


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
    assert pricing.refresh_rates(force=True).fault is None, "the world's price list was not fetched"
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


class _Door(NamedTuple):
    """One paid door's admission on one account: what it admits (or the refusal it raises) and the
    coupon's reserve for it."""

    admits: tuple[float | None, int | None] | type[Exception]
    reserves: tuple[float | None, float | None] | None = None
    issuer: Any = None
    face: Budget | None = None
    # `None`: a backend's cells.
    sends: tuple[Send, ...] | None = None
    declared: float | None = None
    running: dict[str, Any] | None = None
    spent: dict[str, Any] | None = None
    # `None`: a launch.
    paid: Callable[[Stores, Router], Coroutine[Any, Any, None]] | None = None


def _doors() -> tuple[_Door, ...]:
    """The coupon bounds only what the box's keys pay."""
    full = Budget(usd=_FACE.usd, tokens=_FACE.tokens)
    hand_set = Budget(usd=50.0, tokens=50_000)
    unadmitted = {"admitted": False, "reserve": Budget()}
    return (
        # A hand-set grant of the default face: off the free tier, whose ceiling binds below.
        _Door(_UNBOUNDED, (0.30, _FACE.tokens), _WEB, full),
        # A USD ceiling holds no tokens: every send under it is priced, so the dollars bound it.
        _Door((0.15, None), (0.30, None), _WEB, full, declared=0.15),
        _Door(HostAllowanceExhaustedError, issuer=_WEB, face=full, declared=10.0),
        # The free tier's ceiling bounds what a launch holds, a lift's included (bl2.B5).
        _Door((0.03, None), (0.06, None), _WEB),
        # A run none of whose sends the box's keys pay is refused nothing and reserves nothing.
        _Door((10.0, None), (0.0, 0), _WEB, sends=_OWN_ONLY, declared=10.0),
        _Door(_UNBOUNDED, (0.0, 0), _WEB, sends=_OWN_ONLY, running=unadmitted),
        _Door(
            HostAllowanceExhaustedError,
            issuer=_WEB,
            running={"admitted": True, "reserve": full},
        ),
        _Door(QuotaExceededError, issuer=_WEB, running=unadmitted),
        _Door(
            _UNBOUNDED,
            (settings.UNPRICED_GRACE_USD, _FACE.token_amount - 500_000),
            _WEB,
            full,
            spent=_UNPRICEABLE,
        ),
        _Door(_UNBOUNDED, _UNBOUNDED),
        _Door((10.0, None), (20.0, None), _WEB, hand_set, declared=10.0),
        _Door(HostAllowanceExhaustedError, issuer=_WEB, spent=_SPENT_THE_COUPON),
        _Door(_UNBOUNDED, (0, 0), _WEB, sends=_OWN_ONLY, spent=_SPENT_THE_COUPON),
        # A provider no key pays is refused first, on any account, before anything is held.
        _Door(NoApiKeyError, sends=(*_CELLS, ProviderSend("groq", None))),
        # Cells no rate bounds: refused under the run's dollar cap or the coupon's, never in tokens.
        _Door(BudgetUnenforceableError, sends=_UNPRICED_CELLS, declared=10.0),
        _Door(BudgetUnenforceableError, issuer=_WEB, sends=_UNPRICED_CELLS),
        _Door(_UNBOUNDED, _UNBOUNDED, sends=_UNPRICED_CELLS),
        # An own key frees its provider's sends, never a backend's cells.
        _Door(
            HostAllowanceExhaustedError,
            issuer=_WEB,
            sends=(*_CELLS, *_OWN_ONLY),
            spent=_SPENT_THE_COUPON,
        ),
        *(
            _Door(HostAllowanceExhaustedError, issuer=_WEB, spent=_SPENT_THE_COUPON, paid=paid)
            for paid in (_turn, _bench)
        ),
    )


def _launch(
    door: _Door, user: User, stores: Stores, registry: JobRegistry
) -> tuple[Budget, Budget]:
    return admit_launch(
        declared=Budget() if door.declared is None else Budget(usd=door.declared),
        user=user,
        stores=stores,
        job_registry=registry,
        job_id="job-a",
        campaign_id=None,
        sends=_CELLS if door.sends is None else door.sends,
    )


@pytest.mark.usefixtures("run_bound")
def test_every_paid_door_admits_its_declared_ceiling_and_holds_what_the_coupon_has_left(
    tmp_path: Path, offline: Router
) -> None:
    for n, door in enumerate(_doors()):
        # A home per door, as an install is, so no door reads another's jobs or spend.
        with pytest.MonkeyPatch.context() as home:
            home.setenv("PROMPTPOTTER_HOME", str(tmp_path / f"door-{n}"))
            user = User(user_id="sub-9", tenant_id="sub-9", created_at="2026-01-01")
            stores = _account_stores(
                issuer=door.issuer, user=user, spent=door.spent, face=door.face
            )
            registry = JobRegistry(stores.jobs_dir, capacity=lambda _live: 1)
            if door.running is not None:
                _running(stores, registry, **door.running)

            if isinstance(door.admits, type):
                with pytest.raises(door.admits) as refused:
                    if door.paid is None:
                        _launch(door, user, stores, registry)
                    else:
                        asyncio.run(door.paid(stores, offline))
                # The refusal names the ceiling that binds: only the run's own is the run's to drop.
                if isinstance(refused.value, BudgetUnenforceableError):
                    binds = refused.value.details["binds"]
                    assert binds == ("allowance" if door.issuer else "run"), (
                        f"door {n}: names {binds}"
                    )
                continue
            knocked, reserve = _launch(door, user, stores, registry)
            assert (knocked.usd, knocked.tokens) == door.admits, (
                f"door {n}: a launch was admitted under another ceiling than its campaign declared"
            )
            assert (reserve.usd, reserve.tokens) == pytest.approx(door.reserves), (
                f"door {n}: the coupon held back another amount than it has left for the run"
            )


@pytest.mark.usefixtures("run_bound")
def test_a_cycle_retaken_after_a_killed_bench_pass_closes_its_dead_bracket(
    offline: Router,
) -> None:
    user = User(user_id="sub-9", tenant_id="sub-9", created_at="2026-01-01")
    killed = _account_stores(issuer=_WEB, user=user, spent=None)
    held = _minted(killed, offline).hop
    CycleEventLog.open(CycleDir(killed.campaigns.cycle_dir(held))).append(
        PhaseRecord(phase=CampaignPhase.BENCH, step="enter")
    )

    async def _retaken() -> bool:
        async with paid_pass(
            stores=killed,
            pass_kind=MeasurementRole.BENCH,
            at=_at(killed, held),
            declared=Budget(),
            sends=_CELLS,
        ):
            return live_cycle_run(killed, held).pass_open

    assert not asyncio.run(_retaken()), (
        "a cycle retaken after a killed bench pass read the dead bracket as a pass in flight"
    )


@pytest.mark.usefixtures("run_bound")
def test_an_accounts_own_key_wins_and_the_boxs_key_sends_only_on_a_live_coupon(
    monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    # Per provider; the box's refusal names the provider an own key would free.
    user = User(user_id="sub-9", tenant_id="sub-9", created_at="2026-01-01")
    spent_out = _account_stores(
        issuer=_WEB, user=user, spent=_SPENT_THE_COUPON, own_key="openrouter"
    )
    own = paying_key(spent_out.api_keys.load(), "openrouter")
    assert own is not None and (own.paid_by, own.secret.get_secret_value()) == ("user", "sk-own"), (
        "an account's own key lost to the box's"
    )
    asyncio.run(_turn(spent_out, offline))

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
    assert status.own_key_spend.bill == "$5.00", "the own key's spend went unread"
    payers = {route.provider: route.payer for route in status.routes}
    assert (payers["openrouter"], payers["openai"]) == ("user", "host"), (
        "the served payer of a provider is not the one its sends go out on"
    )
    fresh = _account_stores(issuer=_WEB, user=_other(user, "fresh"), spent=None)
    with monkeypatch.context() as patched:
        patched.setattr(settings, "GROQ_API_KEY", SecretStr(""))
        assert provider_send(fresh.api_keys.load(), "groq").payer is None, (
            "a provider no key holds read as paid"
        )


def test_a_cell_keying_its_own_sends_is_paid_as_its_providers_keys_pay() -> None:
    # A spent coupon and an own openrouter key: a library's cells on it reserve no coupon.
    user = User(user_id="sub-7", tenant_id="sub-7", created_at="2026-01-01")
    stores = _account_stores(issuer=_WEB, user=user, spent=_SPENT_THE_COUPON, own_key="openrouter")
    remote = connectors.CONNECTORS.get("termnorm")
    library = dataclasses.replace(remote, holds_own_sends=True, model_names_provider=True)
    table: list[tuple[Connector, list[str | None], str]] = [
        (library, ["openrouter/x"], "user"),
        (library, ["openrouter/x", "openai/y"], "host"),
        (library, [None], "host"),
        (library, [], "host"),
        (remote, ["openrouter/x"], "host"),
    ]
    for connector, models, payer in table:
        config = defaulted_config(
            {"pipeline_overlay": {f"n{i}": {"model": m} for i, m in enumerate(models)}}
        )
        cells, *_ = sends_on(stores, config, connector)
        assert (cells.payer, sends_draw_coupon((cells,))) == (payer, payer == "host"), (
            f"{models} on a {'library' if connector.holds_own_sends else 'remote'} backend "
            f"was planned as paid by {cells.payer}"
        )


# One bill, read by each coupon the host sets over it in turn.
def test_a_coupon_the_host_sets_is_served_and_afresh_draws_from_now_leaving_the_bill_whole() -> (
    None
):
    user = User(user_id="sub-9", tenant_id="sub-9", created_at="2026-01-01T00:00:00Z")
    stores = _account_stores(issuer=_WEB, user=user, spent=_SPENT_THE_COUPON)
    projects = default_projects_root()
    bill = stores.charges.path.read_bytes()
    lifetime = coupon_status(stores).host_key_spend
    assert coupon_status(stores).used_share == 1.0, "the box's default coupon was not drawn whole"
    set_coupon(projects, "sub-9", Budget(usd=2 * _FACE.usd_amount), afresh=False)
    assert coupon_status(stores).used_share == pytest.approx(0.5), (
        "a coupon set over the bill read another draw than the bill since the account opened"
    )
    set_coupon(projects, "sub-9", Budget(usd=_FACE.usd_amount), afresh=True)
    fresh = coupon_status(stores)
    assert (fresh.used_share, account_wallet(stores).state) == (0.0, AllowanceState.OPEN), (
        "a coupon started afresh still drew what was charged before it"
    )
    assert (fresh.host_key_spend, stores.charges.path.read_bytes()) == (lifetime, bill), (
        "a start afresh moved the bill or the account's lifetime spend"
    )
    set_coupon(projects, "sub-9", Budget(usd=_FACE.usd_amount / 2), afresh=False)
    assert coupon_status(stores).used_share == 0.0, "a new face undid the start afresh"
    with pytest.raises(NotFoundError):
        set_coupon(projects, "sub-0", Budget(usd=1.0), afresh=False)


def test_a_grant_file_without_its_start_does_not_load() -> None:
    # Read as granted this instant, it would never age: the coupon would draw nothing it was charged.
    stores = workspace()
    stores.grant.path.parent.mkdir(parents=True, exist_ok=True)
    stores.grant.path.write_text(json.dumps({"face": {"usd": 1.0}}), encoding="utf-8")
    with pytest.raises(FileUnreadableError):
        stores.grant.load()


@pytest.mark.usefixtures("run_bound")
def test_a_reserve_its_ceiling_sized_halts_the_run_on_that_ceiling_never_the_allowance() -> None:
    # `quota.py::_reserve` holds twice a $0.03 ceiling: sends its meter skips drew it, not the face.
    send = SendBound(input_tokens=1, output_tokens=1, usd=0.04)
    for reserve, stop in ((0.06, StopReason.SPEND_BUDGET), (0.05, StopReason.HOST_ALLOWANCE)):
        book = spend_book(0.03, usd_reserve=reserve, meters="search_incurred")
        book.hold(send, _BENCH, what="b", paid_by="host", ledger="b")
        with pytest.raises(SendRefusedError) as past:
            book.hold(send, _BENCH, what="b", paid_by="host", ledger="b")
        assert run_stop_reason(past.value) is stop, (
            f"a run past a ${reserve} reserve of a $0.03 ceiling halted as another stop"
        )


@pytest.mark.usefixtures("run_bound")
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


@pytest.mark.usefixtures("run_bound")
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


@pytest.mark.usefixtures("run_bound")
def test_the_free_tier_holds_an_ungranted_web_account_and_no_other() -> None:
    user = User(user_id="sub-9", tenant_id="sub-9", created_at="2026-01-01")
    hand_set = Budget(usd=50.0, tokens=50_000)
    for n, (issuer, face, on_tier) in enumerate(
        ((_WEB, None, True), (None, None, False), (_WEB, hand_set, False))
    ):
        stores = _account_stores(
            issuer=issuer, user=_other(user, f"tier-{n}"), spent=None, face=face
        )
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
def free_fence() -> _FreeFence:
    """A web account's fence on the box's keys and on its own key, and a check-in laid under it."""
    user = User(user_id="sub-9", tenant_id="sub-9", created_at="2026-01-01")
    boxed = free_tier.free_fence(
        _account_stores(issuer=_WEB, user=_other(user, "boxed"), spent=None),
        connectors.CONNECTORS.get("termnorm"),
    )
    keyed = free_tier.free_fence(
        _account_stores(issuer=_WEB, user=_other(user, "keyed"), spent=None, own_key="openrouter"),
        connectors.CONNECTORS.get("termnorm"),
    )
    assert boxed is not None and keyed is not None and boxed.task and keyed.task, (
        "a backend's cells, paid by the box whatever the account holds, left the tier's fence"
    )
    oss = "openai/gpt-oss-20b:nitro"
    config = _budgeted(Budget(usd=UNBOUNDED))
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
    layered = config.overlaid(*free_tier.free_tier_layers(schema, boxed))
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
    assert pricing.refresh_rates(force=True).fault is None, "the world's price list was not fetched"
    assert (pricing.route_priced("x", "openai"), pricing.route_priced("dear", "openai")) == (
        False,
        True,
    ), "a dollar ceiling would admit a route no rate prices"


@pytest.mark.usefixtures("run_bound")
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


@pytest.mark.usefixtures("run_bound")
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
    tiered = config.overlaid(*free_tier.free_tier_layers(searched, boxed))
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
        return layered.overlaid(Layer("campaign", overlay))

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
    assert isinstance(free_tier.outside_free_tier_refusal(off), PayloadInvalidError)


@pytest.mark.usefixtures("run_bound")
def test_a_later_layer_naming_another_model_drops_the_earlier_layers_route() -> None:
    routed: dict[str, object] = {
        "pipeline_overlay": {"llm_only": {"model": "a/x", "route_order": ["Groq"]}}
    }
    later = {"pipeline_overlay": {"llm_only": {"model": "b/y"}}}
    moved = fold_layers(CAMPAIGN_RULE, [Layer("dataset", routed), Layer("campaign", later)])
    assert moved.document["pipeline_overlay"] == {"llm_only": {"model": "b/y"}}, (
        "a campaign layer moved the model under a route naming the old model's hosts"
    )


@pytest.mark.usefixtures("run_bound", "offline")
def test_the_fence_walks_every_call_a_run_sends_a_judges_model_included(
    free_fence: _FreeFence,
) -> None:
    boxed, _, oss, _, schema, layered = free_fence
    judge = {
        "name": "j",
        "stages": [
            {"role": "grade", "model": "openai/dear", "provider": "openrouter", "temperature": 0.0}
        ],
    }
    judged = layered.overlaid(Layer("campaign", {"judges": {"j": judge}}))
    tiered_params = PipelineParams.model_validate(
        {"steps": ["llm_only"], "llm_only": {"model": oss, "reasoning_effort": "low"}}
    )
    assert free_tier.outside_free_tier(
        judged, select_optimizer(judged.optimization), tiered_params, schema, boxed
    ) == ["the judge model openai/dear"], "a judge on a model the tier does not list ran free"


@pytest.mark.usefixtures("run_bound")
def test_a_paid_verb_never_bills_a_cycle_its_producer_still_holds(offline: Router) -> None:
    operator = User(user_id="op", tenant_id="op", created_at="")
    held_stores = _account_stores(issuer=None, user=operator, spent=None)
    held = _minted(held_stores, offline).hop
    cycle_dir = held_stores.campaigns.cycle_dir(held)
    hold_cycle(cycle_dir)

    async def bill_held() -> None:
        async with paid_pass(
            stores=held_stores,
            pass_kind=MeasurementRole.BENCH,
            at=_at(held_stores, held),
            declared=Budget(),
            sends=_CELLS,
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


def _web_account(sub: str) -> Stores:
    return workspace(
        IdentityContext(
            user_id=UserId(sub),
            tenant_id=TenantId(sub),
            claims={},
            capabilities=frozenset({"campaign.create"}),
        ),
    )


@pytest.mark.usefixtures("run_bound")
def test_a_scoring_pass_holds_a_slot_and_its_reservation_against_a_launch_beside_it() -> None:
    passing = _web_account("sub-8")
    passing.grant.set(Budget(usd=1.0), afresh=False, opened_at=_OPENED)
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
                campaign_id=None,
                sends=_CELLS,
            )[1]
        finally:
            slots.release(launch.job_id)

    async def pass_beside() -> None:
        async with paid_pass(
            stores=passing,
            pass_kind=MeasurementRole.VERIFY,
            at=None,
            declared=Budget(),
            sends=_CELLS,
        ) as pass_book:
            assert pass_book.reserve.usd == pytest.approx(1.0), (
                "a pass declared nothing and did not hold the coupon's headroom"
            )
            assert pass_book.allotment.usd is None, "a pass that declared nothing took a ceiling"
            assert (
                len(machine_status(slots, identity=passing.identity, account_limit=1).running) == 1
            ), "a paid pass ran beside the machine's slot count"
            with pytest.raises(HostAllowanceExhaustedError):
                await asyncio.to_thread(launch_beside)

    asyncio.run(pass_beside())
    assert not machine_status(slots, identity=passing.identity, account_limit=1).running, (
        "a pass kept its slot past its exit"
    )
    assert launch_beside().usd == pytest.approx(1.0), "the pass kept its reservation past its exit"


# The uncap arms over the round cap and token ceiling the changes before it moved.
@pytest.mark.usefixtures("run_bound")
def test_a_budget_change_leaves_the_axis_it_did_not_touch_alone(offline: Router) -> None:
    metered = _web_account("sub-9")
    minted = _minted(metered, offline)
    hop, campaign = minted.hop, minted.campaign
    assert campaign is not None, "a minted session names its campaign"
    registry = JobRegistry(metered.jobs_dir, capacity=lambda _live: 1)
    job = registry.request_slot(
        user_id="sub-9",
        principal_id="sub-9",
        workspace=metered.campaigns.workspace,
        holder=CampaignLaunch("ds1"),
        hop=hop,
    )
    claim_cycle(metered, hop, job.job_id)
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
    assert metered.campaigns.read_campaign_ceiling(campaign).budget == Budget(tokens=1_000), (
        "a money ceiling landed on one cycle, not the campaign every cycle shares"
    )
    # An UNCAP lifts the campaign's ceiling; the account still binds it through the reservation.
    armed = _change_run_limits(dispatcher, hop, Budget(usd=UNBOUNDED))
    assert armed == Budget(usd=UNBOUNDED, tokens=1_000, rounds=3), (
        "the armed ceiling is not what a stop fires at: the campaign's money and the cycle's own"
    )
    lifted, held = (
        metered.campaigns.read_campaign_ceiling(campaign).budget,
        registry.get(job.job_id),
    )
    assert lifted == Budget(usd=UNBOUNDED, tokens=1_000), (
        "an uncapped campaign ceiling was stored as the account's headroom"
    )
    assert held is not None and cycle_share(metered, held).usd is None, (
        "an uncap became a bounded share"
    )
    assert _reserve_of(metered, job.job_id).usd is not None, (
        "an uncap left the account nothing held back for the run"
    )


@pytest.mark.usefixtures("run_bound")
def test_arms_asked_in_one_tick_are_admitted_whatever_order_their_ids_draw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One tick ranks its jobs by their random ids; an arm admitted before an earlier-ranked
    # sibling still asked reads it contending, and refuses the whole act.
    host = _web_account("sub-arms")
    host.grant.set(Budget(usd=10.0), afresh=False, opened_at=_OPENED)
    monkeypatch.setattr(registry_mod, "utcnow_iso", lambda: _OPENED)
    admitted = admit_arms(
        [(Budget(usd=0.1), _CELLS)] * 6,
        stores=host,
        job_registry=JobRegistry.attach(host.jobs_dir),
        holder=EmbeddedRun("ds1"),
    )
    assert all(held.admitted.usd == pytest.approx(0.1) for _, held in admitted), (
        "an arm was admitted at another arm's budget"
    )


@pytest.mark.usefixtures("run_bound")
def test_a_moved_ceiling_counts_every_cycles_spend_against_the_campaign_once(
    tmp_path: Path, offline: Router
) -> None:
    # A fork's walked prefix not twice, and what the parent billed past the cut counts too.
    forker = _web_account("sub-7")
    root, fork = _line(World(forker, offline, tmp_path / "forker"), 1)

    def bill(at: CycleHop, usd: float) -> None:
        with scoped(account=forker):
            ledger = CycleEventLog.open(CycleDir(forker.campaigns.cycle_dir(at)))
            _pay(ledger, usd, input_tokens=1_000, output_tokens=0)

    bill(root, 0.2)
    bill(fork, 0.3)
    registry = JobRegistry(forker.jobs_dir, capacity=lambda _live: 1)
    job = registry.request_slot(
        user_id="sub-7",
        principal_id="sub-7",
        workspace=forker.campaigns.workspace,
        holder=CampaignLaunch("ds1"),
        hop=fork,
    )
    claim_cycle(forker, fork, job.job_id)
    _admitted(forker, registry, job.job_id, Budget(usd=0.8))
    hold_moved(forker, fork, Budget(usd=0.8))
    forker.grant.set(Budget(usd=1.0), afresh=False, opened_at=_OPENED)
    allowance = account_of(forker)
    # A changed ceiling is admitted as a launch is: dollars its cells cannot be held to refuse.
    with pytest.raises(BudgetUnenforceableError):
        change_budget(
            Budget(usd=5.0),
            stores=forker,
            job_registry=registry,
            at=_at(forker, fork),
            sends=_UNPRICED_CELLS,
        )
    at = _at(forker, fork)
    change_budget(Budget(usd=5.0), stores=forker, job_registry=registry, at=at, sends=_CELLS)
    caps = forker.campaigns.read_campaign_ceiling(at.campaign).budget
    _, relaunched = admit_launch(
        declared=Budget(),
        user=allowance,
        stores=forker,
        job_registry=registry,
        job_id=job.job_id,
        campaign_id=fork.campaign_id,
        sends=_CELLS,
    )
    assert caps.usd == pytest.approx(1.0), (
        "$1.00 allowance, $0.50 of it the campaign's own: its ceiling is the whole $1.00, which "
        "already counts that $0.50 — not $1.10 off a fork prefix counted twice"
    )
    assert relaunched.usd == _reserve_of(forker, job.job_id).usd == pytest.approx(0.5), (
        "the reservation held past the $0.50 the coupon has left"
    )


@pytest.mark.usefixtures("run_bound")
def test_a_clamped_spend_cap_reads_asked_armed_and_the_campaigns_own_spend(
    tmp_path: Path, offline: Router
) -> None:
    # A cap read against another book than its own, or a clamp nobody hears of, misleads a spend.
    clamped = _web_account("sub-6")
    root, sibling = _line(World(clamped, offline, tmp_path / "clamped"), 1)
    for at, usd in ((root, 0.2), (sibling, 0.3)):
        with scoped(account=clamped):
            ledger = CycleEventLog.open(CycleDir(clamped.campaigns.cycle_dir(at)))
            _pay(ledger, usd, input_tokens=1_000, output_tokens=0)
    registry = JobRegistry(clamped.jobs_dir, capacity=lambda _live: 1)
    clamped.grant.set(Budget(usd=1.0), afresh=False, opened_at=_OPENED)
    dispatcher = CommandDispatcher(clamped, registry)
    armed = _change_run_limits(dispatcher, sibling, Budget(usd=5.0))
    assert armed.usd == pytest.approx(1.0), "a $5 ask past the $1 allowance was not clamped"
    spend = next(cap for cap in current_caps(clamped, sibling).caps if cap.axis == "usd")
    assert spend.asked == pytest.approx(5.0) and spend.clamped_by == "allowance", (
        "the clamp of a $5 ask to the $1 allowance went unsaid"
    )
    assert spend.used == pytest.approx(0.5) and spend.fill == pytest.approx(0.5), (
        "the campaign's cap was read against one cycle's spend, not the campaign's $0.50"
    )


@pytest.mark.usefixtures("run_bound")
def test_the_served_spend_cap_reads_the_count_its_books_stop_reads(offline: Router) -> None:
    # A cap served short of its book's count halts a run on money no door showed.
    stores = _web_account("sub-9")
    hop = _minted(stores, offline).hop
    ledger = CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(hop)))
    with scoped(account=stores):
        _pay(ledger, 0.2, input_tokens=1_000, output_tokens=0)
    # A cell that broke mid-send under its run's job: no bill, so it lapsed at its bound.
    job, cell, bound = uuid.uuid4().hex, uuid.uuid4().hex, Budget(usd=0.3, tokens=0)
    for record in (
        Hold(
            hold_id=job,
            parent=None,
            key_source="host",
            bound=Budget(usd=1.0, tokens=0),
            resource=None,
            node=None,
            ledger=None,
        ),
        Hold(
            hold_id=cell,
            parent=job,
            key_source="host",
            bound=bound,
            resource="backend",
            node="cell",
            ledger=stores.charges.held_on(ledger.path),
        ),
        Lapse(hold_id=cell, bound=bound),
        Release(hold_id=job),
    ):
        stores.charges.append(record)
    book = SpendBook(
        declared=Budget(),
        share=Budget(usd=0.5),
        charges=stores.charges,
        hold_id=job,
        headroom=Budget(),
        metered_on=ledger.path,
        meters=ceiling_meter(_at(stores, hop).campaign.declaration.purpose),
        opened=0.0,
    )
    assert book.exhausted() is ErrorCategory.SPEND_CEILING, "vacuous — the lapse never tipped it"
    spend = next(cap for cap in current_caps(stores, hop).caps if cap.axis == "usd")
    counted = book.count().counted.usd_amount
    assert counted > 0.2 and spend.used == pytest.approx(counted), (
        "the served cap read the bills alone, short of the lapsed cell its book's stop counts"
    )


@pytest.mark.usefixtures("run_bound")
def test_a_door_reads_a_live_runs_bench_set_aside_as_its_book_stops_on_it(offline: Router) -> None:
    # C0 run__265c4d's shape: billed, a lapsed cell and the bench's set-aside tip the cap together.
    stores = _web_account("sub-9")
    hop = _minted(stores, offline).hop
    ledger = CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(hop)))
    with scoped(account=stores):
        _pay(ledger, 0.2, input_tokens=1_000, output_tokens=0)
    job, cell, bound = run_holding(stores, hop), uuid.uuid4().hex, Budget(usd=0.2, tokens=0)
    for record in (
        Hold.of_job(job, Budget(usd=1.0, tokens=0), parent=None),
        Hold(
            hold_id=cell,
            parent=job,
            key_source="host",
            bound=bound,
            resource="backend",
            node="cell",
            ledger=stores.charges.held_on(ledger.path),
        ),
        Lapse(hold_id=cell, bound=bound),
    ):
        stores.charges.append(record)
    meters = ceiling_meter(_at(stores, hop).campaign.declaration.purpose)
    book = SpendBook(
        declared=Budget(),
        share=Budget(usd=0.5),
        charges=stores.charges,
        hold_id=job,
        headroom=Budget(),
        metered_on=ledger.path,
        meters=meters,
        opened=0.0,
    )
    assert book.exhausted() is None, "vacuous — the cap tipped before the set-aside"
    book.set_aside(0.1, 0, paid_by="host")
    assert book.exhausted() is ErrorCategory.SPEND_CEILING, "the set-aside never tipped the cap"

    def served() -> SpendCount:
        count = next(cap for cap in current_caps(stores, hop).caps if cap.axis == "usd").count
        assert count is not None, "a door served a live run's money cap with no count"
        return count

    for part in ("billed", "unreported", "set_aside", "out"):
        assert getattr(served(), part).usd_amount == pytest.approx(
            getattr(book.count(), part).usd_amount
        ), f"a door served the {part} short of the count the book stopped on"
    book.release_aside()
    assert served().set_aside.usd_amount == 0 and book.exhausted() is None, (
        "a released set-aside still binds"
    )


def _two_admitted_runs(
    tmp_path: Path, offline: Router
) -> tuple[Stores, JobRegistry, list[CycleHop]]:
    """Two runs admitted at $0 reserve on one $0.30 coupon: the account, its registry, their hops."""
    racer = _web_account("sub-8")
    racer.grant.set(Budget(usd=0.30), afresh=False, opened_at=_OPENED)
    racing = JobRegistry(tmp_path / "8" / "jobs", capacity=lambda _live: 2)
    raced = [_minted(racer, offline).hop for _ in range(2)]
    for racing_hop in raced:
        held_job = racing.request_slot(
            user_id="sub-8",
            principal_id="sub-8",
            workspace=racer.campaigns.workspace,
            holder=CampaignLaunch("ds1"),
            hop=racing_hop,
        )
        _admitted(racer, racing, held_job.job_id, Budget(usd=0.0))
        hold_moved(racer, racing_hop, Budget(usd=0.0))
    return racer, racing, raced


@pytest.mark.usefixtures("run_bound")
def test_of_two_set_limits_raises_the_second_reads_the_wallet_after_the_firsts_reserve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    # Set-limits is an admission, so the reserves stand within the face.
    racer, racing, raced = _two_admitted_runs(tmp_path, offline)
    first_read, second_at_gate, go = threading.Event(), threading.Event(), threading.Event()
    wallet_of, gate_of = quota.read_account_wallet, racing.admission_gate

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
        with contextlib.suppress(QuotaExceededError):
            change_budget(
                Budget(usd=0.20),
                stores=racer,
                job_registry=racing,
                at=_at(racer, at),
                sends=_CELLS,
            )

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


def test_a_set_limits_raced_by_a_delete_answers_not_found_and_leaves_no_campaign_dir(
    tmp_path: Path, offline: Router
) -> None:
    # Its door loaded the campaign; the delete lands before its append. At the data root io reads.
    stores = _web_account("sub-g")
    hop = _minted(stores, offline).hop
    at = _at(stores, hop)
    stores.campaigns.delete_campaign(hop.campaign_id, keep_results=False)
    registry = JobRegistry(tmp_path / "g" / "jobs", capacity=lambda _live: 1)
    with pytest.raises(NotFoundError):
        change_budget(Budget(usd=0.2), stores=stores, job_registry=registry, at=at, sends=_CELLS)
    assert not stores.campaigns.campaign_root_dir(hop.campaign_id).exists(), (
        "an append after the delete re-created a campaign no manifest names"
    )


@pytest.mark.usefixtures("run_bound")
def test_set_limits_inside_a_launchs_admission_window_answers_its_limits_or_a_typed_refusal(
    offline: Router,
) -> None:
    # The cycle's holder is a launch whose hold is not written yet: money waits, rounds land.
    starter = _web_account("sub-6")
    starter.grant.set(Budget(usd=1.0), afresh=False, opened_at=_OPENED)
    registry = JobRegistry(starter.jobs_dir, capacity=lambda _live: 1)
    at = _minted(starter, offline).hop
    job = registry.request_slot(
        user_id="sub-6",
        principal_id="sub-6",
        workspace=starter.campaigns.workspace,
        holder=CampaignLaunch("ds1"),
        hop=at,
    )
    claim_cycle(starter, at, job.job_id)
    assert job.holds_spend and not job.admitted, "the launch is not inside its admission window"
    with pytest.raises(RateLimitedError) as waiting:
        change_budget(
            Budget(usd=0.5),
            stores=starter,
            job_registry=registry,
            at=_at(starter, at),
            sends=_CELLS,
        )
    assert waiting.value.code == "launch_contended", "a raise in the window read another refusal"
    assert change_budget(
        Budget(rounds=3), stores=starter, job_registry=registry, at=_at(starter, at), sends=_CELLS
    )
    assert starter.campaigns.read_standing_budget(at).budget.rounds == 3, "the rounds did not land"


@pytest.mark.usefixtures("run_bound")
def test_a_run_its_own_key_pays_holds_none_of_the_coupon(offline: Router) -> None:
    own = _web_account("sub-5")
    own.grant.set(Budget(usd=1.0), afresh=False, opened_at=_OPENED)
    registry = JobRegistry(own.jobs_dir, capacity=lambda _live: 1)
    at = _minted(own, offline).hop
    job = registry.request_slot(
        user_id="sub-5",
        principal_id="sub-5",
        workspace=own.campaigns.workspace,
        holder=CampaignLaunch("ds1"),
        hop=at,
    )
    claim_cycle(own, at, job.job_id)
    _admitted(own, registry, job.job_id, Budget(usd=0.0))
    hold_moved(own, at, Budget(usd=0.0))
    change_budget(
        Budget(usd=0.5), stores=own, job_registry=registry, at=_at(own, at), sends=_OWN_ONLY
    )
    assert _reserve_of(own, job.job_id).usd == 0.0, (
        "a raise on a run no send of which draws the coupon reserved the coupon for it"
    )
    assert account_wallet(own).state is AllowanceState.OPEN, "the account's next launch read held"
    room = account_wallet(own).headroom.usd
    book = SpendBook(
        declared=Budget(),
        share=Budget(),
        charges=own.charges,
        hold_id=job.job_id,
        headroom=Budget(),
        metered_on=None,
        meters="bill",
        opened=0.0,
    )
    book.set_aside(0.25, 0, paid_by="user")
    assert book.count().set_aside.usd_amount == 0.25, "vacuous — the share never saw the set-aside"
    assert account_wallet(own).headroom.usd == room, (
        "the bench's set-aside on the account's own keys held back the coupon from other launches"
    )


@pytest.mark.usefixtures("run_bound")
def test_a_resume_reserves_at_most_what_the_coupon_has_left_past_its_campaigns_spend(
    offline: Router,
) -> None:
    # The campaign's past spend widens its ceiling's room, never the coupon's.
    resumer = _web_account("sub-4")
    resumer.grant.set(Budget(usd=1.0), afresh=False, opened_at=_OPENED)
    at = _minted(resumer, offline).hop
    with scoped(account=resumer):
        ledger = CycleEventLog.open(CycleDir(resumer.campaigns.cycle_dir(at)))
        _pay(ledger, 0.6, input_tokens=1_000, output_tokens=0)
    registry = JobRegistry(resumer.jobs_dir, capacity=lambda _live: 1)
    job = registry.request_slot(
        user_id="sub-4",
        principal_id="sub-4",
        workspace=resumer.campaigns.workspace,
        holder=CampaignLaunch("ds1"),
        hop=at,
    )
    claim_cycle(resumer, at, job.job_id)
    _, reserve = admit_launch(
        declared=Budget(),
        user=account_of(resumer),
        stores=resumer,
        job_registry=registry,
        job_id=job.job_id,
        campaign_id=at.campaign_id,
        sends=_CELLS,
    )
    assert reserve.usd == pytest.approx(0.4), (
        "a resume reserved the coupon past what it has left, by its campaign's past spend"
    )


def _slot(registry: JobRegistry, stores: Stores, user: str, hop: CycleHop) -> Any:
    return registry.request_slot(
        user_id=user,
        principal_id=user,
        workspace=stores.campaigns.workspace,
        holder=CampaignLaunch("ds1"),
        hop=hop,
    )


def _open_send(hold_id: str, parent: str, usd: float, ledger: str) -> Hold:
    return Hold(
        hold_id=hold_id,
        parent=parent,
        key_source="host",
        bound=Budget(usd=usd),
        resource="optimizer",
        node="o",
        ledger=ledger,
    )


def _live_run(offline: Router) -> tuple[Stores, JobRegistry, Any, CycleHop, SpendBook]:
    """A run holding a $0.20 reserve on a $0.30 coupon: account, registry, job, hop, its book."""
    biller = _web_account("sub-6")
    biller.grant.set(Budget(usd=0.30), afresh=False, opened_at=_OPENED)
    billed_hop = _minted(biller, offline).hop
    billing = JobRegistry(biller.jobs_dir, capacity=lambda _live: 2)
    live = _slot(billing, biller, "sub-6", billed_hop)
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
    return biller, billing, live, billed_hop, live_book


# Each headroom reads the holds and bills the steps before it left on the account.
@pytest.mark.usefixtures("run_bound")
def test_a_live_run_holds_its_reserve_less_what_it_billed_until_its_hold_closes(
    offline: Router,
) -> None:
    # Its spend counts once, so a launch beside it is quoted what the coupon can still pay.
    biller, billing, live, billed_hop, live_book = _live_run(offline)
    with scoped(account=biller):
        billed_ledger = CycleEventLog.open(CycleDir(biller.campaigns.cycle_dir(billed_hop)))
        _pay(billed_ledger, 0.08, input_tokens=1_000, output_tokens=0, book=live_book)
    # A fork rebases the run onto another cycle: its bills draw its hold by the hold tree, never by
    # the ledger they landed on.
    forked_ledger = "campaigns/camp-6b/cycles/cycle_forked01/events.jsonl"
    biller.charges.append(_open_send("forked", live.job_id, 0.02, forked_ledger))
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
    # unreported at its bound.
    biller.charges.append(_open_send("left", live.job_id, 0.05, forked_ledger))
    assert quota.account_wallet(biller).headroom.usd == pytest.approx(0.10), (
        "a send open under a live run's reserve was held twice"
    )
    stamped = billing.get(live.job_id)
    assert stamped is not None
    with pytest.raises(RuntimeError), Opening(biller, billing, stamped):
        raise RuntimeError("the run raised")
    assert quota.account_wallet(biller).headroom.usd == pytest.approx(0.15), (
        "an ended run kept its reserve, or the send it left open went unread"
    )


# The sweep closes the dead chat turn's job beside the crashed run's, and reads its send unreported.
@pytest.mark.usefixtures("run_bound")
def test_a_call_on_no_run_holds_the_coupon_under_its_own_job_until_the_sweep_reads_it_unreported(
    monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    biller = _web_account("sub-6")
    biller.grant.set(Budget(usd=0.15), afresh=False, opened_at=_OPENED)
    billed_hop = _minted(biller, offline).hop
    billing = JobRegistry(biller.jobs_dir, capacity=lambda _live: 2)
    # A call on no run is held under its own job, which holds what its calls out hold.
    chat = billing.create(
        user_id="sub-6",
        principal_id="sub-6",
        workspace=biller.campaigns.workspace,
        hop=UNRESOLVED_HOP,
        holder=PaidCall("turn"),
    )
    _admitted(biller, billing, chat.job_id, Budget())
    biller.charges.append(_open_send("turn", chat.job_id, 0.04, "chat/events.jsonl"))
    assert quota.account_wallet(biller).headroom.usd == pytest.approx(0.11), (
        "a call on no run held nothing of the coupon"
    )
    # A dead producer's hold closes at the sweep, never at a read; every admission sweeps first.
    crashed = _slot(billing, biller, "sub-6", billed_hop)
    _admitted(biller, billing, crashed.job_id, Budget(usd=0.05))
    assert quota.account_wallet(biller).headroom.usd == pytest.approx(0.06)
    unreported = money_on(biller.charges.path).unreported.host.unreported_usd
    with monkeypatch.context() as dead:
        dead.setattr(holders, "producer_alive", lambda *_: False)
        sweep(biller.projects_root)
        assert quota.account_wallet(biller).headroom.usd == pytest.approx(0.11), (
            "a crashed run held the coupon past the sweep"
        )
    money = money_on(biller.charges.path)
    assert chat.job_id not in money.open and money.unreported.host.unreported_usd == (
        pytest.approx(unreported + 0.04)
    ), "a dead chat turn's open send stayed held, never read as unreported"


class _Duel(NamedTuple):
    stores: Stores
    definition: Path
    pipeline: dict[str, Any]
    detached: list[JobSpec]


_DUEL_CAMPAIGN: dict[str, Any] = {"optimization": {"degradation_threshold": 0.4}}


@pytest.fixture
def duel(monkeypatch: pytest.MonkeyPatch) -> _Duel:
    """The host's `duel` dataset on a 60-row bank with no bench split; each arm's process exits at once."""
    monkeypatch.setenv(PROVIDER_KEYS[0], NOT_A_KEY)
    stores = local_stores(tenant=None)
    definition = stores.tenant_datasets.dataset_dir("duel") / DEFINITION_FILE
    pipeline = {
        "connector": "termnorm",
        "nodes": {"llm_only": {"config": {"provider": "groq", "model": "openai/gpt-oss-120b"}}},
    }
    write_yaml(definition, {"pipeline": pipeline, "campaign": _DUEL_CAMPAIGN})
    bank = Bank.of(samples(60, "TRUE"))
    stores.tenant_datasets.put_bank(bank)
    stores.tenant_datasets.relabel("duel", bank.dataset_hash)
    detached: list[JobSpec] = []

    async def ran_elsewhere(held: HeldRun) -> asyncio.Task[None]:
        """The arm's own process, run and exited: its slot handed back, nothing spent."""
        spec = held.job_spec()
        detached.append(spec)
        await asyncio.to_thread(held.opening.job_registry.release, spec.job_id)
        held.opening.hand_off()
        return asyncio.create_task(asyncio.sleep(0))

    monkeypatch.setattr(run_job_mod, "spawn_job", ran_elsewhere)
    return _Duel(stores, definition, pipeline, detached)


def _split_bench(duel: _Duel) -> None:
    campaign = {**_DUEL_CAMPAIGN, "dataset_split": {"bench": 10}}
    write_yaml(duel.definition, {"pipeline": duel.pipeline, "campaign": campaign})


def _campaigns(stores: Stores) -> list[str]:
    return sorted(c.campaign_id for c in stores.campaigns.list_campaigns(lifecycle="all"))


def _asked(h2h: str, arms: dict[str, str], budget: Budget) -> MintHeadToHeadPayload:
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


def _dispatched(stores: Stores, asked: MintHeadToHeadPayload) -> None:
    asyncio.run(CommandDispatcher(stores).dispatch(CommandCall(asked, door=Door.HOST)))


@pytest.mark.usefixtures("run_bound", "offline")
def test_a_head_to_head_priced_below_its_round_or_on_an_unsplit_bank_mints_no_arm(
    duel: _Duel,
) -> None:
    stores = duel.stores
    # A dollar cap is priced before any arm takes a key, on the cell's bound where no run has
    # billed one yet: a cap one round outspends mints nothing.
    pair = {"a": "capo", "b": "potter"}
    with pytest.raises(PayloadInvalidError) as short:
        _dispatched(stores, _asked("duel-2", pair, Budget(usd=0.0001, rounds=1)))
    assert short.value.code == "spend_cap_below_round", "guard: refused for its round"
    assert not _campaigns(stores), "an arm whose round outspends its cap took a key"

    # An arm is judged on its bench: a funded act on a bank holding no rows out mints nothing.
    with pytest.raises(PayloadInvalidError) as blind:
        _dispatched(stores, _asked("duel-2", pair, Budget(usd=50.0, rounds=1)))
    assert blind.value.code == "arm_bench_unsplit", "guard: refused for its split"
    assert not _campaigns(stores), "an arm with no bench rows took a key"


# The terminal's verb: one optimizer twice, apart by a knob, each arm bound by the one budget.
@pytest.mark.usefixtures("run_bound", "offline")
def test_the_terminals_head_to_head_mints_each_arm_on_one_origin_and_the_one_budget(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], duel: _Duel
) -> None:
    stores = duel.stores
    _split_bench(duel)
    argv = ["promptpotter", "--json", "head-to-head", "duel", "--head-to-head", "duel-1"]
    argv += ["--arm", "capo=capo", "--arm", "capo-b3=capo,nodes.blocks.config.max_blocks=3"]
    monkeypatch.setattr(sys, "argv", [*argv, "--max-usd", "50", "--max-rounds", "1"])
    cli_main.main()
    answered = LaunchAnswer.model_validate_json(capsys.readouterr().out).launches
    arms = mint.standing_arms(stores, "duel-1")
    assert [launch.hop for launch in answered] == [arms[k].root_hop for k in ("capo", "capo-b3")], (
        "the terminal answered other arms than the head-to-head records"
    )
    assert [spec.hop for spec in duel.detached] == [launch.hop for launch in answered], (
        "an arm ran in no process of its own, or in another arm's cycle"
    )
    assert len({c.root_content_hash for c in arms.values()}) == 1, (
        "two arms of one head-to-head searched from different origins"
    )
    ran = {k: cycle_config(stores, c.root_cycle).config.optimization for k, c in arms.items()}
    assert {k: (o.budget.usd, o.budget.rounds) for k, o in ran.items()} == {
        "capo": (50.0, 1),
        "capo-b3": (50.0, 1),
    }, "an arm ran another budget than the one every arm declared"
    blocks = {k: o.nodes["blocks"].config for k, o in ran.items() if "blocks" in o.nodes}
    assert blocks == {"capo-b3": {"max_blocks": 3}}, (
        "an arm's knob reached its sibling, or never its own arm"
    )


@pytest.mark.usefixtures("run_bound", "offline")
def test_a_head_to_head_past_the_accounts_concurrent_ceiling_mints_none_of_its_arms(
    duel: _Duel,
) -> None:
    stores = duel.stores
    _split_bench(duel)
    minted = _campaigns(stores)
    trio = _asked("duel-3", {"a": "capo", "b": "capo", "c": "capo"}, Budget(usd=50.0, rounds=1))
    with pytest.raises(QuotaExceededError):
        _dispatched(stores, trio)
    assert _campaigns(stores) == minted, "an arm past the account's ceiling minted a campaign"
    assert not JobRegistry.attach(stores.jobs_dir).list_running(), "a refused arm kept its slot"


# A retry under the act's key, at the REST door, answers the arms its act minted.
@pytest.mark.usefixtures("run_bound", "offline")
def test_a_retry_under_the_acts_key_at_the_rest_door_answers_the_arms_it_minted(
    duel: _Duel,
) -> None:
    stores = duel.stores
    _split_bench(duel)
    minted = _campaigns(stores)
    registry = JobRegistry.attach(stores.jobs_dir)
    funded = _asked("duel-2", {"a": "capo", "b": "potter"}, Budget(usd=50.0, rounds=1))
    route = next(r for r in commands_api.commands_router.routes if r.name == "head-to-head")  # type: ignore[attr-defined]
    answers = [
        asyncio.run(
            route.endpoint(  # type: ignore[attr-defined]
                response=Response(),
                job_registry=registry,
                stores=stores,
                payload=funded,
                key="duel-2-once",
            )
        )
        for _ in range(2)
    ]
    assert answers[0] == answers[1], "a retry under the act's key answered other arms"
    assert len(_campaigns(stores)) == len(minted) + 2 and len(duel.detached) == 2, (
        "a retry under the act's key minted or launched its arms again"
    )


@pytest.mark.usefixtures("run_bound")
def test_a_hold_on_the_accounts_own_key_holds_back_what_its_host_paid_children_keep() -> None:
    charges = _charges()
    holds: tuple[tuple[str, str | None, KeySource, float], ...] = (
        ("own", None, "user", 1.0),
        ("host", "own", "host", 0.3),
    )
    for hold_id, parent, key_source, usd in holds:
        charges.append(
            Hold(
                hold_id=hold_id,
                parent=parent,
                key_source=key_source,
                bound=Budget(usd=usd),
                resource="optimizer",
                node="o",
                ledger=None,
            )
        )
    assert money_on(charges.path).held_back(None).usd == pytest.approx(0.3), (
        "a host-paid send under an own-key hold held none of the coupon, or the own key's bound did"
    )


@pytest.mark.usefixtures("run_bound")
def test_a_host_paid_send_in_an_own_key_cell_asks_the_reserve_its_whole_bound() -> None:
    book = spend_book(None, usd_reserve=0.4)
    book.charges.append(
        Hold(
            hold_id="own-cell",
            parent=book.hold_id,
            key_source="user",
            bound=Budget(usd=0.9),
            resource="backend",
            node="cell",
            ledger=None,
        )
    )
    send = SendBound(input_tokens=0, output_tokens=100, usd=0.5)
    with scoped(reservation="own-cell"), pytest.raises(HostAllowanceExhaustedError):
        book.hold(send, _OPTIMIZER, what="a send", paid_by="host", ledger=None)


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


def _spent(stores: Stores, hop: CycleHop, cost_usd: float) -> Path:
    """*hop*'s ledger, one send of *cost_usd* on the box's key billed on it."""
    cycle_dir = stores.campaigns.cycle_dir(hop)
    _pay(CycleEventLog.open(CycleDir(cycle_dir)), cost_usd, input_tokens=2_000, output_tokens=800)
    return CycleLayout(cycle_dir).ledger


class _SpentCampaign(NamedTuple):
    root: CycleHop
    stub: CycleHop
    retried: CycleHop
    stub_ledger: Path
    retried_ledger: Path
    account_usd: Callable[[], float]
    cleanup: Callable[[CycleHop], bool]
    ceiling_counts_now: Callable[[], tuple[float, int]]
    tombstones: Callable[[], list[SpendTombstoneRecord]]


@pytest.fixture
def spent_campaign(built_stores: Stores, world: World, ran_24: Shared) -> _SpentCampaign:
    """A campaign the world ran and two forks off it, each then spent on the box's key, and
    readers of that spend."""
    stores = built_stores
    root, stub, retried = _line(world, 2, ran_24)
    _spent(stores, root, 0.07)
    stub_ledger, retried_ledger = _spent(stores, stub, 0.11), _spent(stores, retried, 0.05)

    def account_usd() -> float:
        return account_bill(stores.charges).host.used_usd

    def cleanup(hop: CycleHop) -> bool:
        return cleanup_stub_fork_if_empty(campaign_store=stores.campaigns, hop=hop)[0]

    def tombstones() -> list[SpendTombstoneRecord]:
        workspace = CycleEventLog(CycleEventLog.workspace_path(stores.campaigns.workspace))
        return [r for r in ledger_records(workspace) if isinstance(r, SpendTombstoneRecord)]

    def ceiling_counts_now() -> tuple[float, int]:
        counted = stores.campaigns.metered_spend(root.campaign_id, "bill")
        return counted.usd_amount, counted.token_amount

    return _SpentCampaign(
        root,
        stub,
        retried,
        stub_ledger,
        retried_ledger,
        account_usd,
        cleanup,
        ceiling_counts_now,
        tombstones,
    )


@pytest.mark.usefixtures("account_bound")
def test_the_account_and_the_campaigns_ceiling_read_what_its_run_billed(ran_24: Shared) -> None:
    hop = ran_24.made
    with ran_24.reading() as stores:
        billed = [
            r.money
            for r in ledger_records(stores.campaigns.layout(hop).ledger)
            if isinstance(r, TokenUsageRecord) and isinstance(r.money, Bill) and not r.mirrored
        ]
        ceiling = stores.campaigns.metered_spend(hop.campaign_id, "bill")
        used = account_bill(stores.charges).host.used_usd
    assert billed, "vacuous: the run billed no send"
    assert ceiling.token_amount == sum(bill.tokens for bill in billed), (
        "the campaign's ceiling read other tokens than its ledger billed"
    )
    assert used == pytest.approx(ceiling.usd_amount), (
        "the account read another sum than the campaign's ceiling"
    )


@pytest.mark.usefixtures("account_bound")
def test_an_admission_whose_hold_cannot_be_written_frees_its_room_and_charges_nothing(
    built_stores: Stores, monkeypatch: pytest.MonkeyPatch, spent_campaign: _SpentCampaign
) -> None:
    stores = built_stores
    root_hop, _, _, _, _, account_usd, cleanup, _, _ = spent_campaign
    before = account_usd()

    def unwritable_line(_book: ChargeBook, _line: object) -> None:
        raise OSError("the account's disk is full")

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
    assert not cleanup(root_hop) and account_usd() == pytest.approx(before), (
        "a cycle the delete REFUSES must not be banked: it keeps its rows, the same money twice"
    )


@pytest.mark.usefixtures("account_bound")
def test_a_ledger_refusing_a_sends_first_part_keeps_its_later_parts_on_the_account(
    built_stores: Stores, monkeypatch: pytest.MonkeyPatch, spent_campaign: _SpentCampaign
) -> None:
    stores = built_stores
    root_hop, _, _, _, _, account_usd, _, _, _ = spent_campaign
    before = account_usd()

    def unappendable(*_: object, **__: object) -> None:
        raise OSError("the ledger's disk is full")

    with (
        scoped(ledger=CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(root_hop)))),
        monkeypatch.context() as patched,
        scoped(book=spend_book(1.0)),
        pytest.raises(OSError),
    ):
        patched.setattr("promptpotter.infrastructure.ledger.CycleEventLog.append", unappendable)
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
    _, stub, _, stub_ledger, _, account_usd, cleanup, ceiling_counts_now, _ = spent_campaign
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
    _, _, retried_hop, _, retried_ledger, account_usd, cleanup, ceiling_counts_now, _ = (
        spent_campaign
    )
    before, ceiling_counts = account_usd(), ceiling_counts_now()
    for _crashed_attempt in range(2):
        bank_spend(
            workspace=stores.campaigns.workspace,
            ledgers=[stores.campaigns.ledger(retried_hop)],
            subject=stores.subject(retried_hop),
            metered={
                meter: stores.campaigns.cycle_metered(retried_hop, meter)
                for meter in get_args(CeilingMeter)
            },
        )
    assert cleanup(retried_hop) and not retried_ledger.exists(), "a banked fork survived its delete"
    assert account_usd() == pytest.approx(before), (
        "banking precedes the delete: a retry after a crash in between must find the tombstone"
    )
    assert ceiling_counts_now() == pytest.approx(ceiling_counts), (
        "a crash retried between bank and delete counted the cycle's metered spend twice, or none"
    )


def _tear_delete(
    stores: Stores, monkeypatch: pytest.MonkeyPatch, root_hop: CycleHop
) -> tuple[Path, Path]:
    """Delete the campaign, killed while removing its tree: (its dir, the inner sandbox it owned)."""
    camp = root_hop.campaign_id
    camp_dir = stores.campaigns.campaign_root_dir(camp)
    owned_sandbox = inner_sandbox(stores, camp, root_hop.cycle_id)
    with monkeypatch.context() as patched:

        def torn(path: Path) -> None:
            if path == camp_dir:
                raise OSError(f"killed while removing {path}")
            rmtree_robust(path)

        patched.setattr(campaign_store_module, "rmtree_robust", torn)
        with pytest.raises(OSError):
            stores.campaigns.delete_campaign(
                camp,
                keep_results=False,
                inner_sandbox_root=owned_sandbox.parent,
            )
    return camp_dir, owned_sandbox


# The pass and the manifest-less delete meet the campaign the torn delete left.
@pytest.mark.usefixtures("account_bound")
def test_a_delete_torn_at_its_tree_commits_at_its_tombstone(
    built_stores: Stores, monkeypatch: pytest.MonkeyPatch, spent_campaign: _SpentCampaign
) -> None:
    stores = built_stores
    root_hop, _, _, _, _, _, _, ceiling_counts_now, _ = spent_campaign
    camp = root_hop.campaign_id
    ceiling_counts = ceiling_counts_now()
    spent_before = campaign_spend(stores.campaigns, stores.charges, camp)
    camp_dir, owned_sandbox = _tear_delete(stores, monkeypatch, root_hop)
    assert camp_dir.is_dir() and not owned_sandbox.exists(), (
        "a delete torn at its tree stranded the campaign's inner sandbox, which no retry reaches"
    )
    assert campaign_spend(stores.campaigns, stores.charges, camp) == spent_before, (
        "a torn delete's rows counted beside its tombstone: the campaign's spend read twice"
    )
    torn_read = stores.campaigns.load_campaign(camp)
    assert torn_read is not None and torn_read.lifecycle_status == "deleted", (
        "a torn delete's detail read the manifest's own status past its tombstone"
    )
    assert ceiling_counts_now() == pytest.approx(ceiling_counts), (
        "a torn delete's rows metered beside its tombstone: the ceiling refuses at half its room"
    )


@pytest.mark.usefixtures("account_bound")
def test_a_campaign_a_delete_tore_admits_no_pass_and_serves_its_bench_as_not_active(
    built_stores: Stores, monkeypatch: pytest.MonkeyPatch, spent_campaign: _SpentCampaign
) -> None:
    stores = built_stores
    root_hop = spent_campaign.root
    _tear_delete(stores, monkeypatch, root_hop)

    # The slot door refuses a run or a pass on it: nothing it would spend past its tombstone
    # reaches a sum, and no job of it holds a slot.
    async def bench_pass() -> None:
        async with paid_pass(
            stores=stores,
            pass_kind=MeasurementRole.BENCH,
            at=_at(stores, root_hop),
            declared=Budget(usd=1.0),
            sends=(),
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
    torn_read = stores.campaigns.load_campaign(root_hop.campaign_id)
    assert torn_read is not None
    served = cycle_bench(
        stores,
        CampaignCycle(torn_read, root_hop.cycle_id),
        Resolution(graded_under, verifier_graded=False),
    ).status
    assert (served.can_run, served.refusal) == (False, PassRefusal.NOT_ACTIVE), (
        "the served bench offers a grade the pass's admission refuses"
    )


@pytest.mark.usefixtures("account_bound")
def test_a_campaign_whose_manifest_a_torn_delete_left_gone_still_deletes_and_banks(
    built_stores: Stores, monkeypatch: pytest.MonkeyPatch, spent_campaign: _SpentCampaign
) -> None:
    stores = built_stores
    root_hop, _, _, _, _, account_usd, _, _, _ = spent_campaign
    before = account_usd()
    camp_dir, _ = _tear_delete(stores, monkeypatch, root_hop)
    # A campaign whose manifest is gone still deletes, and banks: nothing reads its rows as one.
    CampaignLayout(camp_dir).manifest.unlink()
    assert stores.campaigns.delete_campaign(root_hop.campaign_id, keep_results=True)
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
    inner_hop = CycleHop(campaign_id="innerds__20260101-000000", cycle_id="inner-cycle-0")
    inner_ledger = CycleEventLog.open(
        CycleDir(stores.campaigns.in_sandbox(sandbox).cycle_dir(inner_hop))
    )
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
    reclaim_orphan_sandboxes(stores.projects_root)
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
        return asyncio.run(CommandDispatcher(stores).dispatch(CommandCall(payload, door=Door.HOST)))

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
def unframed_session(built_stores: Stores, offline: Router) -> _Framing:
    """A session opened by the real door, declared with no task framing."""
    framing, config = _opened(built_stores, offline)
    declared = declaration_of(framing, config, Optimize())
    return _Framing(framing, declared, list(framing.bank.samples))


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

    async def framed(*_: object, **__: object) -> None:
        _pay(telemetry.billing_ledger("framing"), 0.03, input_tokens=500, output_tokens=200)
        raise PayloadInvalidError("refused after the framing", code="refused")

    monkeypatch.setattr(mint, "commit_task_framing", framed)
    with pytest.raises(PayloadInvalidError):
        asyncio.run(
            mint.mint_framed_cycle(
                framing, effective_config(declared.spec), rows, task_text="frame this"
            )
        )
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

    torn_plan = mint.resolve_cycle_plan(
        framing, effective_config(declared.spec), declared.purpose, rows
    )
    with monkeypatch.context() as tearing, pytest.raises(OSError, match="torn"):
        tearing.setattr(type(stores.campaigns), "mint_cycle", _dies)
        mint.mint(torn_plan, campaign_id="torn__000001", adopting=None)
    assert stores.campaigns.load_campaign("torn__000001") is None, (
        "a mint that died before its cycle left a campaign with none"
    )


@pytest.mark.usefixtures("account_bound")
def test_a_reset_over_a_reshaped_ledger_drops_it_and_the_accounts_charges_with_it(
    built_stores: Stores, offline: Router, spent_campaign: _SpentCampaign
) -> None:
    stores = built_stores
    root, account_usd = spent_campaign.root, spent_campaign.account_usd
    root_ledger = CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(root)))
    tokens_before = account_activity(stores, window="1d", group_by="model").total_tokens
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
    reshaped = _spent(stores, _minted(stores, offline).hop, 0.13)
    lines = [json.loads(line) for line in reshaped.read_text(encoding="utf-8").splitlines()]
    for line in lines:
        line.pop("money", None)
    write_jsonl(reshaped, lines)
    with pytest.raises(FileUnreadableError):
        billed_spend([reshaped])
    activity = account_activity(stores, window="1d", group_by="model")
    billed = sum(sum(b.series_spend.values()) for b in activity.buckets)
    assert billed == pytest.approx(framed_total + 0.13), (
        "the Activity pane read another bill than the account's charges"
    )
    assert activity.total_tokens == tokens_before + 2_800 + 400 + 700 + 2 * 15, (
        "the Activity pane lost the sends of what was deleted or never minted"
    )
    apply_reset(plan_reset(stores.projects_root, tenant_id=None, staged={}))
    assert not stores.campaigns.iter_campaign_dirs(), "a reset kept a campaign it cannot read"
    # The account's charges are run data until the operator is no longer a solo developer (§0011).
    assert account_usd() == 0.0, "a reset kept the account's charges"


@pytest.mark.usefixtures("account_bound")
def test_a_settle_whose_ledger_write_fails_still_closes_its_book_whole(
    built_stores: Stores, offline: Router, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = built_stores

    # Its part billed and the remainder held, never the whole bound held again on top of the bill.
    def unwritable(*_: object, **__: object) -> None:
        raise OSError("the ledger's disk is full")

    after_reset = _minted(stores, offline).hop
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
        patched.setattr("promptpotter.infrastructure.ledger.CycleEventLog.append", unwritable)
        send.settle(
            Billed(TokenAccount(input=2_000, output=400), 0.2),
            unknown=SendBound(input_tokens=0, output_tokens=400, usd=0.1),
        )
    assert book.money_left().usd == pytest.approx(1.0 - 0.2 - 0.1), (
        "a settle that raised left its book holding another sum than it billed and kept held"
    )


@pytest.mark.usefixtures("account_bound")
def test_a_projection_raising_on_a_landed_bill_is_its_ledgers_one_warning(
    built_stores: Stores, offline: Router
) -> None:
    stores = built_stores

    # The part counts once, and the next bill lands with no second warning.
    class _Unwritable(Projection):
        derives = "unwritable.json"

        def _handle_token_usage(self, record: TokenUsageRecord) -> None:  # noqa: ARG002 -- routed by its annotation
            raise OSError("the readout's disk is full")

    faulted = _minted(stores, offline).hop
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
    warned = [r.kind for r in ledger_records(faulted_ledger) if isinstance(r, RoundWarningRecord)]
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
    laid_ledger(campaigns.cycle_dir(gone)).append(billed_call(0.1))
    assert campaigns.delete_campaign(f"{prefix}__bbbbbb", keep_results=True)
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


def test_a_delete_that_spent_nothing_commits_at_its_tombstone_and_its_id_is_never_drawn_again(
    built_stores: Stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaigns = built_stores.campaigns
    prefix = dataset_id_prefix("ds1")
    hop = CycleHop(campaign_id=f"{prefix}__aaaaaa", cycle_id="cycle-0")
    campaigns.mint_cycle(hop)
    camp_dir = campaigns.campaign_root_dir(hop.campaign_id)
    with monkeypatch.context() as patched:

        def torn(path: Path) -> None:
            if path == camp_dir:
                raise OSError(f"killed while removing {path}")
            rmtree_robust(path)

        patched.setattr(campaign_store_module, "rmtree_robust", torn)
        with pytest.raises(OSError):
            campaigns.delete_campaign(hop.campaign_id, keep_results=False)
    assert workspace_bank(campaigns.workspace).deleted(hop.campaign_id), (
        "a zero-spend delete torn at its tree read as live: it wrote no tombstone"
    )
    assert campaigns.delete_campaign(hop.campaign_id, keep_results=False)
    drawn = iter(["aaaaaa", "cccccc"])
    monkeypatch.setattr("secrets.token_hex", lambda _n: next(drawn))
    assert campaigns.claim_campaign_id("ds1", built_stores.charges) == f"{prefix}__cccccc", (
        "a zero-spend delete's id was minted again"
    )


def test_a_reported_zero_of_reasoning_reaches_a_step_as_zero_and_none_as_unreported() -> None:
    said = {"prompt_tokens": 9, "completion_tokens": 4}
    zero = ReportedUsage.model_validate(
        {
            **said,
            "completion_tokens_details": {"reasoning_tokens": 0},
            "prompt_tokens_details": {"cache_write_tokens": 0},
        }
    ).tokens
    silent = ReportedUsage.model_validate(said).tokens
    wrote = ReportedUsage.model_validate(
        {**said, "prompt_tokens_details": {"cache_write_tokens": 5}}
    ).tokens
    assert StepUsage.from_account(zero).reasoning == 0, "a reported zero read as unreported"
    assert StepUsage.from_account(silent).reasoning is None, "an unreported part read as 0"
    # A row's step entry rides the wire, and the fold reads its account off it.
    folds = {
        name: TokenAccount.from_step_tokens(
            {"n": StepUsage.from_wire(StepUsage.from_account(tokens).wire())}
        )
        for name, tokens in (("wrote", wrote), ("zero", zero), ("silent", silent))
    }
    assert [(f.cache_write, f.reasoning) for f in folds.values() if f is not None] == [
        (5, None),
        (0, 0),
        (None, None),
    ], "a step's usage dropped a reported part, or reported one never sent"
    assert (silent + silent).reasoning is None and (silent + zero).reasoning == 0, (
        "a sum turned an unreported part into a count, or a reported one into nothing"
    )
    # Banked `llm_call` and `token_usage` lines, written when the parts were ints, still load.
    banked = {"node": "n", "usage": {"input": 9, "output": 4, "reasoning": 0, "cache_write": 0}}
    usage = LLMCallRecord.model_validate(banked).usage
    assert usage is not None and (usage.reasoning, usage.cache_write) == (0, 0)
    price = CallPrice(
        kind="backend", model=None, provider=None, cost_usd=None, rate_priced_usd=None
    )
    parts = {"reasoning_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    line = TokenUsageRecord.model_validate(
        {"node": "n", "input_tokens": 9, "output_tokens": 4, "money": price.model_dump(), **parts}
    )
    assert (line.reasoning_tokens, line.cache_write_tokens) == (0, 0)
