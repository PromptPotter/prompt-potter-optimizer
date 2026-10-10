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
import selectors
import types
from collections.abc import Callable, Coroutine, Iterator
from pathlib import Path
from typing import Any, cast
from unittest import mock

import pytest
from factories import (
    SANDBOX_CAMPAIGN,
    cycle_result,
    inner_sandbox,
    pipeline_schema,
    scored_candidate,
    spend_book,
)

from promptpotter.application.jobs.reaper import reclaim_orphan_sandboxes
from promptpotter.application.run_phase_control import RunControl
from promptpotter.application.scoring import query_loop
from promptpotter.domain.cycle_paths import CycleDir, CycleHop, WorkspaceDir
from promptpotter.domain.results import ArmOutcome
from promptpotter.domain.run_records import (
    CandidateScoredRecord,
    ConfigOverrides,
    ForkRemainder,
    ForkSpec,
    ForkTrigger,
    TokenUsageRecord,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import GradedCell, MeasuredCell, PipelineData, Scorer
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.domain.spend import StepUsage
from promptpotter.domain.validators import StopSignal
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.request import ChatRequest
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.stores import Stores


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


# 1. What a call costs


def test_a_rate_belongs_to_the_provider_model_pair_not_the_model_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import promptpotter.infrastructure.llm.pricing as spend_mod

    def row(input_usd: float, output_usd: float, lister: str, **tiers: float) -> dict[str, Any]:
        return {
            "input_cost_per_token": input_usd,
            "output_cost_per_token": output_usd,
            "litellm_provider": lister,
            **tiers,
        }

    table = spend_mod._models_to_rates(
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
    monkeypatch.setattr(spend_mod, "load_rates", lambda: table)
    lookup_rate, compute_usd = spend_mod.lookup_rate, spend_mod.compute_usd
    inline_route = spend_mod.inline_route

    assert lookup_rate("deepseek-v4-flash", "deepseek") == spend_mod.Rate(0.00000014, 0.00000028)
    assert lookup_rate("deepseek/deepseek-v4-flash", "openrouter") is None
    assert lookup_rate("deepseek-v4-flash", None) is None

    # A routing suffix selects another upstream host with its own rate: the base price is not it.
    assert lookup_rate("openai/gpt-oss-20b:nitro", "openrouter") is None

    assert lookup_rate("openai/gpt-oss-20b", "openrouter") == spend_mod.Rate(0.00000004, 0.00000015)
    assert lookup_rate("openai/gpt-oss-120b", "groq") == spend_mod.Rate(0.00000015, 0.0000006)
    assert lookup_rate("gpt-4o", "openai") == spend_mod.Rate(
        0.0000025, 0.00001, 0.000003125, 0.00000025
    )

    for key, usd in (
        ("anthropic.claude-haiku-4-5-v1:0", 10 * 0.000001 + 10 * 0.000005),
        ("openrouter/openrouter/auto", 10 * 0.000002 + 10 * 0.000004),
        ("deepseek/deepseek-v4-flash", 10 * 0.00000014 + 10 * 0.00000028),
        ("gpt-4o", 10 * 0.0000025 + 10 * 0.00001),
    ):
        model, provider = inline_route(key)
        assert compute_usd(model, 10, 10, provider=provider) == pytest.approx(usd), key
    assert inline_route("orphan-model") == ("orphan-model", None)
    assert compute_usd("deepseek/deepseek-v4-flash", 10, 10, provider="openrouter") is None

    # A cache read is a SUBSET of the input count: re-priced out of it, never added on top.
    cold = compute_usd("gpt-4o", 1000, 0, provider="openai")
    hit = compute_usd("gpt-4o", 1000, 0, provider="openai", cache_read_tokens=800)
    assert cold == pytest.approx(1000 * 0.0000025)
    assert hit == pytest.approx(200 * 0.0000025 + 800 * 0.00000025)
    assert hit < cold
    # No cache tier on the row: the read bills at the INPUT price, never a rate nobody sourced.
    assert compute_usd(
        "openai/gpt-oss-20b", 1000, 0, provider="openrouter", cache_read_tokens=800
    ) == pytest.approx(compute_usd("openai/gpt-oss-20b", 1000, 0, provider="openrouter"))


def test_wire_cost_reaches_the_response_or_nothing_prices_the_optimizer() -> None:
    from openai.types.chat import ChatCompletion

    from promptpotter.infrastructure.llm.openai_compat import _billed_cost, reply_cost

    def completion(usage: dict[str, object] | None) -> ChatCompletion:
        return ChatCompletion.model_validate(
            {
                "id": "c",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek/deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "{}"},
                    }
                ],
                **({"usage": usage} if usage is not None else {}),
            }
        )

    # OpenRouter's real shape: `cost` rides as an EXTRA on the usage object.
    priced = completion(
        {
            "prompt_tokens": 263,
            "completion_tokens": 152,
            "total_tokens": 415,
            "cost": 7.938e-05,
            "cost_details": {"upstream_inference_cost": 0},
            "is_byok": False,
        }
    )
    assert reply_cost(priced) == 7.938e-05

    # None, not 0.0: 0.0 is a measurement, and satisfies the cap that should escalate to the table.
    unpriced = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
    assert reply_cost(completion(unpriced)) is None
    assert reply_cost(completion(None)) is None

    assert _billed_cost(1e-05, 2e-05) == pytest.approx(3e-05)
    assert _billed_cost(None, 2e-05) == pytest.approx(2e-05)
    assert _billed_cost(1e-05, None) == pytest.approx(1e-05)
    assert _billed_cost(None, None) is None


async def test_a_cell_that_ran_without_a_grade_still_bills_what_it_spent() -> None:
    from promptpotter.application.scoring.formula import compile_scorer
    from promptpotter.application.scoring.sample_measurement import (
        emit_replayed_step_tokens,
        measure_sample,
    )
    from promptpotter.domain.run_records import TokenUsageRecord
    from promptpotter.infrastructure.backend import BackendClient
    from promptpotter.infrastructure.llm import telemetry
    from promptpotter.infrastructure.llm.spend_book import spending_under, unbounded_spend_book
    from promptpotter.shared.errors import CellUnscoreableError, ErrorCategory

    spent = {"agent": {"input": 1200, "output": 300, "estimated": False, "cost_usd": 0.0076}}

    async def _ran_ungraded(*_args: Any) -> dict[str, Any]:
        raise CellUnscoreableError("verifier timed out", spent=spent)

    client = BackendClient(
        "http://unused",
        wire_adapter=lambda query, params: {"query": query},
        session=types.SimpleNamespace(),  # type: ignore[arg-type]
        execution="in_process",
        in_process_run=_ran_ungraded,
        workload=types.SimpleNamespace(),  # type: ignore[arg-type]
        prompt_delivery=types.SimpleNamespace(),  # type: ignore[arg-type]
    )

    class _Ledger:
        def __init__(self) -> None:
            self.records: list[Any] = []

        def append(self, record: Any) -> int:
            self.records.append(record)
            return len(self.records)

    ledger = _Ledger()
    session = types.SimpleNamespace(
        pipeline_schema=pipeline_schema([]),
        identity_keys={},
        state=types.SimpleNamespace(ledger=None),
        backend_client=client,
        scoring=types.SimpleNamespace(
            scorer=compile_scorer("max(0.0, min(1.0, env_reward))", verifier_graded=True)
        ),
    )
    token = telemetry.set_cycle_ledger(ledger)  # type: ignore[arg-type]
    try:
        with spending_under(unbounded_spend_book()):
            row = await measure_sample(
                Sample(id=0, query="10452", ground_truth=None),
                session,  # type: ignore[arg-type]
            )
    finally:
        telemetry.reset_cycle_ledger(token)

    assert row.error_category == ErrorCategory.UNSCOREABLE
    billed = [r for r in ledger.records if isinstance(r, TokenUsageRecord)]
    assert [(r.cost_usd, r.input_tokens, r.cached) for r in billed] == [(0.0076, 1200, False)]

    # A replay states the price its row recorded: re-priced, incurred cost moves with each refresh.
    banked = {"agent": StepUsage(input=1200, output=300, rate_priced_usd=0.25)}
    token = telemetry.set_cycle_ledger(ledger)  # type: ignore[arg-type]
    try:
        emit_replayed_step_tokens(banked, {})
    finally:
        telemetry.reset_cycle_ledger(token)
    replayed = ledger.records[-1]
    assert (replayed.cost_usd, replayed.rate_priced_usd, replayed.cached) == (None, 0.25, True)


def _counting_client(reply: str) -> tuple[Any, list[int]]:
    from openai.types.chat import ChatCompletion

    from promptpotter.infrastructure.llm.openai_compat import OpenAICompatibleClient, ProviderSpec

    calls: list[int] = []

    async def create(**_kw: Any) -> Any:
        calls.append(1)
        # A stub reporting no `cached_tokens` cannot catch the metering dropping the discount.
        completion = ChatCompletion.model_validate(
            {
                "id": "c",
                "object": "chat.completion",
                "created": 0,
                "model": "grader-1",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": reply},
                    }
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 1,
                    "total_tokens": 12,
                    "prompt_tokens_details": {"cached_tokens": 8},
                },
            }
        )
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
    from promptpotter.infrastructure.llm import spend_book
    from promptpotter.infrastructure.store.llm_reuse_cache import LLMReuseCache
    from promptpotter.judges import call as judge_call
    from promptpotter.judges.protocol import JudgeSpec, JudgeStage
    from promptpotter.judges.registry import build_evaluators

    client, calls = _counting_client(reply)
    monkeypatch.setattr(judge_call, "get_llm_client", lambda _p: client)
    metered: list[Any] = []
    monkeypatch.setattr(judge_call, "emit_token_usage", lambda **kw: metered.append(kw))
    monkeypatch.setattr(spend_book, "emit_token_usage", lambda **kw: metered.append(kw))

    cache = LLMReuseCache(tmp_path, "judge_reuse")
    (ev,) = build_evaluators(
        {"answer": JudgeSpec(name="sealqa", stages=[JudgeStage(model="grader-1", provider="p")])},
        cache=cache,
    )
    with spend_book.spending_under(spend_book.unbounded_spend_book()):
        for _ in range(2):
            row = MeasuredCell(sample_id=0, query="who?", predicted="Ada", ground_truth="Ada")
            last = await ev.compute(result=row, schema=None)
    return calls, metered, last


async def test_a_second_grading_of_one_comparison_is_not_re_billed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The replay is still METERED, flagged ``cached``: grading cost is invariant to cache history."""
    calls, metered, score = await _grade_twice(tmp_path, monkeypatch, reply="A")

    assert score.score == 1.0, "the replayed reply must grade identically, not merely cheaply"
    assert len(calls) == 1, f"an identical comparison re-billed the provider: {len(calls)}x"
    assert [m.get("cached", False) for m in metered] == [False, True], (
        "a served grading went unmetered"
    )
    assert {m["kind"] for m in metered} == {"judge"}, "grading spend landed outside its own bucket"

    # ``cached``: OUR cache served the reply. ``cache_read``: the PROVIDER discounted a sent call.
    wire = metered[0]["usage"]
    assert wire.cache_read == 8
    assert wire.input == 11, "cache_read is a SUBSET of input, never a deduction from it"

    # An empty reply is a TRANSIENT failure; cached under the prompt hash it becomes permanent.
    blank_calls, _, blank = await _grade_twice(tmp_path / "blank", monkeypatch, reply="   ")
    assert blank.score is None, "an unreadable grading is an absent verdict, never a zero"
    assert len(blank_calls) == 2, "an empty reply was cached and replayed as if it were a verdict"


def test_a_field_a_client_cannot_send_is_refused_and_the_gateway_wire_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A banked reply is keyed on node config, never the wire, so the gateway body is pinned whole."""
    from pydantic import BaseModel, Field

    from promptpotter.infrastructure.llm import base, openai_compat
    from promptpotter.infrastructure.llm.anthropic import AnthropicClient
    from promptpotter.infrastructure.llm.pricing import PriceTier, RateCeiling
    from promptpotter.infrastructure.llm.send_pacing import SendBudget, under_budget
    from promptpotter.infrastructure.llm.spend_book import (
        CallLabel,
        bind_spend_book,
        unbounded_spend_book,
    )

    class _Reply(BaseModel):
        answer: str = Field(description="The answer.")

    class _SentError(Exception):
        pass

    wire: list[dict[str, Any]] = []

    async def create(**params: Any) -> None:
        wire.append(params)
        raise _SentError

    async def ceiling(*_: Any, **__: Any) -> RateCeiling:
        return RateCeiling(tiers=(PriceTier(0, 0.5, 0.25),))

    monkeypatch.setattr(base, "rate_ceiling", ceiling)
    raw = types.SimpleNamespace(with_raw_response=types.SimpleNamespace(create=create))
    messages = [{"role": "user", "content": "q"}]

    def send(client: Any, request: ChatRequest) -> None:
        async def _send() -> None:
            bind_spend_book(unbounded_spend_book())
            with under_budget(SendBudget(None, attempts=1)):
                await client.chat(request, label=CallLabel("l1", "optimizer"))

        asyncio.run(_send())

    claude = AnthropicClient(api_key="k")
    claude._client = types.SimpleNamespace(messages=raw)  # type: ignore[assignment]
    with pytest.raises(_SentError):
        send(claude, ChatRequest(messages, "m", response_model=_Reply, top_p=0.9))
    assert len(wire) == 1
    for unsendable in ({"seed": 7}, {"reasoning_effort": "low"}, {"route_order": ["Alibaba"]}):
        with pytest.raises(ValueError):
            send(claude, ChatRequest(messages, "m", **unsendable))
    assert len(wire) == 1, "a refused request reached the provider"

    own_host = openai_compat.OpenAICompatibleClient(
        api_key="k", provider="groq", spec=openai_compat.ProviderSpec("Groq", "")
    )
    own_host._client = types.SimpleNamespace(  # type: ignore[assignment]
        chat=types.SimpleNamespace(completions=raw)
    )
    with pytest.raises(ValueError):
        send(own_host, ChatRequest(messages, "m", route_order=["Alibaba"]))
    assert len(wire) == 1, "a route reached a provider that is its own host"

    gateway = openai_compat.OpenAICompatibleClient(
        api_key="k",
        provider="openrouter",
        spec=openai_compat.ProviderSpec("OpenRouter", "", gateway=True),
    )
    gateway._client = types.SimpleNamespace(  # type: ignore[assignment]
        chat=types.SimpleNamespace(completions=raw)
    )
    priced = {"max_price": {"prompt": 500000.0, "completion": 250000.0}}
    with pytest.raises(_SentError):
        send(gateway, ChatRequest(messages, "m"))
    assert json.dumps(wire[1]) == json.dumps(
        {
            "model": "m",
            "messages": messages,
            "temperature": 0.0,
            "extra_body": {"usage": {"include": True}, "provider": priced},
        }
    )
    with pytest.raises(_SentError):
        send(
            gateway,
            ChatRequest(
                messages,
                "m",
                temperature=0.3,
                max_tokens=64,
                response_model=_Reply,
                reasoning_effort="low",
                top_p=0.9,
                seed=7,
                route_order=["Alibaba"],
            ),
        )
    assert json.dumps(wire[2]) == json.dumps(
        {
            "model": "m",
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 64,
            "seed": 7,
            "top_p": 0.9,
            "extra_body": {
                "usage": {"include": True},
                "provider": {**priced, "order": ["Alibaba"], "allow_fallbacks": True},
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
    )


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
    from promptpotter.application.scoring.metrics import INVALID_SCORES
    from promptpotter.shared.measurement_context import measured_candidate_context

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
    from promptpotter.domain.phases import REFUSAL_STOPS, StopLoop
    from promptpotter.shared.errors import SendRefusedError

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
    """Every cell is admitted at, and bills, $1 against ``cap_usd``."""
    from factories import pobb_knobs

    from promptpotter.application.optimizers.potter.pobb.checks import PoBBCheck
    from promptpotter.application.optimizers.potter.race import CatchUpPool
    from promptpotter.domain.pipeline_schema import WebSpendBound
    from promptpotter.domain.spend import TokenAccount
    from promptpotter.infrastructure.llm.spend_book import (
        Billed,
        CallLabel,
        SendBound,
        admitted,
        spending_under,
    )

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
        PoBBCheck(pobb_knobs(), n_min=6, n_samples=len(dataset), ruler=None), _backfill
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
            backend_client=types.SimpleNamespace(
                max_cells_in_flight=max_cells,
                cancel_stops_billing=False,
                holds_own_sends=False,
                node_spend_bound=lambda node, cfg: node.spend_bound,
            ),
            # One node the backend bounds at the cell's dollar, so the scheduler counts in them.
            pipeline_schema=types.SimpleNamespace(
                nodes=[
                    types.SimpleNamespace(
                        name="search",
                        is_llm=False,
                        spend_bound=WebSpendBound(kind="web", queries=1, usd_per_query=1.0),
                    )
                ]
            ),
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
        "depths": depths,
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
    from promptpotter.shared.measurement_context import MeasuredCandidate, measured_candidate

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
        backend_client=types.SimpleNamespace(
            max_cells_in_flight=armed,
            cancel_stops_billing=False,
            holds_own_sends=True,
            derives_spend_bounds=False,
        ),
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
async def test_sample_lookahead_changes_the_bill_and_never_the_record(tmp_path: Path) -> None:
    dataset = [Sample(id=i, query=f"q{i}", ground_truth=str(i % 2)) for i in range(1, 9)]

    d1 = await _walk(dataset, armed=1, cut_at=None)
    d2 = await _walk(dataset, armed=2, cut_at=None)
    assert d2["max_depth"] == 2, "arming did not open the window — the rest proves nothing"
    assert d1["max_depth"] == 1
    assert d1["rows"] == d2["rows"]
    assert d1["calls"] == d2["calls"]

    assert [r.sample_id for r in d2["rows"]] == [s.id for s in dataset]

    c1 = await _walk(dataset, armed=1, cut_at=4)
    c2 = await _walk(dataset, armed=2, cut_at=4)
    assert c2["max_depth"] == 2, "window never opened on the cut walk"
    assert c1["stop_reason"] == c2["stop_reason"] == "stop_rule"
    assert c1["rows"] == c2["rows"]

    # AT MOST one extra call: equality would pin a scheduling accident, two an overgrown window.
    assert 0 <= len(c2["calls"]) - len(c1["calls"]) <= 1

    assert (await _walk(dataset, armed=4, cut_at=None, max_cells=1))["max_depth"] == 1
    assert (await _walk(dataset, armed=4, cut_at=None, max_cells=2))["max_depth"] == 2

    # What a cut discards is bounded by the stop rule's HORIZON, not by the depth.
    deep = await _walk(dataset, armed=8, cut_at=4, max_cells=8)
    assert deep["stop_reason"] == "stop_rule"
    assert deep["rows"] == c1["rows"]
    assert 0 <= len(deep["calls"]) - len(c1["calls"]) <= 1
    assert len(c1["calls"]) == 4, "an unarmed walk launched before the cell ahead was decided"
    # A discarded remote call still bills: the provider charges whether or not anyone waits.
    assert sorted(deep["returned"]) == sorted(deep["calls"])
    assert deep["billed"] == len(deep["calls"])

    # A catch-up holds a slot like a cell, and is archived only for a cell the walk absorbed.
    lone = await _walk(dataset, armed=1, cut_at=4, parent_lacks_cells=True)
    wide = await _walk(dataset, armed=3, cut_at=4, max_cells=3, parent_lacks_cells=True)
    # Catch-up far faster than a cell, so the one started past the cut is back before the cut.
    quick = await _walk(
        dataset, armed=8, cut_at=4, max_cells=8, parent_lacks_cells=True, backfill_pace=0.0001
    )
    assert max(lone["entries"]) == 1
    assert max(wide["entries"]) <= 3
    absorbed = [s.id for s in dataset[:4]]
    assert lone["committed"] == wide["committed"] == quick["committed"] == absorbed
    assert lone["rows"] == wide["rows"] == quick["rows"] == c1["rows"]

    # A round's walks measure AT ONCE and decide IN TURN; the depth bounds the ROUND, not each walk.
    serial = await _round(dataset, armed=1, cuts=[4, None, 6])
    fanned = await _round(dataset, armed=4, cuts=[4, None, 6])
    assert serial["peak"] == 1
    assert fanned["peak"] <= 4
    assert fanned["rows"] == serial["rows"]
    assert fanned["stops"] == serial["stops"] == ["stop_rule", None, "stop_rule"]
    assert fanned["absorbed"] == serial["absorbed"]
    # First cells fastest: the second walk's calls go out while the first still has cells to take.
    ahead = await _round(dataset, armed=4, cuts=[None, None], slowest_last=True)
    assert ahead["absorbed"] == [(w, s.id) for w in (0, 1) for s in dataset]
    last_of_first = max(i for i, e in enumerate(ahead["events"]) if e[:2] == ("absorb", 0))
    assert any(e[:2] == ("call", 1) for e in ahead["events"][:last_of_first]), (
        "no later candidate measured ahead — the identity check below proves nothing"
    )
    for w in (0, 1):
        calls = sorted(sid for kind, who, sid in ahead["events"] if kind == "call" and who == w)
        assert calls == [s.id for s in dataset], f"walk {w}'s calls ran under another identity"
    # The call that returned as the pause was pressed is absorbed, not paid again on resume.
    paused = await _round(dataset, armed=1, cuts=[None], pause_after_call=3)
    assert paused["stops"] == ["graceful"]
    assert len(paused["rows"][0]) == len(paused["calls"]) == 3
    # A pause starts nothing: an unstarted catch-up stays so, one already out launches no cell.
    owing = await _walk(dataset, armed=1, cut_at=None, parent_lacks_cells=True, pause_after_call=2)
    assert owing["stop_reason"] == "graceful"
    assert owing["committed"] == [dataset[0].id]
    assert len(owing["rows"]) == len(owing["calls"]) == 2
    landing = await _walk(
        dataset,
        armed=2,
        cut_at=None,
        parent_lacks_cells=True,
        backfill_pace=0.02,
        pause_after_call=1,
    )
    assert landing["stop_reason"] == "graceful"
    assert landing["committed"] == [dataset[0].id]
    assert len(landing["rows"]) == len(landing["calls"]) == 1
    # What came back behind a head still out is banked, and the resumed walk replays it.
    head = dataset[0].id
    stopped = await _walk(
        dataset, armed=4, max_cells=4, cut_at=None, pause_after_call=3, stall=head
    )
    assert stopped["rows"] == [] and set(stopped["banked"]) == set(stopped["returned"])
    assert stopped["priced"] == {f"cell_{sid}" for sid in stopped["banked"]}
    resumed = await _walk(dataset, armed=4, max_cells=4, cut_at=None, cached=stopped["banked"])
    assert not set(resumed["calls"]) & set(stopped["banked"]), "a banked cell was paid again"
    assert [r.sample_id for r in resumed["rows"]] == [s.id for s in dataset]
    # …but never one past where a rule could cut: the serial walk would not have measured it.
    capped = await _walk(dataset, armed=4, max_cells=4, cut_at=2, pause_after_call=2, stall=head)
    assert set(capped["banked"]) == {head, dataset[1].id}
    # A spend ceiling binds BEFORE a call: every cell out is counted at its bound.
    ceiling = await _walk(dataset, armed=4, max_cells=4, cut_at=None, cap_usd=5.0)
    assert ceiling["stop_reason"] == "spend_budget"
    assert ceiling["billed"] == 5.0
    # A skip made before a stop outlives it: the LAST decision on record per candidate replays.
    from promptpotter.application.runner.measurement import _skips_on_record
    from promptpotter.infrastructure.ledger import CycleEventLog

    ledger = CycleEventLog(tmp_path / "ledger.jsonl")
    for cid, n, reason in (
        ("a", 3, "skipped"),
        ("b", 2, "skipped"),
        ("b", 8, "measured"),
        ("c", 4, "skipped"),
    ):
        scores = scored_candidate(cid, scored_samples=n, outcome=reason)
        ledger.append(
            CandidateScoredRecord(round=2, candidate_idx=0, candidate_total=3, scores=scores)
        )
    assert _skips_on_record(ledger, 2) == {"a": 3, "c": 4}
    replayed = await _walk(dataset, armed=4, max_cells=4, cut_at=None, skip_at=3)
    assert replayed["stop_reason"] == "skip" and len(replayed["rows"]) == 3
    assert sorted(replayed["calls"]) == [s.id for s in dataset[:3]]


async def test_a_resumed_arm_re_reads_its_cells_and_still_reaches_its_bench(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from promptpotter.application.bench import llm_call as call_mod
    from promptpotter.domain.pipeline_schema import WebSpendBound
    from promptpotter.domain.run_records import TokenUsageRecord
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.llm import telemetry
    from promptpotter.infrastructure.llm.response import LLMResponse
    from promptpotter.infrastructure.llm.spend_book import spending_under
    from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_priced_keys
    from promptpotter.infrastructure.store.llm_reuse_cache import LLMReuseCache
    from promptpotter.judges import call as judge_call
    from promptpotter.judges.protocol import JudgeStage

    class _Provider:
        async def chat(self, *_a: Any, **_kw: Any) -> LLMResponse:
            return LLMResponse(content="ok", model="m", cost_usd=0.01)

    monkeypatch.setattr(call_mod, "get_llm_client", lambda _p: _Provider())
    monkeypatch.setattr(judge_call, "get_llm_client", lambda _p: _Provider())
    loop_reuse = LLMReuseCache(tmp_path, "optimizer_reuse")
    judge_reuse = LLMReuseCache(tmp_path, "judge_reuse")

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
    await round_calls()

    dataset = [Sample(id=i, query=f"q{i}", ground_truth="a") for i in range(4)]
    step = {"solve": StepUsage(input=10, output=5, cost_usd=0.01)}
    banked = {
        s.id: MeasuredCell(
            sample_id=s.id,
            query=s.query,
            ground_truth="a",
            predicted="a",
            pipeline=PipelineData(step_tokens=step),
        )
        for s in dataset
    }
    # The ceiling already spent: no cell fits, and the control reads it as reached.
    book = spend_book(0.02, meters="search_incurred", usd_metered=0.02)
    ledger = CycleEventLog.open(CycleDir(tmp_path / "cycle"))
    ledger.bind(book)
    session = types.SimpleNamespace(
        scoring=_row_scoring(),
        state=types.SimpleNamespace(ledger=None),
        control=RunControl(book=book),
        backend_client=types.SimpleNamespace(
            max_cells_in_flight=1,
            cancel_stops_billing=False,
            holds_own_sends=False,
            node_spend_bound=lambda node, cfg: node.spend_bound,
        ),
        pipeline_schema=types.SimpleNamespace(
            nodes=[
                types.SimpleNamespace(
                    name="search",
                    is_llm=False,
                    spend_bound=WebSpendBound(kind="web", queries=1, usd_per_query=0.01),
                )
            ]
        ),
        flight=None,
    )
    taken = [s.id for s in dataset[:3]]
    walk = _walk_over(dataset, session, checks=[], cached=banked, rereads=taken)
    bench = _walk_over(dataset, session, checks=[], cached=banked)
    token = telemetry.set_cycle_ledger(ledger)
    try:
        with spending_under(book):
            stopped = await _stopped_by_operator(
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
    assert [cell.sample_id for cell in walk.results] == taken, "the ceiling held back a re-read"
    assert stopped == "spend_budget"
    assert len(bench.results) == len(dataset), "the search's ceiling stopped the bench pass"
    kinds = [r.kind for _, r in ledger.iter() if isinstance(r, TokenUsageRecord)]
    assert kinds == ["bench"] * len(dataset) + ["optimizer", "judge"], (
        f"a re-read was metered a second time: {kinds}"
    )
    assert book.usd_metered == pytest.approx(0.04)
    priced = scan_ledger_priced_keys([ledger.path])
    assert {f"cell_{s.id}" for s in dataset} <= priced and len(priced) == len(dataset) + 2


def test_a_ledger_index_serves_the_file_as_it_stands(tmp_path: Path) -> None:
    import threading

    from promptpotter.domain.run_records import CycleSeed, CycleSeedRecord, TokenUsageRecord
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.store.account_spend import iter_user_token_usage
    from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
        scan_ledger_cycle_seed,
        scan_ledger_spend,
        scan_ledger_spend_by_round,
    )
    from promptpotter.infrastructure.store.io import write_jsonl
    from promptpotter.infrastructure.store.read_model import LedgerSpan, iter_jsonl

    ledger = CycleEventLog.open(CycleDir(tmp_path))

    def bill(n: int, round: int | None = None) -> None:
        for _ in range(n):
            ledger.append(
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
        ledger.append(CycleSeedRecord(seed=CycleSeed(pipeline_overlay=overlay)))

    def tailed() -> tuple[float, int, CycleSeed | None, float]:
        spend, calls, _worked_s = scan_ledger_spend([LedgerSpan(ledger.path)])
        wallet = iter_user_token_usage(ledgers=[ledger.path], since=0.0, until=float("inf"))
        charted = sum(row.billed_usd or 0.0 for row in wallet)
        return spend.total_used_usd, calls, scan_ledger_cycle_seed(ledger.path), charted

    def cold() -> tuple[float, int, CycleSeed | None, float]:
        rows = iter_jsonl(ledger.path)
        bills = [row["cost_usd"] for row in rows if row["record_type"] == "token_usage"]
        seeds = [row["seed"] for row in rows if row["record_type"] == "cycle_seed"]
        seeded = CycleSeed.model_validate(seeds[-1]) if seeds else None
        return sum(bills), len(bills), seeded, sum(bills)

    bill(3)
    seed("first")
    assert tailed() == cold() and cold()[1] == 3
    bill(2, round=2)
    seed("second")
    assert tailed() == cold() and cold()[1] == 5
    # The per-round split is the same bill: a call that carries no round banks at the origin's.
    by_round = scan_ledger_spend_by_round([LedgerSpan(ledger.path)])
    assert {r: spent.total_used_usd for r, spent in by_round.items()} == {0: 3.0, 2: 2.0}

    # A compaction swaps the file for a shorter one; the index must not keep its old rows.
    write_jsonl(ledger.path, iter_jsonl(ledger.path)[:2])
    assert tailed() == cold() == (2.0, 2, None, 2.0)

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
    assert all(usd == calls for usd, calls in seen), [s for s in seen if s[0] != s[1]][:3]
    assert tailed() == cold() and cold()[1] == 42


@on_jumping_clock
async def test_cell_envelope_cancels_the_inner_campaign(tmp_path: Path, monkeypatch: Any) -> None:
    from promptpotter.application.runner.inner import spawn, spawn_context
    from promptpotter.application.runner.inner.tasks import InnerCells, load_inner_tasks
    from promptpotter.application.scoring.cell_envelope import CellEnvelope
    from promptpotter.domain.results import CycleResult
    from promptpotter.infrastructure.llm import heartbeat as heartbeat_mod
    from promptpotter.infrastructure.llm import telemetry as llm_telemetry
    from promptpotter.infrastructure.store.io import write_json
    from promptpotter.shared.errors import CellUnscoreableError
    from promptpotter.shared.identity import default_identity

    class _RecordingLedger:
        def __init__(self) -> None:
            self.records: list[Any] = []

        def append(self, record: Any) -> int:
            self.records.append(record)
            return len(self.records)

    monkeypatch.setattr(heartbeat_mod, "HEARTBEAT_INTERVAL_S", 0.01)
    monkeypatch.setattr(spawn, "OUTER_SAMPLE_WALL_S_PER_ROUND", 0.02)

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _hanging_inner(
        cell: Any, cycle_dir_box: dict[str, Path], spawned_by: dict[str, Any]
    ) -> CycleResult:
        """SWALLOWS the cancellation and returns normally: a stub that re-raises cannot catch that."""
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
        return cycle_result([], 0.0, [])

    monkeypatch.setattr(spawn, "_run_inner_campaign", _hanging_inner)
    # Loaded through the real validator: the run resolves its panel ONCE and carries it.
    write_json(
        tmp_path / "inner_tasks.yaml",
        {
            "inner_benchmark": "justlogic-d234",
            "inner_benchmark_config": {
                "n_samples_per_inner_round": 24,
                "max_inner_rounds": 7,
            },
            "tasks": [{"id": "justlogic-d234/seed-0", "inner_dataset_seed": 0}],
        },
    )
    spawn_context._INNER_SPAWN.set(
        spawn_context.InnerSpawnContext(
            inner_sandbox_root=tmp_path,
            dataset_config_dir=tmp_path,
            identity=default_identity(),
            shared_root=tmp_path,
            spawn_campaign_id="ppself__aaaaaa",
            spawn_cycle_id="cycle_deadbeef0000",
            asking_cycle_id="cycle_deadbeef0000",
            enclosing=(),
            # No inner dataset resolved: the stubbed inner run never reads one.
            cells=InnerCells(
                panel=load_inner_tasks(tmp_path / "inner_tasks.yaml"), by_dataset={}, treatment="o"
            ),
        )
    )
    llm_telemetry._CYCLE_LEDGER.set(_RecordingLedger())  # type: ignore[arg-type]

    # The connector DECLARES the seconds, the scoring seam enforces them, as in `measure_sample`.
    from promptpotter.domain.sample import Sample

    query = "justlogic-d234/seed-0"
    cell = Sample(
        id=0, query=query, ground_truth=None, source_pin={"id": query, "inner_dataset_seed": 0}
    )
    envelope = CellEnvelope(spawn.inner_cell_envelope_s(cell, {}), attempts=1, label=query)
    with pytest.raises(CellUnscoreableError):
        async with envelope:
            await spawn.run_inner_cycle(cell, {})

    assert started.is_set(), "the inner campaign never started — the envelope proved nothing"
    assert cancelled.is_set(), "the inner campaign outlived its envelope and kept spending"


def _no_spend() -> Any:
    from promptpotter.domain.spend import MeteredSpend

    return MeteredSpend(
        meter="bill",
        metered_usd=0.0,
        metered_tokens=0,
        billed_usd=0.0,
        rate_priced_usd=0.0,
        calls_rate_priced=False,
        rate_known=True,
        bill_is_floor=False,
        metered_is_bill=True,
        incurred_usd=0.0,
        billed_tokens=0,
        unpriced_tokens=0,
        kinds={},
        replay_share=None,
    )


def test_a_run_in_its_own_process_spends_as_the_account_that_launched_it(
    built_stores: Any, tmp_path: Path
) -> None:
    """An identity rebuilt from the tenant alone carries no issuer, and no issuer IS the operator."""
    import types

    from promptpotter.application.jobs.launcher.run_job import JobSpec
    from promptpotter.application.jobs.quota import spends_the_hosts_own_key
    from promptpotter.application.jobs.registry import JobRegistry
    from promptpotter.application.runner.entry import RunMode, _arm_spend_book
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.launch_limits import HeldLimits
    from promptpotter.domain.spend import SpendCeilings
    from promptpotter.infrastructure.store.stores import build_stores
    from promptpotter.shared.identity import IdentityContext, Issuer, TenantId, UserId

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
    wire = JobSpec.of(
        stores=stores,
        job_registry=JobRegistry(tmp_path / "jobs", capacity=lambda _live: 1),
        job_id="job-a",
        hop=CycleHop(campaign_id="ds__000001", cycle_id="cycle_root"),
        limits=held,
        mode=RunMode(diag=True),
    ).model_dump_json()

    spec = JobSpec.model_validate_json(wire)
    assert spec.identity == signup
    # The round allowance crosses with the limits: dropped, a step runs to the campaign's end.
    assert spec.limits == held
    assert spec.mode == RunMode(diag=True)
    book = _arm_spend_book(
        types.SimpleNamespace(
            dashboard=types.SimpleNamespace(spend_metered=lambda _meters: _no_spend()),
            arm_spend_book=lambda _book: None,
        ),
        None,
        declared=spec.limits.ceiling,
        meters="bill",
        reserve=spec.limits.reserve,
    )
    assert (book.ceiling, book.reserve) == (held.ceiling, held.reserve)
    rebuilt = build_stores(
        spec.identity,
        projects_root=Path(spec.projects_root),
        benchmarks_root=Path(spec.benchmarks_root),
        shared_root=Path(spec.shared_root),
    )
    assert spends_the_hosts_own_key(rebuilt) is spends_the_hosts_own_key(stores) is False
    assert rebuilt.base_dir == stores.base_dir


# 3. Ceilings


def test_a_ceiling_the_operator_set_is_never_silently_unenforced(tmp_path: Path) -> None:
    """The declared ceiling lives on the ledger alone, so a launch's flag sweep cannot take it."""
    import types

    from promptpotter.application.campaign_config import load_campaign_config
    from promptpotter.application.runner.entry import _arm_spend_book
    from promptpotter.application.runner.loop import _round_bounds
    from promptpotter.domain.launch_limits import RoundsCap
    from promptpotter.domain.phases import RunPhase, StopReason
    from promptpotter.domain.run_records import RunPhaseRecord
    from promptpotter.domain.spend import SpendCeilings

    campaigns = CampaignStore(WorkspaceDir(tmp_path))
    hop = CycleHop(campaign_id="camp", cycle_id="cyc")
    campaigns.mint_cycle(hop)
    cycle_dir = campaigns.cycle_dir(hop)

    def declare(ceiling: SpendCeilings, rounds: RoundsCap | None = None) -> None:
        campaigns.write_run_limits(
            hop, ceiling, rounds=rounds, pause_at_round=None, reserve=SpendCeilings()
        )

    spent = _no_spend().model_copy(
        update={"metered_usd": 1.0, "metered_tokens": 9_000, "billed_usd": 1.0, "incurred_usd": 1.0}
    )
    observers = types.SimpleNamespace(
        dashboard=types.SimpleNamespace(spend_metered=lambda _meters: spent),
        arm_spend_book=lambda _book: None,
    )

    # A run that declared NOTHING still holds a book, which stays silent until a ceiling exists.
    control = RunControl(
        book=_arm_spend_book(
            observers,
            cycle_dir,
            declared=SpendCeilings(),
            meters="bill",
            reserve=SpendCeilings(),
        )
    )
    assert control.budget_tripped() is None
    declare(SpendCeilings(0.50, None))
    assert control.budget_tripped() == StopReason.SPEND_BUDGET, "a mid-run ceiling reached no book"
    # A launch empties the cycle's command inbox, and leaves the ceiling standing.
    CycleEventLog.open(CycleDir(cycle_dir)).append(RunPhaseRecord(run_phase=RunPhase.RUNNING))
    assert control.budget_tripped() == StopReason.SPEND_BUDGET, "a launch swept the ceiling"

    declare(SpendCeilings(None, 5_000))
    assert control.budget_tripped() == StopReason.TOKEN_BUDGET

    # A LIFT of the round cap reads as no cap, never a fall back to the config's.
    session = types.SimpleNamespace(
        state=types.SimpleNamespace(cycle_id="cyc"),
        hop=hop,
        store=types.SimpleNamespace(campaigns=campaigns),
    )
    config = load_campaign_config(
        {"optimization": {"degradation_threshold": 0.05, "max_rounds": 50}}
    )
    declare(SpendCeilings(), RoundsCap(max_rounds=3))
    assert _round_bounds(session, config) == (3, None), "a mid-run round cap reached no loop"
    assert control.budget_tripped() is None, "a lifted ceiling still governed the run"
    declare(SpendCeilings(), RoundsCap(max_rounds=None))
    assert _round_bounds(session, config) == (None, None)


def _priced_wire(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    import random

    import httpx
    import openai
    from openai.types.chat import ChatCompletion

    from promptpotter.infrastructure.llm.openai_compat import OpenAICompatibleClient, ProviderSpec
    from promptpotter.infrastructure.llm.pricing import Rate, RateTable

    async def no_wait(*_: Any) -> None:
        return None

    monkeypatch.setattr("promptpotter.infrastructure.llm.base.held_wait", no_wait)
    monkeypatch.setattr("promptpotter.infrastructure.backend.held_wait", no_wait)
    rates = RateTable({("openai", "gpt-x"): Rate(1e-6, 2e-6)})
    monkeypatch.setattr("promptpotter.infrastructure.llm.pricing.load_rates", lambda: rates)
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
        reply = ChatCompletion.model_validate(
            {
                "id": "c",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-x",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ],
                "usage": {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": prompt + completion,
                },
            }
        )
        return types.SimpleNamespace(headers={}, parse=lambda: reply)

    client = OpenAICompatibleClient(api_key="k", provider="openai", spec=ProviderSpec("OpenAI", ""))
    raw = types.SimpleNamespace(create=create)
    client._client = types.SimpleNamespace(  # type: ignore[assignment]
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(with_raw_response=raw))
    )
    return types.SimpleNamespace(
        client=client, raw=raw, create=create, flaky=flaky, hang=hang, rng=rng, request=request
    )


async def _owned_chat(
    client: Any, node: str, content: str = "x", *, kind: Any = "optimizer", max_tokens: int = 10
) -> Any:
    from promptpotter.infrastructure.llm.send_pacing import SEND_ATTEMPTS, SendBudget, under_budget
    from promptpotter.infrastructure.llm.spend_book import CallLabel

    with under_budget(SendBudget(None, attempts=SEND_ATTEMPTS)):
        return await client.chat(
            ChatRequest([{"role": "user", "content": content}], "gpt-x", max_tokens=max_tokens),
            label=CallLabel(node, kind),
        )


@contextlib.contextmanager
def _billing_on(ledger: CycleEventLog, book: Any) -> Iterator[None]:
    from promptpotter.infrastructure.llm.spend_book import spending_under
    from promptpotter.infrastructure.llm.telemetry import reset_cycle_ledger, set_cycle_ledger

    token = set_cycle_ledger(ledger)
    try:
        with spending_under(book):
            yield
    finally:
        reset_cycle_ledger(token)


def test_no_burst_of_sends_records_spend_past_its_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record of spend is only ever a BILL: an unreported send binds from its open hold."""
    import openai

    from promptpotter.domain.run_records import SpendHoldRecord
    from promptpotter.infrastructure.llm.spend_book import unreported_on
    from promptpotter.infrastructure.store.account_spend import billed_spend
    from promptpotter.shared.errors import SendRefusedError

    wire = _priced_wire(monkeypatch)
    wire.flaky[0] = True
    rng = wire.rng
    ledger = CycleEventLog(tmp_path / "ledger.jsonl")
    book = spend_book(0.05)
    ledger.bind(book)

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

    with _billing_on(ledger, book):
        outcomes = _on_jumping_clock(burst())

    records = [r for _, r in ledger.iter() if isinstance(r, TokenUsageRecord)]
    # No reply reported a cost: each record is priced at our rate, and the ceiling binds on that.
    assert all(r.cost_usd is None for r in records)
    recorded = sum(r.rate_priced_usd or 0.0 for r in records)
    assert recorded <= 0.05 + 1e-12, f"recorded ${recorded:.6f} past a $0.05 ceiling"
    assert recorded == pytest.approx(book.usd_metered)
    assert any(isinstance(o, SendRefusedError) for o in outcomes), "the ceiling never bound"
    unreported = sum(
        isinstance(o, asyncio.CancelledError | openai.APITimeoutError) for o in outcomes
    )
    left = unreported_on(ledger)
    assert unreported and left.sends == unreported
    assert len(records) == sum(not isinstance(o, BaseException) for o in outcomes)
    assert book.usd_unreported == pytest.approx(left.usd)
    assert book.usd_metered + book.usd_unreported <= 0.05 + 1e-12

    # A hard exit runs no `finally`: the hold written ahead of the call is all that says it left.
    ledger.append(
        SpendHoldRecord(
            hold_id="killed",
            kind="optimizer",
            node="killed",
            input_tokens=600,
            output_tokens=1500,
            cost_usd=0.0036,
        )
    )
    account = billed_spend([ledger.path])
    assert account.used_usd == 0.0, "a price off our rate table was read as spent"
    assert account.rate_priced_usd == pytest.approx(recorded)
    assert account.unreported_usd == pytest.approx(left.usd + 0.0036)
    assert unreported_on(ledger).sends == unreported + 1

    # A bill naming no price closes its hold in tokens alone; the money stays at the hold's bound.
    bare = CycleEventLog(tmp_path / "bare" / "ledger.jsonl")
    bare.append(
        SpendHoldRecord(
            hold_id="bare",
            kind="optimizer",
            node="bare",
            input_tokens=600,
            output_tokens=1500,
            cost_usd=0.0036,
        )
    )
    bare.append(
        TokenUsageRecord(
            kind="optimizer", node="bare", input_tokens=40, output_tokens=9, hold_id="bare"
        )
    )
    assert unreported_on(bare).usd == pytest.approx(0.0036)
    assert billed_spend([bare.path]).unreported_usd == pytest.approx(0.0036)


def test_the_reserve_holds_a_sends_bound_and_the_ceiling_what_such_sends_bill() -> None:
    """Held at its bound against both, a ceiling a few bounds wide walks one cell at a time."""
    from promptpotter.infrastructure.llm.spend_book import CallLabel, SendBound
    from promptpotter.shared.errors import SendRefusedError

    cell = CallLabel("cell", "backend")
    worst = SendBound(input_tokens=0, output_tokens=1000, usd=0.125)
    for reserve, depth in ((0.5, 4), (None, 16), (1.0, 8)):
        own = spend_book(0.5, usd_reserve=reserve)
        assert own.fits(worst, worst) == 4, "nothing billed yet: the bound holds"
        own.learn(cell, 0.03125, 100)
        own.learn(cell, 0.0, 0)
        held = own.held_at(cell, worst)
        assert own.fits(held, worst) == depth
        for _ in range(depth):
            own.hold(held, worst, "backend", what="cell")
        with pytest.raises(SendRefusedError):
            own.hold(held, worst, "backend", what="cell")
        billed = 0
        while own.exhausted() is None:
            own.release(held, worst, "backend")
            own.usd_metered += worst.usd or 0.0
            billed += 1
        assert billed == 4, "the run stops on reaching its ceiling, whatever it reserved"
        out = (depth - billed) * 0.125
        assert reserve is None or own.usd_metered + out <= reserve, (
            "a burst billed past the reserve"
        )

    # A send's room is the ceiling less EVERYTHING owed: bills, unpriced sends, the bench set-aside.
    owed = spend_book(0.5)
    owed.usd_unreported = 0.125
    assert owed.fits(worst, worst) == 3, "a send no bill priced left its room open"
    owed.set_aside(0.25, 0)
    assert owed.fits(worst, worst) == 1, "the search could spend what the bench was set aside"
    assert owed.exhausted() is None
    owed.usd_metered = 0.125
    assert owed.fits(worst, worst) == 0 and owed.exhausted() is not None


def test_a_nested_runs_send_is_held_and_billed_on_its_roots_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from promptpotter.application.scoring.formula import cell_channels_of
    from promptpotter.domain.l4.proxies import inner_cell_facts
    from promptpotter.domain.spend import SpendRollup
    from promptpotter.infrastructure.llm.spend_book import (
        Admission,
        CallLabel,
        SendBound,
        unreported_on,
    )
    from promptpotter.infrastructure.store.account_spend import billed_spend

    wire = _priced_wire(monkeypatch)
    ledger = CycleEventLog(tmp_path / "ledger.jsonl")
    book = spend_book(0.05)
    ledger.bind(book)
    book.ledger = ledger
    inner = CycleEventLog(tmp_path / "inner.jsonl")
    with _billing_on(inner, book):
        _on_jumping_clock(_owned_chat(wire.client, "inner", "nested"))
    carried = [r for _, r in ledger.iter() if isinstance(r, TokenUsageRecord)]
    own = [r for _, r in inner.iter() if isinstance(r, TokenUsageRecord)]
    assert [(r.node, r.mirrored) for r in carried] == [("inner:inner", False)]
    assert [r.mirrored for r in own] == [True]
    assert unreported_on(ledger).sends == 0
    assert billed_spend([inner.path]).sent_usd == 0.0
    assert 0.0 < book.usd_metered == pytest.approx(billed_spend([ledger.path]).sent_usd)
    cut = SendBound(input_tokens=600, output_tokens=1500, usd=0.0036)
    with _billing_on(inner, book):
        Admission(
            book, CallLabel("cut", "optimizer"), cut, cut, model="gpt-x", provider="openai"
        ).unreported()
    assert unreported_on(ledger).sends == 1
    # An inner campaign whose provider reports no bill is not a free cell: cost reads our rate.
    silent = SpendRollup()
    silent.bank(
        TokenUsageRecord(
            kind="optimizer", node="n", input_tokens=10, output_tokens=5, rate_priced_usd=0.02
        )
    )
    facts = inner_cell_facts(
        cycle_result([0.4], 0.3, []).model_copy(update={"spend": silent}), "inner"
    )
    assert facts is not None and silent.total_used_usd == 0.0
    row = MeasuredCell(
        sample_id=0,
        query="q",
        predicted="",
        ground_truth="",
        pipeline=PipelineData.from_wire(facts.model_dump(mode="json")),
    )
    assert cell_channels_of(row, None)["cost"] == pytest.approx(0.02)


def test_a_send_that_left_and_named_no_price_stays_held_at_its_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ssl

    import openai

    from promptpotter.domain.spend import TokenAccount
    from promptpotter.infrastructure.llm.spend_book import (
        Admission,
        Billed,
        CallLabel,
        SendBound,
        unreported_on,
    )

    wire = _priced_wire(monkeypatch)
    ledger = CycleEventLog(tmp_path / "ledger.jsonl")
    book = spend_book(0.05)
    ledger.bind(book)

    # A TLS record fault after the request left is resent like a 5xx, and the broken send held.
    broke = [True]

    async def create_once_broken(**params: Any) -> Any:
        if broke[0]:
            broke[0] = False
            try:
                raise ssl.SSLError("bad record mac")
            except ssl.SSLError as err:
                raise openai.APIConnectionError(request=wire.request) from err
        return await wire.create(**params)

    wire.raw.create = create_once_broken
    with _billing_on(ledger, book):
        _on_jumping_clock(_owned_chat(wire.client, "tls"))
    assert unreported_on(ledger).sends == 1

    # A reply reporting no usage is a bill that never came: the send stays held, never settled at 0.
    async def create_unbilled(**params: Any) -> Any:
        reply = (await wire.create(**params)).parse().model_copy(update={"usage": None})
        return types.SimpleNamespace(headers={}, parse=lambda: reply)

    wire.raw.create = create_unbilled
    bills = sum(isinstance(r, TokenUsageRecord) for _, r in ledger.iter())
    spent_usd = book.usd_metered
    with _billing_on(ledger, book):
        _on_jumping_clock(_owned_chat(wire.client, "unbilled"))
    assert unreported_on(ledger).sends == 2
    assert book.usd_metered == spent_usd
    assert sum(isinstance(r, TokenUsageRecord) for _, r in ledger.iter()) == bills

    # A bill naming tokens and no price is not free: held at the bound, teaching the book nothing.
    nitro = CallLabel("nitro", "optimizer")
    cut = SendBound(input_tokens=600, output_tokens=1500, usd=0.0036)
    before, held_usd, spent_usd = unreported_on(ledger), book.usd_unreported, book.usd_metered
    with _billing_on(ledger, book):
        Admission(book, nitro, cut, cut, model="m:nitro", provider="openrouter").settle(
            Billed(TokenAccount(input=600, output=100), None)
        )
    assert book.usd_metered == spent_usd
    assert book.usd_unreported == pytest.approx(held_usd + 0.0036)
    assert book.held_at(nitro, cut) == cut
    after = unreported_on(ledger)
    assert (after.sends, after.tokens) == (before.sends + 1, before.tokens)
    assert after.usd == pytest.approx(before.usd + 0.0036)


def test_one_unit_of_work_resends_from_one_budget_whichever_loop_asks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx
    import openai

    from promptpotter.infrastructure.llm.send_pacing import SEND_ATTEMPTS, SendBudget, under_budget
    from promptpotter.infrastructure.llm.spend_book import (
        CallLabel,
        spending_under,
        unbounded_spend_book,
    )
    from promptpotter.judges import call as judge_call
    from promptpotter.judges.protocol import JudgeStage

    wire = _priced_wire(monkeypatch)
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
        # A grading is a unit of its own: it opens its budget, and spends exactly that.
        attempts.clear()
        _reply, error = _on_jumping_clock(
            judge_call.ask(JudgeStage(model="gpt-x", provider="openai"), "grade", judge="j")
        )
    assert error and len(attempts) == SEND_ATTEMPTS, f"{len(attempts)} attempts on one grading"


def test_a_backend_cell_is_never_sent_again_while_it_may_still_bill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read timeout is NEVER resent: the backend still works it, and a second POST bills twice."""
    import httpx

    from promptpotter.application.scoring.cell_envelope import CellEnvelope
    from promptpotter.application.scoring.sample_measurement import cell_billing
    from promptpotter.connectors.termnorm import TermNormSession
    from promptpotter.infrastructure.backend import BackendClient
    from promptpotter.infrastructure.llm.spend_book import SendBound, replied
    from promptpotter.shared.errors import CellHaltedError

    _priced_wire(monkeypatch)
    ledger = CycleEventLog(tmp_path / "ledger.jsonl")
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

    def backend(request: httpx.Request) -> httpx.Response:
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

    monkeypatch.setattr("promptpotter.infrastructure.backend._OUTAGE_POLL_S", 0.0)
    cells = BackendClient(
        "http://termnorm",
        wire_adapter=lambda query, params: {"query": query},
        session=TermNormSession(),
        workload=types.SimpleNamespace(),  # type: ignore[arg-type]
        prompt_delivery=types.SimpleNamespace(),  # type: ignore[arg-type]
    )
    cells._http = httpx.AsyncClient(transport=httpx.MockTransport(backend))
    cell = SendBound(input_tokens=100, output_tokens=50, usd=0.01)
    wallet = spend_book()

    async def one_cell() -> Any:
        async with CellEnvelope(None, attempts=cells.cell_attempts, label="q"):
            return await cells.run_query(
                Sample(id=0, query="q", ground_truth=None),
                bound=cell,
                billed=cell_billing(pipeline_schema([]), {}, {"n": attempt}),
            )

    for ends in (httpx.ReadTimeout, CellHaltedError):
        with _billing_on(ledger, wallet), pytest.raises(ends):
            _on_jumping_clock(one_cell())
    assert len(posts) == 4, f"{len(posts)} POSTs — a cell that cannot end otherwise was sent again"
    bills = [r for _, r in ledger.iter() if isinstance(r, TokenUsageRecord)]
    assert [r.cost_usd for r in bills] == [0.0, 0.001]
    assert wallet.usd_unreported == pytest.approx(0.02 + 2 * 0.004)
    assert wallet.tokens_unreported == 2 * 150 + 2 * 60
    # The relay's 422 on an answer that failed validation is a generation: it may have billed.
    assert replied(422, headers=None, said="", answered_on=frozenset({422})).may_have_billed
    assert not replied(422, headers=None, said="").may_have_billed


def test_a_container_trial_reruns_on_the_budget_its_connector_sized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from promptpotter.application.scoring.cell_envelope import CellEnvelope
    from promptpotter.connectors import harbor
    from promptpotter.infrastructure.llm.send_pacing import SEND_ATTEMPTS
    from promptpotter.shared.errors import CellInfrastructureError

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

    async def one_cell() -> Any:
        async with CellEnvelope(None, attempts=harbor.CONNECTOR.cell_attempts, label="task"):
            return await harbor._run_episode(episode)

    with pytest.raises(CellInfrastructureError):
        _on_jumping_clock(one_cell())
    assert len(trials) == harbor.CONNECTOR.cell_attempts < SEND_ATTEMPTS, f"{len(trials)} trials"


def test_a_dspy_cell_bills_each_lm_call_and_its_providers_failure_is_a_hole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dspy
    from dspy import lm15

    from promptpotter.application.scoring.cell_envelope import CellEnvelope
    from promptpotter.application.scoring.sample_measurement import (
        cell_billing,
        emit_replayed_step_tokens,
    )
    from promptpotter.connectors import dspy_module
    from promptpotter.connectors.protocol import InProcessWorkload
    from promptpotter.infrastructure.backend import build_backend_client
    from promptpotter.infrastructure.llm.pricing import Rate, RateTable
    from promptpotter.infrastructure.llm.spend_book import SendBound
    from promptpotter.shared.errors import (
        CellHaltedError,
        CellInfrastructureError,
        CellSendRefusedError,
        ErrorCategory,
    )

    async def no_wait(*_: Any) -> None:
        return None

    monkeypatch.setattr(dspy_module, "held_wait", no_wait)
    rates = RateTable({("openai", "gpt-x"): Rate(1e-6, 2e-6)})
    monkeypatch.setattr("promptpotter.infrastructure.llm.pricing.load_rates", lambda: rates)
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

    ledger = CycleEventLog(tmp_path / "ledger.jsonl")
    wallet = spend_book(1.0)
    example = dspy.Example(question="2+2?", answer="4").with_inputs("question")

    rows: list[PipelineData] = []

    def cell(calls: int, *fails: Exception) -> dict[str, Any]:
        sends.clear()
        script[:] = fails
        program = dspy_module.DspyProgram(
            student=_Calls(calls),
            metric=lambda ex, pred: pred.answer == ex.answer,
            examples=[example],
        )
        client = build_backend_client(
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

        with _billing_on(ledger, wallet):
            data, spent = _on_jumping_clock(one_cell())
        rows.append(spent)
        return cast("dict[str, Any]", data)

    def bills() -> list[tuple[int, int]]:
        return [
            (r.input_tokens, r.output_tokens)
            for _, r in ledger.iter()
            if isinstance(r, TokenUsageRecord) and r.input_tokens
        ]

    answered = cell(2)
    assert answered[dspy_module.SCORE_KEY] == 1.0
    assert answered["step_tokens"][dspy_module.PROGRAM_NODE]["input"] == 22
    assert bills() == [(11, 3)] * 2, "a student's call left no bill of its own"
    # The engine reports tokens and no cost: each call is priced at our rate, and the ceiling binds.
    call_usd = 11 * 1e-6 + 3 * 2e-6
    priced = [
        (r.cost_usd, r.rate_priced_usd)
        for _, r in ledger.iter()
        if isinstance(r, TokenUsageRecord) and r.input_tokens
    ]
    assert priced == [(None, pytest.approx(call_usd))] * 2
    assert wallet.usd_metered == pytest.approx(2 * call_usd)
    # The ROW states that price too: the fitness cost term and a replay's ceiling read it.
    banked = rows[-1].step_tokens[dspy_module.PROGRAM_NODE]
    assert (banked.model, banked.provider) == ("gpt-x", "openai")
    assert banked.rate_priced_usd == pytest.approx(2 * call_usd)
    arm = spend_book(1.0, meters="search_incurred")
    replays = CycleEventLog(tmp_path / "replays.jsonl")
    replays.bind(arm)
    with _billing_on(replays, arm):
        emit_replayed_step_tokens(rows[-1].step_tokens, {})
    assert arm.usd_metered == pytest.approx(2 * call_usd), "a replayed DSPy cell read as free"

    with pytest.raises(CellHaltedError):
        cell(3)
    assert len(sends) == 2, f"{len(sends)} calls sent under a bound sized on two"
    assert len(bills()) == 4, "the calls a halted cell had made went unbilled"

    resent = cell(1, lm15.ServerError("boom", status=500))
    assert resent[dspy_module.SCORE_KEY] == 1.0 and len(sends) == 2

    with pytest.raises(CellInfrastructureError) as dead:
        cell(1, lm15.TransportError("refused"))
    assert dead.value.category is ErrorCategory.CONNECTION
    with pytest.raises(CellSendRefusedError) as spent:
        cell(1, lm15.BillingError("Insufficient credits", status=402))
    assert spent.value.category is ErrorCategory.PROVIDER_CREDIT


def test_a_cell_billed_send_by_send_reserves_its_bound_and_holds_only_what_is_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from promptpotter.application.scoring.cell_envelope import CellEnvelope
    from promptpotter.domain.spend import TokenAccount
    from promptpotter.infrastructure.backend import CELL, BackendClient
    from promptpotter.infrastructure.llm.litellm_sends import ReportedCost
    from promptpotter.infrastructure.llm.spend_book import (
        Billed,
        CallLabel,
        SendBound,
        answered,
        unreported_on,
    )

    # One silent send makes the REPORTED sum unknown, never the smaller price of those that spoke.
    spoke, mixed = ReportedCost(), ReportedCost()
    for cost, heard in ((0.01, (spoke, mixed)), (None, (mixed,))):
        for block in heard:
            block.count(answered(Billed(TokenAccount(input=10, output=5), cost)))
    assert (spoke.usd, mixed.usd) == (0.01, None)

    wire = _priced_wire(monkeypatch)
    turns: list[Any] = []
    cut_short = [True]

    async def episode(_workload: Any, _sample: Any, _payload: dict[str, Any]) -> dict[str, Any]:
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

    agent = BackendClient(
        "",
        wire_adapter=lambda query, params: {"query": query},
        session=types.SimpleNamespace(),  # type: ignore[arg-type]
        execution="in_process",
        in_process_run=episode,
        workload=types.SimpleNamespace(),  # type: ignore[arg-type]
        holds_own_sends=True,
        prompt_delivery=types.SimpleNamespace(),  # type: ignore[arg-type]
    )
    cell_ledger = CycleEventLog(tmp_path / "cell.jsonl")
    purse = spend_book(1.0)
    cell_ledger.bind(purse)
    whole = SendBound(input_tokens=100_000, output_tokens=50_000, usd=0.5)

    async def one_cell(sample_id: int) -> Any:
        async with CellEnvelope(None, attempts=agent.cell_attempts, label="q"):
            return await agent.run_query(
                Sample(id=sample_id, query="q", ground_truth=None),
                bound=whole,
                billed=lambda _data, _refused: (None, None),
            )

    async def cancel_mid_episode() -> None:
        cell = asyncio.ensure_future(one_cell(0))
        while len(turns) < 3:
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.05)
        cell.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cell

    with _billing_on(cell_ledger, purse):
        _on_jumping_clock(cancel_mid_episode())
    bills = [r for _, r in cell_ledger.iter() if isinstance(r, TokenUsageRecord)]
    assert [r.node for r in bills] == ["agent"] * 3
    assert purse.usd_metered == pytest.approx(sum(r.bill_or_rate_usd or 0.0 for r in bills))
    out = unreported_on(cell_ledger)
    assert out.sends == 1 and out.usd < whole.usd / 100, f"a cancel left ${out.usd} unreported"
    assert purse.usd_unreported == pytest.approx(out.usd)
    assert purse.fits(whole, whole) == 1
    # A cut cell teaches nothing; one that ran to its end teaches what such a cell bills.
    assert purse.held_at(CELL, whole) == whole
    cut_short[0] = False
    with _billing_on(cell_ledger, purse):
        _on_jumping_clock(one_cell(1))
    episode_usd = sum(
        r.bill_or_rate_usd or 0.0 for _, r in cell_ledger.iter() if isinstance(r, TokenUsageRecord)
    ) - sum(r.bill_or_rate_usd or 0.0 for r in bills)
    assert purse.held_at(CELL, whole).usd == pytest.approx(episode_usd)
    # A book armed over that ledger (a resume) holds a turn at what turns billed, not its bound.
    turn = CallLabel("agent", "backend")
    resumed = spend_book(1.0, usd_reserve=None)
    assert resumed.held_at(turn, whole) == whole
    resumed.take_up(cell_ledger)
    assert resumed.held_at(turn, whole) == purse.held_at(turn, whole) != whole


async def test_a_price_list_that_could_not_be_fetched_is_an_outage_never_an_unpriced_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.error

    from promptpotter.application.runner.termination import run_stop_reason
    from promptpotter.domain.phases import StopReason
    from promptpotter.infrastructure.llm import pricing
    from promptpotter.infrastructure.llm.base import hold_ceiling
    from promptpotter.infrastructure.llm.spend_book import spending_under, unbounded_spend_book

    fetches: list[str] = []

    def down(url: str, **_kw: Any) -> Any:
        fetches.append(url)
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(pricing.urllib.request, "urlopen", down)
    monkeypatch.setattr(pricing, "_ROUTE_OUTAGE", {})
    monkeypatch.setattr(pricing, "_ROUTE_MEMO", {})
    capped = spend_book(1.0, usd_reserve=None)
    with spending_under(capped):
        for _ in range(2):
            with pytest.raises(pricing.PriceListUnreachableError) as outage:
                await hold_ceiling("vendor/model-x", "openrouter")
    assert len(fetches) == 1, f"{len(fetches)} fetches — one per send, each a full timeout"
    assert run_stop_reason(outage.value) is StopReason.PRICE_LIST_UNREACHABLE
    with spending_under(unbounded_spend_book()):
        assert await hold_ceiling("vendor/model-x", "openrouter") is None


def test_a_send_no_rate_bounds_stops_the_run_on_one_reason_at_init_and_at_its_cell() -> None:
    from promptpotter.application.runner.termination import run_stop_reason
    from promptpotter.domain.phases import StopReason
    from promptpotter.infrastructure.llm.spend_book import SendBound, unbounded_spend_book
    from promptpotter.shared.errors import SendRefusedError

    unpriced = SendBound(input_tokens=100, output_tokens=100, usd=None, unpriced=("m (p)",))
    capped = spend_book(5.0, usd_reserve=None)
    with pytest.raises(SendRefusedError) as at_init:
        capped.refuse_unpriced(unpriced, "the next cell")
    with pytest.raises(SendRefusedError) as at_cell:
        capped.hold(unpriced, unpriced, "backend", what="the next cell")
    assert run_stop_reason(at_init.value) is StopReason.NO_RATE
    assert run_stop_reason(at_cell.value) is StopReason.NO_RATE
    assert str(at_cell.value) == str(at_init.value)
    assert capped.exhausted() is None, "no ceiling was reached: nothing was held or counted"

    open_book = unbounded_spend_book()
    open_book.refuse_unpriced(unpriced, "the next cell")
    open_book.hold(unpriced, unpriced, "backend", what="the next cell")


def test_a_controlled_arms_ceiling_is_its_searchs_incurred_cost() -> None:
    """Arms replay each other's cells: a ceiling on the BILL hands the second arm a bigger search."""
    from promptpotter.domain.run_records import TokenUsageRecord
    from promptpotter.infrastructure.llm.spend_book import (
        CallLabel,
        SendBound,
        reserved,
        spending_under,
    )
    from promptpotter.infrastructure.llm.telemetry import filed_as
    from promptpotter.shared.errors import ErrorCategory, SendRefusedError

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
    assert arm.exhausted() == ErrorCategory.SPEND_CEILING, "a sibling's cache stretched the arm"

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
        filed_as("bench"),
        reserved(CallLabel("cell", "backend"), pass_bound),
    ):
        pass
    with pytest.raises(SendRefusedError):
        small = SendBound(input_tokens=1, output_tokens=1, usd=0.02)
        fresh.hold(small, small, "optimizer", what="o")


def test_a_served_spend_states_its_own_readings_and_never_folds_a_rate_into_the_bill() -> None:
    from promptpotter.application.jobs.account_activity import _peak
    from promptpotter.domain.spend import MeteredSpend, SpendRollup
    from promptpotter.infrastructure.store.account_spend import LifetimeSpend, UserSpend

    spend = SpendRollup()
    spend.bank(
        TokenUsageRecord(kind="backend", node="b", input_tokens=100, output_tokens=10, cost_usd=0.5)
    )
    spend.bank(
        TokenUsageRecord(
            kind="optimizer",
            node="o",
            input_tokens=40,
            output_tokens=4,
            rate_priced_usd=0.02,
            cache_write_tokens=30,
        )
    )
    spend.bank(
        TokenUsageRecord(
            kind="judge", node="j", input_tokens=9, output_tokens=1, cost_usd=0.3, cached=True
        )
    )
    served = MeteredSpend.of(spend, "bill")
    assert (served.billed_usd, served.rate_priced_usd) == (0.5, 0.02), "a rate's price was billed"
    assert served.calls_rate_priced and served.rate_known
    kinds = served.kinds
    assert [kinds[k].sent for k in ("backend", "optimizer", "judge")] == [True, True, False]
    assert kinds["optimizer"].prefix.badge == "c? ·w30", "a prefix written and never read is silent"
    assert kinds["backend"].prefix.badge == "c?"

    billed_only = SpendRollup()
    billed_only.bank(
        TokenUsageRecord(kind="backend", node="b", input_tokens=1, output_tokens=1, cost_usd=0.5)
    )
    assert not MeteredSpend.of(billed_only, "bill").calls_rate_priced
    unpriced = SpendRollup()
    unpriced.bank(TokenUsageRecord(kind="backend", node="b", input_tokens=7, output_tokens=1))
    blind = MeteredSpend.of(unpriced, "bill")
    assert blind.kinds["backend"].sent and not blind.rate_known
    assert LifetimeSpend.of(UserSpend(0.5, 10, 0, rate_priced_usd=0.02)).calls_rate_priced
    assert not LifetimeSpend.of(UserSpend(0.5, 10, 0)).calls_rate_priced

    assert _peak([0.0, 0.25, 0.1]) == 0.25
    assert _peak([0.0, 0.0]) is None and _peak([0, 0]) is None


async def test_a_budget_change_leaves_the_arm_it_did_not_touch_alone(
    built_stores: Any, tmp_path: Path
) -> None:
    import types

    from promptpotter.application.commands.dispatcher import CommandDispatcher
    from promptpotter.application.commands.limits_and_queue import _apply_change_run_limits
    from promptpotter.application.jobs.quota import clamp_budget_change
    from promptpotter.application.jobs.registry import JobRegistry
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.launch_limits import RoundsCap
    from promptpotter.domain.spend import SpendCeilings
    from promptpotter.infrastructure.store.stores import build_stores
    from promptpotter.infrastructure.store.user_store import User
    from promptpotter.shared.identity import IdentityContext, Issuer, TenantId, UserId

    # A metered account: only one reserves anything, so only its job has an arm to release.
    stores = build_stores(
        IdentityContext(
            user_id=UserId("sub-9"),
            tenant_id=TenantId("sub-9"),
            issuer=Issuer("https://accounts.google.com"),
            claims={},
            capabilities=frozenset(),
        ),
        projects_root=built_stores.projects_root,
        benchmarks_root=built_stores.benchmarks_root,
    )
    hop = CycleHop(campaign_id="camp-3", cycle_id="cycle_budget0000")
    campaign = Campaign(
        campaign_id="camp-3", dataset_name="ds1", created_at="", root_cycle_id=hop.cycle_id
    )
    stores.campaigns.create_campaign(campaign)
    registry = JobRegistry(tmp_path / "jobs", capacity=lambda _live: 1)
    job = registry.request_slot(user_id="sub-9", principal_id="sub-9", dataset_name="ds1", hop=hop)
    assert job.holds_slot, "an empty box must hand out a slot, not a place in line"
    registry.mark_admitted(job.job_id, SpendCeilings(0.30, 5_000_000))

    dispatcher = CommandDispatcher(stores, registry)
    await _apply_change_run_limits(dispatcher, hop, SpendCeilings(), RoundsCap(max_rounds=3))
    await _apply_change_run_limits(dispatcher, hop, SpendCeilings(None, 1_000), None)
    held = registry.get(job.job_id)
    assert held is not None
    # The reservation moves with the ceiling: as much again for the calls out.
    assert held.reserve == SpendCeilings(0.30, 2_000), "the untouched USD reservation was released"
    standing = stores.campaigns.read_run_limits(hop)
    assert standing.reserve == held.reserve
    # The standing record is written WHOLE: a spend move must carry the round cap it did not touch.
    assert standing.rounds == RoundsCap(max_rounds=3)

    delegated = types.SimpleNamespace(
        identity=types.SimpleNamespace(
            issuer="https://accounts.google.com",
            user_id="sub-9",
            tenant_id="sub-9",
            claims={"spend_ceiling_usd": 2.0},
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
    assert caps.usd is None, "a grant became a ceiling on an arm the caller left alone"
    assert caps.tokens == 1_000


async def test_moving_one_ceiling_leaves_the_other_at_its_launch_cap(
    built_stores: Any, tmp_path: Path
) -> None:
    import types

    from promptpotter.application.commands.dispatcher import CommandDispatcher
    from promptpotter.application.commands.limits_and_queue import _apply_change_run_limits
    from promptpotter.application.jobs.registry import JobRegistry
    from promptpotter.application.runner.entry import _arm_spend_book
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.phases import StopReason
    from promptpotter.domain.spend import MeteredSpend, SpendCeilings

    hop = CycleHop(campaign_id="camp-4", cycle_id="cycle_budget0001")
    registry = JobRegistry(tmp_path / "jobs", capacity=lambda _live: 1)
    job = registry.request_slot(
        user_id="default", principal_id="default", dataset_name="ds1", hop=hop
    )
    registry.mark_admitted(job.job_id, SpendCeilings(0.30, 5_000_000))
    spent = MeteredSpend(
        meter="bill",
        metered_usd=0.10,
        metered_tokens=210_000,
        billed_usd=0.10,
        rate_priced_usd=0.0,
        calls_rate_priced=False,
        rate_known=True,
        bill_is_floor=False,
        metered_is_bill=True,
        incurred_usd=0.10,
        billed_tokens=0,
        unpriced_tokens=0,
        kinds={},
        replay_share=None,
    )
    observers = types.SimpleNamespace(
        dashboard=types.SimpleNamespace(spend_metered=lambda _meters: spent),
        arm_spend_book=lambda _book: None,
    )
    control = RunControl(
        book=_arm_spend_book(
            observers,
            built_stores.campaigns.cycle_dir(hop),
            declared=SpendCeilings(0.30, 210_000),
            meters="bill",
            reserve=SpendCeilings(),
        )
    )
    assert control.budget_tripped() == StopReason.TOKEN_BUDGET

    await _apply_change_run_limits(
        CommandDispatcher(built_stores, registry), hop, SpendCeilings(0.50, None), None
    )
    assert control.budget_tripped() == StopReason.TOKEN_BUDGET, (
        "a USD raise lifted the token ceiling"
    )


def test_a_moved_ceiling_counts_the_cycles_own_spend_once(
    built_stores: Any, tmp_path: Path
) -> None:
    """What the parent billed past the cut is not the fork's history, and binds it as spend."""
    from promptpotter.application.jobs.quota import admit_launch, clamp_budget_change
    from promptpotter.application.jobs.registry import JobRegistry
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.cycle_paths import CycleDir, CycleHop
    from promptpotter.domain.run_records import TokenUsageRecord
    from promptpotter.domain.spend import SpendCeilings
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.store.stores import build_stores
    from promptpotter.infrastructure.store.user_store import User
    from promptpotter.shared.identity import IdentityContext, Issuer, TenantId, UserId

    stores = build_stores(
        IdentityContext(
            user_id=UserId("sub-7"),
            tenant_id=TenantId("sub-7"),
            issuer=Issuer("https://accounts.google.com"),
            claims={},
            capabilities=frozenset(),
        ),
        projects_root=built_stores.projects_root,
        benchmarks_root=built_stores.benchmarks_root,
    )
    root = CycleHop(campaign_id="camp-7", cycle_id="cycle_budget0007")
    hop = CycleHop(campaign_id="camp-7", cycle_id="cycle_budget0007_fork_r1")
    stores.campaigns.create_campaign(
        Campaign(
            campaign_id="camp-7", dataset_name="ds1", created_at="", root_cycle_id=root.cycle_id
        )
    )
    stores.campaigns.mint_cycle(root)

    def bill(at: CycleHop, usd: float) -> None:
        CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(at))).append(
            TokenUsageRecord(
                kind="optimizer",
                node="l1_generate",
                input_tokens=1_000,
                output_tokens=0,
                cost_usd=usd,
            )
        )

    # $0.10 inherited, $0.10 the parent billed after the cut, $0.30 the fork's own.
    bill(root, 0.1)
    stores.campaigns.mint_fork_cycle(
        root,
        hop.cycle_id,
        ForkSpec(trigger=ForkTrigger.OPERATOR_REWIND, reason="", issued_by="", from_round=1),
        from_round=1,
    )
    bill(root, 0.1)
    bill(hop, 0.3)
    registry = JobRegistry(tmp_path / "jobs", capacity=lambda _live: 1)
    job = registry.request_slot(user_id="sub-7", principal_id="sub-7", dataset_name="ds1", hop=hop)
    registry.mark_admitted(job.job_id, SpendCeilings(0.8, None))

    caps, reserve = clamp_budget_change(
        requested=SpendCeilings(5.0, None),
        user=User(
            user_id="sub-7", tenant_id="sub-7", created_at="2026-01-01", spend_budget_usd_total=1.0
        ),
        stores=stores,
        job_registry=registry,
        hop=hop,
    )
    resumed, _ = admit_launch(
        declared=SpendCeilings(),
        user=User(
            user_id="sub-7", tenant_id="sub-7", created_at="2026-01-01", spend_budget_usd_total=1.0
        ),
        stores=stores,
        job_registry=registry,
        job_id=job.job_id,
        hop=hop,
    )
    # $1.00 allowance, $0.40 in this cycle's history and $0.10 beside it: it may reach $0.90.
    assert caps.usd == resumed.usd == pytest.approx(0.9)
    assert reserve.usd == pytest.approx(0.9)


def test_host_wallet_ceilings_hold_in_both_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TWO units, because a price needs a rate on file and a token count never does (ADR-0003 D1)."""
    import json
    import types

    from promptpotter.application.jobs.quota import QuotaExceededError, admit_launch, paid_verb
    from promptpotter.config.settings import settings
    from promptpotter.domain.spend import SpendCeilings
    from promptpotter.infrastructure.store.account_spend import sum_user_spend
    from promptpotter.infrastructure.store.user_store import User

    def _stores(
        *, issuer: str | None, ledgers: list[Path], claims: dict[str, float] | None = None
    ) -> types.SimpleNamespace:
        """`issuer` set makes this a WEB identity; the box operator has none and is unmetered."""
        return types.SimpleNamespace(
            identity=types.SimpleNamespace(
                issuer=issuer, user_id="sub-9", tenant_id="sub-9", claims=claims or {}
            ),
            campaigns=types.SimpleNamespace(
                iter_cycle_ledgers=lambda: ledgers, workspace=tmp_path / "ws"
            ),
            users=types.SimpleNamespace(get_or_create=lambda **_: free_tier),
        )

    def _ledger(name: str, *, model: str) -> list[Path]:
        path = tmp_path / name
        path.write_text(
            json.dumps(
                {
                    "record_type": "token_usage",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "model": model,
                    "provider": "openrouter",
                    "input_tokens": 400_000,
                    "output_tokens": 100_000,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return [path]

    web = "https://accounts.google.com"
    free_tier = User(user_id="sub-9", tenant_id="sub-9", created_at="2026-01-01")
    assert free_tier.spend_budget_usd_total is None
    assert free_tier.token_budget_total is None
    idle = types.SimpleNamespace(list_running=lambda *, user_id: [])

    # No override must NOT read as uncapped; the USD arm is one STEP, not the whole ceiling.
    fresh, _ = admit_launch(
        declared=SpendCeilings(),
        user=free_tier,
        stores=_stores(issuer=web, ledgers=[]),
        job_registry=idle,
        job_id="job-a",
        hop=None,
    )
    assert fresh.usd == pytest.approx(settings.FREE_TIER_LAUNCH_STEP_USD)
    assert fresh.usd < settings.FREE_TIER_SPEND_CAP_USD
    assert fresh.tokens == settings.FREE_TIER_TOKEN_CAP

    # A declaration the account cannot cover is refused at the door, never clamped to halt mid-run.
    with pytest.raises(QuotaExceededError):
        admit_launch(
            declared=SpendCeilings(10.0, None),
            user=free_tier,
            stores=_stores(issuer=web, ledgers=[]),
            job_registry=idle,
            job_id="job-a",
            hop=None,
        )

    # A cycle in flight holds its whole declared ceiling, or two launches share one remainder.
    def _sibling(*, admitted_at: str | None, reserve: SpendCeilings) -> Any:
        held = types.SimpleNamespace(
            job_id="job-b", hop=None, admitted_at=admitted_at, reserve=reserve
        )
        return types.SimpleNamespace(list_running=lambda *, user_id: [held])

    with pytest.raises(QuotaExceededError):
        admit_launch(
            declared=SpendCeilings(),
            user=free_tier,
            stores=_stores(issuer=web, ledgers=[]),
            job_registry=_sibling(
                admitted_at="2026-01-01T00:00:00+00:00",
                reserve=SpendCeilings(
                    settings.FREE_TIER_SPEND_CAP_USD, settings.FREE_TIER_TOKEN_CAP
                ),
            ),
            job_id="job-a",
            hop=None,
        )

    # One admitted but not yet STAMPED holds an amount nothing can read: refused, never zero.
    with pytest.raises(QuotaExceededError):
        admit_launch(
            declared=SpendCeilings(),
            user=free_tier,
            stores=_stores(issuer=web, ledgers=[]),
            job_registry=_sibling(admitted_at=None, reserve=SpendCeilings()),
            job_id="job-a",
            hop=None,
        )

    # `:nitro` is unpriceable BY DESIGN: the grace bounds the USD arm, the token arm the rest.
    blind, _ = admit_launch(
        declared=SpendCeilings(),
        user=free_tier,
        stores=_stores(
            issuer=web, ledgers=_ledger("blind.jsonl", model="openai/gpt-oss-20b:nitro")
        ),
        job_registry=idle,
        job_id="job-a",
        hop=None,
    )
    assert blind.usd <= settings.UNPRICED_GRACE_USD
    assert blind.usd == pytest.approx(settings.FREE_TIER_LAUNCH_STEP_USD)
    assert blind.tokens == settings.FREE_TIER_TOKEN_CAP - 500_000

    # A call is priced with its provider when recorded; dropped, namespaced models land UNPRICED.
    from promptpotter.domain.spend import TokenAccount
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.llm.pricing import Rate, RateTable
    from promptpotter.infrastructure.llm.telemetry import (
        emit_token_usage,
        reset_cycle_ledger,
        set_cycle_ledger,
    )

    rates = RateTable({("openrouter", "openai/gpt-4o"): Rate(1e-6, 2e-6)})
    monkeypatch.setattr("promptpotter.infrastructure.llm.pricing.load_rates", lambda: rates)
    bound = set_cycle_ledger(CycleEventLog(tmp_path / "priced.jsonl"))
    try:
        emit_token_usage(
            node="l1_generate",
            kind="optimizer",
            usage=TokenAccount(input=400_000, output=100_000),
            duration_s=0.0,
            model="openai/gpt-4o",
            provider="openrouter",
        )
    finally:
        reset_cycle_ledger(bound)
    priced = sum_user_spend(ledgers=[tmp_path / "priced.jsonl"])
    assert priced.unpriced_tokens == 0
    assert priced.used_usd == 0.0, "a price off our rate table was read as spent"
    assert priced.rate_priced_usd == pytest.approx(400_000 * 1e-6 + 100_000 * 2e-6)

    assert admit_launch(
        declared=SpendCeilings(),
        user=free_tier,
        stores=_stores(issuer=None, ledgers=[]),
        job_registry=idle,
        job_id="job-a",
        hop=None,
    ) == (SpendCeilings(), SpendCeilings())

    generous = User(
        user_id="sub-9",
        tenant_id="sub-9",
        spend_budget_usd_total=50.0,
        token_budget_total=50_000,
        created_at="2026-01-01",
    )

    def _delegated(user: User, declared: float | None, grant: float | None) -> float | None:
        claims = {"spend_ceiling_usd": grant} if grant is not None else {}
        return admit_launch(
            declared=SpendCeilings(declared, None),
            user=user,
            stores=_stores(issuer=web, ledgers=[], claims=claims),
            job_registry=idle,
            job_id="job-a",
            hop=None,
        )[0].usd

    assert _delegated(generous, 10.0, 2.0) == 2.0
    assert _delegated(generous, 10.0, None) == 10.0
    # The grant is a CEILING on what may be declared, never a declaration.
    thin = generous.model_copy(update={"spend_budget_usd_total": 1.0})
    assert _delegated(thin, None, 2.0) == 1.0

    # The origin resolver's call is reachable BEFORE a campaign: no launch admission has run.
    monkeypatch.setattr("promptpotter.application.jobs.registry.default_jobs_dir", lambda: tmp_path)
    spent = tmp_path / "spent.jsonl"
    spent.write_text(
        json.dumps(
            {
                "record_type": "token_usage",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "model": "openai/gpt-4o",
                "provider": "openrouter",
                "input_tokens": 1_000,
                "output_tokens": 500,
                "cost_usd": settings.FREE_TIER_SPEND_CAP_USD,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    async def turn(stores: Any) -> None:
        async with paid_verb(stores=stores, bucket="turn", hop=None):
            pass

    with pytest.raises(QuotaExceededError):
        asyncio.run(turn(_stores(issuer=web, ledgers=[spent])))
    asyncio.run(turn(_stores(issuer=None, ledgers=[spent])))

    # Every verb buying cells outside a run takes that admission INSIDE the application function.
    from promptpotter.application.diagnostics.noise_floor import measure_noise_floor
    from promptpotter.application.diagnostics.probe_reasoning import probe_reasoning
    from promptpotter.application.diagnostics.seed_screen import screen_inner_seeds
    from promptpotter.application.runner.grade_bench import grade_line_bench
    from promptpotter.shared.errors import ConflictError

    hop = CycleHop(campaign_id="camp", cycle_id="cycle_x")

    def _paid_verbs(stores: Any) -> dict[str, Any]:
        return {
            "bench": lambda: grade_line_bench(stores=stores, hop=hop),
            "noise-floor": lambda: measure_noise_floor(stores=stores, hop=hop, k=2),
            "seed-screen": lambda: screen_inner_seeds(
                stores=stores, identity=stores.identity, dataset_name="d", seeds=[1], n_samples=1
            ),
            "probe-reasoning": lambda: probe_reasoning("openai/gpt-4o", stores=stores),
        }

    exhausted = _stores(issuer=web, ledgers=[spent])
    exhausted.campaigns.cycle_dir = lambda _hop: tmp_path / "no-producer"
    for send in _paid_verbs(exhausted).values():
        with pytest.raises(QuotaExceededError):
            asyncio.run(send())

    # None bills a cycle a producer holds, and the holder is whoever holds the cycle's LOCK.
    from promptpotter.domain.phases import PauseCause, StopReason
    from promptpotter.domain.run_records import RunPhaseRecord
    from promptpotter.infrastructure.producer_lock import hold_cycle, release_cycle

    held = _stores(issuer=None, ledgers=[])
    held.campaigns = CampaignStore(WorkspaceDir(tmp_path / "held"))
    held.campaigns.mint_cycle(hop)
    cycle_dir = held.campaigns.cycle_dir(hop)
    ledger = CycleEventLog.open(CycleDir(cycle_dir))
    hold_cycle(cycle_dir)

    async def bill() -> None:
        async with paid_verb(stores=cast("Stores", held), bucket="bench", hop=hop):
            pass

    with pytest.raises(ConflictError):
        asyncio.run(bill())
    with pytest.raises(ConflictError):
        held.campaigns._guard_and_release(hop.campaign_id, "delete")
    # A declared pause opens neither door while the producer still runs it out.
    ledger.append(RunPhaseRecord.stop(StopReason.PAUSED, cause=PauseCause.INTERRUPT))
    with pytest.raises(ConflictError):
        asyncio.run(bill())
    release_cycle(cycle_dir)
    asyncio.run(bill())
    held.campaigns._guard_and_release(hop.campaign_id, "delete")


def test_an_offshoot_is_capped_at_what_its_parent_has_left() -> None:
    """A cap the fork's seed names is the operator's, ``0`` included."""
    from promptpotter.domain.spend import SpendCeilings

    parent = SpendCeilings(2.0, 9_000)
    left = ForkRemainder.of(rounds_closed=3, max_rounds=5, metered_usd=0.75, ceiling=parent)
    inherited = left.under(ConfigOverrides())
    assert (inherited.max_rounds, inherited.ceiling) == (2, SpendCeilings(1.25, None))
    own = left.under(ConfigOverrides(max_rounds=9, ceiling=SpendCeilings(0.0, 500)))
    assert (own.max_rounds, own.ceiling) == (9, SpendCeilings(0.0, 500))

    spent = ForkRemainder.of(rounds_closed=7, max_rounds=5, metered_usd=3.0, ceiling=parent)
    assert (spent.max_rounds, spent.ceiling.usd) == (1, 0.0)
    unread = ForkRemainder.of(rounds_closed=1, max_rounds=None, metered_usd=None, ceiling=parent)
    assert (unread.max_rounds, unread.ceiling.usd) == (None, None)


# 4. Spend outlives what spent it


def test_deleting_a_spent_stub_fork_does_not_un_spend_it(built_stores: Any) -> None:
    import json

    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.infrastructure.store.account_spend import (
        account_ledgers,
        bank_spend,
        sum_user_spend,
    )
    from promptpotter.infrastructure.store.layout import CycleLayout
    from promptpotter.infrastructure.store.session_pointer import cleanup_stub_fork_if_empty

    stores = built_stores
    root = "cycle_root0000"
    stub, retried = f"{root}_fork_aaaa", f"{root}_fork_bbbb"
    campaign_dir = stores.campaigns.campaign_root_dir("camp-2")
    campaign_dir.mkdir(parents=True, exist_ok=True)
    (campaign_dir / "campaign.json").write_text(json.dumps({"campaign_id": "camp-2"}), "utf-8")

    def _spent_cycle(cycle_id: str, *, cost_usd: float) -> Path:
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

    root_ledger = _spent_cycle(root, cost_usd=0.07)
    stub_ledger = _spent_cycle(stub, cost_usd=0.11)
    retried_ledger = _spent_cycle(retried, cost_usd=0.05)

    def _account_usd() -> float:
        return sum_user_spend(ledgers=account_ledgers(stores.campaigns)).used_usd

    before = _account_usd()
    assert before == pytest.approx(0.23)

    def _cleanup(cycle_id: str) -> tuple[bool, str]:
        return cleanup_stub_fork_if_empty(
            campaign_store=stores.campaigns,
            hop=CycleHop(campaign_id="camp-2", cycle_id=cycle_id),
            parent_cycle_id=root,
        )

    # A cycle the delete REFUSES must not be banked: it keeps its rows, the same money twice.
    assert not _cleanup(root)[0]
    assert _account_usd() == pytest.approx(before)

    assert _cleanup(stub)[0]
    assert not stub_ledger.exists()
    assert _account_usd() == pytest.approx(before)

    # Banking precedes the delete: a retry after a crash in between must find the tombstone.
    retried_hop = CycleHop(campaign_id="camp-2", cycle_id=retried)
    for _crashed_attempt in range(2):
        bank_spend(
            workspace=stores.campaigns.workspace,
            cycle_dirs=[stores.campaigns.cycle_dir(retried_hop)],
            campaign_id="camp-2",
            cycle_id=retried,
        )
    assert _cleanup(retried)[0]
    assert not retried_ledger.exists()
    assert _account_usd() == pytest.approx(before)

    # Banked by the destroyer itself, under BOTH `keep_results` arms, so no caller can skip it.
    stores.campaigns.delete_campaign(
        "camp-2", keep_results=False, changed_at="2026-01-02T00:00:00Z"
    )
    assert not root_ledger.exists()
    assert _account_usd() == pytest.approx(before)


def _spend_inner(
    sandbox: Path,
    *,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    mirrored: bool = False,
) -> Path:
    cycle_dir = sandbox / "tenant" / "campaigns" / "innerds__20260101-000000" / "cycles"
    cycle_dir = cycle_dir / "inner-cycle-0"
    CycleEventLog.open(CycleDir(cycle_dir)).append(
        TokenUsageRecord(
            kind="backend",
            node="llm_only",
            model="openai/gpt-oss-20b",
            provider="openrouter",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_s=1.0,
            cost_usd=cost_usd,
            mirrored=mirrored,
        )
    )
    return cycle_dir


def _tombstones(stores: Stores) -> list[dict[str, object]]:
    path = CycleEventLog.workspace_path(stores.campaigns.workspace)
    if not path.is_file():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [r for r in rows if r.get("record_type") == "spend_tombstone"]


def test_a_forwarded_inner_cycle_is_not_banked_twice(built_stores: Stores) -> None:
    """An inner sandbox is a SIBLING of the tenant tree, so no account-wide walk reaches it."""
    sandbox = inner_sandbox(built_stores, SANDBOX_CAMPAIGN, "orphaned-outer-cycle")
    # One call carried out, one not: only the second is still this sandbox's to bank.
    _spend_inner(sandbox, input_tokens=400, output_tokens=100, cost_usd=0.10, mirrored=True)
    _spend_inner(sandbox, input_tokens=600, output_tokens=100, cost_usd=0.15)

    assert reclaim_orphan_sandboxes(built_stores.projects_root) == 1
    assert not sandbox.exists()
    banked = _tombstones(built_stores)
    assert len(banked) == 1
    assert banked[0]["used_usd"] == pytest.approx(0.15)
    assert banked[0]["used_tokens"] == 700
