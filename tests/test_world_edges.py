"""What a run meets at its edges: the rates it prices by, and the offline world's own faults."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any, NoReturn

import pytest
from factories import run_init_round, samples, spend_book
from offline_world import PolicyFault, Router

from promptpotter.application.jobs.machine_status import machine_status
from promptpotter.application.jobs.reaper import RATES_STEP, ROUTE_ANSWERS_STEP, periodic_sweep
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
from promptpotter.shared.identity import default_identity

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


def _a_day_on() -> None:
    """The home's list as a day later finds it: its fetch and its failed try a day older."""
    held = RatesFile.model_validate_json(cache_path().read_bytes())
    back = pricing._TTL_SECONDS + 1
    assert held.tried_at is not None, "the sweep tried no fetch"
    fetched_at = None if held.fetched_at is None else held.fetched_at - back
    aged = held.model_copy(update={"tried_at": held.tried_at - back, "fetched_at": fetched_at})
    write_text(cache_path(), aged.model_dump_json())


async def test_a_dead_price_list_stands_in_machine_status_and_is_tried_once_a_day(
    built_stores: Stores, offline: Router, monkeypatch: pytest.MonkeyPatch
) -> None:
    name, body = next(iter(offline.rates.items()))
    offline.rates[name] = body | {"input_cost_per_token": body["input_cost_per_token"] * 2}
    offline.upstream_dead = True
    # Each rates pass is held once recorded, so the world moves between two ticks alone.
    passed, go, left = threading.Semaphore(0), threading.Semaphore(0), threading.Semaphore(0)
    record_pass = JobRegistry.record_pass

    def held(registry: JobRegistry, step: str, faults: Any) -> None:
        record_pass(registry, step, faults)
        if step == RATES_STEP:
            passed.release()
            go.acquire()
            left.release()

    monkeypatch.setattr(JobRegistry, "record_pass", held)
    jobs = JobRegistry.attach(built_stores.jobs_dir)
    holding = [False]

    async def tick() -> list[str]:
        """The rates step's standing after its next pass, as `machine-status` serves it."""
        if holding[0]:
            go.release()
            await asyncio.to_thread(left.acquire)
        await asyncio.to_thread(passed.acquire)
        holding[0] = True
        served = machine_status(jobs, identity=default_identity(), account_limit=1).unswept
        return [line for line in served if line.startswith(f"{RATES_STEP}: ")]

    sweeping = asyncio.create_task(periodic_sweep(built_stores.projects_root, interval_s=0))
    try:
        for _ in range(3):
            assert len(await tick()) == 1, "a dead price list stood nowhere in machine-status"
        assert offline.rate_fetches == 1, "a dead upstream was tried again within its day"
        _a_day_on()
        offline.upstream_dead = False
        assert await tick() == [], "a fetch that succeeded left the fault standing"
        assert offline.rate_fetches == 2, "a day-old try was not tried again"
        fetched = RatesFile.model_validate_json(cache_path().read_bytes()).models[name]
        assert fetched.input_cost_per_token == body["input_cost_per_token"] * 2
        offline.upstream_dead = True
        _a_day_on()
        for _ in range(2):
            assert len(await tick()) == 1, "a day-old list gone dead stood nowhere"
        assert offline.rate_fetches == 3, "a dead upstream costs one try a day"
    finally:
        sweeping.cancel()
        if holding[0]:
            go.release()
            await asyncio.to_thread(left.acquire)


async def _route_answers_after_a_boot_pass(projects_root: Path, jobs: JobRegistry) -> list[str]:
    """The route-answers step's standing after the sweep's boot pass, as `machine-status` serves it."""
    recorded, record_pass = threading.Event(), JobRegistry.record_pass

    def held(registry: JobRegistry, step: str, faults: Any) -> None:
        record_pass(registry, step, faults)
        if step == ROUTE_ANSWERS_STEP:
            recorded.set()

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(JobRegistry, "record_pass", held)
        sweeping = asyncio.create_task(periodic_sweep(projects_root, interval_s=3600))
        try:
            await asyncio.to_thread(recorded.wait)
        finally:
            sweeping.cancel()
    served = machine_status(jobs, identity=default_identity(), account_limit=1).unswept
    return [line for line in served if line.startswith(f"{ROUTE_ANSWERS_STEP}: ")]


async def test_a_route_answer_the_sweep_cannot_refresh_stands_in_machine_status(
    built_stores: Stores, offline: Router, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One unpriced answer, then the sweep's passes over it: each pass's world is the last one's.
    unhosted, jobs = "vendor/unhosted-model", JobRegistry.attach(built_stores.jobs_dir)
    offline.hosts[unhosted] = {"data": {"endpoints": []}}
    assert pricing.route_priced(unhosted, "openrouter") is False
    monkeypatch.setattr(pricing, "_OUTAGE_MEMO_S", 0.0)
    offline.openrouter_dead = True
    (said,) = await _route_answers_after_a_boot_pass(built_stores.projects_root, jobs)
    assert unhosted in said, f"the standing fault named another route: {said}"
    offline.openrouter_dead = False
    with monkeypatch.context() as stuck:
        # A day-old answer whose drop the disk refuses.
        stuck.setattr(pricing, "_TTL_SECONDS", 0.0)
        stuck.setattr(pricing, "unlink_robust", _refused)
        (said,) = await _route_answers_after_a_boot_pass(built_stores.projects_root, jobs)
        assert unhosted in said, f"a refused drop stood for another route: {said}"
    assert await _route_answers_after_a_boot_pass(built_stores.projects_root, jobs) == [], (
        "a pass that refreshed every route answer left a fault standing"
    )


def _refused(path: Path) -> NoReturn:
    raise PermissionError(f"refused: {path.name}")


# 2. A fault in the world's own policy raises out of the call, never reads as a refused one


async def test_a_fault_in_the_worlds_policy_raises_past_the_call_it_answers(
    offline: Router, tmp_path: Path
) -> None:
    def stale(_body: dict[str, Any]) -> NoReturn:
        raise KeyError("a key the policy no longer finds")

    offline.llm.says = stale
    spec = JudgeSpec(
        name="answer_grounding",
        stages=[JudgeStage(role="grade", temperature=0.0, model="gpt-4o", provider="openai")],
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
