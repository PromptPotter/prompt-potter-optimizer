"""What a run meets at its edges: the rates it prices by, and the offline world's own faults."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path
from typing import Any, NoReturn

import pytest
from factories import run_init_round, samples, spend_book
from offline_world import PolicyFault, Router

from promptpotter.application.jobs.reaper import RATES_STEP, periodic_sweep
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.scoring.judges.grounding import ANSWER_GROUNDING
from promptpotter.domain.judges import JudgeSpec, JudgeStage
from promptpotter.domain.scoring import MeasuredCell, PipelineData
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm import pricing
from promptpotter.infrastructure.llm.pricing import RatesFile, cache_path
from promptpotter.infrastructure.run_scope import scoped
from promptpotter.infrastructure.store.io import write_text
from promptpotter.infrastructure.store.stores import Stores

# 1. Every process that prices refreshes the rates before it sends, and no refresh outlives it


async def test_a_launch_prices_off_what_upstream_lists_and_leaves_no_refresh_running(
    built_stores: Stores, offline: Router
) -> None:
    name, body = next(iter(offline.rates.items()))
    offline.rates[name] = body | {"input_cost_per_token": body["input_cost_per_token"] * 2}
    before = set(threading.enumerate())
    await run_init_round(built_stores, offline, samples(1, "TRUE"))
    # The loop's own executor pool aside, which the loop joins at its close.
    outliving = {
        t for t in set(threading.enumerate()) - before if not t.name.startswith("asyncio_")
    }
    assert not outliving, f"a thread outlived the launch that started it: {outliving}"
    fetched = RatesFile.model_validate_json(cache_path().read_bytes()).models[name]
    assert fetched.input_cost_per_token == body["input_cost_per_token"] * 2


async def test_the_api_sweep_refreshes_day_old_rates_and_tries_a_dead_upstream_once_a_day(
    built_stores: Stores, offline: Router, monkeypatch: pytest.MonkeyPatch
) -> None:
    name, body = next(iter(offline.rates.items()))
    offline.rates[name] = body | {"input_cost_per_token": body["input_cost_per_token"] * 2}
    day_old = time.time() - pricing._TTL_SECONDS - 1
    write_text(cache_path(), RatesFile(models={}).model_dump_json())
    os.utime(cache_path(), (day_old, day_old))
    # Each rates pass is counted; the third holds its thread until the sweep is cancelled.
    passes, cancelled, left, seen = (
        threading.Semaphore(0),
        threading.Event(),
        threading.Event(),
        [0],
    )
    record_pass = JobRegistry.record_pass

    def counted(registry: JobRegistry, step: str, faults: Any) -> None:
        record_pass(registry, step, faults)
        if step == RATES_STEP:
            seen[0] += 1
            passes.release()
            if seen[0] == 3:
                cancelled.wait()
                left.set()

    monkeypatch.setattr(JobRegistry, "record_pass", counted)
    sweeping = asyncio.create_task(periodic_sweep(built_stores.projects_root, interval_s=0))
    await asyncio.to_thread(passes.acquire)
    fetched = RatesFile.model_validate_json(cache_path().read_bytes()).models[name]
    assert fetched.input_cost_per_token == body["input_cost_per_token"] * 2, "no launch, no refresh"
    os.utime(cache_path(), (day_old, day_old))
    dead = "https://upstream.invalid/rates.json"
    monkeypatch.setattr(pricing, "UPSTREAM_URL", dead)
    await asyncio.to_thread(passes.acquire)
    assert f"urlopen {dead}" in offline.unrouted, "a day-old list was not fetched again"
    offline.unrouted.discard(f"urlopen {dead}")
    await asyncio.to_thread(passes.acquire)
    sweeping.cancel()
    cancelled.set()
    await asyncio.to_thread(left.wait)
    assert f"urlopen {dead}" not in offline.unrouted, "a dead upstream was fetched again in its TTL"


# 2. A fault in the world's own policy raises out of the call, never reads as a refused one


async def test_a_fault_in_the_worlds_policy_raises_past_the_call_it_answers(
    offline: Router, tmp_path: Path
) -> None:
    def stale(_body: dict[str, Any]) -> NoReturn:
        raise KeyError("a key the policy no longer finds")

    offline.llm.says = stale
    spec = JudgeSpec(
        name="answer_grounding", stages=[JudgeStage(model="gpt-4o", provider="openai")]
    )
    cell = MeasuredCell(
        sample_key="k0", predicted="1891", pipeline=PipelineData(reasoning_trace="read the docs")
    )
    ledger = tmp_path / "ledger.jsonl"
    with (
        scoped(ledger=CycleEventLog(ledger)),
        scoped(book=spend_book()),
        pytest.raises(PolicyFault) as raised,
    ):
        await ANSWER_GROUNDING.grade(spec, cell)
    assert isinstance(raised.value.__cause__, KeyError), "the policy's own fault, as it raised"
    filed = ledger.read_text(encoding="utf-8") if ledger.exists() else ""
    assert "KeyError" not in filed, "the harness's fault was filed as the call's answer"
