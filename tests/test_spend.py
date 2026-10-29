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
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, cast, get_args
from unittest import mock

import httpx
import pytest
from factories import (
    PROMPT_INFO,
    SANDBOX_CAMPAIGN,
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
    on_jumping_clock,
    pipeline_schema,
    pobb_knobs,
    samples,
    score_summary,
    scorer_of,
    spend_book,
    workspace,
)
from offline_world import NOT_A_KEY, PROVIDER_KEYS, Hang, Router, Said, completion
from pydantic import BaseModel, Field, ValidationError

from promptpotter.application import free_tier
from promptpotter.application.account import account_of, local_stores
from promptpotter.application.bench import llm_call as call_mod
from promptpotter.application.commands.dispatcher import (
    CommandCall,
    CommandDispatcher,
    DispatchedToCycle,
)
from promptpotter.application.commands.limits_and_queue import change_run_limits
from promptpotter.application.datasets.draft_campaign import new_draft
from promptpotter.application.datasets.loaders import archive_entry
from promptpotter.application.diagnostics.noise_floor import measure_noise_floor
from promptpotter.application.diagnostics.probe_reasoning import probe_reasoning
from promptpotter.application.diagnostics.seed_screen import screen_inner_seeds
from promptpotter.application.jobs import mint
from promptpotter.application.jobs.launcher import run_job as run_job_mod
from promptpotter.application.jobs.launcher.admission import (
    admit_and_share,
    embedded_share,
    open_spend_book,
    paid_pass,
)
from promptpotter.application.jobs.launcher.head_to_head import HeadToHeadStarted
from promptpotter.application.jobs.launcher.run_job import HeldRun, JobSpec
from promptpotter.application.jobs.machine_status import machine_status
from promptpotter.application.jobs.quota import (
    QuotaExceededError,
    admit_launch,
    clamp_budget_change,
    cycle_share,
    hold_budget_change,
    hold_moved,
    on_free_tier,
    paid_call,
    spends_the_hosts_own_key,
)
from promptpotter.application.jobs.reaper import reclaim_orphan_sandboxes
from promptpotter.application.jobs.registry import (
    UNRESOLVED_HOP,
    CampaignLaunch,
    EmbeddedRun,
    JobRegistry,
)
from promptpotter.application.maintenance.archive_maintenance import purge_cold_store
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.optimizers.potter.pobb.checks import PoBBCheck
from promptpotter.application.optimizers.potter.race import CatchUpPool
from promptpotter.application.pipeline_resolve import resolve_campaign_config
from promptpotter.application.run_callbacks import RunCallbacks
from promptpotter.application.run_phase_control import RunControl
from promptpotter.application.runner.grade_bench import grade_line_bench
from promptpotter.application.runner.inner import spawn, spawn_context
from promptpotter.application.runner.loop import round_bounds
from promptpotter.application.runner.termination import run_stop_reason
from promptpotter.application.scoring import query_loop
from promptpotter.application.scoring.cell_envelope import CellEnvelope
from promptpotter.application.scoring.formula.compiler import cell_channels_of
from promptpotter.application.scoring.judges import call as judge_call
from promptpotter.application.scoring.judges.registry import build_evaluators
from promptpotter.application.scoring.sample_measurement import (
    cell_billing,
    emit_replayed_step_tokens,
    measure_sample,
)
from promptpotter.application.scoring.search_point_scorer import (
    open_walk,
    replayable_on,
    score_search_point,
)
from promptpotter.config.settings import settings
from promptpotter.connectors.protocol import InProcessWorkload, cell_envelope_of
from promptpotter.connectors.termnorm import TermNormSession
from promptpotter.domain.arms import CutKind
from promptpotter.domain.campaign import Optimize
from promptpotter.domain.campaign_config import (
    CampaignConfig,
    OptimizationConfig,
    merge_config_layers,
)
from promptpotter.domain.cells import PinnedCell
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
from promptpotter.domain.diagnostic_run import RescoreCount
from promptpotter.domain.draft_edit import EditDraftPatch, OptimizationOverrides
from promptpotter.domain.judges import JudgeSpec, JudgeStage
from promptpotter.domain.l4.proxies import inner_cell_facts
from promptpotter.domain.measurement_provenance import REUSABLE_MIN_GRADE
from promptpotter.domain.paired_reading import ArmKey, RoundArm
from promptpotter.domain.phases import (
    REFUSAL_STOPS,
    PauseCause,
    RunPhase,
    StopLoop,
    StopReason,
    WalkEnd,
)
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import (
    PipelineNode,
    WebSpendBound,
)
from promptpotter.domain.ruler import RulerReach
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.run_records import (
    ArmedCeilingEffect,
    BankedSubject,
    ConfigOverrides,
    CycleSeed,
    CycleSeedRecord,
    CycleShareRecord,
    ForkRemainder,
    ForkSpec,
    ForkTrigger,
    RoundEnteredRecord,
    RunPhaseRecord,
    SampleScoredRecord,
    SampleStartedRecord,
    SpendHoldRecord,
    SpendTombstoneRecord,
    TokenUsageRecord,
)
from promptpotter.domain.sample import Bank, Sample
from promptpotter.domain.scoring import GradedCell, MeasuredCell, PipelineData, Scorer
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.domain.spend import (
    CYCLE_AXES,
    MONEY_AXES,
    Budget,
    CeilingMeter,
    HeldBudget,
    SpendRollup,
    StepUsage,
    TokenAccount,
)
from promptpotter.domain.validators import StopSignal
from promptpotter.infrastructure.backend import CELL, BackendClient
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm import base, pricing, telemetry
from promptpotter.infrastructure.llm.openai_compat import ChatReply, billed_cost
from promptpotter.infrastructure.llm.pricing import (
    PriceTier,
    Rate,
    RateCeiling,
)
from promptpotter.infrastructure.llm.registry import get_llm_client
from promptpotter.infrastructure.llm.request import ChatRequest
from promptpotter.infrastructure.llm.send_pacing import (
    SEND_ATTEMPTS,
    SendBudget,
    under_budget,
)
from promptpotter.infrastructure.llm.spend_book import (
    Admission,
    Billed,
    CallLabel,
    SendBound,
    SpendBook,
    admitted,
    answered,
    bind_spend_book,
    replied,
    reserved,
    spending_under,
    unbounded_spend_book,
    unreported_on,
)
from promptpotter.infrastructure.producer_lock import hold_cycle, release_cycle
from promptpotter.infrastructure.store.account_spend import (
    account_ledgers,
    bank_spend,
    billed_spend,
    iter_user_token_usage,
    sum_user_spend,
)
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_last,
    scan_ledger_priced_keys,
    scan_ledger_spend,
    scan_ledger_spend_by_round,
    scan_standing_rounds,
)
from promptpotter.infrastructure.store.dataset_access import DEFINITION_FILE, resolve_dataset
from promptpotter.infrastructure.store.io import write_jsonl, write_yaml
from promptpotter.infrastructure.store.layout import CampaignLayout, CycleLayout, draft_dirs
from promptpotter.infrastructure.store.llm_reuse_cache import LLMReuseCache
from promptpotter.infrastructure.store.measurement_archive import ReplayFeed
from promptpotter.infrastructure.store.read_model import LedgerSpan, iter_jsonl
from promptpotter.infrastructure.store.session_pointer import cleanup_stub_fork_if_empty
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.infrastructure.store.user_store import User
from promptpotter.presentation.api.routers import commands as commands_api
from promptpotter.presentation.cli import campaign_runner
from promptpotter.shared.errors import (
    CellHaltedError,
    CellInfrastructureError,
    CellSendRefusedError,
    CellUnscoreableError,
    ConflictError,
    CycleBusyError,
    ErrorCategory,
    NotFoundError,
    PayloadInvalidError,
    SendRefusedError,
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


@pytest.fixture
def run_bound(tmp_path: Path) -> Iterator[None]:
    ledger = telemetry.set_cycle_ledger(CycleEventLog(tmp_path / "bound.jsonl"))
    with telemetry.at_round(1):
        yield
    telemetry.reset_cycle_ledger(ledger)


def _held(
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
    registry.mark_admitted(job.job_id, Budget() if reserve is None else reserve)
    return HeldBudget(
        admitted=admitted,
        standing=Budget() if standing is None else standing,
        job_id=job.job_id,
        share=admitted.only(MONEY_AXES),
        step_rounds=step_rounds,
    )


def _armed(
    stores: Stores, hop: CycleHop | None, held: HeldBudget, registry: JobRegistry
) -> SpendBook:
    """A run's book, live on its job and its cycle's records where it has one; its share lands
    there first, as admission lands it, unless one already stands."""
    if hop is not None and stores.campaigns.read_cycle_share(hop) is None:
        hold_moved(stores, hop, held.share)
    return open_spend_book(
        held,
        stores=stores,
        job_registry=registry,
        hop=hop,
        meters="bill",
        opened=time.monotonic(),
        live=hop is not None,
    )


def _budgeted(budget: Budget) -> CampaignConfig:
    config = CampaignConfig.model_validate({"optimization": {"degradation_threshold": 0.05}})
    optimization = config.optimization.model_copy(update={"budget": budget})
    return config.model_copy(update={"optimization": optimization})


def _change_run_limits(dispatcher: CommandDispatcher, hop: CycleHop, budget: Budget) -> Budget:
    """The ceiling the change armed, as its ack's effect states it."""
    payload = ChangeRunLimitsPayload(
        campaign_id=hop.campaign_id, cycle_id=hop.cycle_id, budget=budget
    )
    applier = change_run_limits(DispatchedToCycle(dispatcher, payload, minted_campaign(hop), hop))

    async def apply() -> None:
        ran = applier.run()
        assert inspect.isawaitable(ran)
        await ran

    asyncio.run(apply())
    assert applier.effect_fn is not None, "a limits change acks no ceiling for its trace"
    effect = applier.effect_fn()
    assert isinstance(effect, ArmedCeilingEffect), f"a limits change acked {effect!r}"
    return effect.armed


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
    (ev,) = build_evaluators({"answer": JudgeSpec(name="sealqa", stages=[judge])}, cache=cache)
    graded = CycleEventLog(tmp_path / "graded.jsonl")
    with _billing_on(graded, unbounded_spend_book()):
        for _ in range(2):
            row = MeasuredCell(sample_key="k0", query="who?", predicted="Ada", ground_truth="Ada")
            computed = ev.compute(result=row, schema=None)
            assert inspect.isawaitable(computed)
            last = await computed
    return len(offline.llm.sent), _token_bills(graded), last


class _Reply(BaseModel):
    answer: str = Field(description="The answer.")


_MESSAGES = [{"role": "user", "content": "q"}]
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


@pytest.mark.usefixtures("run_bound")
async def test_a_call_is_priced_on_its_route_sent_as_asked_and_billed_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
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
        }
    }
    (tmp_path / "rates.json").write_text(json.dumps(listed), encoding="utf-8")
    with monkeypatch.context() as patched, contextlib.ExitStack() as unpatching:
        patched.setattr(pricing, "BUNDLED_PATH", tmp_path / "rates.json")
        pricing.load_rates.cache_clear()
        unpatching.callback(pricing.load_rates.cache_clear)
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
            model, provider = pricing.inline_route(key)
            assert usd(model, 10, 10, provider=provider) == pytest.approx(expected), (
                f"{key}: a key prices on the route it names inline"
            )
        assert pricing.inline_route("orphan-model") == ("orphan-model", None), (
            "a row naming no lister invented a provider"
        )
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

    # What a client cannot send never reaches the provider; the gateway caps the price it pays.
    async def ceiling(*_: Any, **__: Any) -> RateCeiling:
        return RateCeiling(tiers=(PriceTier(0, 0.5, 0.25),))

    async def send(client: Any, asked: dict[str, Any]) -> None:
        bind_spend_book(unbounded_spend_book())
        with under_budget(SendBudget(None, attempts=1)):
            await client.chat(
                ChatRequest(_MESSAGES, "m", **asked), label=CallLabel("l1", "optimizer")
            )

    offline.llm.says = lambda _body: '{"answer": "a"}'
    with monkeypatch.context() as patched:
        patched.setattr(base, "rate_ceiling", ceiling)
        for host, asked, sent in (
            ("anthropic", {"seed": 7}, False),
            ("anthropic", {"reasoning_effort": "low"}, False),
            ("anthropic", {"route_order": ["Alibaba"]}, False),
            ("groq", {"route_order": ["Alibaba"]}, False),
            ("openrouter", {}, True),
            ("openrouter", _EVERY_FIELD, True),
        ):
            offline.llm.sent.clear()
            sending = asyncio.create_task(send(get_llm_client(host), asked))
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

    # A cell that ran without a grade still bills what it spent; a replay states its row's price.
    async def ran_ungraded(*_args: Any) -> dict[str, Any]:
        spent = {"agent": {"input": 1200, "output": 300, "estimated": False, "cost_usd": 0.0076}}
        raise CellUnscoreableError("verifier timed out", spent=spent)

    cells = CycleEventLog(tmp_path / "cells.jsonl")
    unbounded = unbounded_spend_book()
    session = loop_session(
        workspace(tmp_path / "ungraded"),
        pipeline_schema([]),
        [],
        control=RunControl(book=unbounded),
        client=backend_client(
            tmp_path / "jobs", execution="in_process", in_process_run=ran_ungraded
        ),
        scorer=scorer_of("max(0.0, min(1.0, env_reward))", verifier_graded=True),
    )
    token = telemetry.set_cycle_ledger(cells)
    try:
        with spending_under(unbounded):
            row_ = await measure_sample(Sample(id=0, query="10452", ground_truth=None), session)
        banked = {"agent": StepUsage(input=1200, output=300, rate_priced_usd=0.25)}
        emit_replayed_step_tokens(banked, {})
    finally:
        telemetry.reset_cycle_ledger(token)
    *ran, replayed = [r for _, r in cells.iter() if isinstance(r, TokenUsageRecord)]
    assert row_.error_category == ErrorCategory.UNSCOREABLE, "an ungraded cell read as graded"
    assert [(r.cost_usd, r.input_tokens, r.cached) for r in ran] == [(0.0076, 1200, False)], (
        "a cell that ran without a grade billed nothing of what it spent"
    )
    assert (replayed.cost_usd, replayed.rate_priced_usd, replayed.cached) == (None, 0.25, True), (
        "a replay states the price its row recorded: re-priced, incurred cost moves with each refresh"
    )

    # The replay of a grading is still METERED, flagged `cached`: its cost ignores cache history.
    calls, metered, score = await _grade_twice(tmp_path / "judge", offline, reply="A")
    blank_calls, _, blank = await _grade_twice(tmp_path / "blank", offline, reply="   ")
    assert score.score == 1.0, "the replayed reply must grade identically, not merely cheaply"
    assert calls == 1, f"an identical comparison re-billed the provider: {calls}x"
    assert [m.cached for m in metered] == [False, True], "a served grading went unmetered"
    assert {m.kind for m in metered} == {"judge"}, "grading spend landed outside its own bucket"
    # `cached`: OUR cache served the reply. `cache_read`: the PROVIDER discounted a sent call.
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
        return REFUSAL_STOPS[refused.category].value
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
    sp = resumes or JobSearchPoint(pipeline_params={"search": {"walk": uuid.uuid4().hex}})
    committed: list[int] = []
    returned: list[int] = []
    book = spend_book(cap_usd)
    dollar = SendBound(input_tokens=0, output_tokens=0, usd=1.0)

    async def _measure(sample: Sample, _session: Any, _pipeline_params: Any = None) -> Any:
        with admitted(CallLabel("cell", "backend"), dollar, model=None, provider=None) as bill:
            if sample.id == stall:
                await asyncio.sleep(0.3)
            row = await backend.measure(sample)
            returned.append(sample.id)
            bill.settle(Billed(TokenAccount(), 1.0))
        return row

    def _backfill(_sp: Any, sample: Sample, _prior_id: str) -> Any:
        call = asyncio.ensure_future(backend.hold(sample, backfill_pace))

        def _commit() -> list[Any]:
            committed.append(sample.id)
            return [_HIT.grade(call.result())]

        return call, _commit, call.cancel

    # A parent measured on none of the round's cells, so every cell owes it one catch-up call.
    priors = CatchUpPool(
        PoBBCheck(
            pobb_knobs(), n_min=6, n_samples=len(dataset), ruler=None, parent=ArmKey("parent")
        ),
        _backfill,
        "elimination",
    )
    priors.admit(ArmKey("parent"), [], JobSearchPoint())

    # The walk's own ledger: what each launch recorded of the look-ahead it was sent under.
    started = CycleEventLog.open(CycleDir(stores.projects_root / "walks" / uuid.uuid4().hex))
    bound = telemetry.set_cycle_ledger(started)
    try:
        with mock.patch.object(query_loop, "measure_sample", _measure), spending_under(book):
            session = loop_session(
                stores,
                # One node the backend bounds at the cell's dollar, so the scheduler counts in them.
                _searching(1.0),
                dataset,
                backend_id="walks",
                scorer=_HIT,
                control=_control_pausing(
                    lambda: pause_after_call is not None and len(returned) >= pause_after_call,
                    held_lookahead=armed,
                    book=book,
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
    finally:
        telemetry.reset_cycle_ledger(bound)
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
        "billed": book.usd_metered,
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
    bound = telemetry.set_cycle_ledger(ledger)
    try:
        with mock.patch.object(query_loop, "measure_sample", _measure):
            stopped = await _stopped_by_operator(
                query_loop.run_walks(dict(enumerate(walks)), session, keep_cut=False)
            )
    finally:
        telemetry.reset_cycle_ledger(bound)
    return {
        "rows": [list(walk.results) for walk in walks],
        "stops": [stopped or _ended_on(walk) for walk in walks],
        "absorbed": absorbed(),
        "asked": asked,
        "calls": list(backend.calls),
        "peak": max(backend.entries),
    }


@pytest.mark.usefixtures("run_bound")
@on_jumping_clock
async def test_a_run_bills_each_cell_once_whatever_its_process_lookahead_or_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    built_stores: Stores,
    offline: Router,
) -> None:
    # An identity rebuilt from the tenant alone carries no issuer, and no issuer IS the operator.
    signup = IdentityContext(
        user_id=UserId("sub-9"),
        tenant_id=TenantId("sub-9"),
        issuer=Issuer("https://accounts.google.com"),
        email="a@example.com",
        claims={"spend_ceiling_usd": 2.0},
        capabilities=frozenset({"campaign.run"}),
    )
    stores = workspace(tmp_path, signup)
    spawned = JobRegistry(tmp_path / "jobs", capacity=lambda _live: 1)
    held = _held(
        spawned,
        Budget(usd=0.25, tokens=40_000, rounds=None, wall_clock_s=900.0),
        reserve=Budget(usd=0.5, tokens=80_000),
        # The operator named the USD axis and LIFTED the rounds cap: a lift stands as a lift.
        standing=Budget(usd=0.25, rounds=None),
        step_rounds=2,
    )
    # Equality is over the axes NAMED: a lift is no bound, and an omitted axis no lift.
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
    hop = CycleHop(campaign_id="ds__000001", cycle_id="cycle_root")
    mode = RunMode(from_round=2, diag=True)
    launched_config = CampaignConfig(optimization=OptimizationConfig(degradation_threshold=0.05))
    launched = HeldRun(
        job_registry=spawned,
        job_id=held.job_id,
        session=loop_session(stores, pipeline_schema([]), [], hop=hop),
        campaign_config=launched_config,
        budget=held,
        optimizer=select_optimizer(launched_config.optimization),
    )
    spec = launched.job_spec(mode)
    book = _armed(stores, None, spec.budget, spawned)
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
    assert spends_the_hosts_own_key(rebuilt) is spends_the_hosts_own_key(stores) is False, (
        "a signup's run, rebuilt in its own process, spent the host's own key"
    )
    assert rebuilt.base_dir == stores.base_dir, "the spawned run billed another tenant's tree"

    # The look-ahead changes the bill and never the record.
    dataset = [Sample(id=i, query=f"q{i}", ground_truth=str(i % 2)) for i in range(1, 9)]
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
    # A catch-up holds a slot like a cell, and is archived only for a cell the walk absorbed.
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
    # A round's walks measure AT ONCE and decide IN TURN; the depth bounds the ROUND, not each walk.
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
    paused = await _round(built_stores, dataset, armed=1, cuts=[None], pause_after_call=3)
    assert paused["stops"] == ["graceful"], "a pause read as another stop"
    assert len(paused["rows"][0]) == len(paused["calls"]) == 3, (
        "the call that returned as the pause was pressed is absorbed, not paid again on resume"
    )
    # A pause starts nothing: an unstarted catch-up stays so, one already out launches no cell.
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
    capped = await _walk(
        built_stores, dataset, armed=4, max_cells=4, cut_at=2, pause_after_call=2, stall=head
    )
    assert set(capped["banked"]) == {head, dataset[1].id}, (
        "a cell past where a rule could cut was banked: the serial walk would not have measured it"
    )
    ceiling = await _walk(built_stores, dataset, armed=4, max_cells=4, cut_at=None, cap_usd=5.0)
    assert (ceiling["stop_reason"], ceiling["billed"]) == ("spend_budget", 5.0), (
        "a spend ceiling binds BEFORE a call: every cell out is counted at its bound"
    )
    # A skip made before a stop outlives it: the LAST decision on record per candidate replays.
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
            RunCallbacks(skips).on_arm(2, ArmKey(cid), arm_state(cid, cut=cut))

    def standing_skips() -> dict[tuple[int, ArmKey], int]:
        return scan_standing_rounds([LedgerSpan(skips.path)]).skipped

    assert standing_skips() == {(2, "a"): 3, (2, "c"): 4}, "a superseded skip replayed"
    RunCallbacks(skips).on_round_entered(2)
    assert standing_skips() == {(2, "a"): 3, (2, "c"): 4}, "a resume re-entering lost a skip"
    skips.append(RoundEnteredRecord(round=2, rewound=True))
    assert standing_skips() == {}, "a regenerated round inherited a displaced skip"
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

    # A cell's envelope cancels the inner campaign it spawned, so nothing spends past it.
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
    spawning = spawn_context._INNER_SPAWN.set(
        spawn_context.InnerSpawnContext(
            inner_sandbox_root=inner_sandbox(built_stores, owner.campaign_id, owner.cycle_id),
            identity=built_stores.identity,
            shared_root=built_stores.shared_root,
            spawn_campaign_id=owner.campaign_id,
            spawn_cycle_id=owner.cycle_id,
            enclosing=(),
            cells=seats,
            purpose=Optimize(),
            ruler=RulerReach(version=0, own=None),
        )
    )
    token = telemetry.set_cycle_ledger(CycleEventLog(tmp_path / "outer.jsonl"))
    (seat,) = seats.cells
    query = seat.id
    pin = PinnedCell(cell=seat, instrument="i").pin()
    cell = Sample(id=0, query=query, ground_truth=None, source_pin=pin)
    # The connector DECLARES the seconds, the scoring seam enforces them, as in `measure_sample`.
    envelope = CellEnvelope(spawn.inner_cell_envelope_s(cell, {}), attempts=1, label=query)
    try:
        with pytest.raises(CellUnscoreableError):
            async with envelope:
                await spawn.run_inner_cycle(cell, {})
    finally:
        telemetry.reset_cycle_ledger(token)
        spawn_context._INNER_SPAWN.reset(spawning)
    assert started.is_set(), "the inner campaign never started — the envelope proved nothing"
    assert cancelled.is_set(), "the inner campaign outlived its envelope and kept spending"

    # A resumed arm re-reads its cells unbilled, and its bench pass still runs past the ceiling.
    billed_cent = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost": 0.01}
    offline.llm.says = lambda body: completion("ok", model=body["model"], usage=billed_cent)
    loop_reuse = LLMReuseCache(tmp_path / "reuse", "optimizer_reuse")
    judge_reuse = LLMReuseCache(tmp_path / "reuse", "judge_reuse")

    async def round_calls() -> None:
        await call_mod.llm_call(
            [{"role": "user", "content": "q"}],
            config={"provider": "openai", "model": "m"},
            context=call_mod.LLMCallContext(cache=loop_reuse),
        )
        with judge_call.bind_cache(judge_reuse):
            await judge_call.ask(JudgeStage(model="m", provider="openai"), "grade", judge="j")

    # Another campaign's run banks both replies; this arm only ever replays them.
    telemetry.bind_priced(set())
    with _billing_on(CycleEventLog(tmp_path / "other.jsonl"), unbounded_spend_book()):
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
    arm = spend_book(0.02, meters="search_incurred", metered=Budget.amount(0.02, 0))
    ledger = CycleEventLog.open(CycleDir(tmp_path / "cycle"))
    ledger.bind(arm)
    session = loop_session(
        built_stores,
        _searching(0.01),
        reread,
        backend_id="reread",
        scorer=_HIT,
        control=RunControl(book=arm),
        client=backend_client(built_stores.jobs_dir, max_cells_in_flight=1),
    )
    # The arm's individual and the one the bench grades, every cell of each already filed.
    picked, graded = (JobSearchPoint(pipeline_params={"search": {"sp": n}}) for n in "pg")
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
    token = telemetry.set_cycle_ledger(ledger)
    try:
        # The stopped launch priced the cells it took.
        telemetry.bind_priced({feed.cell_key(s.key) for s in reread[:3]})
        with spending_under(arm):
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
    finally:
        telemetry.reset_cycle_ledger(token)
    assert [c.key for c in walk.results] == [s.key for s in reread[:3]], (
        "the ceiling held back a re-read"
    )
    assert halted == "spend_budget", "a spent ceiling did not stop the arm"
    assert len(bench.cells) == len(reread), "the search's ceiling stopped the bench pass"
    kinds = [r.kind for _, r in ledger.iter() if isinstance(r, TokenUsageRecord)]
    assert kinds == ["bench"] * len(reread) + ["optimizer", "judge"], (
        f"a re-read was metered a second time: {kinds}"
    )
    assert arm.usd_metered == pytest.approx(0.04), "the arm's book read another bill than metered"
    priced = scan_ledger_priced_keys([ledger.path])
    benched = _filed(built_stores, graded, session)
    benched_keys = {benched.cell_key(s.key) for s in reread}
    assert benched_keys <= priced and len(priced) == len(reread) + 2, (
        "the ledger names another set of priced calls than the arm paid for"
    )

    # A ledger index serves the file as it stands: grown, compacted, raced and shared.
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
        overlay = {"llm_only": {"model": model}}
        index.append(CycleSeedRecord(seed=CycleSeed(pipeline_overlay=overlay)))

    def tailed() -> tuple[float, int, CycleSeed | None, float]:
        spend, calls, _worked_s = scan_ledger_spend([LedgerSpan(index.path)])
        wallet = iter_user_token_usage(ledgers=[index.path], since=0.0, until=float("inf"))
        charted = sum(row.billed_usd or 0.0 for row in wallet)
        seeded = scan_last([LedgerSpan(index.path)], CycleSeedRecord)
        return spend.total_used_usd, calls, None if seeded is None else seeded.seed, charted

    def cold() -> tuple[float, int, CycleSeed | None, float]:
        rows = iter_jsonl(index.path)
        bills = [row["cost_usd"] for row in rows if row["record_type"] == "token_usage"]
        seeds = [row["seed"] for row in rows if row["record_type"] == "cycle_seed"]
        seeded = CycleSeed.model_validate(seeds[-1]) if seeds else None
        return sum(bills), len(bills), seeded, sum(bills)

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
    write_jsonl(index.path, iter_jsonl(index.path)[:2])
    assert tailed() == cold() == (2.0, 2, None, 2.0), (
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

    # A cell Ada paid for is hers until the host names one install-owned archive home, and Bob's
    # replay of it from there is the same measurement: it bills him nothing.
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
        book = spend_book(1.0, meters="bill")
        ledger = CycleEventLog(homes / f"bob-{shared}.jsonl")
        ledger.bind(book)
        with _billing_on(ledger, book):
            for answer in account("bob-id").archive.population(cell_entry):
                emit_replayed_step_tokens(answer.cell.pipeline.step_tokens, {})
        return replayed_by_bob(), book.usd_metered

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
    with under_budget(SendBudget(None, attempts=SEND_ATTEMPTS)):
        return await get_llm_client("openai").chat(
            ChatRequest([{"role": "user", "content": content}], "gpt-x", max_tokens=max_tokens),
            label=CallLabel(node, "optimizer"),
        )


@contextlib.contextmanager
def _billing_on(ledger: CycleEventLog, book: Any) -> Iterator[None]:
    token = telemetry.set_cycle_ledger(ledger)
    try:
        with spending_under(book):
            yield
    finally:
        telemetry.reset_cycle_ledger(token)


def _token_bills(ledger: CycleEventLog) -> list[TokenUsageRecord]:
    return [r for _, r in ledger.iter() if isinstance(r, TokenUsageRecord)]


def test_a_campaigns_ceiling_binds_every_run_it_is_shared_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, built_stores: Stores
) -> None:
    # The declared ceiling lives on the ledger alone, so a launch's flag sweep cannot take it.
    campaigns = built_stores.campaigns
    hop = CycleHop(campaign_id="camp", cycle_id="cyc")
    campaigns.mint_cycle(hop)
    cycle_dir = campaigns.cycle_dir(hop)
    jobs = JobRegistry(tmp_path / "jobs", capacity=lambda _live: 1)
    declared = _held(jobs, Budget())

    def declare(budget: Budget) -> None:
        # Money binds on the cycle's share, the rest on its own axes: both records, both live.
        campaigns.write_standing_budget(hop, budget.only(CYCLE_AXES), pause_at_round=None)
        hold_moved(built_stores, hop, budget.only(MONEY_AXES))

    CycleEventLog.open(CycleDir(cycle_dir)).append(billed_call(1.0, input_tokens=9_000))
    control = RunControl(book=_armed(built_stores, hop, declared, jobs))
    assert control.budget_tripped() is None, (
        "a run that declared NOTHING still holds a book, which stays silent until a ceiling exists"
    )
    declare(Budget(usd=0.50))
    assert control.budget_tripped() == StopReason.SPEND_BUDGET, "a mid-run ceiling reached no book"
    CycleEventLog.open(CycleDir(cycle_dir)).append(RunPhaseRecord(run_phase=RunPhase.RUNNING))
    assert control.budget_tripped() == StopReason.SPEND_BUDGET, "a launch swept the ceiling"
    declare(Budget(tokens=5_000))
    assert control.budget_tripped() == StopReason.TOKEN_BUDGET, "a token ceiling bound nothing"
    session = loop_session(built_stores, pipeline_schema([]), [], hop=hop)
    config = _budgeted(Budget(usd=0.025, rounds=50))
    declare(Budget(rounds=3))
    bounded, pause_at = round_bounds(session, config)
    assert (bounded.optimization.budget.rounds, pause_at) == (3, None), (
        "a mid-run round cap reached no loop"
    )
    assert control.budget_tripped() is None, "a lifted ceiling still governed the run"
    # Another PROCESS's reshare lands on the cycle's ledger, behind this process's own write of it.
    CycleEventLog.open(CycleDir(cycle_dir)).append(CycleShareRecord(allotment=Budget(usd=0.50)))
    assert control.budget_tripped() == StopReason.SPEND_BUDGET, (
        "a share another process moved was hidden from the running book"
    )
    declare(Budget(rounds=None))
    bounded, _ = round_bounds(session, config)
    assert bounded.optimization.budget.rounds is None, (
        "a LIFT of the round cap reads as no cap, never a fall back to the config's"
    )

    # Moving one ceiling leaves the other at its launch cap.
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
    registry.mark_admitted(job.job_id, Budget(usd=0.30, tokens=5_000_000))
    caps = HeldBudget(
        admitted=Budget(usd=0.30, tokens=210_000),
        standing=Budget(),
        job_id=job.job_id,
        share=Budget(usd=0.30, tokens=210_000),
        step_rounds=None,
    )
    moved_book = _armed(built_stores, moved, caps, registry)
    control = RunControl(book=moved_book)
    assert control.budget_tripped() == StopReason.TOKEN_BUDGET, "the token ceiling bound nothing"
    dispatcher = CommandDispatcher(built_stores, registry)
    armed = _change_run_limits(dispatcher, moved, Budget(usd=0.50))
    assert armed.usd == pytest.approx(0.50), (
        "the trace holds no ceiling a replay could stop the run at"
    )
    assert control.budget_tripped() == StopReason.TOKEN_BUDGET, (
        "a USD raise lifted the token ceiling"
    )
    assert moved_book.allotment.usd == pytest.approx(0.50), (
        "$0.50 ceiling, $0.10 of it this cycle's own: the running book binds another share"
    )
    _change_run_limits(dispatcher, moved, Budget(usd=None))
    assert moved_book.allotment.usd is None, "a lifted ceiling still bounds the running share"

    # An offshoot takes the rounds its parent has left; a cap the fork's seed names is the operator's.
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
    # A fork's money is its campaign's share (below), so no entry point's seed may name its own.
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

    # Spend is no remainder: forks of one campaign launched side by side hold ONE ceiling between
    # them, so the second is admitted only what the first left.
    root = CycleHop(campaign_id="camp-5", cycle_id="cycle_share0005")
    forks = [
        CycleHop(campaign_id="camp-5", cycle_id=f"cycle_share0005_fork_{name}")
        for name in ("a", "b", "c")
    ]
    built_stores.campaigns.create_campaign(minted_campaign(root))
    built_stores.campaigns.mint_cycle(root)
    root_dir = built_stores.campaigns.cycle_dir(root)

    def bill(at: CycleHop, usd: float) -> None:
        CycleEventLog.open(CycleDir(built_stores.campaigns.cycle_dir(at))).append(billed_call(usd))

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
    # The jobs dir every reader attaches to, with room for every fork.
    shared = JobRegistry(tmp_path / "jobs", capacity=lambda _live: 4)
    payer = account_of(built_stores)

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
            )
        except QuotaExceededError as exc:
            shared.release(job.job_id, refusal=str(exc))
            raise
        return job, held

    def share(held: HeldBudget) -> float | None:
        job = shared.get(held.job_id)
        assert job is not None
        return cycle_share(built_stores, job).usd

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
    beside = _armed(built_stores, fork_b, held_b, shared)
    eighth = SendBound(input_tokens=0, output_tokens=0, usd=0.125)
    assert beside.fits(eighth, eighth) == 4, (
        "fork b's book spends what fork a still holds: two forks, two ceilings"
    )
    # A third fork is refused: its siblings hold the ceiling whole.
    with pytest.raises(QuotaExceededError):
        launch(fork_c, 1.0)

    # An embedded run holds a share through the same admission, and never a machine slot.
    async def embedded() -> None:
        async with embedded_share(
            _budgeted(Budget(usd=1.0)),
            stores=built_stores,
            dataset_name="ds1",
            hop=fork_c,
            requested=Budget(),
            step_rounds=None,
        ) as (_, held):
            assert held.admitted.usd == pytest.approx(1.0)
            run = shared.running_job_for(built_stores.campaigns.workspace, fork_c)
            assert run is not None and cycle_share(built_stores, run).usd == pytest.approx(0.5), (
                "an embedded run held no share, or another one"
            )
            assert fork_c not in {j.hop for j in shared.list_running()}, (
                "an embedded run took a machine slot to carry its share"
            )

    with pytest.raises(QuotaExceededError):
        asyncio.run(embedded())
    shared.release(job_b.job_id)
    asyncio.run(embedded())
    assert shared.running_job_for(built_stores.campaigns.workspace, fork_c) is None, (
        "an embedded run kept its share past its exit"
    )

    # A raised ceiling is re-shared over every live cycle, never handed whole to one: A runs at $1,
    # B is admitted at $3 beside it ($2), and the raise to $3 leaves A its $1, not B's money too.
    pair = [CycleHop(campaign_id="camp-6", cycle_id=f"cycle_pair000{n}") for n in (1, 2)]
    built_stores.campaigns.create_campaign(minted_campaign(pair[0]))
    for cycle in pair:
        built_stores.campaigns.mint_cycle(cycle)
    job_a, held_a = launch(pair[0], 1.0)
    book_a = _armed(built_stores, pair[0], held_a, shared)
    job_b6, held_b6 = launch(pair[1], 3.0)
    assert (share(held_a), share(held_b6)) == (1.0, 2.0), (
        "two live cycles were admitted more than their campaign's ceiling between them"
    )
    _change_run_limits(CommandDispatcher(built_stores, shared), pair[0], Budget(usd=3.0))
    assert (book_a.allotment.usd, share(held_a), share(held_b6)) == (1.0, 1.0, 2.0), (
        "a raised ceiling handed one cycle what its sibling holds: the two may spend past it"
    )
    assert book_a.fits(eighth, eighth) == 8, "A's live book binds on another share than it holds"
    # One campaign's money axis binds every live cycle or none, whichever arrived first.
    with pytest.raises(QuotaExceededError):
        launch(CycleHop(campaign_id="camp-6", cycle_id="cycle_pair0003"), None)
    for job in (job_a, job_b6):
        shared.release(job.job_id)
    open_c, _ = launch(CycleHop(campaign_id="camp-6", cycle_id="cycle_pair0004"), None)
    with pytest.raises(QuotaExceededError):
        launch(CycleHop(campaign_id="camp-6", cycle_id="cycle_pair0005"), 1.0)
    shared.release(open_c.job_id)

    # A launch landing mid-reshare waits it out: A holds $1 of $1 while a raise to $3 re-shares,
    # and B, launching at $3 meanwhile, is admitted on the shares as moved, never on a torn split.
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
        hold_budget_change(
            job_registry=shared,
            stores=built_stores,
            hop=race[0],
            change=Budget(usd=3.0),
            reserve=Budget(),
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
            shared.release(job.job_id)
    # A ceiling its campaign has spent admits nothing, siblings or none.
    bill(race[0], 3.0)
    with pytest.raises(QuotaExceededError):
        launch(race[1], 3.0)

    # Sharing divides the CAMPAIGN's ceiling, never what the account admits: a $2 delegate on a
    # $10 campaign that has spent $3 shares the $7 left, and its account binds through the reserve.
    delegate = workspace(
        tmp_path,
        IdentityContext(
            user_id=UserId("sub-d"),
            tenant_id=TenantId("sub-d"),
            issuer=Issuer("https://accounts.google.com"),
            claims={"spend_ceiling_usd": 2.0},
            capabilities=frozenset({"campaign.run"}),
        ),
    )
    delegate.users.save(
        account_of(delegate).model_copy(
            update={"spend_budget_usd_total": 50.0, "token_budget_total": 50_000}
        )
    )
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
    )
    claim_held = shared.get(claim.job_id)
    assert claim_held is not None and cycle_share(delegate, claim_held).usd == pytest.approx(7.0), (
        "a $2 delegate's share was cut from its account's claim, not the campaign's $7 left"
    )
    assert claim_held.reserve.usd == pytest.approx(2.0), "the delegate's claim did not bind it"
    shared.release(claim.job_id)


# 4. What a send is held at


@pytest.mark.usefixtures("run_bound")
def test_a_send_is_held_at_its_bound_until_its_bill_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Router
) -> None:
    """A record of spend is only ever a BILL: an unreported send binds from its open hold."""
    # A remote cell still open at the run's wall clock is CUT: the clock bounds every cell.
    offline.backend.faults = {"/matches": [Hang()]}
    jobs = tmp_path / "jobs"
    remote = backend_client(jobs, cell_envelope_s=cell_envelope_of(600.0))
    clock = spend_book(None, usd_reserve=None, wall_clock_s=30.0)
    cut_session = loop_session(
        workspace(tmp_path / "cut"),
        pipeline_schema([]),
        [],
        scorer=_HIT,
        control=RunControl(book=clock),
        client=remote,
    )

    async def cut_cell() -> tuple[MeasuredCell, float]:
        loop = asyncio.get_running_loop()
        began = loop.time()
        with spending_under(clock):
            row = await measure_sample(Sample(id=0, query="q", ground_truth="a"), cut_session)
        return row, loop.time() - began

    row, waited = jumping_clock(cut_cell())
    assert row.error_category == ErrorCategory.HALTED, (
        "a cell open at the wall clock's edge ended as anything but a HALTED cut"
    )
    assert waited == pytest.approx(30.0, abs=1.0), (
        f"the cell ran {waited:.0f}s: its envelope, not the run's clock, bounded it"
    )
    clock.opened -= 31.0
    assert cut_session.state.control.budget_tripped() == StopReason.WALL_CLOCK, (
        "a run past its wall clock was not stopped on it"
    )

    # The reserve holds a send's bound, and the ceiling what such sends bill.
    cell = CallLabel("cell", "backend")
    worst = SendBound(input_tokens=0, output_tokens=1000, usd=0.125)
    for reserve, depth in ((0.5, 4), (None, 16), (1.0, 8)):
        own = spend_book(0.5, usd_reserve=reserve)
        assert own.fits(worst, worst) == 4, (
            f"reserve {reserve}: nothing billed yet, the bound holds"
        )
        own.learn(cell, Budget.amount(0.03125, 100))
        own.learn(cell, Budget.amount(0.0, 0))
        held = own.held_at(cell, worst)
        assert own.fits(held, worst) == depth, (
            f"reserve {reserve}: held at its bound against both, a ceiling a few bounds wide "
            "walks one cell at a time"
        )
        for _ in range(depth):
            own.hold(held, worst, "backend", what="cell", reservation=None)
        with pytest.raises(SendRefusedError):
            own.hold(held, worst, "backend", what="cell", reservation=None)
        billed = 0
        while own.exhausted() is None:
            own.release(held, worst, "backend")
            own.usd_metered += worst.usd or 0.0
            billed += 1
        assert billed == 4, f"reserve {reserve}: the run stops on reaching its ceiling"
        assert reserve is None or own.usd_metered + (depth - billed) * 0.125 <= reserve, (
            f"reserve {reserve}: a burst billed past the reserve"
        )
    owed = spend_book(0.5)
    owed.usd_unreported = 0.125
    assert owed.fits(worst, worst) == 3, "a send no bill priced left its room open"
    owed.set_aside(0.25, 0)
    assert owed.fits(worst, worst) == 1, "the search could spend what the bench was set aside"
    assert owed.exhausted() is None, "a set-aside read as spent"
    owed.usd_metered = 0.125
    assert owed.fits(worst, worst) == 0 and owed.exhausted() is not None, (
        "a send's room is the ceiling less EVERYTHING owed: bills, unpriced sends, the set-aside"
    )

    # A price list that could not be fetched is an outage, never an unpriced call.
    fetches: list[str] = []

    def down(url: str, **_kw: Any) -> Any:
        fetches.append(url)
        raise urllib.error.URLError("no route to host")

    async def held_twice() -> BaseException:
        with spending_under(spend_book(1.0, usd_reserve=None)):
            for _ in range(2):
                with pytest.raises(pricing.PriceListUnreachableError) as outage:
                    await base.hold_ceiling("vendor/model-x", "openrouter")
        with spending_under(unbounded_spend_book()):
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

    # A controlled arm's ceiling is its search's incurred cost: a replay spends no BILL.
    def replay(usd: float) -> TokenUsageRecord:
        return billed_call(
            usd, kind="backend", node="n", input_tokens=10, output_tokens=5, cached=True
        )

    arm = spend_book(0.10, meters="search_incurred")
    ordinary = spend_book(0.10)
    for book in (arm, ordinary):
        book.count(replay(0.06))
        book.count(replay(0.05))
    assert ordinary.exhausted() is None, "a replay billed nothing, so it spends no bill"
    assert arm.exhausted() == ErrorCategory.SPEND_CEILING, (
        "arms replay each other's cells: a ceiling on the BILL hands the second arm a bigger search"
    )
    fresh = spend_book(0.10, meters="search_incurred")
    fresh.count(replay(0.09))
    pass_bound = SendBound(input_tokens=100, output_tokens=100, usd=5.0)
    fresh.hold(pass_bound, pass_bound, "bench", what="bench pass", reservation=None)
    fresh.release(pass_bound, pass_bound, "bench")
    fresh.count(billed_call(5.0, kind="bench", node="b", output_tokens=1))
    assert fresh.exhausted() is None, "the bench pass ate into the arm's search budget"
    # The pass's cells are backend cells filed as bench: admitted in the bucket they bill to.
    with (
        spending_under(fresh),
        telemetry.filed_as("bench"),
        reserved(CallLabel("cell", "backend"), pass_bound),
    ):
        pass
    small = SendBound(input_tokens=1, output_tokens=1, usd=0.02)
    with pytest.raises(SendRefusedError):
        fresh.hold(small, small, "optimizer", what="o", reservation=None)

    # No burst of sends records spend past its ceiling.
    wire = _priced_wire(offline)
    wire.flaky = True
    rng = wire.rng
    burst_ledger = CycleEventLog(tmp_path / "burst.jsonl")
    ceiling_usd = 0.05
    book = spend_book(ceiling_usd)
    burst_ledger.bind(book)

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
    assert all(r.cost_usd is None for r in records), "a reply that reported no cost read as one"
    recorded = sum(r.rate_priced_usd or 0.0 for r in records)
    assert recorded <= ceiling_usd + 1e-12, (
        f"recorded ${recorded:.6f} past a ${ceiling_usd} ceiling"
    )
    assert recorded == pytest.approx(book.usd_metered), (
        "no reply reported a cost: each record is priced at our rate, and the ceiling binds on that"
    )
    assert any(isinstance(o, SendRefusedError) for o in outcomes), "the ceiling never bound"
    unreported = sum(isinstance(o, asyncio.CancelledError | httpx.ReadTimeout) for o in outcomes)
    left_out = unreported_on(burst_ledger)
    assert unreported and left_out.sends == unreported, "a send that left unbilled lost its hold"
    assert len(records) == sum(not isinstance(o, BaseException) for o in outcomes), (
        "a reply left no bill, or a failed send left one"
    )
    assert book.usd_unreported == pytest.approx(left_out.usd), "the book forgot an open hold"
    assert book.usd_metered + book.usd_unreported <= ceiling_usd + 1e-12, (
        "bills and open holds together ran past the ceiling"
    )
    # A hard exit runs no `finally`: the hold written ahead of the call is all that says it left.
    hold_usd = 0.0036
    killed = SpendHoldRecord(
        hold_id="killed",
        node="killed",
        kind="optimizer",
        input_tokens=600,
        output_tokens=1500,
        cost_usd=hold_usd,
    )
    burst_ledger.append(killed)
    account = billed_spend([burst_ledger.path])
    assert account.used_usd == 0.0, "a price off our rate table was read as spent"
    assert account.rate_priced_usd == pytest.approx(recorded), "the rate-priced sum moved"
    assert account.unreported_usd == pytest.approx(left_out.usd + hold_usd), (
        "a killed send's hold was not counted as out"
    )
    assert unreported_on(burst_ledger).sends == unreported + 1, "a killed send's hold went unread"
    # A bill naming no price closes its hold in tokens alone; the money stays at the hold's bound.
    bare = CycleEventLog(tmp_path / "bare" / "ledger.jsonl")
    bare.append(killed.model_copy(update={"hold_id": "bare", "node": "bare"}))
    bare.append(billed_call(node="bare", input_tokens=40, output_tokens=9, hold_id="bare"))
    assert unreported_on(bare).usd == pytest.approx(hold_usd), "a priceless bill freed its hold"
    assert billed_spend([bare.path]).unreported_usd == pytest.approx(hold_usd), (
        "the account read a priceless bill as settling its hold"
    )

    # A nested run's send is admitted by its own book AND its root's, held and billed on the root's
    # ledger.
    root = CycleEventLog(tmp_path / "root.jsonl")
    book = spend_book(0.05)
    root.bind(book)
    book.ledger = root
    nested = CycleEventLog(tmp_path / "nested.jsonl")
    inner = spend_book(5.0)
    nested.bind(inner)
    inner.ledger, inner.enclosing = nested, book
    with _billing_on(nested, inner):
        jumping_clock(_owned_chat("inner", "nested"))
    assert [(r.node, r.mirrored) for r in _token_bills(root)] == [("inner:inner", False)], (
        "a nested send was not billed on its root's ledger"
    )
    assert [r.mirrored for r in _token_bills(nested)] == [True], "the nested copy bills twice"
    assert unreported_on(root).sends == 0, "a billed nested send stayed held"
    assert billed_spend([nested.path]).sent_usd == 0.0, "the nested ledger billed its mirror"
    assert 0.0 < book.usd_metered == pytest.approx(billed_spend([root.path]).sent_usd), (
        "the root's book read another bill than its ledger holds"
    )
    assert inner.usd_metered == pytest.approx(book.usd_metered), (
        "the nested book counted another bill than its root's"
    )
    room = (inner.money_left(), book.money_left())
    past_root = SendBound(input_tokens=0, output_tokens=0, usd=0.06)
    with pytest.raises(SendRefusedError):
        inner.hold(past_root, past_root, "optimizer", what="past the root", reservation=None)
    assert (inner.money_left(), book.money_left()) == room, (
        "a nested send past its ROOT's ceiling was admitted on its own book's room, or held "
        "half-way"
    )
    cut = SendBound(input_tokens=600, output_tokens=1500, usd=hold_usd)
    with _billing_on(nested, inner):
        Admission(
            inner, CallLabel("cut", "optimizer"), cut, cut, model="gpt-x", provider="openai"
        ).unreported()
    assert unreported_on(root).sends == 1, "a nested send cut short was held nowhere"
    assert book.usd_unreported == pytest.approx(hold_usd), (
        "a nested send cut short stayed held on its own book and freed its root's"
    )
    # An inner campaign whose provider reports no bill is not a free cell: cost reads our rate.
    silent = SpendRollup()
    silent.bank(billed_call(node="n", input_tokens=10, output_tokens=5, rate_priced_usd=0.02))
    facts = inner_cell_facts(cycle_result([0.4], 0.3, []).model_copy(update={"spend": silent}))
    assert silent.total_used_usd == 0.0, "an unbilled inner cell vanished"
    inner_row = MeasuredCell(
        sample_key="k0",
        query="q",
        predicted="",
        ground_truth="",
        pipeline=PipelineData(inner=facts),
    )
    assert cell_channels_of(inner_row, None)["cost"] == pytest.approx(0.02), (
        "an inner campaign whose provider reported no bill read as a free cell"
    )

    # A send that left and named no price stays held at its bound.
    held_ledger = CycleEventLog(tmp_path / "held.jsonl")
    book = spend_book(0.05)
    held_ledger.bind(book)
    wire.broken_once = True
    with _billing_on(held_ledger, book):
        jumping_clock(_owned_chat("tls"))
    assert unreported_on(held_ledger).sends == 1, (
        "a TLS record fault after the request left is resent like a 5xx, and the broken send held"
    )
    wire.unbilled = True
    bills, spent_usd = len(_token_bills(held_ledger)), book.usd_metered
    with _billing_on(held_ledger, book):
        jumping_clock(_owned_chat("unbilled"))
    assert unreported_on(held_ledger).sends == 2 and book.usd_metered == spent_usd, (
        "a reply reporting no usage is a bill that never came: held, never settled at 0"
    )
    assert len(_token_bills(held_ledger)) == bills, "a reply with no usage wrote a bill"
    wire.unbilled = False
    nitro = CallLabel("nitro", "optimizer")
    before, held_usd, spent_usd = unreported_on(held_ledger), book.usd_unreported, book.usd_metered
    with _billing_on(held_ledger, book):
        Admission(book, nitro, cut, cut, model="m:nitro", provider="openrouter").settle(
            Billed(TokenAccount(input=600, output=100), None)
        )
    assert book.usd_metered == spent_usd, "a bill naming tokens and no price read as free"
    assert book.usd_unreported == pytest.approx(held_usd + hold_usd), "it was not held at its bound"
    assert book.held_at(nitro, cut) == cut, "a priceless bill taught the book a price"
    after = unreported_on(held_ledger)
    assert (after.sends, after.tokens) == (before.sends + 1, before.tokens), (
        "a priceless bill's hold was not counted as out"
    )
    assert after.usd == pytest.approx(before.usd + hold_usd), "a priceless hold left its bound"

    # One unit of work resends from one budget, whichever loop asks.
    async def two_rungs() -> None:
        with under_budget(SendBudget(None, attempts=SEND_ATTEMPTS)):
            for _rung in range(2):
                with pytest.raises(httpx.HTTPStatusError):
                    await get_llm_client("openai").chat(
                        ChatRequest([{"role": "user", "content": "x"}], "gpt-x", max_tokens=10),
                        label=CallLabel("down", "optimizer"),
                    )

    wire.down = True
    with spending_under(unbounded_spend_book()):
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

    # A backend cell is never sent again while it may still bill.
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
    # A repaired node's resend is IN its sums: holding it again halted runs short of their cap.
    repaired = {"step_tokens": {"n": {"attempts": 2, "input": 10, "output": 5, "cost_usd": 0.001}}}
    bill, _ = cell_billing(pipeline_schema([]), {}, {"n": SendBound(40, 20, 0.004)})(repaired)
    assert bill is not None and bill.unknown is None, "an answered attempt was held as unreported"
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
        async with CellEnvelope(600.0, attempts=remote.cell_attempts, label="q"):
            return await remote.run_query(
                Sample(id=0, query="q", ground_truth=None),
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
    assert [r.cost_usd for r in _token_bills(cell_ledger)] == [0.0, 0.001], (
        "a failed POST's reported step spend went unbilled"
    )
    assert wallet.usd_unreported == pytest.approx(2 * 0.01 + 2 * 0.004), (
        "a cell that may bill was freed"
    )
    assert wallet.tokens_unreported == 2 * 150 + 2 * 60, "a cell that may bill freed its tokens"
    assert replied(422, headers=None, said="", answered_on=frozenset({422})).may_have_billed, (
        "the relay's 422 on an answer that failed validation is a generation: it may have billed"
    )
    assert not replied(422, headers=None, said="").may_have_billed, "a plain 422 read as billed"

    # A container trial reruns on the budget its connector sized.
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

    # A DSPy cell bills each LM call, and its provider's failure is a hole.
    import dspy  # extras: dspy
    from dspy import lm15

    from promptpotter.connectors import dspy_module  # extras: dspy

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
        pairs = {dspy_module.PROGRAM_NODE: client.priced_as(config)}

        async def one_cell() -> Any:
            async with CellEnvelope(600.0, attempts=client.cell_attempts, label="q"):
                return await client.run_query(
                    Sample(id=0, query="q", ground_truth="4"),
                    {dspy_module.PROGRAM_NODE: config},
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
    assert [r.cost_usd for r in priced] == [None, None] and [
        r.rate_priced_usd for r in priced
    ] == pytest.approx([call_usd] * 2), (
        "the engine reports tokens and no cost: each call is priced at our rate"
    )
    assert purse.usd_metered == pytest.approx(2 * call_usd), "the ceiling bound another price"
    banked_step = rows[-1].step_tokens[dspy_module.PROGRAM_NODE]
    assert (banked_step.model, banked_step.provider) == ("gpt-x", "openai"), (
        "the ROW names no route its price could be read on"
    )
    assert banked_step.rate_priced_usd == pytest.approx(2 * call_usd), (
        "the ROW states another price: the fitness cost term and a replay's ceiling read it"
    )
    replay_arm = spend_book(1.0, meters="search_incurred")
    replays = CycleEventLog(tmp_path / "replays.jsonl")
    replays.bind(replay_arm)
    with _billing_on(replays, replay_arm):
        emit_replayed_step_tokens(rows[-1].step_tokens, {})
    assert replay_arm.usd_metered == pytest.approx(2 * call_usd), (
        "a replayed DSPy cell read as free"
    )
    with pytest.raises(CellHaltedError):
        dspy_cell(3)
    assert len(sends) == 2, f"{len(sends)} calls sent under a bound sized on two"
    assert len(lm_bills()) == 4, "the calls a halted cell had made went unbilled"
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

    # A cell billed send by send reserves its bound and holds only what is out.
    spoke, mixed = harbor.ReportedCost(), harbor.ReportedCost()
    for cost, heard in ((0.01, (spoke, mixed)), (None, (mixed,))):
        for block in heard:
            block.count(answered(Billed(TokenAccount(input=10, output=5), cost)))
    assert (spoke.usd, mixed.usd) == (0.01, None), (
        "one silent send makes the REPORTED sum unknown, never the smaller price of those that spoke"
    )
    turns: list[Any] = []
    cut_short = [True]
    three_answered = asyncio.Event()

    async def episode_run(_workload: Any, _sample: Any, _payload: dict[str, Any]) -> dict[str, Any]:
        for n in range(4):
            wire.hang = cut_short[0] and n == 3
            turns.append(
                await get_llm_client("openai").chat(
                    ChatRequest(
                        [{"role": "user", "content": f"turn {n}"}], "gpt-x", max_tokens=100
                    ),
                    label=CallLabel("agent", "backend"),
                )
            )
            if len(turns) == 3:
                three_answered.set()
        return {"data": {"terminal_node": "agent"}}

    agent = backend_client(
        jobs, execution="in_process", in_process_run=episode_run, holds_own_sends=True
    )
    turns_ledger = CycleEventLog(tmp_path / "turns.jsonl")
    purse = spend_book(1.0)
    turns_ledger.bind(purse)
    whole = SendBound(input_tokens=100_000, output_tokens=50_000, usd=0.5)

    async def agent_cell(sample_id: int) -> Any:
        async with CellEnvelope(600.0, attempts=agent.cell_attempts, label="q"):
            return await agent.run_query(
                Sample(id=sample_id, query="q", ground_truth=None),
                bound=whole,
                billed=lambda _data: (None, None),
            )

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
    assert purse.usd_metered == pytest.approx(sum(r.bill_or_rate_usd or 0.0 for r in cut_bills)), (
        "the book read another bill than the turns wrote"
    )
    out = unreported_on(turns_ledger)
    assert whole.usd is not None
    assert out.sends == 1 and out.usd < whole.usd / 100, f"a cancel left ${out.usd} unreported"
    assert purse.usd_unreported == pytest.approx(out.usd), "the book held another sum than is out"
    assert purse.fits(whole, whole) == 1, "a cell's bound was not reserved whole"
    assert purse.held_at(CELL, whole) == whole, "a cut cell taught the book a price"
    cut_short[0] = False
    with _billing_on(turns_ledger, purse):
        jumping_clock(agent_cell(1))
    episode_usd = sum(r.bill_or_rate_usd or 0.0 for r in _token_bills(turns_ledger)) - sum(
        r.bill_or_rate_usd or 0.0 for r in cut_bills
    )
    assert purse.held_at(CELL, whole).usd == pytest.approx(episode_usd), (
        "a cell that ran to its end did not teach what such a cell bills"
    )
    turn = CallLabel("agent", "backend")
    resumed = spend_book(1.0, usd_reserve=None)
    assert resumed.held_at(turn, whole) == whole, "a fresh book held a turn below its bound"
    resumed.take_up(turns_ledger)
    assert resumed.held_at(turn, whole) == purse.held_at(turn, whole) != whole, (
        "a book armed over that ledger (a resume) holds a turn at its bound, not what turns billed"
    )


# 5. An account's ceiling


_WEB = "https://accounts.google.com"
_GRANT_USD = settings.FREE_TIER_SPEND_CAP_USD
_TOKENS = settings.FREE_TIER_TOKEN_CAP
_GENEROUS = {"spend_budget_usd_total": 50.0, "token_budget_total": 50_000}
_HELD_HOP = CycleHop(campaign_id="camp", cycle_id="cycle_x")


def _account_stores(
    tmp_path: Path,
    *,
    issuer: str | None,
    user: User,
    spent: dict[str, Any] | None,
    grant: float | None = None,
) -> Stores:
    """`issuer` set makes this a WEB identity; the box operator has none and is unmetered. Its one
    campaign's cycle is `_HELD_HOP`, which holds what the account *spent*."""
    stores = workspace(
        tmp_path,
        IdentityContext(
            user_id=UserId(user.user_id),
            tenant_id=TenantId(user.tenant_id),
            issuer=None if issuer is None else Issuer(issuer),
            email="",
            claims={} if grant is None else {"spend_ceiling_usd": grant},
        ),
    )
    stores.users.save(user)
    stores.campaigns.create_campaign(minted_campaign(_HELD_HOP, config=_budgeted(Budget())))
    stores.campaigns.mint_cycle(_HELD_HOP)
    if spent is not None:
        CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(_HELD_HOP))).append(
            billed_call(
                kind="backend",
                node="solve",
                provider="openrouter",
                timestamp="2026-01-01T00:00:00+00:00",
                **spent,
            )
        )
    return stores


def _running(registry: JobRegistry, *, admitted: bool, reserve: Budget) -> None:
    """Another run of the account holding *reserve*, admitted or still at its gate."""
    held = registry.create(
        user_id="sub-9",
        principal_id="sub-9",
        workspace=WorkspaceDir(registry.jobs_dir),
        hop=UNRESOLVED_HOP,
        holder=CampaignLaunch(dataset_name="d"),
        stage=RunPhase.RUNNING,
    )
    registry.set_reserve(held.job_id, reserve)
    if admitted:
        registry.mark_admitted(held.job_id, reserve)


async def _paid_door(door: str, stores: Any) -> None:
    match door:
        case "turn":
            async with paid_call(stores=stores, bucket="turn"):
                pass
        case "bench":
            await grade_line_bench(stores=stores, hop=_HELD_HOP)
        case "noise-floor":
            await measure_noise_floor(stores=stores, hop=_HELD_HOP, k=RescoreCount(2))
        case "seed-screen":
            await screen_inner_seeds(
                stores=stores, identity=stores.identity, dataset_name="d", seeds=[1], n_samples=1
            )
        case "probe-reasoning":
            await probe_reasoning("openai/gpt-4o", stores=stores)


_SPENT_THE_CAP = {
    "model": "openai/gpt-4o",
    "input_tokens": 1_000,
    "output_tokens": 500,
    "cost_usd": settings.FREE_TIER_SPEND_CAP_USD,
}
_UNPRICEABLE = {
    "model": "openai/gpt-oss-20b:nitro",
    "input_tokens": 400_000,
    "output_tokens": 100_000,
}


@pytest.mark.usefixtures("run_bound")
def test_an_accounts_ceiling_holds_at_every_paid_door_in_both_units(
    tmp_path: Path,
    offline: Router,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """TWO units, because a price needs a rate on file and a token count never does (decisions.md § 0003)."""
    offline.rates = {
        "openrouter/openai/gpt-4o": {"input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6}
    }
    assert pricing.refresh_rates(force=True), "the world's price list was not fetched"
    telemetry.emit_token_usage(
        node="l1_generate",
        kind="optimizer",
        usage=TokenAccount(input=400_000, output=100_000),
        duration_s=0.0,
        model="openai/gpt-4o",
        provider="openrouter",
    )
    priced = sum_user_spend(ledgers=[tmp_path / "bound.jsonl"])
    assert (priced.used_usd, priced.unpriced_tokens) == (0.0, 0), (
        "a price off our rate table was recorded as spend, or its tokens as unpriced"
    )
    assert priced.rate_priced_usd == pytest.approx(400_000 * 1e-6 + 100_000 * 2e-6), (
        "a price off our rate table was not recorded beside spend"
    )

    for n, (door, issuer, budget, declared, grant, running, spent, admitted_caps) in enumerate(
        (
            ("launch", _WEB, {}, None, None, None, None, Budget(usd=_GRANT_USD, tokens=_TOKENS)),
            # A free-tier run is admitted whole at its campaign's own ceiling, never a ration under it.
            ("launch", _WEB, {}, 0.15, None, None, None, Budget(usd=0.15, tokens=_TOKENS)),
            ("launch", _WEB, {}, 10.0, None, None, None, None),
            (
                "launch",
                _WEB,
                {},
                None,
                None,
                {
                    "admitted": True,
                    "reserve": Budget(usd=settings.FREE_TIER_SPEND_CAP_USD, tokens=_TOKENS),
                },
                None,
                None,
            ),
            (
                "launch",
                _WEB,
                {},
                None,
                None,
                {"admitted": False, "reserve": Budget()},
                None,
                None,
            ),
            (
                "launch",
                _WEB,
                {},
                None,
                None,
                None,
                _UNPRICEABLE,
                Budget(usd=settings.UNPRICED_GRACE_USD, tokens=_TOKENS - 500_000),
            ),
            ("launch", None, {}, None, None, None, None, Budget(usd=None, tokens=None)),
            ("launch", _WEB, _GENEROUS, 10.0, 2.0, None, None, Budget(usd=2.0, tokens=50_000)),
            ("launch", _WEB, _GENEROUS, 10.0, None, None, None, Budget(usd=10.0, tokens=50_000)),
            (
                "launch",
                _WEB,
                {**_GENEROUS, "spend_budget_usd_total": 1.0},
                None,
                2.0,
                None,
                None,
                Budget(usd=1.0, tokens=50_000),
            ),
            ("turn", None, {}, None, None, None, _SPENT_THE_CAP, Budget(usd=None, tokens=None)),
            *(
                (door, _WEB, {}, None, None, None, _SPENT_THE_CAP, None)
                for door in ("turn", "bench", "noise-floor", "seed-screen", "probe-reasoning")
            ),
        )
    ):
        at = tmp_path / f"door-{n}"
        at.mkdir()
        user = User.model_validate(
            {"user_id": "sub-9", "tenant_id": "sub-9", "created_at": "2026-01-01", **budget}
        )
        stores = _account_stores(at, issuer=issuer, user=user, spent=spent, grant=grant)
        registry = JobRegistry(stores.jobs_dir, capacity=lambda _live: 1)
        if running is not None:
            _running(registry, **cast("dict[str, Any]", running))
        refused = pytest.raises(QuotaExceededError)
        with refused if admitted_caps is None else contextlib.nullcontext():
            knocked = Budget(usd=None, tokens=None)
            if door == "launch":
                knocked = admit_launch(
                    declared=Budget() if declared is None else Budget(usd=declared),
                    user=user,
                    stores=stores,
                    job_registry=registry,
                    job_id="job-a",
                    hop=None,
                )[0]
            else:
                asyncio.run(_paid_door(door, stores))
            assert admitted_caps is not None
            assert knocked == admitted_caps, (
                f"door {n} ({door}): an account was admitted under another ceiling than it holds"
            )

    # The free tier bounds what a run may ask for, off one table; paid and host accounts are not on it.
    for n, (issuer, budget, on_tier) in enumerate(
        ((_WEB, {}, True), (None, {}, False), (_WEB, _GENEROUS, False))
    ):
        at = tmp_path / f"tier-{n}"
        at.mkdir()
        user = User.model_validate(
            {"user_id": "sub-9", "tenant_id": "sub-9", "created_at": "2026-01-01", **budget}
        )
        stores = _account_stores(at, issuer=issuer, user=user, spent=None)
        assert on_free_tier(stores) is on_tier, f"account {n} sits on the wrong side of the tier"
    offline.rates = {"openai/dear": {"input_cost_per_token": 5e-8, "output_cost_per_token": 6e-7}}
    assert pricing.refresh_rates(force=True), "the world's price list was not fetched"
    assert (pricing.route_priced("x", "openai"), pricing.route_priced("dear", "openai")) == (
        False,
        True,
    ), "a dollar ceiling would admit a route no rate prices"
    oss = "openai/gpt-oss-20b:nitro"
    config = _budgeted(Budget(usd=None))
    layered = free_tier.with_free_tier_layer(config, {"llm_only": {"model": oss}})
    assert (
        layered.sp_budget_origin,
        layered.optimization.nodes["adaptive_queue"].config["sp_budget_round"],
        layered.pipeline_overlay,
    ) == (20, 20, {"llm_only": {"reasoning_effort": "low"}}), (
        "a free check-in measured on more cells, or launched above its model's lowest rung"
    )
    schema = parse_pipeline_response(
        {
            "nodes": {
                "llm_only": {
                    "config": {"model": oss},
                    "optimizer": {"param_keys": ["reasoning_effort"]},
                }
            },
            "pipelines": {"default": ["llm_only"]},
        }
    )

    def optimizer_on(effort: str) -> CampaignConfig:
        luna = {"model": "openai/gpt-6-luna", "reasoning_effort": effort}
        overlay = {"optimization": {"nodes": {"l1_generate": {"config": luna}}}}
        return CampaignConfig.model_validate(
            merge_config_layers(layered.model_dump(mode="json", exclude_unset=True), overlay)
        )

    for run, effort, steps, outside in (
        (layered, "low", ["llm_only"], 0),
        (layered, "none", ["llm_only"], 1),
        # The axis left open: the optimizer could search any rung, so Start refuses it.
        (config, "low", ["llm_only"], 1),
        (layered, "low", ["web_search", "llm_only"], 1),
        (optimizer_on("medium"), "low", ["llm_only"], 0),
        (optimizer_on("high"), "low", ["llm_only"], 1),
    ):
        params = {"steps": steps, "llm_only": {"model": oss, "reasoning_effort": effort}}
        off = free_tier.outside_free_tier(run, select_optimizer(run.optimization), params, schema)
        assert len(off) == outside, f"Start ran {off or params} on the free tier, or refused it"
    with pytest.raises(PayloadInvalidError):
        free_tier.refuse_outside_free_tier(off)

    # A paid verb never bills a cycle its producer still holds.
    operator = User(user_id="op", tenant_id="op", created_at="")
    held_stores = _account_stores(tmp_path / "held", issuer=None, user=operator, spent=None)
    cycle_dir = held_stores.campaigns.cycle_dir(_HELD_HOP)
    hold_cycle(cycle_dir)

    async def bill_held() -> None:
        async with paid_pass(stores=held_stores, verb="bench", hop=_HELD_HOP, declared=Budget()):
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

    # A scoring pass holds a slot and its reservation: a launch admitted beside it must not spend
    # the headroom the pass is already spending.
    passing = workspace(
        tmp_path,
        IdentityContext(
            user_id=UserId("sub-8"),
            tenant_id=TenantId("sub-8"),
            issuer=Issuer(_WEB),
            claims={},
            capabilities=frozenset(),
        ),
    )
    payer = account_of(passing).model_copy(update={"spend_budget_usd_total": 1.0})
    passing.users.save(payer)
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
            )[0]
        finally:
            slots.release(launch.job_id)

    async def pass_beside() -> None:
        async with paid_pass(
            stores=passing, verb="verify", hop=None, declared=Budget()
        ) as pass_book:
            assert pass_book.reserve.usd == pass_book.allotment.usd == pytest.approx(1.0), (
                "a pass declared nothing and did not hold the account's headroom, or ran unshared"
            )
            assert len(machine_status(slots, identity=passing.identity).running) == 1, (
                "a paid pass ran beside the machine's slot count"
            )
            with pytest.raises(QuotaExceededError):
                launch_beside()

    asyncio.run(pass_beside())
    assert not machine_status(slots, identity=passing.identity).running, (
        "a pass kept its slot past its exit"
    )
    assert launch_beside().usd == pytest.approx(1.0), "the pass kept its reservation past its exit"

    # A budget change leaves the arm it did not touch alone.
    def web_account(sub: str) -> Stores:
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

    metered = web_account("sub-9")
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
    registry.mark_admitted(job.job_id, Budget(usd=0.30, tokens=5_000_000))
    hold_moved(metered, hop, Budget(usd=0.15, tokens=2_500_000))
    dispatcher = CommandDispatcher(metered, registry)
    _change_run_limits(dispatcher, hop, Budget(rounds=3))
    _change_run_limits(dispatcher, hop, Budget(tokens=1_000))
    held = registry.get(job.job_id)
    assert held is not None and held.reserve == Budget(usd=0.30, tokens=2_000), (
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
    reread = metered.campaigns.load_campaign("camp-3")
    assert reread is not None and reread.standing_budget == Budget(tokens=1_000), (
        "a money ceiling landed on one cycle, not the campaign every cycle shares"
    )
    # An UNCAP lifts the campaign's ceiling; the account still binds it through the reservation.
    armed = _change_run_limits(dispatcher, hop, Budget(usd=None))
    assert armed == Budget(usd=None, tokens=1_000, rounds=3), (
        "the ack's ceiling is not what a stop fires at: the campaign's money and the cycle's own"
    )
    lifted, held = metered.campaigns.load_campaign("camp-3"), registry.get(job.job_id)
    assert lifted is not None and lifted.standing_budget == Budget(usd=None, tokens=1_000), (
        "an uncapped campaign ceiling was stored as the account's headroom"
    )
    assert held is not None and cycle_share(metered, held).usd is None, (
        "an uncap became a bounded share"
    )
    assert held.reserve.usd is not None, "an uncap left the account nothing held back for the run"
    granted = User(user_id="sub-9", tenant_id="sub-9", created_at="2026-01-01")
    caps, _ = clamp_budget_change(
        requested=Budget(tokens=1_000),
        user=granted,
        stores=_account_stores(
            tmp_path / "delegated", issuer=_WEB, user=granted, spent=None, grant=2.0
        ),
        job_registry=JobRegistry(tmp_path / "jobs-d", capacity=lambda _live: 1),
        hop=_HELD_HOP,
    )
    assert caps == Budget(tokens=1_000), "a grant became a ceiling on an axis the caller left alone"

    # A moved ceiling is the CAMPAIGN's: every cycle's spend counts against it once, a fork's
    # walked prefix not twice, and what the parent billed past the cut counts too.
    forker = web_account("sub-7")
    root = CycleHop(campaign_id="camp-7", cycle_id="cycle_budget0007")
    fork = CycleHop(campaign_id="camp-7", cycle_id="cycle_budget0007_fork_r1")
    forker.campaigns.create_campaign(minted_campaign(root))
    forker.campaigns.mint_cycle(root)

    def bill(at: CycleHop, usd: float) -> None:
        CycleEventLog.open(CycleDir(forker.campaigns.cycle_dir(at))).append(
            billed_call(usd, input_tokens=1_000)
        )

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
    registry.mark_admitted(job.job_id, Budget(usd=0.8))
    hold_moved(forker, fork, Budget(usd=0.8))
    allowance = User(
        user_id="sub-7", tenant_id="sub-7", created_at="2026-01-01", spend_budget_usd_total=1.0
    )
    caps, reserve = clamp_budget_change(
        requested=Budget(usd=5.0),
        user=allowance,
        stores=forker,
        job_registry=registry,
        hop=fork,
    )
    relaunched, _ = admit_launch(
        declared=Budget(),
        user=allowance,
        stores=forker,
        job_registry=registry,
        job_id=job.job_id,
        hop=fork,
    )
    assert caps.usd == relaunched.usd == pytest.approx(1.0), (
        "$1.00 allowance, $0.50 of it the campaign's own: its ceiling is the whole $1.00, which "
        "already counts that $0.50 — not $1.10 off a fork prefix counted twice"
    )
    assert reserve.usd == pytest.approx(1.0), "the reservation moved off the ceiling it backs"

    # A head-to-head is one door: every arm planned, priced and admitted before any takes a key,
    # so a refused act leaves no arm behind; each arm runs the budget it declared, and a retry
    # under the act's key answers the arms it minted.
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
    rows = Bank.of(samples(60, "TRUE"))
    stores.tenant_datasets.put_bank(rows)
    stores.tenant_datasets.relabel("duel", rows.dataset_hash)
    detached: list[JobSpec] = []

    def ran_elsewhere(registry: JobRegistry, spec: JobSpec, **_: Stores) -> asyncio.Task[None]:
        """The arm's own process, run and exited: its slot handed back, nothing spent."""
        detached.append(spec)
        registry.release(spec.job_id)
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
            "capo-b3=capo,nodes.blocks.max_blocks=3",
            "--max-usd",
            "50",
            "--max-rounds",
            "1",
        ],
    )
    campaign_runner.main()
    started = HeadToHeadStarted.model_validate_json(capsys.readouterr().out)
    arms = mint.standing_arms(stores, "duel-1")
    assert {a.arm_key: a.hop for a in started.arms} == {k: c.root_hop for k, c in arms.items()}, (
        "the terminal answered other arms than the head-to-head records"
    )
    assert [spec.hop for spec in detached] == [a.hop for a in started.arms], (
        "an arm ran in no process of its own, or in another arm's cycle"
    )
    assert len({c.root_content_hash for c in arms.values()}) == 1, (
        "two arms of one head-to-head searched from different origins"
    )
    ran = {k: resolve_campaign_config(stores, c, c.root_hop).optimization for k, c in arms.items()}
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
    answers = [
        asyncio.run(commands_api.mint_head_to_head(registry, stores, funded, "duel-2-once"))
        for _ in range(2)
    ]
    assert answers[0] == answers[1], "a retry under the act's key answered other arms"
    assert len(campaigns()) == len(minted) + 2 and len(detached) == 4, (
        "a retry under the act's key minted or launched its arms again"
    )


# 6. Spend outlives what spent it


def test_spend_outlives_the_fork_the_campaign_and_the_sandbox_that_spent_it(
    built_stores: Stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = built_stores
    root = "cycle_root0000"
    stub, retried = f"{root}_fork_aaaa", f"{root}_fork_bbbb"
    stores.campaigns.create_campaign(minted_campaign(CycleHop(campaign_id="camp-2", cycle_id=root)))

    def spent_cycle(cycle_id: str, *, cost_usd: float) -> Path:
        hop = CycleHop(campaign_id="camp-2", cycle_id=cycle_id)
        stores.campaigns.mint_cycle(hop)
        cycle_dir = stores.campaigns.cycle_dir(hop)
        CycleEventLog.open(CycleDir(cycle_dir)).append(
            billed_call(
                cost_usd,
                kind="backend",
                node="backend",
                model="openai/gpt-4o",
                provider="openrouter",
                input_tokens=2_000,
                output_tokens=800,
                timestamp="2026-01-01T00:00:00+00:00",
            )
        )
        return CycleLayout(cycle_dir).ledger

    spent_cycle(root, cost_usd=0.07)
    stub_ledger = spent_cycle(stub, cost_usd=0.11)
    retried_ledger = spent_cycle(retried, cost_usd=0.05)

    def account_usd() -> float:
        return sum_user_spend(ledgers=account_ledgers(stores.campaigns)).used_usd

    def cleanup(cycle_id: str) -> bool:
        return cleanup_stub_fork_if_empty(
            campaign_store=stores.campaigns, hop=CycleHop(campaign_id="camp-2", cycle_id=cycle_id)
        )[0]

    def tombstones() -> list[SpendTombstoneRecord]:
        workspace = CycleEventLog(CycleEventLog.workspace_path(stores.campaigns.workspace))
        return [r for _, r in workspace.iter() if isinstance(r, SpendTombstoneRecord)]

    before = account_usd()
    assert before == pytest.approx(0.07 + 0.11 + 0.05), (
        "the account read another sum than its cycles billed"
    )
    assert not cleanup(root) and account_usd() == pytest.approx(before), (
        "a cycle the delete REFUSES must not be banked: it keeps its rows, the same money twice"
    )

    def ceiling_counts_now() -> tuple[float, int]:
        counted = stores.campaigns.metered_spend("camp-2", "bill")
        return counted.usd_amount, counted.token_amount

    ceiling_counts = ceiling_counts_now()
    assert ceiling_counts == pytest.approx((before, 3 * 2_800)), (
        "the campaign's ceiling read another sum than its cycles metered"
    )
    assert cleanup(stub) and not stub_ledger.exists(), "a spent stub fork survived its delete"
    assert account_usd() == pytest.approx(before), "deleting a spent stub fork un-spent it"
    assert ceiling_counts_now() == pytest.approx(ceiling_counts), (
        "deleting a spent stub fork handed its money back to the campaign's ceiling"
    )
    retried_hop = CycleHop(campaign_id="camp-2", cycle_id=retried)
    for _crashed_attempt in range(2):
        bank_spend(
            workspace=stores.campaigns.workspace,
            cycle_dirs=[stores.campaigns.cycle_dir(retried_hop)],
            subject=BankedSubject(campaign_id="camp-2", cycle_id=retried),
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
    # A campaign whose manifest is gone still deletes, and banks: nothing reads its rows as one.
    camp_dir = stores.campaigns.campaign_root_dir("camp-2")
    CampaignLayout(camp_dir).manifest.unlink()
    assert stores.campaigns.delete_campaign(
        "camp-2", keep_results=True, changed_at="2026-01-02T00:00:00Z"
    )
    assert not camp_dir.exists(), "a manifest-less campaign kept rows nothing marks deleted"
    assert account_usd() == pytest.approx(before), (
        "banked by the destroyer itself, under BOTH `keep_results` arms, so no caller can skip it"
    )

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
    assert [b.used_tokens for b in banked] == [600 + 100] and [
        b.used_usd for b in banked
    ] == pytest.approx([0.15]), (
        "a forwarded inner cycle was banked twice, or its own spend not at all"
    )

    # A draft never minted banks under its own id, and a Start's claim on it holds off a discard.
    def spent_draft(draft_id: str, cost_usd: float) -> Path:
        draft_dir = stores.drafts.draft_dir(draft_id)
        CycleEventLog.open(CycleDir(draft_dir)).append(
            billed_call(
                cost_usd,
                kind="backend",
                node="checkin",
                model="openai/gpt-4o",
                provider="openrouter",
                input_tokens=300,
                output_tokens=100,
            )
        )
        return draft_dir

    draft = new_draft(
        tenant_id=stores.identity.tenant_id,
        owner_user_id=str(stores.identity.user_id),
        slug="drafted",
        n_samples=1,
        sample_preview=[],
        headers=[],
        source_dataset="drafted",
    )
    stores.drafts.write_draft(draft.draft_id, draft.to_disk())
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
    intruder = dataclasses.replace(
        stores, identity=dataclasses.replace(stores.identity, user_id=UserId("intruder"))
    )
    with pytest.raises(NotFoundError):
        asyncio.run(CommandDispatcher(intruder).dispatch(CommandCall(edit)))
    claim.release()
    command(DiscardDraftPayload(draft_id=draft.draft_id))
    assert not draft_dir.exists(), "a discard left the draft's dir, or wrote it back"
    with pytest.raises(NotFoundError):
        command(edit)
    assert not draft_dir.exists(), "an edit on a closed draft wrote its dir back"
    (draft_bank,) = [t for t in tombstones() if t.draft_id == draft.draft_id]
    assert not draft_bank.campaign_id and draft_bank.used_usd == pytest.approx(0.04), (
        "a discarded draft banked under a campaign's id, or not its own spend"
    )
    assert account_usd() == pytest.approx(with_draft), "discarding a spent draft un-spent it"
    with pytest.raises(NotFoundError):
        command(StartCheckinPayload(draft_id=draft.draft_id))
    with pytest.raises(NotFoundError):
        stores.drafts.claim(draft.draft_id)
    assert not draft_dir.exists(), "a Start on a discarded draft wrote its dir back"

    # A framing paid before its cycle exists bills a draft with no check-in, and its discard banks it.
    async def framed(*_: object, ledger: CycleEventLog, **__: object) -> None:
        ledger.append(
            billed_call(
                0.03,
                node="checkin",
                model="openai/gpt-4o",
                provider="openrouter",
                input_tokens=500,
                output_tokens=200,
            )
        )
        raise PayloadInvalidError("refused after the framing", code="refused")

    monkeypatch.setattr(mint, "commit_task_framing", framed)
    unframed = {"optimization": {"degradation_threshold": 0.4}}
    write_yaml(
        stores.tenant_datasets.dataset_dir("drafted") / DEFINITION_FILE,
        {"pipeline": {"backend_type": "termnorm"}, "campaign": unframed},
    )
    rows = samples(30)
    solve = PipelineNode(name="solve", tunes_llm=False, prompt_info=PROMPT_INFO)
    framing = loop_session(
        stores,
        pipeline_schema([solve], name="drafted"),
        rows,
        dataset=resolve_dataset(stores, "drafted"),
    )
    declared = mint.declare(framing, CampaignConfig.model_validate(unframed), purpose=Optimize())
    with pytest.raises(PayloadInvalidError):
        asyncio.run(
            mint.mint_framed_cycle(
                framing, declared, rows, campaign_id="framed__000001", task_text="frame this"
            )
        )
    assert not draft_dirs(stores.campaigns.workspace), "an unminted framing left its ledger"
    (framing_bank,) = [t for t in tombstones() if t.draft_id not in ("", draft.draft_id)]
    assert not framing_bank.campaign_id and framing_bank.used_usd == pytest.approx(0.03), (
        "a refused mint's framing went unbanked, or banked under the campaign it never minted"
    )
    assert account_usd() == pytest.approx(with_draft + 0.03), "a refused mint un-spent its framing"
