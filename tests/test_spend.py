"""Money — what is billed, and what a ceiling holds.

Owns `infrastructure/llm/` (pricing, the spend book, wire cost), `application/jobs/quota.py`,
`account_spend.py`, the runner's budget gate and judge billing. Spend that reads $0, a cell billed
twice, a ceiling nothing enforces, a delete that hands the money back.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import random
import selectors
import ssl
import threading
import types
import urllib.error
from collections.abc import Callable, Coroutine, Iterator
from pathlib import Path
from typing import Any, cast
from unittest import mock

import httpx
import openai
import pytest
from factories import (
    SANDBOX_CAMPAIGN,
    backend_client,
    cycle_result,
    inner_sandbox,
    pipeline_schema,
    pobb_knobs,
    scored_candidate,
    spend_book,
)
from openai.types.chat import ChatCompletion
from pydantic import BaseModel, Field

import promptpotter.infrastructure.llm.spend_book as spending
from promptpotter.application.bench import llm_call as call_mod
from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.commands.dispatcher import CommandDispatcher
from promptpotter.application.commands.limits_and_queue import _apply_change_run_limits
from promptpotter.application.diagnostics.noise_floor import measure_noise_floor
from promptpotter.application.diagnostics.probe_reasoning import probe_reasoning
from promptpotter.application.diagnostics.seed_screen import screen_inner_seeds
from promptpotter.application.jobs.launcher.run_job import HeldRun, JobSpec
from promptpotter.application.jobs.quota import (
    QuotaExceededError,
    admit_launch,
    clamp_budget_change,
    paid_verb,
    spends_the_hosts_own_key,
)
from promptpotter.application.jobs.reaper import reclaim_orphan_sandboxes
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.optimizers.potter.pobb.checks import PoBBCheck
from promptpotter.application.optimizers.potter.race import CatchUpPool
from promptpotter.application.run_phase_control import RunControl
from promptpotter.application.runner.entry import _arm_spend_book
from promptpotter.application.runner.grade_bench import grade_line_bench
from promptpotter.application.runner.inner import spawn, spawn_context
from promptpotter.application.runner.inner.tasks import InnerCells, load_inner_tasks
from promptpotter.application.runner.loop import _round_bounds
from promptpotter.application.runner.measurement import _skips_on_record
from promptpotter.application.runner.termination import run_stop_reason
from promptpotter.application.scoring import query_loop
from promptpotter.application.scoring.cell_envelope import CellEnvelope
from promptpotter.application.scoring.formula import cell_channels_of, compile_scorer
from promptpotter.application.scoring.metrics import INVALID_SCORES
from promptpotter.application.scoring.sample_measurement import (
    cell_billing,
    emit_replayed_step_tokens,
    measure_sample,
)
from promptpotter.config.settings import settings
from promptpotter.connectors.protocol import InProcessWorkload
from promptpotter.connectors.termnorm import TermNormSession
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.cycle_paths import CycleDir, CycleHop, WorkspaceDir
from promptpotter.domain.l4.proxies import inner_cell_facts
from promptpotter.domain.launch_limits import HeldLimits, RoundsCap, RunMode
from promptpotter.domain.phases import REFUSAL_STOPS, PauseCause, RunPhase, StopLoop, StopReason
from promptpotter.domain.pipeline_schema import PipelineNode, WebSpendBound
from promptpotter.domain.results import ArmOutcome
from promptpotter.domain.run_records import (
    RECORD_ADAPTER,
    CandidateScoredRecord,
    ConfigOverrides,
    CycleSeed,
    CycleSeedRecord,
    ForkRemainder,
    ForkSpec,
    ForkTrigger,
    RunLimitsRecord,
    RunPhaseRecord,
    SpendHoldRecord,
    SpendTombstoneRecord,
    TokenUsageRecord,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import GradedCell, MeasuredCell, PipelineData, Scorer
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.domain.spend import (
    MeteredSpend,
    SpendCeilings,
    SpendRollup,
    StepUsage,
    TokenAccount,
)
from promptpotter.domain.validators import StopSignal
from promptpotter.infrastructure import backend as backend_mod
from promptpotter.infrastructure.backend import CELL, BackendClient
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm import base, heartbeat, pricing, telemetry
from promptpotter.infrastructure.llm.anthropic import AnthropicClient
from promptpotter.infrastructure.llm.litellm_sends import ReportedCost
from promptpotter.infrastructure.llm.openai_compat import (
    OpenAICompatibleClient,
    ProviderSpec,
    _billed_cost,
    reply_cost,
)
from promptpotter.infrastructure.llm.pricing import PriceTier, Rate, RateCeiling, RateTable
from promptpotter.infrastructure.llm.request import ChatRequest
from promptpotter.infrastructure.llm.response import LLMResponse
from promptpotter.infrastructure.llm.send_pacing import SEND_ATTEMPTS, SendBudget, under_budget
from promptpotter.infrastructure.llm.spend_book import (
    Admission,
    Billed,
    CallLabel,
    SendBound,
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
    scan_ledger_cycle_seed,
    scan_ledger_priced_keys,
    scan_ledger_spend,
    scan_ledger_spend_by_round,
)
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.io import append_line, write_json, write_jsonl
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.llm_reuse_cache import LLMReuseCache
from promptpotter.infrastructure.store.read_model import LedgerSpan, iter_jsonl
from promptpotter.infrastructure.store.session_pointer import cleanup_stub_fork_if_empty
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.infrastructure.store.user_store import User
from promptpotter.judges import call as judge_call
from promptpotter.judges.protocol import JudgeSpec, JudgeStage
from promptpotter.judges.registry import build_evaluators
from promptpotter.shared.errors import (
    CellHaltedError,
    CellInfrastructureError,
    CellSendRefusedError,
    CellUnscoreableError,
    ConflictError,
    ErrorCategory,
    SendRefusedError,
)
from promptpotter.shared.identity import IdentityContext, Issuer, TenantId, UserId, default_identity
from promptpotter.shared.measurement_context import (
    MeasuredCandidate,
    measured_candidate,
    measured_candidate_context,
)


class _SpentWaits(selectors.DefaultSelector):
    """A selector that spends a wait on the clock instead of sitting through it."""

    now = 0.0

    def select(self, timeout: float | None = None) -> Any:
        if timeout is None:  # nothing scheduled: only real I/O can wake the loop
            return super().select(None)
        self.now += max(timeout, 0.0)
        return super().select(0)


class _JumpingClockLoop(asyncio.SelectorEventLoop):
    """Its clock jumps to the next timer: sleeps keep their ORDER and cost no wall clock."""

    def __init__(self) -> None:
        self._waits = _SpentWaits()
        super().__init__(self._waits)

    def time(self) -> float:
        return self._waits.now


def _on_jumping_clock[T](main: Coroutine[Any, Any, T]) -> T:
    with asyncio.Runner(loop_factory=_JumpingClockLoop) as runner:
        return runner.run(main)


def on_jumping_clock[**P, T](test: Callable[P, Coroutine[Any, Any, T]]) -> Callable[P, T]:
    @functools.wraps(test)
    def run(*args: P.args, **kwargs: P.kwargs) -> T:
        return _on_jumping_clock(test(*args, **kwargs))

    return run


@pytest.fixture
def run_bound(tmp_path: Path) -> Iterator[None]:
    ledger = telemetry.set_cycle_ledger(CycleEventLog(tmp_path / "bound.jsonl"))
    round_num = telemetry.set_current_round(1)
    yield
    telemetry.reset_current_round(round_num)
    telemetry.reset_cycle_ledger(ledger)


def _reply(model: str, content: str, usage: dict[str, Any] | None) -> ChatCompletion:
    return ChatCompletion.model_validate(
        {
            "id": "c",
            "object": "chat.completion",
            "created": 0,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
            **({"usage": usage} if usage is not None else {}),
        }
    )


def _observing(*bills: TokenUsageRecord) -> Any:
    spend = SpendRollup()
    for bill in bills:
        spend.bank(bill)
    return types.SimpleNamespace(
        dashboard=types.SimpleNamespace(
            spend_metered=lambda meters: MeteredSpend.of(spend, meters)
        ),
        arm_spend_book=lambda _book: None,
    )


# 1. What a call costs


def _counting_client(reply: str) -> tuple[Any, list[int]]:
    calls: list[int] = []

    async def create(**_kw: Any) -> Any:
        calls.append(1)
        # A stub reporting no `cached_tokens` cannot catch the metering dropping the discount.
        usage = {
            "prompt_tokens": 11,
            "completion_tokens": 1,
            "total_tokens": 12,
            "prompt_tokens_details": {"cached_tokens": 8},
        }
        completion = _reply("grader-1", reply, usage)
        return types.SimpleNamespace(headers={}, parse=lambda: completion)

    client = OpenAICompatibleClient(api_key="k", provider="p", spec=ProviderSpec("P", ""))
    client._client = types.SimpleNamespace(  # type: ignore[assignment]
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(
                with_raw_response=types.SimpleNamespace(create=create)
            )
        )
    )
    return client, calls


async def _grade_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, reply: str
) -> tuple[list[int], list[Any], Any]:
    client, calls = _counting_client(reply)
    monkeypatch.setattr(judge_call, "get_llm_client", lambda _p: client)
    metered: list[Any] = []
    monkeypatch.setattr(judge_call, "emit_token_usage", lambda **kw: metered.append(kw))
    monkeypatch.setattr(spending, "emit_token_usage", lambda **kw: metered.append(kw))

    cache = LLMReuseCache(tmp_path, "judge_reuse")
    (ev,) = build_evaluators(
        {"answer": JudgeSpec(name="sealqa", stages=[JudgeStage(model="grader-1", provider="p")])},
        cache=cache,
    )
    with spending_under(unbounded_spend_book()):
        for _ in range(2):
            row = MeasuredCell(sample_id=0, query="who?", predicted="Ada", ground_truth="Ada")
            last = await ev.compute(result=row, schema=None)
    return calls, metered, last


class _Reply(BaseModel):
    answer: str = Field(description="The answer.")


class _SentError(Exception):
    pass


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


async def test_a_call_is_priced_on_its_route_sent_as_asked_and_billed_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_bound: None
) -> None:
    def row(input_usd: float, output_usd: float, lister: str, **tiers: float) -> dict[str, Any]:
        return {
            "input_cost_per_token": input_usd,
            "output_cost_per_token": output_usd,
            "litellm_provider": lister,
            **tiers,
        }

    table = pricing._models_to_rates(
        {
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
    )
    with monkeypatch.context() as patched:
        patched.setattr(pricing, "load_rates", lambda: table)
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
        assert (cold, hit) == (
            pytest.approx(1000 * 0.0000025),
            pytest.approx(200 * 0.0000025 + 800 * 0.00000025),
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
    assert reply_cost(_reply("m", "{}", priced)) == 7.938e-05, (
        "wire cost never reached the response, so nothing prices the optimizer"
    )
    for usage in ({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}, None):
        assert reply_cost(_reply("m", "{}", usage)) is None, (
            f"{usage}: None, not 0.0 — 0.0 is a measurement, and satisfies the cap that should "
            "escalate to the table"
        )
    for reported, billed in (
        ((1e-05, 2e-05), 3e-05),
        ((None, 2e-05), 2e-05),
        ((1e-05, None), 1e-05),
        ((None, None), None),
    ):
        assert _billed_cost(*reported) == (None if billed is None else pytest.approx(billed)), (
            f"{reported}: a bill is the sum of the parts that named one, absent only when none did"
        )

    # What a client cannot send never reaches the provider, and the gateway wire holds: a banked
    # reply is keyed on node config, never the wire, so the gateway body is pinned whole.
    sent: list[dict[str, Any]] = []

    async def create(**params: Any) -> None:
        sent.append(params)
        raise _SentError

    async def ceiling(*_: Any, **__: Any) -> RateCeiling:
        return RateCeiling(tiers=(PriceTier(0, 0.5, 0.25),))

    async def send(client: Any, asked: dict[str, Any]) -> None:
        bind_spend_book(unbounded_spend_book())
        with under_budget(SendBudget(None, attempts=1)):
            await client.chat(
                ChatRequest(_MESSAGES, "m", **asked), label=CallLabel("l1", "optimizer")
            )

    raw = types.SimpleNamespace(with_raw_response=types.SimpleNamespace(create=create))
    gateway = {
        "model": "m",
        "messages": _MESSAGES,
        "temperature": 0.0,
        "extra_body": {"usage": {"include": True}, "provider": _MAX_PRICE},
    }
    every_field = {
        "model": "m",
        "messages": _MESSAGES,
        "temperature": 0.3,
        "max_tokens": 64,
        "seed": 7,
        "top_p": 0.9,
        "extra_body": {
            "usage": {"include": True},
            "provider": {**_MAX_PRICE, "order": ["Alibaba"], "allow_fallbacks": True},
            "reasoning": {"effort": "low"},
        },
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "_Reply",
                "schema": {
                    "properties": {"answer": {"description": "The answer.", "type": "string"}},
                    "required": ["answer"],
                    "type": "object",
                },
                "strict": False,
            },
        },
    }
    client: Any
    with monkeypatch.context() as patched:
        patched.setattr(base, "rate_ceiling", ceiling)
        for host, asked, wire in (
            ("anthropic", {"seed": 7}, None),
            ("anthropic", {"reasoning_effort": "low"}, None),
            ("anthropic", {"route_order": ["Alibaba"]}, None),
            ("groq", {"route_order": ["Alibaba"]}, None),
            ("openrouter", {}, gateway),
            ("openrouter", _EVERY_FIELD, every_field),
        ):
            sent.clear()
            if host == "anthropic":
                client = AnthropicClient(api_key="k")
                client._client = types.SimpleNamespace(messages=raw)
            else:
                spec = ProviderSpec(host.title(), "", gateway=host == "openrouter")
                client = OpenAICompatibleClient(api_key="k", provider=host, spec=spec)
                client._client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=raw))
            with pytest.raises(_SentError if wire else ValueError):
                await asyncio.create_task(send(client, asked))
            assert [json.dumps(body) for body in sent] == ([json.dumps(wire)] if wire else []), (
                f"{host} {sorted(asked)}: a field its client cannot send reached the provider, "
                "or the gateway body a banked reply stands for moved"
            )

    # A cell that ran without a grade still bills what it spent; a replay states its row's price.
    async def ran_ungraded(*_args: Any) -> dict[str, Any]:
        spent = {"agent": {"input": 1200, "output": 300, "estimated": False, "cost_usd": 0.0076}}
        raise CellUnscoreableError("verifier timed out", spent=spent)

    cells = CycleEventLog(tmp_path / "cells.jsonl")
    session = types.SimpleNamespace(
        pipeline_schema=pipeline_schema([]),
        identity_keys={},
        state=types.SimpleNamespace(ledger=None),
        backend_client=backend_client(execution="in_process", in_process_run=ran_ungraded),
        scoring=types.SimpleNamespace(
            scorer=compile_scorer("max(0.0, min(1.0, env_reward))", verifier_graded=True)
        ),
    )
    token = telemetry.set_cycle_ledger(cells)
    try:
        with spending_under(unbounded_spend_book()):
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
    with monkeypatch.context() as patched:
        calls, metered, score = await _grade_twice(tmp_path / "judge", patched, reply="A")
        blank_calls, _, blank = await _grade_twice(tmp_path / "blank", patched, reply="   ")
    assert score.score == 1.0, "the replayed reply must grade identically, not merely cheaply"
    assert len(calls) == 1, f"an identical comparison re-billed the provider: {len(calls)}x"
    assert [m.get("cached", False) for m in metered] == [False, True], (
        "a served grading went unmetered"
    )
    assert {m["kind"] for m in metered} == {"judge"}, "grading spend landed outside its own bucket"
    # `cached`: OUR cache served the reply. `cache_read`: the PROVIDER discounted a sent call.
    wire_usage = metered[0]["usage"]
    assert (wire_usage.cache_read, wire_usage.input) == (8, 11), (
        "cache_read is a SUBSET of input, never a deduction from it"
    )
    assert blank.score is None, "an unreadable grading is an absent verdict, never a zero"
    assert len(blank_calls) == 2, "an empty reply was cached and replayed as if it were a verdict"


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

    async def measure(self, sample: Sample, session: Any, *, pipeline_params: Any = None) -> Any:
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
            sample_id=sample.id,
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

    def earliest_stop(self, results: list[Any], upcoming: list[Any]) -> int | None:
        m = max(self.n, len(results) + 1)
        return m if m <= len(results) + len(upcoming) else None

    def check(self, results: list[Any]) -> Any:
        if len(results) < self.n:
            return None
        return StopSignal(self.name, ArmOutcome.ELIMINATED, {"queries_scored": len(results)})


def _row_scoring() -> Any:
    scorer = Scorer(id="hit", per_cell=None, fitness=lambda _row: 1.0, objective=None)
    return types.SimpleNamespace(scorer=scorer, require_scorer=lambda: scorer)


def _searching(usd_per_query: float) -> Any:
    bound = WebSpendBound(kind="web", queries=1, usd_per_query=usd_per_query)
    return pipeline_schema([PipelineNode(name="search", tunes_llm=False, spend_bound=bound)])


def _walk_over(
    dataset: list[Sample],
    session: Any,
    *,
    checks: list[Any],
    measured: Any = None,
    slot: Any = None,
    on_taken: Any = None,
    cached: dict[int, Any] | None = None,
    banked: dict[int, Any] | None = None,
    rereads: list[int] | None = None,
) -> Any:
    class _Recorder:
        def scores(self, cells: Any) -> Any:
            return INVALID_SCORES.model_copy(update={"accuracy": 1.0})

        def take(self, cell: GradedCell) -> GradedCell:
            if on_taken and not cell.facts.cached:
                on_taken(cell)
            return cell

        def bank(self, cells: Any) -> None:
            if banked is not None:
                banked.update({cell.sample_id: cell.facts for cell in cells})

        def close(self, cells: Any, scores: Any) -> None:
            return None

    ctx = query_loop.QueryLoopState(
        search_point=JobSearchPoint(),
        session=session,
        cached_sample_results=dict(cached or {}),
        slot=slot,
        role="panel",
        sample_index=None,
        deprecated_samples={},
        recorder=_Recorder(),
        claim_cell=None,
        cell_keys={s.id: f"cell_{s.id}" for s in dataset},
        counted={f"cell_{sid}" for sid in rereads or ()},
        rereads=frozenset(rereads or ()),
    )
    return query_loop.Walk(dataset, ctx, checks, measured_candidate_context(measured))


def _control_pausing(pressed: Callable[[], bool], **bound: Any) -> RunControl:
    class _Pressed(RunControl):
        def pause_requested(self) -> bool:
            return pressed()

    return _Pressed(**bound)


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
    dataset: list[Sample],
    *,
    armed: int,
    cut_at: int | None,
    max_cells: int = 2,
    parent_lacks_cells: bool = False,
    backfill_pace: float = 0.01,
    pause_after_call: int | None = None,
    cached: dict[int, Any] | None = None,
    stall: int | None = None,
    skip_at: int | None = None,
    cap_usd: float | None = None,
) -> dict[str, Any]:
    backend = _OrderedFakeBackend(len(dataset))
    banked: dict[int, Any] = {}
    depths: list[int] = []
    committed: list[int] = []
    returned: list[int] = []
    book = spend_book(cap_usd)
    dollar = SendBound(input_tokens=0, output_tokens=0, usd=1.0)

    async def _measure(sample: Sample, session: Any, *, pipeline_params: Any = None) -> Any:
        with admitted(CallLabel("cell", "backend"), dollar, model=None, provider=None) as bill:
            if sample.id == stall:
                await asyncio.sleep(0.3)
            row = await backend.measure(sample, session, pipeline_params=pipeline_params)
            returned.append(sample.id)
            bill.settle(Billed(TokenAccount(), 1.0))
        return row

    def _backfill(sp: Any, sample: Sample, prior_id: str) -> Any:
        call = asyncio.ensure_future(backend.hold(sample, backfill_pace))

        def _commit() -> list[Any]:
            committed.append(sample.id)
            return [_row_scoring().scorer.grade(call.result())]

        return call, _commit, call.cancel

    # A parent measured on none of the round's cells, so every cell owes it one catch-up call.
    priors = CatchUpPool(
        PoBBCheck(pobb_knobs(), n_min=6, n_samples=len(dataset), ruler=None),
        _backfill,
    )
    priors.admit("parent", [], JobSearchPoint())

    def _started(*, sample_lookahead: int, **_record: Any) -> None:
        depths.append(sample_lookahead)

    with (
        mock.patch.object(query_loop, "measure_sample", _measure),
        mock.patch.object(query_loop, "emit_sample_started", _started),
        spending_under(book),
    ):
        session = types.SimpleNamespace(
            scoring=_row_scoring(),
            state=types.SimpleNamespace(ledger=None),
            control=_control_pausing(
                lambda: pause_after_call is not None and len(returned) >= pause_after_call,
                held_lookahead=armed,
                book=book,
            ),
            backend_client=backend_client(max_cells_in_flight=max_cells),
            # One node the backend bounds at the cell's dollar, so the scheduler counts in them.
            pipeline_schema=_searching(1.0),
            flight=None,
        )
        walk = _walk_over(
            dataset,
            session,
            checks=[_CutAfter(cut_at)] if cut_at else [],
            slot=query_loop.ArmSlot(0, 1, "arm"),
            cached=cached,
            banked=banked,
        )
        walk.skip_at = skip_at
        stopped = await _stopped_by_operator(
            query_loop.run_walks(
                [walk],
                session,
                keep_cut=False,
                backfills=priors if parent_lacks_cells else None,
            )
        )
    return {
        "rows": list(walk.results),
        "stop_reason": stopped or walk.outcome.ended_on,
        "calls": list(backend.calls),
        "entries": list(backend.entries),
        "committed": committed,
        "banked": banked,
        "priced": set(walk.ctx.counted),
        "returned": returned,
        "billed": book.usd_metered,
        "max_depth": max(depths) if depths else 0,
    }


async def _round(
    dataset: list[Sample],
    *,
    armed: int,
    cuts: list[int | None],
    slowest_last: bool = False,
    pause_after_call: int | None = None,
) -> dict[str, Any]:
    backend = _OrderedFakeBackend(len(dataset), slowest_last=slowest_last)
    events: list[tuple[str, int, int]] = []
    flag = {"pause": False}

    async def _measure(sample: Sample, session: Any, *, pipeline_params: Any = None) -> Any:
        call = len(backend.calls) + 1
        events.append(("call", cast("MeasuredCandidate", measured_candidate()).idx, sample.id))
        row = await backend.measure(sample, session, pipeline_params=pipeline_params)
        if call == pause_after_call:
            flag["pause"] = True
        return row

    def _taken(walk: int, cell: GradedCell) -> None:
        events.append(("absorb", walk, cell.sample_id))

    session = types.SimpleNamespace(
        scoring=_row_scoring(),
        state=types.SimpleNamespace(ledger=None),
        control=_control_pausing(lambda: flag["pause"], held_lookahead=armed),
        backend_client=backend_client(max_cells_in_flight=armed, holds_own_sends=True),
        flight=None,
    )
    walks = [
        _walk_over(
            dataset,
            session,
            checks=[_CutAfter(cut)] if cut else [],
            measured=MeasuredCandidate(idx=w, candidate_id=f"c{w}", label=f"C1.{w + 1}"),
            on_taken=functools.partial(_taken, w),
        )
        for w, cut in enumerate(cuts)
    ]
    with mock.patch.object(query_loop, "measure_sample", _measure):
        stopped = await _stopped_by_operator(query_loop.run_walks(walks, session, keep_cut=False))
    return {
        "rows": [list(walk.results) for walk in walks],
        "stops": [stopped or walk.outcome.ended_on for walk in walks],
        "absorbed": [(w, sid) for kind, w, sid in events if kind == "absorb"],
        "events": events,
        "calls": list(backend.calls),
        "peak": max(backend.entries),
    }


@on_jumping_clock
async def test_a_run_bills_each_cell_once_whatever_its_process_lookahead_or_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, built_stores: Stores, run_bound: None
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
    stores = build_stores(
        signup,
        projects_root=built_stores.projects_root,
        benchmarks_root=built_stores.benchmarks_root,
    )
    held = HeldLimits(
        halt_at_accuracy=0.9,
        ceiling=SpendCeilings(0.25, 40_000),
        operator=SpendCeilings(0.25, None),
        reserve=SpendCeilings(0.5, 80_000),
        step_rounds=2,
    )
    hop = CycleHop(campaign_id="ds__000001", cycle_id="cycle_root")
    mode = RunMode(from_round=2, diag=True)
    launched = HeldRun(
        job_registry=JobRegistry(tmp_path / "jobs", capacity=lambda _live: 1),
        job_id="job-a",
        session=types.SimpleNamespace(store=stores, hop=hop),
        campaign_config=None,
        limits=held,
    )
    spec = JobSpec.model_validate_json(launched.job_spec(mode).model_dump_json())
    assert (spec.identity, spec.hop, spec.mode) == (signup, hop, mode), (
        "a run in its own process spent as another account, cycle or mode than launched it"
    )
    assert spec.limits == held, (
        "the round allowance crosses with the limits: dropped, a step runs to the campaign's end"
    )
    book = _arm_spend_book(
        _observing(),
        None,
        declared=spec.limits.ceiling,
        meters="bill",
        reserve=spec.limits.reserve,
    )
    assert (book.ceiling, book.reserve) == (held.ceiling, held.reserve), (
        "the book the spawned run armed is not the ceiling its launch held"
    )
    rebuilt = build_stores(
        spec.identity,
        projects_root=spec.projects_root,
        benchmarks_root=spec.benchmarks_root,
        shared_root=spec.shared_root,
    )
    assert spends_the_hosts_own_key(rebuilt) is spends_the_hosts_own_key(stores) is False, (
        "a signup's run, rebuilt in its own process, spent the host's own key"
    )
    assert rebuilt.base_dir == stores.base_dir, "the spawned run billed another tenant's tree"

    # The look-ahead changes the bill and never the record.
    dataset = [Sample(id=i, query=f"q{i}", ground_truth=str(i % 2)) for i in range(1, 9)]
    d1 = await _walk(dataset, armed=1, cut_at=None)
    d2 = await _walk(dataset, armed=2, cut_at=None)
    assert (d1["max_depth"], d2["max_depth"]) == (1, 2), (
        "arming did not open the window — the rest proves nothing"
    )
    assert d1["rows"] == d2["rows"] and d1["calls"] == d2["calls"], "a window moved the record"
    assert [r.sample_id for r in d2["rows"]] == [s.id for s in dataset], "a window reordered rows"
    c1 = await _walk(dataset, armed=1, cut_at=4)
    c2 = await _walk(dataset, armed=2, cut_at=4)
    assert c2["max_depth"] == 2, "window never opened on the cut walk"
    assert c1["stop_reason"] == c2["stop_reason"] == "stop_rule", "a window moved the cut"
    assert c1["rows"] == c2["rows"], "a window moved what a cut walk recorded"
    assert len(c1["calls"]) == 4, "an unarmed walk launched before the cell ahead was decided"
    # AT MOST one extra call: equality would pin a scheduling accident, two an overgrown window.
    assert 0 <= len(c2["calls"]) - len(c1["calls"]) <= 1, "a window bought more than one call"
    for cells in (1, 2):
        wide = await _walk(dataset, armed=4, cut_at=None, max_cells=cells)
        assert wide["max_depth"] == cells, "the window outgrew the cells its backend holds"
    deep = await _walk(dataset, armed=8, cut_at=4, max_cells=8)
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
    lone = await _walk(dataset, armed=1, cut_at=4, parent_lacks_cells=True)
    wide = await _walk(dataset, armed=3, cut_at=4, max_cells=3, parent_lacks_cells=True)
    # Catch-up far faster than a cell, so the one started past the cut is back before the cut.
    quick = await _walk(
        dataset, armed=8, cut_at=4, max_cells=8, parent_lacks_cells=True, backfill_pace=0.0001
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
    serial = await _round(dataset, armed=1, cuts=[4, None, 6])
    fanned = await _round(dataset, armed=4, cuts=[4, None, 6])
    assert serial["peak"] == 1 and fanned["peak"] <= 4, "the depth did not bound the round"
    assert fanned["rows"] == serial["rows"], "measuring walks at once moved a record"
    assert fanned["stops"] == serial["stops"] == ["stop_rule", None, "stop_rule"], (
        "measuring walks at once moved a cut"
    )
    assert fanned["absorbed"] == serial["absorbed"], "walks decided out of turn"
    # First cells fastest: the second walk's calls go out while the first still has cells to take.
    ahead = await _round(dataset, armed=4, cuts=[None, None], slowest_last=True)
    assert ahead["absorbed"] == [(w, s.id) for w in (0, 1) for s in dataset], (
        "a later candidate's cell was absorbed before an earlier one's"
    )
    last_of_first = max(i for i, e in enumerate(ahead["events"]) if e[:2] == ("absorb", 0))
    assert any(e[:2] == ("call", 1) for e in ahead["events"][:last_of_first]), (
        "no later candidate measured ahead — the identity check below proves nothing"
    )
    for w in (0, 1):
        calls = sorted(sid for kind, who, sid in ahead["events"] if kind == "call" and who == w)
        assert calls == [s.id for s in dataset], f"walk {w}'s calls ran under another identity"
    paused = await _round(dataset, armed=1, cuts=[None], pause_after_call=3)
    assert paused["stops"] == ["graceful"], "a pause read as another stop"
    assert len(paused["rows"][0]) == len(paused["calls"]) == 3, (
        "the call that returned as the pause was pressed is absorbed, not paid again on resume"
    )
    # A pause starts nothing: an unstarted catch-up stays so, one already out launches no cell.
    for armed_, pace, after, taken in ((1, 0.01, 2, 2), (2, 0.02, 1, 1)):
        owing = await _walk(
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
        dataset, armed=4, max_cells=4, cut_at=None, pause_after_call=3, stall=head
    )
    assert stopped["rows"] == [] and set(stopped["banked"]) == set(stopped["returned"]), (
        "what came back behind a head still out was not banked"
    )
    assert stopped["priced"] == {f"cell_{sid}" for sid in stopped["banked"]}, (
        "a banked cell's price was not recorded against its key"
    )
    resumed = await _walk(dataset, armed=4, max_cells=4, cut_at=None, cached=stopped["banked"])
    assert not set(resumed["calls"]) & set(stopped["banked"]), "a banked cell was paid again"
    assert [r.sample_id for r in resumed["rows"]] == [s.id for s in dataset], (
        "the resumed walk did not replay what it banked"
    )
    capped = await _walk(dataset, armed=4, max_cells=4, cut_at=2, pause_after_call=2, stall=head)
    assert set(capped["banked"]) == {head, dataset[1].id}, (
        "a cell past where a rule could cut was banked: the serial walk would not have measured it"
    )
    ceiling = await _walk(dataset, armed=4, max_cells=4, cut_at=None, cap_usd=5.0)
    assert (ceiling["stop_reason"], ceiling["billed"]) == ("spend_budget", 5.0), (
        "a spend ceiling binds BEFORE a call: every cell out is counted at its bound"
    )
    # A skip made before a stop outlives it: the LAST decision on record per candidate replays.
    skips = CycleEventLog(tmp_path / "skips.jsonl")
    for cid, n, reason in (
        ("a", 3, "skipped"),
        ("b", 2, "skipped"),
        ("b", 8, "measured"),
        ("c", 4, "skipped"),
    ):
        scores = scored_candidate(cid, scored_samples=n, outcome=reason)
        skips.append(
            CandidateScoredRecord(round=2, candidate_idx=0, candidate_total=3, scores=scores)
        )
    assert _skips_on_record(skips, 2) == {"a": 3, "c": 4}, "a superseded skip replayed"
    replayed = await _walk(dataset, armed=4, max_cells=4, cut_at=None, skip_at=3)
    assert replayed["stop_reason"] == "skip" and len(replayed["rows"]) == 3, "a skip did not hold"
    assert sorted(replayed["calls"]) == [s.id for s in dataset[:3]], "a skipped cell was paid"

    # A cell's envelope cancels the inner campaign it spawned, so nothing spends past it.
    monkeypatch.setattr(heartbeat, "HEARTBEAT_INTERVAL_S", 0.01)
    monkeypatch.setattr(spawn, "OUTER_SAMPLE_WALL_S_PER_ROUND", 0.02)
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def _hanging_inner(
        cell: Any, cycle_dir_box: dict[str, Path], spawned_by: dict[str, Any]
    ) -> Any:
        """SWALLOWS the cancellation and returns normally: a stub that re-raises cannot catch that."""
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
        return cycle_result([], 0.0, [])

    monkeypatch.setattr(spawn, "_run_inner_campaign", _hanging_inner)
    inner = tmp_path / "inner"
    inner.mkdir()
    # Loaded through the real validator: the run resolves its panel ONCE and carries it.
    write_json(
        inner / "inner_tasks.yaml",
        {
            "inner_benchmark": "justlogic-d234",
            "inner_benchmark_config": {
                "n_samples_per_inner_round": 24,
                "max_inner_rounds": 7,
            },
            "tasks": [{"id": "justlogic-d234/seed-0", "inner_dataset_seed": 0}],
        },
    )
    spawning = spawn_context._INNER_SPAWN.set(
        spawn_context.InnerSpawnContext(
            inner_sandbox_root=inner,
            dataset_config_dir=inner,
            identity=default_identity(),
            shared_root=inner,
            spawn_campaign_id="ppself__aaaaaa",
            spawn_cycle_id="cycle_deadbeef0000",
            enclosing=(),
            # No inner dataset resolved: the stubbed inner run never reads one.
            cells=InnerCells(
                panel=load_inner_tasks(inner / "inner_tasks.yaml"), by_dataset={}, treatment="o"
            ),
        )
    )
    token = telemetry.set_cycle_ledger(CycleEventLog(inner / "outer.jsonl"))
    query = "justlogic-d234/seed-0"
    cell = Sample(
        id=0, query=query, ground_truth=None, source_pin={"id": query, "inner_dataset_seed": 0}
    )
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
    class _Provider:
        async def chat(self, *_a: Any, **_kw: Any) -> LLMResponse:
            return LLMResponse(content="ok", model="m", cost_usd=0.01)

    monkeypatch.setattr(call_mod, "get_llm_client", lambda _p: _Provider())
    monkeypatch.setattr(judge_call, "get_llm_client", lambda _p: _Provider())
    loop_reuse = LLMReuseCache(tmp_path / "reuse", "optimizer_reuse")
    judge_reuse = LLMReuseCache(tmp_path / "reuse", "judge_reuse")

    async def round_calls() -> None:
        await call_mod.llm_call(
            [{"role": "user", "content": "q"}],
            config={"provider": "p", "model": "m"},
            context=call_mod.LLMCallContext(cache=loop_reuse),
        )
        with judge_call.bind_cache(judge_reuse):
            await judge_call.ask(JudgeStage(model="m", provider="p"), "grade", judge="j")

    # Another campaign's run banks both replies; this arm only ever replays them.
    telemetry.bind_priced(set())
    other = telemetry.set_cycle_ledger(CycleEventLog(tmp_path / "other.jsonl"))
    await round_calls()
    telemetry.reset_cycle_ledger(other)

    reread = [Sample(id=i, query=f"q{i}", ground_truth="a") for i in range(4)]
    step = {"solve": StepUsage(input=10, output=5, cost_usd=0.01)}
    banked = {
        s.id: MeasuredCell(
            sample_id=s.id,
            query=s.query,
            ground_truth="a",
            predicted="a",
            pipeline=PipelineData(step_tokens=step),
        )
        for s in reread
    }
    # The ceiling already spent: no cell fits, and the control reads it as reached.
    arm = spend_book(0.02, meters="search_incurred", usd_metered=0.02)
    ledger = CycleEventLog.open(CycleDir(tmp_path / "cycle"))
    ledger.bind(arm)
    session = types.SimpleNamespace(
        scoring=_row_scoring(),
        state=types.SimpleNamespace(ledger=None),
        control=RunControl(book=arm),
        backend_client=backend_client(max_cells_in_flight=1),
        pipeline_schema=_searching(0.01),
        flight=None,
    )
    taken = [s.id for s in reread[:3]]
    walk = _walk_over(reread, session, checks=[], cached=banked, rereads=taken)
    bench = _walk_over(reread, session, checks=[], cached=banked)
    token = telemetry.set_cycle_ledger(ledger)
    try:
        with spending_under(arm):
            halted = await _stopped_by_operator(
                query_loop.run_walks([walk], session, keep_cut=False)
            )
            with telemetry.filed_as("bench"):
                await query_loop.run_walks([bench], session, keep_cut=False)
            # The stopped launch made the round's calls; the resumed one learns them off the ledger.
            telemetry.bind_priced(set())
            await round_calls()
            telemetry.bind_priced(scan_ledger_priced_keys([ledger.path]))
            await round_calls()
    finally:
        telemetry.reset_cycle_ledger(token)
    assert [c.sample_id for c in walk.results] == taken, "the ceiling held back a re-read"
    assert halted == "spend_budget", "a spent ceiling did not stop the arm"
    assert len(bench.results) == len(reread), "the search's ceiling stopped the bench pass"
    kinds = [r.kind for _, r in ledger.iter() if isinstance(r, TokenUsageRecord)]
    assert kinds == ["bench"] * len(reread) + ["optimizer", "judge"], (
        f"a re-read was metered a second time: {kinds}"
    )
    assert arm.usd_metered == pytest.approx(0.04), "the arm's book read another bill than metered"
    priced = scan_ledger_priced_keys([ledger.path])
    assert {f"cell_{s.id}" for s in reread} <= priced and len(priced) == len(reread) + 2, (
        "the ledger names another set of priced calls than the arm paid for"
    )

    # A ledger index serves the file as it stands: grown, compacted and raced.
    index = CycleEventLog.open(CycleDir(tmp_path / "index"))

    def bill(n: int, round: int | None = None) -> None:
        for _ in range(n):
            index.append(
                TokenUsageRecord(
                    kind="backend",
                    node="llm_only",
                    model="openai/gpt-oss-20b",
                    provider="openrouter",
                    input_tokens=1,
                    output_tokens=1,
                    duration_s=1.0,
                    cost_usd=1.0,
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
        return spend.total_used_usd, calls, scan_ledger_cycle_seed(index.path), charted

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

    readers = [threading.Thread(target=read) for _ in range(4)]
    for reader in readers:
        reader.start()
    bill(40)
    seed("third")
    written.set()
    for reader in readers:
        reader.join()
    assert all(usd == calls for usd, calls in seen), (
        f"a reader racing the writer saw a torn bill: {[s for s in seen if s[0] != s[1]][:3]}"
    )
    assert tailed() == cold() and cold()[1] == 2 + 40, "the index lost a row written under a race"


# 3. Ceilings


def _priced_wire(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    async def no_wait(*_: Any) -> None:
        return None

    monkeypatch.setattr(base, "held_wait", no_wait)
    monkeypatch.setattr(backend_mod, "held_wait", no_wait)
    rates = RateTable({("openai", "gpt-x"): Rate(1e-6, 2e-6)})
    monkeypatch.setattr(pricing, "load_rates", lambda: rates)
    rng = random.Random(7)
    request = httpx.Request("POST", "https://x")
    flaky, hang = [False], [False]

    async def create(**params: Any) -> Any:
        await asyncio.sleep(rng.random() * 0.02)
        if flaky[0] and rng.random() < 0.2:
            raise openai.APITimeoutError(request=request)
        if hang[0]:
            await asyncio.Event().wait()
        prompt = rng.randint(1, len(params["messages"][0]["content"]))
        completion = rng.randint(0, params["max_tokens"])
        usage = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        }
        reply = _reply("gpt-x", "ok", usage)
        return types.SimpleNamespace(headers={}, parse=lambda: reply)

    client = OpenAICompatibleClient(api_key="k", provider="openai", spec=ProviderSpec("OpenAI", ""))
    raw = types.SimpleNamespace(create=create)
    client._client = types.SimpleNamespace(  # type: ignore[assignment]
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(with_raw_response=raw))
    )
    return types.SimpleNamespace(
        client=client,
        raw=raw,
        create=create,
        flaky=flaky,
        hang=hang,
        rng=rng,
        request=request,
        no_wait=no_wait,
    )


async def _owned_chat(
    client: Any, node: str, content: str = "x", *, kind: Any = "optimizer", max_tokens: int = 10
) -> Any:
    with under_budget(SendBudget(None, attempts=SEND_ATTEMPTS)):
        return await client.chat(
            ChatRequest([{"role": "user", "content": content}], "gpt-x", max_tokens=max_tokens),
            label=CallLabel(node, kind),
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


def test_a_runs_book_holds_each_send_at_its_bound_and_stops_the_run_at_its_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, built_stores: Stores, run_bound: None
) -> None:
    """A record of spend is only ever a BILL: an unreported send binds from its open hold."""
    # The declared ceiling lives on the ledger alone, so a launch's flag sweep cannot take it.
    campaigns = CampaignStore(WorkspaceDir(tmp_path / "ws"))
    hop = CycleHop(campaign_id="camp", cycle_id="cyc")
    campaigns.mint_cycle(hop)
    cycle_dir = campaigns.cycle_dir(hop)

    def declare(ceiling: SpendCeilings, rounds: RoundsCap | None = None) -> None:
        campaigns.write_run_limits(
            hop, ceiling, rounds=rounds, pause_at_round=None, reserve=SpendCeilings()
        )

    spent = TokenUsageRecord(
        kind="optimizer", node="l1_generate", input_tokens=9_000, output_tokens=0, cost_usd=1.0
    )
    control = RunControl(
        book=_arm_spend_book(
            _observing(spent),
            cycle_dir,
            declared=SpendCeilings(),
            meters="bill",
            reserve=SpendCeilings(),
        )
    )
    assert control.budget_tripped() is None, (
        "a run that declared NOTHING still holds a book, which stays silent until a ceiling exists"
    )
    declare(SpendCeilings(0.50, None))
    assert control.budget_tripped() == StopReason.SPEND_BUDGET, "a mid-run ceiling reached no book"
    CycleEventLog.open(CycleDir(cycle_dir)).append(RunPhaseRecord(run_phase=RunPhase.RUNNING))
    assert control.budget_tripped() == StopReason.SPEND_BUDGET, "a launch swept the ceiling"
    declare(SpendCeilings(None, 5_000))
    assert control.budget_tripped() == StopReason.TOKEN_BUDGET, "a token ceiling bound nothing"
    session = types.SimpleNamespace(
        state=types.SimpleNamespace(cycle_id="cyc"),
        hop=hop,
        store=types.SimpleNamespace(campaigns=campaigns),
    )
    config = CampaignConfig.model_validate(
        {"optimization": {"degradation_threshold": 0.05, "max_rounds": 50}}
    )
    declare(SpendCeilings(), RoundsCap(max_rounds=3))
    assert _round_bounds(session, config) == (3, None), "a mid-run round cap reached no loop"
    assert control.budget_tripped() is None, "a lifted ceiling still governed the run"
    # Another PROCESS's ceiling lands as bytes no append in this one handed over, behind its own.
    append_line(
        CycleLayout(cycle_dir).ledger,
        RECORD_ADAPTER.dump_json(RunLimitsRecord(ceiling=SpendCeilings(0.50, None))).decode(),
    )
    assert control.budget_tripped() == StopReason.SPEND_BUDGET, (
        "a ceiling another process appended was hidden behind this process's own appends"
    )
    declare(SpendCeilings(), RoundsCap(max_rounds=None))
    assert _round_bounds(session, config) == (None, None), (
        "a LIFT of the round cap reads as no cap, never a fall back to the config's"
    )

    # Moving one ceiling leaves the other at its launch cap.
    moved = CycleHop(campaign_id="camp-4", cycle_id="cycle_budget0001")
    registry = JobRegistry(tmp_path / "jobs", capacity=lambda _live: 1)
    job = registry.request_slot(
        user_id="default", principal_id="default", dataset_name="ds1", hop=moved
    )
    registry.mark_admitted(job.job_id, SpendCeilings(0.30, 5_000_000))
    at_tokens = TokenUsageRecord(
        kind="optimizer", node="l1_generate", input_tokens=210_000, output_tokens=0, cost_usd=0.10
    )
    control = RunControl(
        book=_arm_spend_book(
            _observing(at_tokens),
            built_stores.campaigns.cycle_dir(moved),
            declared=SpendCeilings(0.30, 210_000),
            meters="bill",
            reserve=SpendCeilings(),
        )
    )
    assert control.budget_tripped() == StopReason.TOKEN_BUDGET, "the token ceiling bound nothing"
    asyncio.run(
        _apply_change_run_limits(
            CommandDispatcher(built_stores, registry), moved, SpendCeilings(0.50, None), None
        )
    )
    assert control.budget_tripped() == StopReason.TOKEN_BUDGET, (
        "a USD raise lifted the token ceiling"
    )

    # An offshoot is capped at what its parent has left; a cap the fork's seed names is the operator's.
    parent = SpendCeilings(2.0, 9_000)
    left = ForkRemainder.of(rounds_closed=3, max_rounds=5, metered_usd=0.75, ceiling=parent)
    for overrides, expected in (
        (ConfigOverrides(), (2, SpendCeilings(1.25, None))),
        (
            ConfigOverrides(max_rounds=9, ceiling=SpendCeilings(0.0, 500)),
            (9, SpendCeilings(0.0, 500)),
        ),
    ):
        under = left.under(overrides)
        assert (under.max_rounds, under.ceiling) == expected, (
            f"{overrides}: an offshoot ran past what its parent left, or over its operator's cap"
        )
    for remainder, expected_left in (
        (
            ForkRemainder.of(rounds_closed=7, max_rounds=5, metered_usd=3.0, ceiling=parent),
            (1, 0.0),
        ),
        (
            ForkRemainder.of(rounds_closed=1, max_rounds=None, metered_usd=None, ceiling=parent),
            (None, None),
        ),
    ):
        assert (remainder.max_rounds, remainder.ceiling.usd) == expected_left, (
            "a parent spent past its cap left its offshoot room, or an unread one invented a cap"
        )

    # The reserve holds a send's bound, and the ceiling what such sends bill.
    cell = CallLabel("cell", "backend")
    worst = SendBound(input_tokens=0, output_tokens=1000, usd=0.125)
    for reserve, depth in ((0.5, 4), (None, 16), (1.0, 8)):
        own = spend_book(0.5, usd_reserve=reserve)
        assert own.fits(worst, worst) == 4, (
            f"reserve {reserve}: nothing billed yet, the bound holds"
        )
        own.learn(cell, 0.03125, 100)
        own.learn(cell, 0.0, 0)
        held = own.held_at(cell, worst)
        assert own.fits(held, worst) == depth, (
            f"reserve {reserve}: held at its bound against both, a ceiling a few bounds wide "
            "walks one cell at a time"
        )
        for _ in range(depth):
            own.hold(held, worst, "backend", what="cell")
        with pytest.raises(SendRefusedError):
            own.hold(held, worst, "backend", what="cell")
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

    # A send no rate bounds is refused under a dollar ceiling and sent under none.
    unpriced = SendBound(input_tokens=100, output_tokens=100, usd=None, unpriced=("m (p)",))
    capped = spend_book(5.0, usd_reserve=None)
    with pytest.raises(SendRefusedError) as at_cell:
        capped.hold(unpriced, unpriced, "backend", what="the next cell")
    assert run_stop_reason(at_cell.value) is StopReason.NO_RATE, (
        "an unpriced send stopped as another"
    )
    assert capped.exhausted() is None, "no ceiling was reached: nothing was held or counted"
    unbounded_spend_book().hold(unpriced, unpriced, "backend", what="the next cell")

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

    with monkeypatch.context() as patched:
        patched.setattr(pricing.urllib.request, "urlopen", down)
        patched.setattr(pricing, "_ROUTE_OUTAGE", {})
        patched.setattr(pricing, "_ROUTE_MEMO", {})
        outage = asyncio.run(held_twice())
    assert len(fetches) == 1, f"{len(fetches)} fetches — one per send, each a full timeout"
    assert run_stop_reason(outage) is StopReason.PRICE_LIST_UNREACHABLE, "an outage read as no rate"

    # A controlled arm's ceiling is its search's incurred cost: a replay spends no BILL.
    def replay(usd: float) -> TokenUsageRecord:
        return TokenUsageRecord(
            kind="backend", node="n", input_tokens=10, output_tokens=5, cost_usd=usd, cached=True
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
    fresh.hold(pass_bound, pass_bound, "bench", what="bench pass")
    fresh.release(pass_bound, pass_bound, "bench")
    fresh.count(
        TokenUsageRecord(kind="bench", node="b", input_tokens=1, output_tokens=1, cost_usd=5.0)
    )
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
        fresh.hold(small, small, "optimizer", what="o")

    # No burst of sends records spend past its ceiling.
    wire = _priced_wire(monkeypatch)
    wire.flaky[0] = True
    rng = wire.rng
    burst_ledger = CycleEventLog(tmp_path / "burst.jsonl")
    ceiling_usd = 0.05
    book = spend_book(ceiling_usd)
    burst_ledger.bind(book)

    async def burst() -> list[Any]:
        async def one(i: int) -> Any:
            return await _owned_chat(
                wire.client, f"n{i}", "x" * rng.randint(10, 400), max_tokens=1500
            )

        tasks = [asyncio.ensure_future(one(i)) for i in range(40)]
        await asyncio.sleep(0.005)
        for task in tasks[::5]:
            task.cancel()
        return await asyncio.gather(*tasks, return_exceptions=True)

    with _billing_on(burst_ledger, book):
        outcomes = _on_jumping_clock(burst())
    wire.flaky[0] = False
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
    unreported = sum(
        isinstance(o, asyncio.CancelledError | openai.APITimeoutError) for o in outcomes
    )
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
    killed = {"kind": "optimizer", "input_tokens": 600, "output_tokens": 1500, "cost_usd": hold_usd}
    burst_ledger.append(SpendHoldRecord(hold_id="killed", node="killed", **killed))
    account = billed_spend([burst_ledger.path])
    assert account.used_usd == 0.0, "a price off our rate table was read as spent"
    assert account.rate_priced_usd == pytest.approx(recorded), "the rate-priced sum moved"
    assert account.unreported_usd == pytest.approx(left_out.usd + hold_usd), (
        "a killed send's hold was not counted as out"
    )
    assert unreported_on(burst_ledger).sends == unreported + 1, "a killed send's hold went unread"
    # A bill naming no price closes its hold in tokens alone; the money stays at the hold's bound.
    bare = CycleEventLog(tmp_path / "bare" / "ledger.jsonl")
    bare.append(SpendHoldRecord(hold_id="bare", node="bare", **killed))
    bare.append(
        TokenUsageRecord(
            kind="optimizer", node="bare", input_tokens=40, output_tokens=9, hold_id="bare"
        )
    )
    assert unreported_on(bare).usd == pytest.approx(hold_usd), "a priceless bill freed its hold"
    assert billed_spend([bare.path]).unreported_usd == pytest.approx(hold_usd), (
        "the account read a priceless bill as settling its hold"
    )

    # A nested run's send is held and billed on its root's ledger.
    root = CycleEventLog(tmp_path / "root.jsonl")
    book = spend_book(0.05)
    root.bind(book)
    book.ledger = root
    nested = CycleEventLog(tmp_path / "nested.jsonl")
    with _billing_on(nested, book):
        _on_jumping_clock(_owned_chat(wire.client, "inner", "nested"))
    assert [(r.node, r.mirrored) for r in _token_bills(root)] == [("inner:inner", False)], (
        "a nested send was not billed on its root's ledger"
    )
    assert [r.mirrored for r in _token_bills(nested)] == [True], "the nested copy bills twice"
    assert unreported_on(root).sends == 0, "a billed nested send stayed held"
    assert billed_spend([nested.path]).sent_usd == 0.0, "the nested ledger billed its mirror"
    assert 0.0 < book.usd_metered == pytest.approx(billed_spend([root.path]).sent_usd), (
        "the root's book read another bill than its ledger holds"
    )
    cut = SendBound(input_tokens=600, output_tokens=1500, usd=hold_usd)
    with _billing_on(nested, book):
        Admission(
            book, CallLabel("cut", "optimizer"), cut, cut, model="gpt-x", provider="openai"
        ).unreported()
    assert unreported_on(root).sends == 1, "a nested send cut short was held nowhere"
    # An inner campaign whose provider reports no bill is not a free cell: cost reads our rate.
    silent = SpendRollup()
    silent.bank(
        TokenUsageRecord(
            kind="optimizer", node="n", input_tokens=10, output_tokens=5, rate_priced_usd=0.02
        )
    )
    facts = inner_cell_facts(cycle_result([0.4], 0.3, []).model_copy(update={"spend": silent}))
    assert facts is not None and silent.total_used_usd == 0.0, "an unbilled inner cell vanished"
    inner_row = MeasuredCell(
        sample_id=0,
        query="q",
        predicted="",
        ground_truth="",
        pipeline=PipelineData.from_wire(facts.model_dump(mode="json")),
    )
    assert cell_channels_of(inner_row, None)["cost"] == pytest.approx(0.02), (
        "an inner campaign whose provider reported no bill read as a free cell"
    )

    # A send that left and named no price stays held at its bound.
    held_ledger = CycleEventLog(tmp_path / "held.jsonl")
    book = spend_book(0.05)
    held_ledger.bind(book)
    broke = [True]

    async def create_once_broken(**params: Any) -> Any:
        if broke[0]:
            broke[0] = False
            try:
                raise ssl.SSLError("bad record mac")
            except ssl.SSLError as err:
                raise openai.APIConnectionError(request=wire.request) from err
        return await wire.create(**params)

    async def create_unbilled(**params: Any) -> Any:
        reply = (await wire.create(**params)).parse().model_copy(update={"usage": None})
        return types.SimpleNamespace(headers={}, parse=lambda: reply)

    wire.raw.create = create_once_broken
    with _billing_on(held_ledger, book):
        _on_jumping_clock(_owned_chat(wire.client, "tls"))
    assert unreported_on(held_ledger).sends == 1, (
        "a TLS record fault after the request left is resent like a 5xx, and the broken send held"
    )
    wire.raw.create = create_unbilled
    bills, spent_usd = len(_token_bills(held_ledger)), book.usd_metered
    with _billing_on(held_ledger, book):
        _on_jumping_clock(_owned_chat(wire.client, "unbilled"))
    assert unreported_on(held_ledger).sends == 2 and book.usd_metered == spent_usd, (
        "a reply reporting no usage is a bill that never came: held, never settled at 0"
    )
    assert len(_token_bills(held_ledger)) == bills, "a reply with no usage wrote a bill"
    wire.raw.create = wire.create
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
    attempts: list[int] = []

    async def create_down(**_params: Any) -> Any:
        attempts.append(1)
        raise openai.InternalServerError(
            "down", response=httpx.Response(500, request=wire.request), body=None
        )

    async def two_rungs() -> None:
        with under_budget(SendBudget(None, attempts=SEND_ATTEMPTS)):
            for _rung in range(2):
                with pytest.raises(openai.InternalServerError):
                    await wire.client.chat(
                        ChatRequest([{"role": "user", "content": "x"}], "gpt-x", max_tokens=10),
                        label=CallLabel("down", "optimizer"),
                    )

    wire.raw.create = create_down
    monkeypatch.setattr(judge_call, "get_llm_client", lambda _provider: wire.client)
    with spending_under(unbounded_spend_book()):
        _on_jumping_clock(two_rungs())
        assert len(attempts) == SEND_ATTEMPTS + 1, f"{len(attempts)} attempts in one unit of work"
        attempts.clear()
        _reply_text, error = _on_jumping_clock(
            judge_call.ask(JudgeStage(model="gpt-x", provider="openai"), "grade", judge="j")
        )
    assert error and len(attempts) == SEND_ATTEMPTS, (
        f"{len(attempts)} attempts on one grading: a grading opens its own budget, and spends that"
    )
    wire.raw.create = wire.create

    # A backend cell is never sent again while it may still bill.
    cell_ledger = CycleEventLog(tmp_path / "termnorm.jsonl")
    posts: list[int] = []
    probes: list[int] = []
    billed_step = {
        "step_tokens": {"n": {"attempts": 3, "input": 10, "output": 5, "cost_usd": 0.001}}
    }
    attempt = SendBound(input_tokens=40, output_tokens=20, usd=0.004)
    deadline = {
        "detail": {"error_code": "llm_timeout", "retryable": False, "message": "no reply in 164s"},
        "data": {"step_tokens": None},
    }

    def termnorm(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            probes.append(1)
            if len(probes) == 1:
                raise httpx.ConnectError("still restarting", request=request)
            return httpx.Response(200, json={"status": "ok"})
        posts.append(1)
        if len(posts) == 1:
            raise httpx.ConnectError("refused", request=request)
        if len(posts) == 2:
            return httpx.Response(500, json={"detail": "upstream", "data": billed_step})
        if len(posts) == 4:
            return httpx.Response(504, json=deadline)
        raise httpx.ReadTimeout("slow", request=request)

    monkeypatch.setattr(backend_mod, "_OUTAGE_POLL_S", 0.0)
    remote = backend_client(session_factory=TermNormSession)
    remote._http = httpx.AsyncClient(transport=httpx.MockTransport(termnorm))
    whole_cell = SendBound(input_tokens=100, output_tokens=50, usd=0.01)
    wallet = spend_book()

    async def remote_cell() -> Any:
        async with CellEnvelope(None, attempts=remote.cell_attempts, label="q"):
            return await remote.run_query(
                Sample(id=0, query="q", ground_truth=None),
                bound=whole_cell,
                billed=cell_billing(pipeline_schema([]), {}, {"n": attempt}),
            )

    for ends in (httpx.ReadTimeout, CellHaltedError):
        with _billing_on(cell_ledger, wallet), pytest.raises(ends):
            _on_jumping_clock(remote_cell())
    assert len(posts) == 4, (
        f"{len(posts)} POSTs — a read timeout is NEVER resent: the backend still works it, and a "
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
    episode = harbor._Episode(
        query="task",
        task={},
        reward_key="reward",
        agent_name="terminus-2",
        agent_kwargs={},
        model=None,
        provider=None,
        environment="docker",
        cached=False,
        prompt=None,
        in_system_prompt=False,
        tools=(),
    )

    async def container_cell() -> Any:
        async with CellEnvelope(None, attempts=harbor.CONNECTOR.cell_attempts, label="task"):
            return await harbor._run_episode(episode)

    with pytest.raises(CellInfrastructureError):
        _on_jumping_clock(container_cell())
    assert len(trials) == harbor.CONNECTOR.cell_attempts, (
        f"{len(trials)} trials: a container cell reran past the budget its connector sized"
    )

    # A DSPy cell bills each LM call, and its provider's failure is a hole.
    import dspy  # extras: dspy
    from dspy import lm15

    from promptpotter.connectors import dspy_module  # extras: dspy

    monkeypatch.setattr(dspy_module, "held_wait", wire.no_wait)
    sends: list[int] = []
    script: list[Exception] = []

    class _Provider:
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
                step.lm = dspy.LM("openai/gpt-x", engine=_Provider(), cache=False)

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
            dspy_module.CONNECTOR, "", workload=InProcessWorkload(experiment=None, program=program)
        )
        config = {"prompt": "Answer.", "model": "openai/gpt-x", "max_calls": 2, "max_tokens": 50}
        pairs = {dspy_module.PROGRAM_NODE: client.priced_as(config)}

        async def one_cell() -> Any:
            async with CellEnvelope(None, attempts=client.cell_attempts, label="q"):
                return await client.run_query(
                    Sample(id=0, query="q", ground_truth="4"),
                    {dspy_module.PROGRAM_NODE: config},
                    bound=SendBound(input_tokens=20_000, output_tokens=2_000, usd=0.5),
                    billed=cell_billing(pipeline_schema([]), pairs, {}),
                )

        with _billing_on(dspy_ledger, purse):
            data, spent = _on_jumping_clock(one_cell())
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
    assert [(r.cost_usd, r.rate_priced_usd) for r in lm_bills()] == [
        (None, pytest.approx(call_usd))
    ] * 2, "the engine reports tokens and no cost: each call is priced at our rate"
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
    spoke, mixed = ReportedCost(), ReportedCost()
    for cost, heard in ((0.01, (spoke, mixed)), (None, (mixed,))):
        for block in heard:
            block.count(answered(Billed(TokenAccount(input=10, output=5), cost)))
    assert (spoke.usd, mixed.usd) == (0.01, None), (
        "one silent send makes the REPORTED sum unknown, never the smaller price of those that spoke"
    )
    turns: list[Any] = []
    cut_short = [True]

    async def episode_run(_workload: Any, _sample: Any, _payload: dict[str, Any]) -> dict[str, Any]:
        for n in range(4):
            wire.hang[0] = cut_short[0] and n == 3
            turns.append(
                await wire.client.chat(
                    ChatRequest(
                        [{"role": "user", "content": f"turn {n}"}], "gpt-x", max_tokens=100
                    ),
                    label=CallLabel("agent", "backend"),
                )
            )
        return {"data": {"terminal_node": "agent"}}

    agent = backend_client(execution="in_process", in_process_run=episode_run, holds_own_sends=True)
    turns_ledger = CycleEventLog(tmp_path / "turns.jsonl")
    purse = spend_book(1.0)
    turns_ledger.bind(purse)
    whole = SendBound(input_tokens=100_000, output_tokens=50_000, usd=0.5)

    async def agent_cell(sample_id: int) -> Any:
        async with CellEnvelope(None, attempts=agent.cell_attempts, label="q"):
            return await agent.run_query(
                Sample(id=sample_id, query="q", ground_truth=None),
                bound=whole,
                billed=lambda _data, _refused: (None, None),
            )

    async def cancel_mid_episode() -> None:
        running = asyncio.ensure_future(agent_cell(0))
        while len(turns) < 3:
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.05)
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    with _billing_on(turns_ledger, purse):
        _on_jumping_clock(cancel_mid_episode())
    cut_bills = _token_bills(turns_ledger)
    assert [r.node for r in cut_bills] == ["agent"] * 3, "a turn that answered left no bill"
    assert purse.usd_metered == pytest.approx(sum(r.bill_or_rate_usd or 0.0 for r in cut_bills)), (
        "the book read another bill than the turns wrote"
    )
    out = unreported_on(turns_ledger)
    assert out.sends == 1 and out.usd < whole.usd / 100, f"a cancel left ${out.usd} unreported"
    assert purse.usd_unreported == pytest.approx(out.usd), "the book held another sum than is out"
    assert purse.fits(whole, whole) == 1, "a cell's bound was not reserved whole"
    assert purse.held_at(CELL, whole) == whole, "a cut cell taught the book a price"
    cut_short[0] = False
    with _billing_on(turns_ledger, purse):
        _on_jumping_clock(agent_cell(1))
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


_WEB = "https://accounts.google.com"
_STEP_USD = settings.FREE_TIER_LAUNCH_STEP_USD
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
) -> Any:
    """`issuer` set makes this a WEB identity; the box operator has none and is unmetered."""
    ledgers: list[Path] = []
    if spent is not None:
        ledgers.append(tmp_path / "spent.jsonl")
        CycleEventLog(ledgers[0]).append(
            TokenUsageRecord(
                kind="backend",
                node="solve",
                provider="openrouter",
                timestamp="2026-01-01T00:00:00+00:00",
                **spent,
            )
        )
    return types.SimpleNamespace(
        identity=types.SimpleNamespace(
            issuer=issuer,
            user_id=user.user_id,
            tenant_id=user.tenant_id,
            claims={} if grant is None else {"spend_ceiling_usd": grant},
        ),
        campaigns=types.SimpleNamespace(
            iter_cycle_ledgers=lambda: ledgers,
            workspace=tmp_path / "ws",
            cycle_dir=lambda _hop: tmp_path / "no-producer",
        ),
        users=types.SimpleNamespace(load=lambda: user),
    )


def _running(admitted_at: str | None, reserve: SpendCeilings) -> Any:
    held = types.SimpleNamespace(job_id="job-b", hop=None, admitted_at=admitted_at, reserve=reserve)
    return types.SimpleNamespace(list_running=lambda *, user_id: [held])


async def _paid_door(door: str, stores: Any) -> None:
    match door:
        case "turn":
            async with paid_verb(stores=stores, bucket="turn", hop=None):
                pass
        case "bench":
            await grade_line_bench(stores=stores, hop=_HELD_HOP)
        case "noise-floor":
            await measure_noise_floor(stores=stores, hop=_HELD_HOP, k=2)
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


def test_an_accounts_ceiling_holds_at_every_paid_door_in_both_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, built_stores: Stores, run_bound: None
) -> None:
    """TWO units, because a price needs a rate on file and a token count never does (ADR-0003 D1)."""
    with monkeypatch.context() as patched:
        rates = RateTable({("openrouter", "openai/gpt-4o"): Rate(1e-6, 2e-6)})
        patched.setattr(pricing, "load_rates", lambda: rates)
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
            ("launch", _WEB, {}, None, None, None, None, SpendCeilings(_STEP_USD, _TOKENS)),
            ("launch", _WEB, {}, 10.0, None, None, None, None),
            (
                "launch",
                _WEB,
                {},
                None,
                None,
                _running(
                    "2026-01-01T00:00:00+00:00",
                    SpendCeilings(settings.FREE_TIER_SPEND_CAP_USD, _TOKENS),
                ),
                None,
                None,
            ),
            ("launch", _WEB, {}, None, None, _running(None, SpendCeilings()), None, None),
            (
                "launch",
                _WEB,
                {},
                None,
                None,
                None,
                _UNPRICEABLE,
                SpendCeilings(_STEP_USD, _TOKENS - 500_000),
            ),
            ("launch", None, {}, None, None, None, None, SpendCeilings()),
            ("launch", _WEB, _GENEROUS, 10.0, 2.0, None, None, SpendCeilings(2.0, 50_000)),
            ("launch", _WEB, _GENEROUS, 10.0, None, None, None, SpendCeilings(10.0, 50_000)),
            (
                "launch",
                _WEB,
                {**_GENEROUS, "spend_budget_usd_total": 1.0},
                None,
                2.0,
                None,
                None,
                SpendCeilings(1.0, 50_000),
            ),
            ("turn", None, {}, None, None, None, _SPENT_THE_CAP, SpendCeilings()),
            *(
                (door, _WEB, {}, None, None, None, _SPENT_THE_CAP, None)
                for door in ("turn", "bench", "noise-floor", "seed-screen", "probe-reasoning")
            ),
        )
    ):
        at = tmp_path / f"door-{n}"
        at.mkdir()
        monkeypatch.setattr(
            "promptpotter.application.jobs.registry.default_jobs_dir", functools.partial(Path, at)
        )
        user = User(user_id="sub-9", tenant_id="sub-9", created_at="2026-01-01", **budget)
        stores = _account_stores(at, issuer=issuer, user=user, spent=spent, grant=grant)
        refused = pytest.raises(QuotaExceededError)
        with refused if admitted_caps is None else contextlib.nullcontext():
            knocked = SpendCeilings()
            if door == "launch":
                knocked = admit_launch(
                    declared=SpendCeilings(declared, None),
                    user=user,
                    stores=stores,
                    job_registry=running or types.SimpleNamespace(list_running=lambda **_: []),
                    job_id="job-a",
                    hop=None,
                )[0]
            else:
                asyncio.run(_paid_door(door, stores))
            assert knocked == admitted_caps, (
                f"door {n} ({door}): an account was admitted under another ceiling than it holds"
            )

    # A paid verb never bills a cycle its producer still holds.
    operator = User(user_id="op", tenant_id="op", created_at="")
    held_stores = _account_stores(tmp_path, issuer=None, user=operator, spent=None)
    held_stores.campaigns = CampaignStore(WorkspaceDir(tmp_path / "held"))
    held_stores.campaigns.mint_cycle(_HELD_HOP)
    cycle_dir = held_stores.campaigns.cycle_dir(_HELD_HOP)
    hold_cycle(cycle_dir)

    async def bill_held() -> None:
        async with paid_verb(stores=held_stores, bucket="bench", hop=_HELD_HOP):
            pass

    with pytest.raises(ConflictError):
        asyncio.run(bill_held())
    CycleEventLog.open(CycleDir(cycle_dir)).append(
        RunPhaseRecord.stop(StopReason.PAUSED, cause=PauseCause.INTERRUPT)
    )
    with pytest.raises(ConflictError):
        asyncio.run(bill_held())
    release_cycle(cycle_dir)
    asyncio.run(bill_held())

    # A budget change leaves the arm it did not touch alone.
    def web_account(sub: str) -> Stores:
        return build_stores(
            IdentityContext(
                user_id=UserId(sub),
                tenant_id=TenantId(sub),
                issuer=Issuer(_WEB),
                claims={},
                capabilities=frozenset(),
            ),
            projects_root=built_stores.projects_root,
            benchmarks_root=built_stores.benchmarks_root,
        )

    metered = web_account("sub-9")
    hop = CycleHop(campaign_id="camp-3", cycle_id="cycle_budget0000")
    campaign = Campaign(
        campaign_id="camp-3", dataset_name="ds1", created_at="", root_cycle_id=hop.cycle_id
    )
    metered.campaigns.create_campaign(campaign)
    registry = JobRegistry(tmp_path / "jobs-3", capacity=lambda _live: 1)
    job = registry.request_slot(user_id="sub-9", principal_id="sub-9", dataset_name="ds1", hop=hop)
    assert job.holds_slot, "an empty box must hand out a slot, not a place in line"
    registry.mark_admitted(job.job_id, SpendCeilings(0.30, 5_000_000))
    dispatcher = CommandDispatcher(metered, registry)
    asyncio.run(_apply_change_run_limits(dispatcher, hop, SpendCeilings(), RoundsCap(max_rounds=3)))
    asyncio.run(_apply_change_run_limits(dispatcher, hop, SpendCeilings(None, 1_000), None))
    held = registry.get(job.job_id)
    assert held is not None and held.reserve == SpendCeilings(0.30, 2_000), (
        "the untouched USD reservation was released, or the token one did not move with its "
        "ceiling: as much again for the calls out"
    )
    standing = metered.campaigns.read_run_limits(hop)
    assert standing.reserve == held.reserve, "the standing record holds another reservation"
    assert standing.rounds == RoundsCap(max_rounds=3), (
        "the standing record is written WHOLE: a spend move must carry the round cap it did not touch"
    )
    delegated = types.SimpleNamespace(
        identity=types.SimpleNamespace(
            issuer=_WEB, user_id="sub-9", tenant_id="sub-9", claims={"spend_ceiling_usd": 2.0}
        ),
        campaigns=types.SimpleNamespace(
            iter_cycle_ledgers=lambda: [],
            workspace=tmp_path / "ws-d",
            load_campaign=lambda _id: campaign,
            cycle_dir=lambda _hop: tmp_path / "cycle-d",
        ),
    )
    caps, _ = clamp_budget_change(
        requested=SpendCeilings(None, 1_000),
        user=User(user_id="sub-9", tenant_id="sub-9", created_at="2026-01-01"),
        stores=delegated,
        job_registry=types.SimpleNamespace(
            list_running=lambda *, user_id: [], running_job_for=lambda _hop: None
        ),
        hop=hop,
    )
    assert (caps.usd, caps.tokens) == (None, 1_000), (
        "a grant became a ceiling on an arm the caller left alone"
    )

    # A moved ceiling counts the cycle's own spend once: what the parent billed past the cut is not
    # the fork's history, and binds it as spend.
    forker = web_account("sub-7")
    root = CycleHop(campaign_id="camp-7", cycle_id="cycle_budget0007")
    fork = CycleHop(campaign_id="camp-7", cycle_id="cycle_budget0007_fork_r1")
    forker.campaigns.create_campaign(
        Campaign(
            campaign_id="camp-7", dataset_name="ds1", created_at="", root_cycle_id=root.cycle_id
        )
    )
    forker.campaigns.mint_cycle(root)

    def bill(at: CycleHop, usd: float) -> None:
        CycleEventLog.open(CycleDir(forker.campaigns.cycle_dir(at))).append(
            TokenUsageRecord(
                kind="optimizer",
                node="l1_generate",
                input_tokens=1_000,
                output_tokens=0,
                cost_usd=usd,
            )
        )

    bill(root, 0.1)
    forker.campaigns.mint_fork_cycle(
        root,
        fork.cycle_id,
        ForkSpec(trigger=ForkTrigger.OPERATOR_REWIND, reason="", issued_by="", from_round=1),
        from_round=1,
    )
    bill(root, 0.1)
    bill(fork, 0.3)
    registry = JobRegistry(tmp_path / "jobs-7", capacity=lambda _live: 1)
    job = registry.request_slot(user_id="sub-7", principal_id="sub-7", dataset_name="ds1", hop=fork)
    registry.mark_admitted(job.job_id, SpendCeilings(0.8, None))
    allowance = User(
        user_id="sub-7", tenant_id="sub-7", created_at="2026-01-01", spend_budget_usd_total=1.0
    )
    caps, reserve = clamp_budget_change(
        requested=SpendCeilings(5.0, None),
        user=allowance,
        stores=forker,
        job_registry=registry,
        hop=fork,
    )
    relaunched, _ = admit_launch(
        declared=SpendCeilings(),
        user=allowance,
        stores=forker,
        job_registry=registry,
        job_id=job.job_id,
        hop=fork,
    )
    assert caps.usd == relaunched.usd == pytest.approx(0.9), (
        "$1.00 allowance, $0.40 in this cycle's history and $0.10 beside it: it may reach $0.90"
    )
    assert reserve.usd == pytest.approx(0.9), "the reservation moved off the ceiling it backs"


# 4. Spend outlives what spent it


def test_spend_outlives_the_fork_the_campaign_and_the_sandbox_that_spent_it(
    built_stores: Stores,
) -> None:
    stores = built_stores
    root = "cycle_root0000"
    stub, retried = f"{root}_fork_aaaa", f"{root}_fork_bbbb"
    campaign_dir = stores.campaigns.campaign_root_dir("camp-2")
    campaign_dir.mkdir(parents=True, exist_ok=True)
    (campaign_dir / "campaign.json").write_text(json.dumps({"campaign_id": "camp-2"}), "utf-8")

    def spent_cycle(cycle_id: str, *, cost_usd: float) -> Path:
        hop = CycleHop(campaign_id="camp-2", cycle_id=cycle_id)
        stores.campaigns.mint_cycle(hop)
        cycle_dir = stores.campaigns.cycle_dir(hop)
        CycleEventLog.open(CycleDir(cycle_dir)).append(
            TokenUsageRecord(
                kind="backend",
                node="backend",
                model="openai/gpt-4o",
                provider="openrouter",
                input_tokens=2_000,
                output_tokens=800,
                cost_usd=cost_usd,
                timestamp="2026-01-01T00:00:00+00:00",
            )
        )
        return CycleLayout(cycle_dir).ledger

    root_ledger = spent_cycle(root, cost_usd=0.07)
    stub_ledger = spent_cycle(stub, cost_usd=0.11)
    retried_ledger = spent_cycle(retried, cost_usd=0.05)

    def account_usd() -> float:
        return sum_user_spend(ledgers=account_ledgers(stores.campaigns)).used_usd

    def cleanup(cycle_id: str) -> bool:
        return cleanup_stub_fork_if_empty(
            campaign_store=stores.campaigns,
            hop=CycleHop(campaign_id="camp-2", cycle_id=cycle_id),
            parent_cycle_id=root,
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
    assert cleanup(stub) and not stub_ledger.exists(), "a spent stub fork survived its delete"
    assert account_usd() == pytest.approx(before), "deleting a spent stub fork un-spent it"
    retried_hop = CycleHop(campaign_id="camp-2", cycle_id=retried)
    for _crashed_attempt in range(2):
        bank_spend(
            workspace=stores.campaigns.workspace,
            cycle_dirs=[stores.campaigns.cycle_dir(retried_hop)],
            campaign_id="camp-2",
            cycle_id=retried,
        )
    assert cleanup(retried) and not retried_ledger.exists(), "a banked fork survived its delete"
    assert account_usd() == pytest.approx(before), (
        "banking precedes the delete: a retry after a crash in between must find the tombstone"
    )
    stores.campaigns.delete_campaign(
        "camp-2", keep_results=False, changed_at="2026-01-02T00:00:00Z"
    )
    assert not root_ledger.exists(), "a deleted campaign kept its ledger"
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
            TokenUsageRecord(
                kind="backend",
                node="llm_only",
                model="openai/gpt-oss-20b",
                provider="openrouter",
                input_tokens=input_tokens,
                output_tokens=100,
                duration_s=1.0,
                cost_usd=cost_usd,
                mirrored=mirrored,
            )
        )
    banked_before = len(tombstones())
    assert reclaim_orphan_sandboxes(stores.projects_root) == 1, (
        "an orphan sandbox was not reclaimed"
    )
    assert not sandbox.exists(), "a reclaimed sandbox survived"
    banked = tombstones()[banked_before:]
    assert [(b.used_usd, b.used_tokens) for b in banked] == [(pytest.approx(0.15), 600 + 100)], (
        "a forwarded inner cycle was banked twice, or its own spend not at all"
    )
