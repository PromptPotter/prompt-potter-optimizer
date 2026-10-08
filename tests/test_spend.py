"""Money — what is billed, and what a ceiling holds.

Owns `infrastructure/llm/` (pricing, the spend book, wire cost), `infrastructure/identity/quota.py`,
`account_spend.py`, the runner's budget gate and judge billing. Spend that reads $0, a cell billed
twice, a ceiling nothing enforces, a delete that hands the money back.
"""

from __future__ import annotations

import asyncio
import functools
import json
import types
from pathlib import Path
from typing import Any, cast
from unittest import mock

import pytest

from promptpotter.application.jobs.reaper import reclaim_orphan_sandboxes
from promptpotter.application.scoring import query_loop
from promptpotter.domain.cycle_paths import CycleDir, CycleHop, WorkspaceDir
from promptpotter.domain.results import ArmOutcome
from promptpotter.domain.run_records import SnapshotRecord, TokenUsageRecord
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.domain.validators import StopSignal
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.request import ChatRequest
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.io import write_json
from promptpotter.infrastructure.store.layout import inner_sandbox_dir, sandbox_owner_path
from promptpotter.infrastructure.store.stores import Stores

# 1. What a call costs


def test_a_rate_belongs_to_the_provider_model_pair_not_the_model_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A price is a property of WHO billed it. The table registers one model under many
    vendors at prices that differ several-fold, so a model-only lookup answers with
    somebody else's list — and the old chain did exactly that, matching across providers
    by suffix and then by bare substring.

    The row that paid for this: every optimizer call goes to OpenRouter's
    ``deepseek/deepseek-v4-flash``, which is character-for-character DeepSeek's own
    first-party key at $0.14/$0.28 against OpenRouter's listed $0.088/$0.176. ``None`` is
    the honest answer; it arms the "USD cap inactive" warning instead of quoting a 1.6x
    guess as a measurement.

    This docstring used to add "OpenRouter returns no wire cost on that route", and that
    was never true — the route reports ``cost`` on every call and our own client dropped
    it before anyone downstream could read it (see
    ``test_wire_cost_reaches_the_response_or_nothing_prices_the_optimizer``). The estimate
    was the only number because of a bug on THIS side, and an explanation naming upstream
    is why nobody went looking for it. The rule below is unaffected: a wire cost overrides
    the table, and where there is none the pair-keyed lookup is still what answers.

    Driven from a FIXTURE table, not the shipped one. The claim is about the resolution
    rule, and pinning it to today's prices makes it assert two things at once — the first
    version read the operator's local ``.promptpotter/rates.json`` (2519 keys) and would
    have gone red against the checked-in bundled floor (2253, no ``deepseek-v4-flash``)
    on CI and on every fresh clone, with its own "table unavailable" guard unable to see
    the difference. Upstream re-keying a model must not be able to red this.
    """
    import promptpotter.infrastructure.llm.pricing as spend_mod

    table = {
        # The defect in one row: DeepSeek's own first-party key, character-for-character
        # OpenRouter's model id, and OpenRouter has NO key of its own here — which is
        # exactly the shipped table's shape for this model, and why the old chain's
        # cross-provider match had something wrong to reach for.
        "deepseek/deepseek-v4-flash": spend_mod.Rate(0.00000014, 0.00000028),
        "openrouter/openai/gpt-oss-20b": spend_mod.Rate(0.00000004, 0.00000015),
        # Groq answers a provider-less model id while the table keys it prefixed.
        "groq/openai/gpt-oss-120b": spend_mod.Rate(0.00000015, 0.0000006),
        # The bare namespace the table keeps first-party OpenAI/Anthropic in, and the only row
        # here carrying cache tiers — reads at 0.1x input, writes at 1.25x.
        "gpt-4o": spend_mod.Rate(0.0000025, 0.00001, 0.000003125, 0.00000025),
    }
    monkeypatch.setattr(spend_mod, "load_rates", lambda: table)
    lookup_rate, compute_usd = spend_mod.lookup_rate, spend_mod.compute_usd

    # 1. The defect. The bare key exists and is a DIFFERENT vendor's price, so a
    #    provider-less lookup answers with it — and asking AS OpenRouter must refuse
    #    rather than quote it, even though OpenRouter's own key is right there.
    assert lookup_rate("deepseek/deepseek-v4-flash") == table["deepseek/deepseek-v4-flash"]
    assert lookup_rate("deepseek/deepseek-v4-flash", "openrouter") is None

    # 2. A routing suffix selects another upstream provider with its own rate (measured ~6x
    #    on a nitro route), so the base-model price is not an approximation of it.
    assert lookup_rate("openai/gpt-oss-20b:nitro", "openrouter") is None

    # 3. Composition still resolves what it should: the provider-prefixed convention...
    assert lookup_rate("openai/gpt-oss-20b", "openrouter") == table["openrouter/openai/gpt-oss-20b"]
    #    ...the wire echoing its own provider back inside the model id...
    assert lookup_rate("groq:openai/gpt-oss-120b", "groq") == lookup_rate(
        "openai/gpt-oss-120b", "groq"
    )
    #    ...and the bare namespace the table keeps first-party OpenAI/Anthropic in.
    assert lookup_rate("gpt-4o", "openai") == table["gpt-4o"]

    # 4. And the provider reaches the pricing call, not just the lookup beneath it.
    assert compute_usd("deepseek/deepseek-v4-flash", 10, 10, provider="openrouter") is None
    assert compute_usd("deepseek/deepseek-v4-flash", 10, 10) is not None

    # 5. A cache read is a SUBSET of the input count, so it is re-priced OUT of it rather than
    #    added on top — 1000 input of which 800 cached bills 200 cold + 800 at the read tier.
    cold = compute_usd("gpt-4o", 1000, 0, provider="openai")
    hit = compute_usd("gpt-4o", 1000, 0, provider="openai", cache_read_tokens=800)
    assert cold == pytest.approx(1000 * 0.0000025)
    assert hit == pytest.approx(200 * 0.0000025 + 800 * 0.00000025)
    assert hit < cold
    #    A model whose table row carries no cache tier bills the read at the INPUT price, so the
    #    number is UNCHANGED rather than silently discounted by a rate nobody sourced.
    assert compute_usd(
        "openai/gpt-oss-20b", 1000, 0, provider="openrouter", cache_read_tokens=800
    ) == pytest.approx(compute_usd("openai/gpt-oss-20b", 1000, 0, provider="openrouter"))


def test_wire_cost_reaches_the_response_or_nothing_prices_the_optimizer() -> None:
    """The provider's own price must survive the client, because on the optimizer route it is
    the ONLY price there is: the rate table has no ``openrouter/deepseek/*`` key and correctly
    refuses to quote DeepSeek's first-party number for an OpenRouter call, so a dropped wire
    cost leaves the call unpriced with no error anywhere.

    That is what happened. ``call.py`` read ``response.usage["cost"]`` while the client built
    ``usage`` from four token keys and never copied it, so every optimizer row on disk carried
    ``cost_usd: null``, the optimizer bucket's ``used_usd`` read $0.00 in every cycle ever run, and
    ``store/account_spend.py::record_cost_usd`` floored each call to 0.0 — a USD ceiling that could not
    see the half of the bill it was capping. Nothing raised; the numbers were simply absent.

    Silent because the shape is right and only the value is missing: an unpriced call and a
    free call are the same row.
    """
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

    # OpenRouter's real shape: `cost` rides as an EXTRA on the usage object (the SDK's models
    # are extra="allow"), beside `cost_details`/`is_byok`. Measured live on this route.
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

    # A provider that reports nothing (Groq, OpenAI) must yield None, not 0.0 — 0.0 is a
    # measurement and would silently satisfy the cap it should have escalated to the table.
    unpriced = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
    assert reply_cost(completion(unpriced)) is None
    assert reply_cost(completion(None)) is None

    # A schema-repair retry bills BOTH round-trips, same contract the token sums follow.
    assert _billed_cost(1e-05, 2e-05) == pytest.approx(3e-05)
    # ...and one silent half must not drag a real number down to nothing.
    assert _billed_cost(None, 2e-05) == pytest.approx(2e-05)
    assert _billed_cost(1e-05, None) == pytest.approx(1e-05)
    assert _billed_cost(None, None) is None


async def test_a_cell_that_ran_without_a_grade_still_bills_what_it_spent() -> None:
    """A connector that runs a paid episode and gets no verdict raises ``CellUnscoreableError``,
    and ``measure_sample`` is its one catcher. The success path bills ``step_tokens``; the raise
    had nothing to bill with, so a verifier timeout banked the hole and dropped the agent's spend —
    the campaign ceiling under-counted and the sidebar read $0.00 for a cell that cost money."""
    from promptpotter.application.scoring.formula import compile_scorer
    from promptpotter.application.scoring.sample_measurement import measure_sample
    from promptpotter.domain.run_records import TokenUsageRecord
    from promptpotter.infrastructure.backend import BackendClient
    from promptpotter.infrastructure.llm import telemetry
    from promptpotter.infrastructure.llm.spend_book import spending_under, unbounded_spend_book
    from promptpotter.shared.errors import CellUnscoreableError, ErrorCategory

    spent = {"agent": {"input": 1200, "output": 300, "estimated": False, "cost_usd": 0.0076}}

    async def _ran_ungraded(*_args: Any) -> dict[str, Any]:
        raise CellUnscoreableError("verifier timed out", spent=spent, step_timings={})

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
        pipeline_schema=types.SimpleNamespace(nodes=[]),
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

    assert row["error_category"] == ErrorCategory.UNSCOREABLE
    billed = [r for r in ledger.records if isinstance(r, TokenUsageRecord)]
    assert [(r.cost_usd, r.input_tokens, r.cached) for r in billed] == [(0.0076, 1200, False)]


def _counting_client(reply: str) -> tuple[Any, list[int]]:
    """One provider reached through the real client's send seam — only its SDK is stubbed — and
    the round-trips it took."""
    from openai.types.chat import ChatCompletion

    from promptpotter.infrastructure.llm.openai_compat import OpenAICompatibleClient

    calls: list[int] = []

    async def create(**_kw: Any) -> Any:
        calls.append(1)
        # The provider's own prefix-cache discount rides `cached_tokens`. A judge prompt is the most
        # cacheable shape we send — the rubric is a module constant, so most of it is byte-identical
        # on every cell — so a stub reporting none cannot catch the metering dropping it.
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

    client = OpenAICompatibleClient(api_key="k", provider="p", display_name="P")
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
    """Grade one identical cell twice through the real evaluator, and report the round-trips and
    what they metered."""
    from factories import measurement

    from promptpotter.infrastructure.llm import spend_book
    from promptpotter.infrastructure.store.stores import LLMReuseCache
    from promptpotter.judges import build_evaluators
    from promptpotter.judges import call as judge_call
    from promptpotter.judges.protocol import JudgeSpec, JudgeStage

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
            row = measurement(sample_id=0, fitness=0.0)
            row["query"], row["predicted"], row["ground_truth"] = "who?", "Ada", "Ada"
            last = await ev.compute(result=row, schema=None)
    return calls, metered, last


async def test_a_second_grading_of_one_comparison_is_not_re_billed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stale-data ladder re-enters ``measure_sample`` twice more per degraded sample, and two
    candidates whose mutation did not change the answer present the grader an identical comparison
    — so without reuse a judged campaign pays for the same verdict over and over, invisibly.

    Both halves are asserted, because each fails on its own. The provider is reached ONCE, and the
    replay is still METERED — flagged ``cached`` — since the cell was still graded and grading cost
    must stay invariant to our cache history, exactly as ``llm_call`` and ``emit_replayed_step_tokens``
    keep it."""
    calls, metered, score = await _grade_twice(tmp_path, monkeypatch, reply="A")

    assert score == 1.0, "the replayed reply must grade identically, not merely cheaply"
    assert len(calls) == 1, f"an identical comparison re-billed the provider: {len(calls)}x"
    assert [m.get("cached", False) for m in metered] == [False, True], (
        "a served grading went unmetered"
    )
    assert {m["kind"] for m in metered} == {"judge"}, "grading spend landed outside its own bucket"

    # ``cached`` and ``cache_read_tokens`` are OPPOSITE facts: the first says OUR cache served the
    # reply, the second that the PROVIDER's prefix cache discounted a call that went out. The
    # judge's rubric is a module constant, so most of its prompt is byte-identical on every cell —
    # dropped, its whole bucket reads 0% forever. Asserted on the WIRE call.
    wire = metered[0]["usage"]
    assert wire.cache_read == 8
    assert wire.input == 11, "cache_read is a SUBSET of input, never a deduction from it"

    # Emptiness is a TRANSIENT provider failure, and the key is the prompt hash — so storing one
    # makes it permanent for every future grading of that comparison, in a tenant-global tree
    # that outlives the run and the campaign both.
    blank_calls, _, blank = await _grade_twice(tmp_path / "blank", monkeypatch, reply="   ")
    assert blank is None, "an unreadable grading is an absent verdict, never a zero"
    assert len(blank_calls) == 2, "an empty reply was cached and replayed as if it were a verdict"


def test_a_field_a_client_cannot_send_is_refused_and_the_gateway_wire_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A request field a client cannot carry must refuse, never evaporate: dropped, a `seed` or
    `reasoning_effort` arm scores against an identical call and the round credits the axis.

    A banked reply is keyed on the node's config (`hash_call`), never the wire, so a wire that
    moved under an unchanged key replays the old request's answer — the gateway body is pinned
    whole, order included."""
    from pydantic import BaseModel, Field

    from promptpotter.infrastructure.llm import base, openai_compat
    from promptpotter.infrastructure.llm.anthropic import AnthropicClient
    from promptpotter.infrastructure.llm.pricing import PriceTier, RateCeiling
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
    monkeypatch.setattr(openai_compat, "rate_ceiling", ceiling)
    raw = types.SimpleNamespace(with_raw_response=types.SimpleNamespace(create=create))
    messages = [{"role": "user", "content": "q"}]

    def send(client: Any, request: ChatRequest) -> None:
        async def _send() -> None:
            bind_spend_book(unbounded_spend_book())
            await client.chat(request, label=CallLabel("l1", "optimizer"))

        asyncio.run(_send())

    claude = AnthropicClient(api_key="k")
    claude._client = types.SimpleNamespace(messages=raw)  # type: ignore[assignment]
    with pytest.raises(_SentError):
        send(claude, ChatRequest(messages, "m", response_model=_Reply, top_p=0.9))
    assert len(wire) == 1
    for unsendable in ({"seed": 7}, {"reasoning_effort": "low"}, {"route_order": ["Alibaba"]}):
        with pytest.raises(ValueError, match=next(iter(unsendable))):
            send(claude, ChatRequest(messages, "m", **unsendable))
    assert len(wire) == 1, "a refused request reached the provider"

    gateway = openai_compat.OpenAICompatibleClient(
        api_key="k", provider="openrouter", display_name="OpenRouter", gateway=True
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
            "reasoning_effort": "low",
            "seed": 7,
            "top_p": 0.9,
            "extra_body": {
                "usage": {"include": True},
                "provider": {**priced, "order": ["Alibaba"], "allow_fallbacks": True},
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
    """Finishes LATER samples FIRST. With uniform latency two slots complete in submission order
    anyway, so the test would pass without the loop ordering anything.

    ``slowest_last`` inverts that, and the barrier check needs it: when the FIRST sample is the
    last to finish, its group-mates have already drained by the time it is absorbed, so a sliding
    refill and a group barrier are indistinguishable. Absorbing the first while the rest still run
    is the only arrangement in which the two differ."""

    def __init__(self, n: int, *, slowest_last: bool = False) -> None:
        self.slowest_last = slowest_last
        self.n = n
        self.calls: list[int] = []
        self._inflight = 0
        # How many were ALREADY running as each call began — the backend's own witness that a
        # window physically opened, which the walk's declared depth cannot supply. Read its PEAK
        # only: a later reading also falls when a peer retires early, so on a loaded box the tail
        # of this series measures the scheduler rather than the arming.
        self.entries: list[int] = []

    async def measure(self, sample: Sample, session: Any, *, pipeline_params: Any = None) -> Any:
        self.calls.append(sample.id)
        return await self.hold(sample)

    async def hold(self, sample: Sample, pace: float = 0.01) -> Any:
        """One call's worth of backend capacity — a PoBB backfill spends it as a cell does."""
        self._inflight += 1
        self.entries.append(self._inflight)
        rank = sample.id if self.slowest_last else (self.n - sample.id + 1)
        try:
            await asyncio.sleep(rank * pace)
        finally:
            self._inflight -= 1
        return {
            "sample_id": sample.id,
            "query": sample.query,
            "ground_truth": sample.ground_truth,
            "predicted": sample.ground_truth,
            "fitness": 1.0,
            "objective": 1.0,
            "cached": False,
            "error": None,
            "pipeline_data": {"total_time": 0.5},
        }


class _CutAfter:
    """Stands in for the PoBB gate on the same seam, firing where the test picks rather than
    where a posterior has to be coaxed to."""

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
    """A session's scoring whose scorer reads each row's own stamped grades."""
    scorer = types.SimpleNamespace(
        fitness=lambda r: r["fitness"], objective=lambda r: r["objective"]
    )
    return types.SimpleNamespace(scorer=scorer, require_scorer=lambda: scorer)


def _walk_over(
    dataset: list[Sample],
    session: Any,
    *,
    checks: list[Any],
    measured: Any = None,
    on_sample_starting: Any = None,
    on_taken: Any = None,
    cached: dict[int, Any] | None = None,
    banked: dict[int, Any] | None = None,
    rereads: list[int] | None = None,
) -> Any:
    """A walk as the gateway opens one, over stubbed persistence: ``cached`` is the archive it
    replays from, ``banked`` collects what a stop keeps for its resumption, ``rereads`` the cells the
    campaign already priced."""
    from promptpotter.shared.instrument import measured_candidate_context

    class _Recorder:
        def scores(self, rows: Any) -> Any:
            return {"accuracy": 1.0}

        def persist(self, rows: Any) -> Any:
            return on_taken(rows) if on_taken else self.scores(rows)

        def bank(self, rows: Any, kept: Any) -> None:
            if banked is not None:
                banked.update({r["sample_id"]: r for r in kept})

        def close(self, rows: Any, scores: Any) -> None:
            return None

    ctx = query_loop.QueryLoopState(
        search_point=JobSearchPoint(),
        session=session,
        run_id="walk_run",
        cached_sample_results=dict(cached or {}),
        on_sample_scored=None,
        sample_index=None,
        deprecated_samples={},
        recorder=_Recorder(),
        claim_cell=None,
        cell_keys={s.id: f"cell_{s.id}" for s in dataset},
        counted={f"cell_{sid}" for sid in rereads or ()},
        rereads=frozenset(rereads or ()),
    )
    return query_loop.Walk(
        dataset, ctx, checks, on_sample_starting, measured_candidate_context(measured)
    )


async def _stopped_by_operator(phase: Any) -> str | None:
    """Run a scoring phase; the reason if a pause or a spend ceiling ended it."""
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
    """``stall`` names a sample whose call lands well after every other; ``skip_at`` is a skip on
    record from before a stop. Every cell is admitted at, and bills, $1, against ``cap_usd``."""
    from factories import pobb_knobs

    from promptpotter.application.optimizers.potter.pobb.checks import PoBBCheck
    from promptpotter.application.optimizers.potter.race import CatchUpPool
    from promptpotter.application.runner.termination import BudgetGate
    from promptpotter.domain.pipeline_schema import WebSpendBound
    from promptpotter.domain.spend import TokenAccount
    from promptpotter.infrastructure.llm.spend_book import (
        Billed,
        CallLabel,
        SendBound,
        SpendBook,
        admitted,
        spending_under,
    )

    backend = _OrderedFakeBackend(len(dataset))
    banked: dict[int, Any] = {}
    depths: list[int] = []
    committed: list[int] = []
    returned: list[int] = []
    book = SpendBook(
        usd_cap=lambda: cap_usd,
        tokens_cap=lambda: None,
        usd_reserve=lambda: cap_usd,
        tokens_reserve=lambda: None,
        meters="bill",
    )
    dollar = SendBound(input_tokens=0, output_tokens=0, usd=1.0)

    async def _measure(sample: Sample, session: Any, *, pipeline_params: Any = None) -> Any:
        with admitted(CallLabel("cell", "backend"), dollar, model=None, provider=None) as bill:
            if sample.id == stall:
                await asyncio.sleep(0.3)
            row = await backend.measure(sample, session, pipeline_params=pipeline_params)
            returned.append(sample.id)
            bill.settle(Billed(TokenAccount(), 1.0))
        return row

    gate = BudgetGate(book=book)

    def _backfill(sp: Any, sample: Sample, prior_id: str) -> Any:
        call = asyncio.ensure_future(backend.hold(sample, backfill_pace))

        def _commit() -> list[Any]:
            committed.append(sample.id)
            return [call.result()]

        return call, _commit, call.cancel

    # A parent measured on none of the round's cells, so every cell owes it one catch-up call.
    priors = CatchUpPool(
        PoBBCheck(pobb_knobs(), n_min=6, n_samples=len(dataset), ruler=None), _backfill
    )
    priors.admit("parent", [], JobSearchPoint())
    # The one seam stubbed; the window, cursors, checkpoints and discard are shipping code.
    with mock.patch.object(query_loop, "measure_sample", _measure), spending_under(book):
        session = types.SimpleNamespace(
            scoring=_row_scoring(),
            state=types.SimpleNamespace(ledger=None),
            pause_check=lambda: pause_after_call is not None and len(returned) >= pause_after_call,
            skip_check=None,
            skip_consume=None,
            budget_tripped=gate.tripped,
            spend_used=lambda: book.usd_spent,
            sample_lookahead_check=lambda: armed,
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
            on_sample_starting=lambda q, i, t, sid, depth, horizon: depths.append(depth),
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
        "rows": walk.results,
        "stop_reason": stopped or walk.outcome.ended_on,
        "calls": list(backend.calls),
        "entries": list(backend.entries),
        "committed": committed,
        "banked": banked,
        "priced": set(walk.ctx.counted),
        "returned": returned,
        "billed": book.usd_spent,
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
    """Several candidates' walks driven as one round, as the measurement drives them. Each
    backend call is logged under the walk whose context it ran in. ``pause_after_call`` presses
    pause as that call returns."""
    from promptpotter.shared.instrument import MeasuredCandidate, measured_candidate

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

    def _taken(walk: int, rows: list[Any]) -> dict[str, float]:
        events.append(("absorb", walk, rows[-1]["sample_id"]))
        return {"accuracy": 1.0}

    session = types.SimpleNamespace(
        scoring=_row_scoring(),
        state=types.SimpleNamespace(ledger=None),
        pause_check=lambda: flag["pause"],
        skip_check=None,
        skip_consume=None,
        budget_tripped=None,
        spend_used=None,
        sample_lookahead_check=lambda: armed,
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
        "rows": [walk.results for walk in walks],
        "stops": [stopped or walk.outcome.ended_on for walk in walks],
        "absorbed": [(w, sid) for kind, w, sid in events if kind == "absorb"],
        "events": events,
        "calls": list(backend.calls),
        "peak": max(backend.entries),
    }


async def test_sample_lookahead_changes_the_bill_and_never_the_record(tmp_path: Path) -> None:
    """Look-ahead must move the wall clock and NOTHING a measurement is read from.

    Silent by construction: if the second in-flight sample could reach the archive, or shift where a
    candidate is cut, one campaign would record different rows under a throughput toggle with
    nothing raised — and the arming would become a steer, forcing a babysat stamp."""
    dataset = [Sample(id=i, query=f"q{i}", ground_truth=str(i % 2)) for i in range(1, 9)]

    # 1. A candidate that runs to completion records byte-identical rows, and costs the same.
    d1 = await _walk(dataset, armed=1, cut_at=None)
    d2 = await _walk(dataset, armed=2, cut_at=None)
    assert d2["max_depth"] == 2, "arming did not open the window — the rest proves nothing"
    assert d1["max_depth"] == 1
    assert d1["rows"] == d2["rows"]
    assert d1["calls"] == d2["calls"]

    # 2. Absorption is in WALK order even though the backend finished later samples first.
    assert [r["sample_id"] for r in d2["rows"]] == [s.id for s in dataset]

    # 3. A candidate cut mid-walk is cut at the same sample and records the same rows — the
    #    in-flight acquisition is discarded, not appended, and not error-filled twice.
    c1 = await _walk(dataset, armed=1, cut_at=4)
    c2 = await _walk(dataset, armed=2, cut_at=4)
    assert c2["max_depth"] == 2, "window never opened on the cut walk"
    assert c1["stop_reason"] == c2["stop_reason"] == "stop_rule"
    assert c1["rows"] == c2["rows"]

    # 4. …and the only difference is on the bill: AT MOST one extra call, sometimes none (awaiting
    #    an already-finished task does not yield, so the slot is cancelled before its request went
    #    out). Equality here would pin a scheduling accident; two would mean the window overgrew.
    assert 0 <= len(c2["calls"]) - len(c1["calls"]) <= 1

    # 5. The BACKEND's ceiling binds, not the request: a connector declaring 1 has nothing to
    #    overlap, and the operator cannot arm past what one declaring 2 will hold. This is the
    #    half that used to be answered by `execution != "remote_http"` — a transport fact
    #    standing in for a cost one, which pinned every in-process backend to 1 including the
    #    one whose sample is a whole nested campaign.
    assert (await _walk(dataset, armed=4, cut_at=None, max_cells=1))["max_depth"] == 1
    assert (await _walk(dataset, armed=4, cut_at=None, max_cells=2))["max_depth"] == 2

    # 6. What a cut discards is bounded by the stop rule's HORIZON, not by the depth: armed far
    #    past the cut, the walk launches one cell beyond the earliest row the rule could fire at.
    deep = await _walk(dataset, armed=8, cut_at=4, max_cells=8)
    assert deep["stop_reason"] == "stop_rule"
    assert deep["rows"] == c1["rows"]
    assert 0 <= len(deep["calls"]) - len(c1["calls"]) <= 1
    assert len(c1["calls"]) == 4, "an unarmed walk launched before the cell ahead was decided"
    # …and a remote call it discards still lands on the bill: the backend finishes it and the
    # provider charges for it whether or not anyone waits, so cancelling only lost the record.
    assert sorted(deep["returned"]) == sorted(deep["calls"])
    assert deep["billed"] == len(deep["calls"])

    # 7. A PoBB catch-up call is a whole inner campaign on L4, so it holds a slot like a cell —
    #    the depth bounds everything the walk has out, which is what keeps the connector's
    #    ceiling a memory bound. It may START early, but it lands in the archive only for a cell
    #    the walk absorbed, in walk order: one written for a cell past the cut would be a row
    #    the unarmed walk never measured, replayed later as if it had been.
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

    # 8. A round's candidates walk AT ONCE and decide IN TURN: a later walk may measure ahead
    #    of the ones before it, but is taken, cut and persisted only in its turn — so the rows,
    #    the cuts and their order are the serial round's. The depth bounds the ROUND, not each
    #    walk, or three walks would hold three times the declared ceiling.
    serial = await _round(dataset, armed=1, cuts=[4, None, 6])
    fanned = await _round(dataset, armed=4, cuts=[4, None, 6])
    assert serial["peak"] == 1
    assert fanned["peak"] <= 4
    assert fanned["rows"] == serial["rows"]
    assert fanned["stops"] == serial["stops"] == ["stop_rule", None, "stop_rule"]
    assert fanned["absorbed"] == serial["absorbed"]
    # A cell the one loop launched runs as the candidate it measures, or an L4 inner campaign is
    # filed under the candidate on turn. First cells fastest, so the second walk's calls go out
    # while the first still has cells to take.
    ahead = await _round(dataset, armed=4, cuts=[None, None], slowest_last=True)
    assert ahead["absorbed"] == [(w, s.id) for w in (0, 1) for s in dataset]
    last_of_first = max(i for i, e in enumerate(ahead["events"]) if e[:2] == ("absorb", 0))
    assert any(e[:2] == ("call", 1) for e in ahead["events"][:last_of_first]), (
        "no later candidate measured ahead — the identity check below proves nothing"
    )
    for w in (0, 1):
        calls = sorted(sid for kind, who, sid in ahead["events"] if kind == "call" and who == w)
        assert calls == [s.id for s in dataset], f"walk {w}'s calls ran under another identity"
    # A stop keeps what is already back and owes nothing: the call that returned as the pause was
    # pressed is absorbed, not discarded and paid again on resume.
    paused = await _round(dataset, armed=1, cuts=[None], pause_after_call=3)
    assert paused["stops"] == ["graceful"]
    assert len(paused["rows"][0]) == len(paused["calls"]) == 3
    # …and starts nothing while it does: a pause is a promise to spend nothing more. A catch-up
    # no one started stays unstarted, and waiting on one already out launches no cell.
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
    # What came back behind a head still out is banked, not dropped: the resumed walk replays it
    # rather than paying again, and still takes the uninterrupted walk's rows in its order.
    head = dataset[0].id
    stopped = await _walk(
        dataset, armed=4, max_cells=4, cut_at=None, pause_after_call=3, stall=head
    )
    assert stopped["rows"] == [] and set(stopped["banked"]) == set(stopped["returned"])
    # Paid already: the resumed walk meets each as a replay, and must not meter it as search again.
    assert stopped["priced"] == {f"cell_{sid}" for sid in stopped["banked"]}
    resumed = await _walk(dataset, armed=4, max_cells=4, cut_at=None, cached=stopped["banked"])
    assert not set(resumed["calls"]) & set(stopped["banked"]), "a banked cell was paid again"
    assert [r["sample_id"] for r in resumed["rows"]] == [s.id for s in dataset]
    # …but never one past where a rule could cut: the serial walk would not have measured it.
    capped = await _walk(dataset, armed=4, max_cells=4, cut_at=2, pause_after_call=2, stall=head)
    assert set(capped["banked"]) == {head, dataset[1].id}
    # A spend ceiling binds BEFORE a call rather than after it: every cell out is counted at its
    # bound, so a window of four never carries the run past the ceiling it was checked against.
    ceiling = await _walk(dataset, armed=4, max_cells=4, cut_at=None, cap_usd=5.0)
    assert ceiling["stop_reason"] == "spend_budget"
    assert ceiling["billed"] == 5.0
    # A skip made before a stop outlives it: the resumed round reads the last decision the ledger
    # holds for each candidate, replays it at the same row, and launches nothing the skip spared.
    from promptpotter.application.runner.measurement import _skips_on_record
    from promptpotter.infrastructure.ledger import CycleEventLog

    ledger = CycleEventLog(tmp_path / "ledger.jsonl")
    for cid, n, reason in (
        ("a", 3, "skipped"),
        ("b", 2, "skipped"),
        ("b", 8, "measured"),
        ("c", 4, "skipped"),
    ):
        scores = {"candidate_id": cid, "scored_samples": n, "outcome": reason}
        ledger.append(SnapshotRecord(event="candidate_scored", round=2, payload={"scores": scores}))
    assert _skips_on_record(ledger, 2) == {"a": 3, "c": 4}
    replayed = await _walk(dataset, armed=4, max_cells=4, cut_at=None, skip_at=3)
    assert replayed["stop_reason"] == "skip" and len(replayed["rows"]) == 3
    assert sorted(replayed["calls"]) == [s.id for s in dataset[:3]]


async def test_a_resumed_arm_re_reads_its_cells_and_still_reaches_its_bench(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resume re-walks the origin, and the round it stopped in, over cells its own ledger already
    priced. Metered again, a controlled arm pays for each twice; near its ceiling it halts in run
    init, and a spent search ceiling stops its bench pass too, so its selection is never graded.
    Silent: the halt reads as the arm's budget, and the headline is simply missing. The stopped
    round's optimizer and judge calls replay too, and are priced once per campaign the same way."""
    from promptpotter.application.bench import llm_call as call_mod
    from promptpotter.application.runner.termination import BudgetGate
    from promptpotter.domain.pipeline_schema import WebSpendBound
    from promptpotter.domain.run_records import TokenUsageRecord
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.llm import telemetry
    from promptpotter.infrastructure.llm.response import LLMResponse
    from promptpotter.infrastructure.llm.spend_book import SpendBook, spending_under
    from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_priced_keys
    from promptpotter.infrastructure.store.stores import LLMReuseCache
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
    step = {"solve": {"input": 10, "output": 5, "cost_usd": 0.01}}
    banked = {
        s.id: {
            "sample_id": s.id,
            "query": s.query,
            "ground_truth": "a",
            "predicted": "a",
            "fitness": 1.0,
            "objective": 1.0,
            "error": None,
            "pipeline_data": {"step_tokens": step},
        }
        for s in dataset
    }
    # The ceiling already spent: no cell fits, and the gate reads it as reached.
    book = SpendBook(
        usd_cap=lambda: 0.02,
        tokens_cap=lambda: None,
        usd_reserve=lambda: 0.02,
        tokens_reserve=lambda: None,
        meters="search_incurred",
        usd_spent=0.02,
    )
    ledger = CycleEventLog.open(CycleDir(tmp_path / "cycle"))
    ledger.bind(book)
    session = types.SimpleNamespace(
        scoring=_row_scoring(),
        state=types.SimpleNamespace(ledger=None),
        pause_check=lambda: False,
        skip_check=None,
        skip_consume=None,
        budget_tripped=BudgetGate(book=book).tripped,
        sample_lookahead_check=lambda: 1,
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
    # The selection's bench pass, filed beside the arm's search ceiling.
    bench = _walk_over(dataset, session, checks=[], cached=banked)
    token = telemetry.set_cycle_ledger(ledger)
    try:
        with spending_under(book):
            stopped = await _stopped_by_operator(
                query_loop.run_walks([walk], session, keep_cut=False)
            )
            with telemetry.filed_as("bench"):
                await query_loop.run_walks([bench], session, keep_cut=False)
            # The launch that stopped made the round's calls; the resumed one learns them off the
            # ledger and replays the same round.
            telemetry.bind_priced(set())
            await round_calls()
            telemetry.bind_priced(scan_ledger_priced_keys([ledger.path]))
            await round_calls()
    finally:
        telemetry.reset_cycle_ledger(token)
    assert [r["sample_id"] for r in walk.results] == taken, "the ceiling held back a re-read"
    # The next cell no earlier launch took is search again, and the spent ceiling stops it.
    assert stopped == "spend_budget"
    assert len(bench.results) == len(dataset), "the search's ceiling stopped the bench pass"
    kinds = [r.kind for _, r in ledger.iter() if isinstance(r, TokenUsageRecord)]
    assert kinds == ["bench"] * len(dataset) + ["optimizer", "judge"], (
        f"a re-read was metered a second time: {kinds}"
    )
    assert book.usd_spent == pytest.approx(0.04)
    # No surface watched the bench walk, and the next launch still learns every cell it priced —
    # and each of the two calls.
    priced = scan_ledger_priced_keys([ledger.path])
    assert {f"cell_{s.id}" for s in dataset} <= priced and len(priced) == len(dataset) + 2


def test_a_ledger_index_serves_the_file_as_it_stands(tmp_path: Path) -> None:
    """A polled read folds only what was appended since its last read, so it is wrong the moment
    the tail it trusts is not the file's: an append it missed under-reads a bill or keeps a
    superseded seed, a rewrite it reads through counts rows the file no longer holds, and a read
    racing an append reports a call count and a total from two different moments."""
    import threading

    from promptpotter.domain.run_records import CycleSeed, CycleSeedRecord, TokenUsageRecord
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.store.account_spend import iter_user_token_usage
    from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
        scan_ledger_cycle_seed,
        scan_ledger_spend,
    )
    from promptpotter.infrastructure.store.io import write_jsonl
    from promptpotter.infrastructure.store.read_model import LedgerSpan, iter_jsonl

    ledger = CycleEventLog.open(CycleDir(tmp_path))

    def bill(n: int) -> None:
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
                )
            )

    def seed(model: str) -> None:
        overlay = {"llm_only": {"model": model}}
        ledger.append(CycleSeedRecord(seed=CycleSeed(pipeline_overlay=overlay)))

    def tailed() -> tuple[float, int, CycleSeed | None, float]:
        spend, calls = scan_ledger_spend([LedgerSpan(ledger.path)])
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
    bill(2)
    seed("second")
    assert tailed() == cold() and cold()[1] == 5

    # A compaction swaps the file for a shorter one; the index must not keep its old rows.
    write_jsonl(ledger.path, iter_jsonl(ledger.path)[:2])
    assert tailed() == cold() == (2.0, 2, None, 2.0)

    seen: list[tuple[float, int]] = []

    def read() -> None:
        for _ in range(200):
            seen.append(tailed()[:2])

    readers = [threading.Thread(target=read) for _ in range(4)]
    for reader in readers:
        reader.start()
    bill(40)
    seed("third")
    for reader in readers:
        reader.join()
    assert all(usd == calls for usd, calls in seen), [s for s in seen if s[0] != s[1]][:3]
    assert tailed() == cold() and cold()[1] == 42


async def test_cell_envelope_cancels_the_inner_campaign(tmp_path: Path, monkeypatch: Any) -> None:
    """A cell that outlives its envelope is a SILENT spend leak.

    The envelope only bounds spend because the work is awaited directly all the way down,
    making it the awaiting coroutine's ``_fut_waiter`` so the timeout's cancellation
    reaches it. Detach that await — ``asyncio.shield``, ``asyncio.wait``, a ``gather``
    — and the timed-out campaign keeps running, keeps calling the optimizer, and keeps
    billing tokens against a sample nobody will read. Nothing errors; the run just
    costs more and ends later. So this pins the PROPERTY (the work stops), not the
    shape of the code that achieves it.
    """
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
        """Models the campaign as it BEHAVED, not as it should: it outlives the envelope and
        then SWALLOWS the cancellation, returning a normal result.

        That is what the real inner chain did for months — three seams answered
        ``CancelledError`` with a plain return — and it made this entire guard vanish.
        ``asyncio.timeout`` raises TimeoutError only when a CancelledError travels back up,
        so a swallowing callee let the await complete, no deadline fired, and an over-budget
        campaign was scored as a genuine measurement of the optimizer prompt that ran it. A stub
        that politely re-raises cannot catch that, which is why this one does not.
        """
        started.set()
        try:
            await asyncio.sleep(30)  # far past the envelope
        except asyncio.CancelledError:
            cancelled.set()
        return CycleResult(
            stop_reason="max_rounds",
            rounds=[],
            n_rounds_after_origin=0,
            result_accuracy=0.0,
            result_round=0,
            origin_accuracy=0.0,
            result_prompt_fields={},
            started_at="",
            finished_at="",
        )

    monkeypatch.setattr(spawn, "_run_inner_campaign", _hanging_inner)
    # `resolve_inner_task` has no default ladder — the benchmark, its sample count,
    # round cap and target score are declared, or the spawn raises. Written, then loaded
    # through the real validator, because the run resolves its panel ONCE and carries it.
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
            # No inner dataset resolved: the stubbed inner run never reads one.
            cells=InnerCells(
                panel=load_inner_tasks(tmp_path / "inner_tasks.yaml"), by_dataset={}, treatment="o"
            ),
        )
    )
    llm_telemetry._CYCLE_LEDGER.set(_RecordingLedger())  # type: ignore[arg-type]

    # The connector DECLARES the seconds and the scoring seam PUTS THEM IN FORCE — driven apart
    # here exactly as `measure_sample` drives them, so a cell keeping its own timeout would pass
    # this while the seam bounded nothing.
    from promptpotter.domain.sample import Sample

    query = "justlogic-d234/seed-0"
    cell = Sample(
        id=0, query=query, ground_truth=None, source_pin={"id": query, "inner_dataset_seed": 0}
    )
    envelope = CellEnvelope(spawn.inner_cell_envelope_s(cell, {}), label=query)
    with pytest.raises(CellUnscoreableError, match="wall-clock envelope"):
        async with envelope:
            await spawn.run_inner_cycle(cell, {})

    assert started.is_set(), "the inner campaign never started — the envelope proved nothing"
    assert cancelled.is_set(), "the inner campaign outlived its envelope and kept spending"


def test_a_run_in_its_own_process_spends_as_the_account_that_launched_it(
    built_stores: Any, tmp_path: Path
) -> None:
    """A server-launched run executes in its own process, which rebuilds its stores from what the
    server hands it. An identity rebuilt from the tenant alone carries no issuer, and no issuer IS
    the box operator: the run of a metered signup would then spend the host's key unmetered, under
    a ceiling nobody admitted, with every number on screen still rendering."""
    from promptpotter.application.jobs.launcher.run_job import JobSpec
    from promptpotter.application.jobs.quota import spends_the_hosts_own_key
    from promptpotter.application.jobs.registry import JobRegistry
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.launch_limits import HeldLimits
    from promptpotter.domain.spend import BudgetChange, SpendCeilings
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
        operator=BudgetChange(0.25, None),
        reserve=SpendCeilings(0.5, 80_000),
    )
    wire = JobSpec.of(
        stores=stores,
        job_registry=JobRegistry(
            tmp_path / "jobs", capacity=lambda _live: 1, projects_root=tmp_path / "projects"
        ),
        job_id="job-a",
        hop=CycleHop(campaign_id="ds__000001", cycle_id="cycle_root"),
        session_id=None,
        limits=held,
        backend_url="http://127.0.0.1:8000",
    ).model_dump_json()

    spec = JobSpec.model_validate_json(wire)
    assert spec.identity == signup
    assert spec.limits == held
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
    """``change-run-limits`` acks ``applied`` the moment the ledger takes the record — it cannot
    see whether anything will ever READ the ceiling it wrote, so every way of writing one nothing
    polls is a lie the operator has no way to catch. Two existed. A run launched declaring nothing
    got no ``BudgetGate`` at all, so the file was written and read by no one for the life of the
    campaign. And a ceiling set while a cycle was PAUSED was swept with the other polled flags at
    the next launch, before the resume could read it. Both end the same way: the number is on the
    dashboard, the command returned 202, and the run spends past it to completion.

    What the operator declared lives on the ledger (``RunLimitsRecord``), which every launch
    re-declares; the file the gate polls is only its mirror, so a launch may sweep it.
    """
    import types

    from promptpotter.application.campaign_config import load_campaign_config
    from promptpotter.application.runner.entry import _build_budget_gate
    from promptpotter.application.runner.loop import _armed_round_cap
    from promptpotter.domain.launch_limits import RoundsCap
    from promptpotter.domain.phases import StopReason
    from promptpotter.domain.spend import BudgetChange, MeteredSpend, SpendCeilings
    from promptpotter.infrastructure.runtime_flags import (
        clear_run_control_flags,
        write_run_limits_mirror,
    )

    cycle_dir = tmp_path / "cyc"
    spent = MeteredSpend(
        meter="bill",
        usd=1.0,
        tokens=9_000,
        billed_usd=1.0,
        incurred_usd=1.0,
        billed_tokens=0,
        unpriced_tokens=0,
        kinds={},
        replay_share=None,
    )
    observers = types.SimpleNamespace(
        dashboard=types.SimpleNamespace(spend_metered=lambda _meters: spent),
        arm_spend_book=lambda _book: None,
    )

    # A run that declared NOTHING is still gated, and the gate stays silent until a ceiling exists.
    gate = _build_budget_gate(
        observers,
        cycle_dir,
        declared=SpendCeilings(None, None),
        meters="bill",
        reserve=SpendCeilings(None, None),
    )
    assert gate.tripped() is None
    write_run_limits_mirror(
        cycle_dir, BudgetChange(0.50, None), rounds=None, reserve=BudgetChange(None, None)
    )
    assert gate.tripped() == StopReason.SPEND_BUDGET, "a mid-run ceiling reached no gate"

    # The token arm binds on its own, in the unit that survives an unpriced model.
    write_run_limits_mirror(
        cycle_dir, BudgetChange(None, 5_000), rounds=None, reserve=BudgetChange(None, None)
    )
    assert gate.tripped() == StopReason.TOKEN_BUDGET

    # The round cap rides the same mirror into the loop's boundary: lowered mid-run it binds over
    # the config's, and a LIFT reads as no cap rather than falling back to the config's.
    session = types.SimpleNamespace(
        state=types.SimpleNamespace(cycle_id="cyc"),
        hop=None,
        store=types.SimpleNamespace(
            campaigns=types.SimpleNamespace(cycle_dir=lambda _h: cycle_dir)
        ),
    )
    config = load_campaign_config(
        {"optimization": {"degradation_threshold": 0.05, "max_rounds": 50}}
    )
    write_run_limits_mirror(
        cycle_dir,
        BudgetChange(None, None),
        rounds=RoundsCap(max_rounds=3),
        reserve=BudgetChange(None, None),
    )
    assert _armed_round_cap(session, config) == 3, "a mid-run round cap reached no loop"
    write_run_limits_mirror(
        cycle_dir,
        BudgetChange(None, None),
        rounds=RoundsCap(max_rounds=None),
        reserve=BudgetChange(None, None),
    )
    assert _armed_round_cap(session, config) is None

    # The launch sweep drops the mirror; the standing ceiling itself is the ledger's to carry.
    clear_run_control_flags(cycle_dir)
    assert gate.tripped() is None, "a swept mirror still governed the next run"


def test_no_burst_of_sends_records_spend_past_its_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A $0.10 campaign ended at $0.1018: the ceiling was compared against spend already recorded,
    so every call out when it tripped landed past it — and a call cancelled on the way out billed
    the provider with no record at all. Nothing raised; the number was simply higher than the cap.

    A burst of concurrent sends through the real client — some answered, some cancelled mid-call,
    some timed out after the request left — must record no more than the ceiling. And a record of
    spend is only ever a BILL: a send that never reported writes none, and binds the ceiling from
    its open hold instead — every surface once summed a cancelled cell's whole worst case as
    money spent ($1.87 on a campaign the provider had billed $0.235)."""
    import contextlib
    import random
    import ssl
    import types

    import httpx
    import openai
    from openai.types.chat import ChatCompletion

    from promptpotter.domain.run_records import SpendHoldRecord, TokenUsageRecord
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.llm.openai_compat import OpenAICompatibleClient
    from promptpotter.infrastructure.llm.pricing import Rate
    from promptpotter.infrastructure.llm.spend_book import (
        Admission,
        CallLabel,
        SendBound,
        SpendBook,
        spending_under,
        unreported_on,
    )
    from promptpotter.infrastructure.llm.telemetry import reset_cycle_ledger, set_cycle_ledger
    from promptpotter.infrastructure.store.account_spend import billed_spend
    from promptpotter.shared.errors import SendRefusedError

    monkeypatch.setattr(
        "promptpotter.infrastructure.llm.pricing.load_rates",
        lambda: {"gpt-x": Rate(1e-6, 2e-6)},
    )
    rng = random.Random(7)
    request = httpx.Request("POST", "https://x")
    flaky, hang = [True], [False]

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

    client = OpenAICompatibleClient(api_key="k", provider="openai", display_name="OpenAI")
    client._client = types.SimpleNamespace(  # type: ignore[assignment]
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(
                with_raw_response=types.SimpleNamespace(create=create)
            )
        )
    )
    ledger = CycleEventLog(tmp_path / "ledger.jsonl")
    book = SpendBook(
        usd_cap=lambda: 0.05,
        tokens_cap=lambda: None,
        usd_reserve=lambda: 0.05,
        tokens_reserve=lambda: None,
        meters="bill",
    )
    ledger.bind(book)

    async def burst() -> list[Any]:
        async def one(i: int) -> Any:
            return await client.chat(
                ChatRequest(
                    [{"role": "user", "content": "x" * rng.randint(10, 400)}],
                    model="gpt-x",
                    max_tokens=1500,
                ),
                label=CallLabel(f"n{i}", "optimizer"),
            )

        tasks = [asyncio.ensure_future(one(i)) for i in range(40)]
        await asyncio.sleep(0.005)
        for task in tasks[::5]:
            task.cancel()
        return await asyncio.gather(*tasks, return_exceptions=True)

    token = set_cycle_ledger(ledger)
    try:
        with spending_under(book):
            outcomes = asyncio.run(burst())
    finally:
        reset_cycle_ledger(token)

    records = [r for _, r in ledger.iter() if isinstance(r, TokenUsageRecord)]
    recorded = sum(r.cost_usd or 0.0 for r in records)
    assert recorded <= 0.05 + 1e-12, f"recorded ${recorded:.6f} past a $0.05 ceiling"
    assert recorded == pytest.approx(book.usd_spent)
    assert any(isinstance(o, SendRefusedError) for o in outcomes), "the ceiling never bound"
    unreported = sum(
        isinstance(o, asyncio.CancelledError | openai.APITimeoutError) for o in outcomes
    )
    left = unreported_on(ledger)
    # Never written as a bill: each stays an open hold, at the bound it was admitted on…
    assert unreported and left.sends == unreported
    assert len(records) == sum(not isinstance(o, BaseException) for o in outcomes)
    assert book.usd_unreported == pytest.approx(left.usd)
    # …and the ceiling binds bills and unknowns together.
    assert book.usd_spent + book.usd_unreported <= 0.05 + 1e-12

    # Apart from the ceiling, the reserve holds each send's bound while the ceiling holds what such
    # sends bill: the run stops on its ceiling, and no burst then out bills past the reserve.
    cell = CallLabel("cell", "backend")
    worst = SendBound(input_tokens=0, output_tokens=1000, usd=0.125)
    for reserve, depth in ((0.5, 4), (None, 16), (1.0, 8)):
        own = SpendBook(
            usd_cap=lambda: 0.5,
            tokens_cap=lambda: None,
            usd_reserve=lambda reserve=reserve: reserve,
            tokens_reserve=lambda: None,
            meters="bill",
        )
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
            own.usd_spent += worst.usd or 0.0
            billed += 1
        assert billed == 4, "the run stops on reaching its ceiling, whatever it reserved"
        out = (depth - billed) * 0.125
        assert reserve is None or own.usd_spent + out <= reserve, "a burst billed past the reserve"

    # A hard exit runs no `finally`: the hold written ahead of the call is all that says it left.
    # The account reads it as unreported, never as spent, and a resumed book holds it.
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
    assert account.used_usd == pytest.approx(recorded)
    assert account.unreported_usd == pytest.approx(left.usd + 0.0036)
    assert unreported_on(ledger).sends == unreported + 1

    # A nested run (an L4 cell) spends under its ROOT's book: the call is held on the root ledger
    # and carried there as it settles, while the inner ledger keeps its own view — which no sum of
    # money counts a second time.
    flaky[0] = False
    book.ledger = ledger
    inner = CycleEventLog(tmp_path / "inner.jsonl")
    token = set_cycle_ledger(inner)
    try:
        with spending_under(book):
            asyncio.run(
                client.chat(
                    ChatRequest([{"role": "user", "content": "nested"}], "gpt-x", max_tokens=10),
                    label=CallLabel("inner", "optimizer"),
                )
            )
    finally:
        reset_cycle_ledger(token)
    carried = [r for _, r in ledger.iter() if isinstance(r, TokenUsageRecord)]
    own = [r for _, r in inner.iter() if isinstance(r, TokenUsageRecord)]
    assert [(r.node, r.mirrored) for r in carried[-1:]] == [("inner:inner", False)]
    assert [r.mirrored for r in own] == [True]
    assert unreported_on(ledger).sends == unreported + 1
    assert billed_spend([inner.path]).used_usd == 0.0
    assert book.usd_spent == pytest.approx(billed_spend([ledger.path]).used_usd)
    # …and a nested send that ends with no bill leaves its hold on the ROOT ledger, where the
    # root's ceiling and its account read it.
    token = set_cycle_ledger(inner)
    try:
        cut = SendBound(input_tokens=600, output_tokens=1500, usd=0.0036)
        Admission(
            book, CallLabel("cut", "optimizer"), cut, cut, model="gpt-x", provider="openai"
        ).unreported()
    finally:
        reset_cycle_ledger(token)
    assert unreported_on(ledger).sends == unreported + 2

    # A connection that broke AFTER the request left (a TLS record fault) crashed a whole campaign
    # on one optimizer call. It is retried like a 5xx, and the broken send stays held.
    broke = [True]
    answer = create

    async def create_once_broken(**params: Any) -> Any:
        if broke[0]:
            broke[0] = False
            try:
                raise ssl.SSLError("bad record mac")
            except ssl.SSLError as err:
                raise openai.APIConnectionError(request=request) from err
        return await answer(**params)

    async def no_wait(*_: Any) -> None:
        return None

    monkeypatch.setattr("promptpotter.infrastructure.llm.base.wait_with_countdown", no_wait)
    client._client.chat.completions.with_raw_response.create = create_once_broken  # type: ignore[union-attr]
    token = set_cycle_ledger(ledger)
    try:
        with spending_under(book):
            asyncio.run(
                client.chat(
                    ChatRequest([{"role": "user", "content": "x"}], "gpt-x", max_tokens=10),
                    label=CallLabel("tls", "optimizer"),
                )
            )
    finally:
        reset_cycle_ledger(token)
    assert unreported_on(ledger).sends == unreported + 3

    # A backend cell: a connection never made is retried free once `/status` answers; a 5xx that
    # billed is settled off its error envelope and retried; a read timeout is left unreported and
    # NEVER sent again — the backend is still working it, and a second POST was a second bill
    # nobody recorded. Nor is a 5xx the backend declares deterministic: its bill stays unreported.
    from promptpotter.application.scoring.sample_measurement import cell_billing
    from promptpotter.connectors.termnorm import TermNormSession
    from promptpotter.infrastructure.backend import BackendClient
    from promptpotter.shared.errors import CellHaltedError

    posts: list[int] = []
    probes: list[int] = []
    billed_step = {"step_tokens": {"n": {"input": 10, "output": 5, "cost_usd": 0.001}}}
    deadline = {
        "detail": {"error_code": "llm_timeout", "retryable": False, "message": "no reply in 164s"},
        "data": {"step_tokens": None},
    }

    async def _no_wait(*_args: Any) -> None:
        return None

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

    monkeypatch.setattr("promptpotter.infrastructure.backend.wait_with_countdown", _no_wait)
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
    wallet = SpendBook(
        usd_cap=lambda: None,
        tokens_cap=lambda: None,
        usd_reserve=lambda: None,
        tokens_reserve=lambda: None,
        meters="bill",
    )
    before = sum(isinstance(r, TokenUsageRecord) for _, r in ledger.iter())
    token = set_cycle_ledger(ledger)
    try:
        for ends in (httpx.ReadTimeout, CellHaltedError):
            with spending_under(wallet), pytest.raises(ends):
                asyncio.run(
                    cells.run_query(
                        Sample(id=0, query="q", ground_truth=None),
                        bound=cell,
                        billed=cell_billing(types.SimpleNamespace(nodes=[]), {}),  # type: ignore[arg-type]
                    )
                )
    finally:
        reset_cycle_ledger(token)
    assert len(posts) == 4, f"{len(posts)} POSTs — a cell that cannot end otherwise was sent again"
    after = [r for _, r in ledger.iter() if isinstance(r, TokenUsageRecord)][before:]
    assert [r.cost_usd for r in after] == [0.0, 0.001]
    assert wallet.usd_unreported == pytest.approx(0.02)

    # A cell whose sends are each billed where they are made (Harbor's agent) RESERVES its bound
    # rather than holding it as one send. Cancelled mid-episode, it has paid for the turns that
    # answered and leaves only the send it had out unreported — never the whole cell.
    turns: list[Any] = []

    async def episode(_workload: Any, _sample: Any, _payload: dict[str, Any]) -> dict[str, Any]:
        for n in range(4):
            hang[0] = n == 3
            turns.append(
                await client.chat(
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
    purse = SpendBook(
        usd_cap=lambda: 1.0,
        tokens_cap=lambda: None,
        usd_reserve=lambda: 1.0,
        tokens_reserve=lambda: None,
        meters="bill",
    )
    cell_ledger.bind(purse)
    whole = SendBound(input_tokens=100_000, output_tokens=50_000, usd=0.5)

    async def cancel_mid_episode() -> None:
        cell = asyncio.ensure_future(
            agent.run_query(
                Sample(id=0, query="q", ground_truth=None), bound=whole, billed=lambda _d: None
            )
        )
        while len(turns) < 3:
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.05)
        cell.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cell

    token = set_cycle_ledger(cell_ledger)
    try:
        with spending_under(purse):
            asyncio.run(cancel_mid_episode())
    finally:
        reset_cycle_ledger(token)
    bills = [r for _, r in cell_ledger.iter() if isinstance(r, TokenUsageRecord)]
    assert [r.node for r in bills] == ["agent"] * 3
    assert purse.usd_spent == pytest.approx(sum(r.cost_usd or 0.0 for r in bills))
    out = unreported_on(cell_ledger)
    assert out.sends == 1 and out.usd < whole.usd / 100, f"a cancel left ${out.usd} unreported"
    assert purse.usd_unreported == pytest.approx(out.usd)
    # The reservation is gone with the cell: the whole bound fits again beside what was paid.
    assert purse.fits(whole, whole) == 1


def test_a_controlled_arms_ceiling_is_its_searchs_incurred_cost() -> None:
    """Arms of one head-to-head replay each other's cells, so a ceiling on the BILL hands the arm
    that arrived second a bigger search for the same money — and a bench pass counted inside it
    leaves the arm with the longer pick less search. Both are silent: every arm still halts at its
    number. A controlled arm's book meters its search at incurred cost, the bench beside it."""
    from promptpotter.domain.run_records import TokenUsageRecord
    from promptpotter.infrastructure.llm.spend_book import (
        CallLabel,
        SendBound,
        SpendBook,
        reserved,
        spending_under,
    )
    from promptpotter.infrastructure.llm.telemetry import filed_as
    from promptpotter.shared.errors import ErrorCategory, SendRefusedError

    def replay(usd: float) -> TokenUsageRecord:
        return TokenUsageRecord(
            kind="backend", node="n", input_tokens=10, output_tokens=5, cost_usd=usd, cached=True
        )

    arm = SpendBook(
        usd_cap=lambda: 0.10,
        tokens_cap=lambda: None,
        usd_reserve=lambda: 0.10,
        tokens_reserve=lambda: None,
        meters="search_incurred",
    )
    ordinary = SpendBook(
        usd_cap=lambda: 0.10,
        tokens_cap=lambda: None,
        usd_reserve=lambda: 0.10,
        tokens_reserve=lambda: None,
        meters="bill",
    )
    for book in (arm, ordinary):
        book.count(replay(0.06))
        book.count(replay(0.05))
    assert ordinary.exhausted() is None, "a replay billed nothing, so it spends no bill"
    assert arm.exhausted() == ErrorCategory.SPEND_CEILING, "a sibling's cache stretched the arm"

    fresh = SpendBook(
        usd_cap=lambda: 0.10,
        tokens_cap=lambda: None,
        usd_reserve=lambda: 0.10,
        tokens_reserve=lambda: None,
        meters="search_incurred",
    )
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


async def test_a_budget_change_leaves_the_arm_it_did_not_touch_alone(
    built_stores: Any, tmp_path: Path
) -> None:
    """``change-run-limits`` takes each ceiling independently, and both halves of "leave it alone"
    are silent when they break. Down at the clamp, a delegate's grant composed into an ABSENT arm
    writes a USD ceiling the caller never asked for, and `BudgetGate` then halts a run nobody
    capped. Up in the job's reservation, an absent arm has to stay at the JOB's prior: merged
    against the file's, which starts empty, it reads released, so the account quotes headroom this
    cycle is still holding and the next launch spends it twice.
    """
    import types

    from promptpotter.application.commands.dispatcher import CommandDispatcher
    from promptpotter.application.jobs.quota import clamp_budget_change
    from promptpotter.application.jobs.registry import JobRegistry
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.launch_limits import RoundsCap
    from promptpotter.domain.spend import BudgetChange
    from promptpotter.infrastructure.runtime_flags import (
        read_reserve_mirror,
        read_run_limits_mirror,
    )
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
    registry = JobRegistry(
        tmp_path / "jobs", capacity=lambda _live: 1, projects_root=tmp_path / "projects"
    )
    job = registry.request_slot(user_id="sub-9", dataset_name="ds1", hop=hop)
    assert job.status == "pending", "an empty box must hand out a slot, not a place in line"
    registry.set_caps(job.job_id, cap_usd=0.30, cap_tokens=5_000_000)

    dispatcher = CommandDispatcher(stores, registry)
    await dispatcher._apply_change_run_limits(
        hop, BudgetChange(None, None), RoundsCap(max_rounds=3)
    )
    await dispatcher._apply_change_run_limits(hop, BudgetChange(None, 1_000), None)
    held = registry.get(job.job_id)
    assert held is not None
    # The reservation moves with the ceiling under the launch's own rule — as much again for the
    # calls out — and reaches the running gate through the mirror the ceiling rides.
    assert held.cap_tokens == 2_000
    assert held.cap_usd == pytest.approx(0.30), "the untouched USD reservation was released"
    assert read_reserve_mirror(stores.campaigns.cycle_dir(hop)) == (pytest.approx(0.30), 2_000)
    # The standing record is written WHOLE, so a spend move must carry the round cap it did not
    # touch — dropped, the run falls back to the config's cap and spends past the operator's.
    assert stores.campaigns.read_run_limits(hop).rounds == RoundsCap(max_rounds=3)
    assert read_run_limits_mirror(stores.campaigns.cycle_dir(hop)).rounds == RoundsCap(max_rounds=3)

    # An absent arm stays absent through the clamp too, delegated ceiling or not.
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
        requested=BudgetChange(None, 1_000),
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
    """A run declaring nothing reserves the account's headroom while the wallet bound composes its
    ceiling far lower. Moving one arm must leave the other at that composed cap: pinned at the
    reservation in ``run_limits.json``, which the gate prefers, it lets the run spend past it."""
    import types

    from promptpotter.application.commands.dispatcher import CommandDispatcher
    from promptpotter.application.jobs.registry import JobRegistry
    from promptpotter.application.runner.entry import _build_budget_gate
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.phases import StopReason
    from promptpotter.domain.spend import BudgetChange, MeteredSpend, SpendCeilings

    hop = CycleHop(campaign_id="camp-4", cycle_id="cycle_budget0001")
    registry = JobRegistry(
        tmp_path / "jobs", capacity=lambda _live: 1, projects_root=tmp_path / "projects"
    )
    job = registry.request_slot(user_id="default", dataset_name="ds1", hop=hop)
    registry.set_caps(job.job_id, cap_usd=0.30, cap_tokens=5_000_000)
    spent = MeteredSpend(
        meter="bill",
        usd=0.10,
        tokens=210_000,
        billed_usd=0.10,
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
    gate = _build_budget_gate(
        observers,
        built_stores.campaigns.cycle_dir(hop),
        declared=SpendCeilings(0.30, 210_000),
        meters="bill",
        reserve=SpendCeilings(None, None),
    )
    assert gate.tripped() == StopReason.TOKEN_BUDGET

    await CommandDispatcher(built_stores, registry)._apply_change_run_limits(
        hop, BudgetChange(0.50, None), None
    )
    assert gate.tripped() == StopReason.TOKEN_BUDGET, "a USD raise lifted the token ceiling"


def test_a_moved_ceiling_counts_the_cycles_own_spend_once(
    built_stores: Any, tmp_path: Path
) -> None:
    """A cycle's ceiling counts every call in its history — a fork's inherited prefix included —
    and the account's spend already holds those calls. Clamped against the bare headroom, its own
    spend is subtracted twice and the raise silently lands short; clamped past it, the account
    overspends. What the parent billed past the cut is not the fork's, and binds it as spend."""
    from promptpotter.application.jobs.quota import admit_launch, clamp_budget_change
    from promptpotter.application.jobs.registry import JobRegistry
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.cycle_paths import CycleDir, CycleHop
    from promptpotter.domain.run_records import TokenUsageRecord
    from promptpotter.domain.spend import BudgetChange, SpendCeilings
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
    stores.campaigns.create(root, {})
    stores.campaigns.create(hop, {"parent_cycle_id": root.cycle_id, "forked_at_offset": 1})

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
    bill(root, 0.1)
    bill(hop, 0.3)
    registry = JobRegistry(
        tmp_path / "jobs", capacity=lambda _live: 1, projects_root=tmp_path / "projects"
    )
    job = registry.request_slot(user_id="sub-7", dataset_name="ds1", hop=hop)
    registry.set_caps(job.job_id, cap_usd=0.8, cap_tokens=None)

    caps, reserve = clamp_budget_change(
        requested=BudgetChange(5.0, None),
        user=User(
            user_id="sub-7", tenant_id="sub-7", created_at="2026-01-01", spend_budget_usd_total=1.0
        ),
        stores=stores,
        job_registry=registry,
        hop=hop,
    )
    resumed, _ = admit_launch(
        declared=SpendCeilings(None, None),
        user=User(
            user_id="sub-7", tenant_id="sub-7", created_at="2026-01-01", spend_budget_usd_total=1.0
        ),
        stores=stores,
        job_registry=registry,
        job_id=job.job_id,
        hop=hop,
    )
    # $1.00 allowance, $0.40 in this cycle's history and $0.10 beside it: it may reach $0.90,
    # whether the ceiling is moved mid-run or declared by a resume.
    assert caps.usd == resumed.usd == pytest.approx(0.9)
    assert reserve.usd == pytest.approx(0.9)


def test_host_wallet_ceilings_hold_in_both_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Losing a free-tier ceiling is silent: the run completes, the dashboard looks normal, and the
    host pays. Signing up is the grant, so this gate is the only thing standing between a stranger
    and the host's provider key (ADR-0003 D1) — and it answers in TWO units because a price needs a
    rate on file while a token count never does.
    """
    import json
    import types

    from promptpotter.application.jobs.quota import QuotaExceededError, admit_launch, admit_spend
    from promptpotter.config.settings import settings
    from promptpotter.domain.spend import SpendCeilings
    from promptpotter.infrastructure.store.account_spend import sum_user_spend
    from promptpotter.infrastructure.store.user_store import User

    def _stores(
        *, issuer: str | None, ledgers: list[Path], claims: dict[str, float] | None = None
    ) -> types.SimpleNamespace:
        """`issuer` set is what makes this a WEB identity rather than the box operator, and the
        operator is exempt from metering."""
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

    # No override must NOT read as uncapped in either unit. The USD arm is one STEP, not the whole
    # ceiling — the offer is denominated in runs, and a first run declaring the lot funds no second.
    fresh, _ = admit_launch(
        declared=SpendCeilings(None, None),
        user=free_tier,
        stores=_stores(issuer=web, ledgers=[]),
        job_registry=idle,
        job_id="job-a",
        hop=None,
    )
    assert fresh.usd == pytest.approx(settings.FREE_TIER_LAUNCH_STEP_USD)
    assert fresh.usd < settings.FREE_TIER_SPEND_CAP_USD
    assert fresh.tokens == settings.FREE_TIER_TOKEN_CAP

    # Clamping a declaration down to the remainder is what makes a campaign halt mid-run, so a
    # declaration the account cannot cover is refused at the door instead.
    with pytest.raises(QuotaExceededError):
        admit_launch(
            declared=SpendCeilings(10.0, None),
            user=free_tier,
            stores=_stores(issuer=web, ledgers=[]),
            job_registry=idle,
            job_id="job-a",
            hop=None,
        )

    # A cycle already in flight holds its whole declared ceiling, or two concurrent launches are
    # both admitted against one remainder and the pair spends double it.
    def _sibling(**caps: Any) -> Any:
        held = types.SimpleNamespace(job_id="job-b", hop=None, **caps)
        return types.SimpleNamespace(list_running=lambda *, user_id: [held])

    with pytest.raises(QuotaExceededError):
        admit_launch(
            declared=SpendCeilings(None, None),
            user=free_tier,
            stores=_stores(issuer=web, ledgers=[]),
            job_registry=_sibling(
                cap_usd=settings.FREE_TIER_SPEND_CAP_USD,
                cap_tokens=settings.FREE_TIER_TOKEN_CAP,
            ),
            job_id="job-a",
            hop=None,
        )

    # ...and one admitted but not yet STAMPED holds an amount nothing can read. Counted as zero,
    # both launches inside that window are quoted the same remainder and the pair spends twice the
    # ceiling, with no error at any step. It must refuse instead.
    with pytest.raises(QuotaExceededError):
        admit_launch(
            declared=SpendCeilings(None, None),
            user=free_tier,
            stores=_stores(issuer=web, ledgers=[]),
            job_registry=_sibling(cap_usd=None, cap_tokens=None),
            job_id="job-a",
            hop=None,
        )

    # `:nitro` is a route selector, so the call is unpriceable BY DESIGN and the account's USD
    # total reads $0.00 for 500k billed tokens. Trusting `ceiling - spent` would hand back nearly
    # the whole ceiling; the grace bounds it, and the token arm counts what the USD arm cannot.
    blind, _ = admit_launch(
        declared=SpendCeilings(None, None),
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

    # A rate belongs to the (provider, model) PAIR, so a call is priced with its provider when it is
    # recorded. Dropped, every namespaced model lands UNPRICED: the USD total stays $0.00 for real
    # spend and the grace renews on each launch, which is the ceiling silently not existing.
    from promptpotter.domain.spend import TokenAccount
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.llm.pricing import Rate
    from promptpotter.infrastructure.llm.telemetry import (
        emit_token_usage,
        reset_cycle_ledger,
        set_cycle_ledger,
    )

    monkeypatch.setattr(
        "promptpotter.infrastructure.llm.pricing.load_rates",
        lambda: {"openrouter/openai/gpt-4o": Rate(1e-6, 2e-6)},
    )
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
    assert priced.used_usd == pytest.approx(400_000 * 1e-6 + 100_000 * 2e-6)

    # The box operator spends their own money and is metered in neither unit.
    assert admit_launch(
        declared=SpendCeilings(None, None),
        user=free_tier,
        stores=_stores(issuer=None, ledgers=[]),
        job_registry=idle,
        job_id="job-a",
        hop=None,
    ) == ((None, None), (None, None))

    # A delegated spend ceiling (ADR-0005) clamps the effective cap — a sub-principal cannot
    # outspend its grant even where the declared and account caps are higher.
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
    # The grant is a CEILING on what may be declared, never a declaration. Read as one, a launch
    # declaring nothing was refused for exceeding a headroom it would have been held to anyway.
    thin = generous.model_copy(update={"spend_budget_usd_total": 1.0})
    assert _delegated(thin, None, 2.0) == 1.0

    # The origin resolver is the one optimizer call reachable BEFORE a campaign, so no launch
    # admission has run and no ``BudgetGate`` is watching. Unchecked, an account already at its
    # ceiling keeps firing turns on the host's key for as long as it sends HTTP requests.
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
    with pytest.raises(QuotaExceededError):
        admit_spend(stores=_stores(issuer=web, ledgers=[spent]), bucket="turn")
    admit_spend(stores=_stores(issuer=None, ledgers=[spent]), bucket="turn")


# 4. Spend outlives what spent it


def test_deleting_a_spent_stub_fork_does_not_un_spend_it(built_stores: Any) -> None:
    """The stub-delete path takes a whole cycle tree, ledger included, and a stub is deletable at
    ``n_rounds == inherited`` — which an origin-scored fork reaches having already paid for round 0.
    Unbanked, the free-tier ceiling is re-earnable one fork at a time by the auto-cleanup itself, on
    `campaign.lifecycle` alone. Nothing errors; the account simply reads poorer than it is.

    The bank also has to be REFUSAL-safe and RETRY-safe in opposite directions: banking a cycle the
    delete then refuses counts the money twice, and banking after the rmtree loses it outright.
    """
    import json

    from promptpotter.application.bench.resume_and_fork.fork_siblings import (
        cleanup_stub_fork_if_empty,
    )
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.infrastructure.store.account_spend import (
        account_ledgers,
        bank_spend,
        sum_user_spend,
    )
    from promptpotter.infrastructure.store.io import write_json

    stores = built_stores
    root = "cycle_root0000"
    stub, retried = f"{root}_fork_aaaa", f"{root}_fork_bbbb"
    campaign_dir = stores.campaigns.campaign_root_dir("camp-2")
    campaign_dir.mkdir(parents=True, exist_ok=True)
    (campaign_dir / "campaign.json").write_text(json.dumps({"campaign_id": "camp-2"}), "utf-8")

    def _spent_cycle(cycle_id: str, *, n_rounds: int, cost_usd: float) -> Path:
        write_json(
            campaign_dir / "cycles" / cycle_id / "index.json",
            {
                "campaign_id": "camp-2",
                "cycle_id": cycle_id,
                "n_rounds": n_rounds,
                "finished_at": "2026-01-01T00:00:00Z",
            },
        )
        ledger = campaign_dir / "cycles" / cycle_id / ".runtime" / "ledger.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(
            json.dumps(
                {
                    "record_type": "token_usage",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "kind": "backend",
                    "model": "openai/gpt-4o",
                    "provider": "openrouter",
                    "input_tokens": 2_000,
                    "output_tokens": 800,
                    "cost_usd": cost_usd,
                }
            )
            + "\n",
            "utf-8",
        )
        return ledger

    root_ledger = _spent_cycle(root, n_rounds=3, cost_usd=0.07)
    stub_ledger = _spent_cycle(stub, n_rounds=0, cost_usd=0.11)
    retried_ledger = _spent_cycle(retried, n_rounds=0, cost_usd=0.05)

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

    # A cycle the delete REFUSES must not be banked — it keeps its rows, so a tombstone beside
    # them is the same money counted twice, and nothing ever removes a tombstone.
    assert not _cleanup(root)[0]
    assert _account_usd() == pytest.approx(before)

    # The plain path: the rows go, the money stays.
    assert _cleanup(stub)[0]
    assert not stub_ledger.exists()
    assert _account_usd() == pytest.approx(before)

    # Banking precedes the delete, so a crash in between leaves the tombstone standing with the
    # rows still there; every retry from that state must find it rather than bank a second one.
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

    # `delete_campaign` takes every remaining ledger under BOTH `keep_results` arms, and needs only
    # the lifecycle cap signup grants. Banked by the destroyer itself, so no caller can skip it.
    stores.campaigns.delete_campaign(
        "camp-2", keep_results=False, changed_at="2026-01-02T00:00:00Z"
    )
    assert not root_ledger.exists()
    assert _account_usd() == pytest.approx(before)


_CAMPAIGN = "testds__20260101-000000"


def _sandbox(stores: Stores, owner_campaign_id: str, owner_cycle_id: str) -> Path:
    """The inner-sandbox scratch tree owned by one cycle, holding one inner cycle.

    Built through the real key + owner record, so a change to either shows up here rather
    than leaving the reaper tested against a shape nothing writes.
    """
    sandbox = inner_sandbox_dir(
        stores.shared_root,
        str(stores.tenant_id),
        CycleHop(campaign_id=owner_campaign_id, cycle_id=owner_cycle_id),
    )
    write_json(
        sandbox_owner_path(sandbox),
        {
            "tenant_id": str(stores.tenant_id),
            "campaign_id": owner_campaign_id,
            "cycle_id": owner_cycle_id,
        },
    )
    inner = CampaignStore(WorkspaceDir(sandbox / "tenant"))
    inner.create(CycleHop(campaign_id="innerds__20260101-000000", cycle_id="inner-cycle-0"), {})
    return sandbox


def _spend_inner(
    sandbox: Path,
    *,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    mirrored: bool = False,
) -> Path:
    """Put real money on the sandbox's inner cycle ledger, through the real writer."""
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
    """An inner sandbox is a SIBLING of the tenant tree, so no account-wide walk reaches it — the
    reaper deleting one unbanked makes real money vanish from the lifetime record with no error.

    And an inner cycle's calls are carried onto its outer ledger as they settle, so those rows are
    already counted. Banking them again on the way out bills that money a second time — silently,
    because a tombstone is indistinguishable from spend that never reached anywhere else."""
    sandbox = _sandbox(built_stores, _CAMPAIGN, "orphaned-outer-cycle")
    # One call carried out, one not: only the second is still this sandbox's to bank.
    _spend_inner(sandbox, input_tokens=400, output_tokens=100, cost_usd=0.10, mirrored=True)
    _spend_inner(sandbox, input_tokens=600, output_tokens=100, cost_usd=0.15)

    assert reclaim_orphan_sandboxes(built_stores.projects_root) == 1
    assert not sandbox.exists()
    banked = _tombstones(built_stores)
    assert len(banked) == 1
    assert banked[0]["used_usd"] == pytest.approx(0.15)
    assert banked[0]["used_tokens"] == 700
