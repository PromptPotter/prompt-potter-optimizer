"""L4 liveness: the outer cycle heartbeats its OWN ledger while it awaits a
multi-minute inner campaign, carrying live inner progress on the record's
``detail`` — so the outer chat/dashboard never read as silent.

Regression guard for the "Run went silent" bug: the inner campaign emits only to
its own sandbox ledger, so without this heartbeat the outer ledger froze for the
whole inner run.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from promptpotter.application.optimization.dispatch.llm_call import heartbeat as heartbeat_mod
from promptpotter.application.runner import inner_recursion
from promptpotter.domain.results import CycleResult, CycleSpend, RoundResult
from promptpotter.domain.run_records import LLMCallProgressRecord
from promptpotter.infrastructure.llm import models as llm_models
from promptpotter.infrastructure.store.io import write_json


class _RecordingLedger:
    """Minimal stand-in for ``CycleEventLog`` — captures appended records."""

    def __init__(self) -> None:
        self.records: list[Any] = []

    def append(self, record: Any) -> int:
        self.records.append(record)
        return len(self.records)


def _write_inner_tasks(dataset_dir: Path) -> None:
    """`_resolve_inner_task` has no default ladder — the benchmark, its sample count, round cap
    and target score are declared, or the spawn raises. Mirrors `datasets/promptpotter-self/`."""
    write_json(
        dataset_dir / "inner_tasks.json",
        {
            "inner_benchmark": "justlogic",
            "inner_benchmark_config": {
                "n_samples_per_inner_round": 24,
                "max_inner_rounds": 7,
                "target_score": 0.6,
            },
            "tasks": [{"id": "justlogic-d67/seed-0", "inner_dataset_seed": 0}],
        },
    )


def _fake_result() -> CycleResult:
    # One real L1 round: `run_inner_cycle` excludes an evidence-free cycle (`rounds=[]` would
    # raise `InnerCycleUnscoreableError`, never reaching the `{"data": …}` this test asserts on).
    return CycleResult(
        rounds=[
            RoundResult(
                round=1,
                label="R1",
                accuracy=0.55,
                hits=11,
                total=20,
                improved=True,
                prompt_fields={},
                candidates_scored=2,
            )
        ],
        n_l1_rounds=1,
        best_accuracy=0.55,
        best_round=1,
        origin_accuracy=0.5,
        origin_level=0.5,
        round_discovered_levels=[0.55],
        winner_prompt_fields={},
        stop_reason="max_rounds",  # SUCCESS outcome — not excluded
        started_at="t0",
        finished_at="t1",
        # Fully-priced, non-zero: a cycle recording no spend is excluded as unscoreable, since
        # dividing lift by a zero cost would report it as maximally efficient.
        spend=CycleSpend(input_tokens=5000, output_tokens=2000, cost_usd=0.018),
    )


async def test_outer_ledger_gets_progress_with_detail(tmp_path: Path, monkeypatch: Any) -> None:
    # Tighten the tick so a sub-second stubbed inner run still emits several ticks.
    monkeypatch.setattr(heartbeat_mod, "HEARTBEAT_INTERVAL_S", 0.01)

    inner_dir = tmp_path / "inner_cycle"
    inner_dir.mkdir()

    async def _fake_inner(
        ctx: Any, spec: Any, overrides: Any, cycle_dir_box: dict[str, Path]
    ) -> CycleResult:
        # Publish the (fake) inner cycle dir + a dashboard.json the detail_fn tails.
        write_json(
            inner_dir / "dashboard.json",
            {"round": 2, "best": 0.55, "run_limits": {"max_rounds": 3}},
        )
        cycle_dir_box["dir"] = inner_dir
        await asyncio.sleep(0.05)  # long enough for ≥1 heartbeat tick
        return _fake_result()

    monkeypatch.setattr(inner_recursion, "_run_inner_campaign", _fake_inner)

    _write_inner_tasks(tmp_path)
    ctx = inner_recursion.InnerSpawnContext(
        inner_sandbox_root=tmp_path,
        dataset_config_dir=tmp_path,
        identity=None,  # type: ignore[arg-type]  # stubbed inner run never reads it
        shared_root=tmp_path,
    )
    inner_recursion._INNER_SPAWN.set(ctx)

    ledger = _RecordingLedger()
    llm_models._CYCLE_LEDGER.set(ledger)  # type: ignore[arg-type]

    out = await inner_recursion.run_inner_cycle("justlogic-d67/seed-0", {})
    assert "data" in out

    progress = [r for r in ledger.records if isinstance(r, LLMCallProgressRecord)]
    assert progress, "outer ledger received no heartbeat during the inner run"
    detailed = [r for r in progress if r.detail]
    assert detailed, "heartbeat records carried no inner-progress detail"
    assert any(r.detail == "inner r2/3 · best 55%" for r in detailed)
    assert all(r.call_id == "inner:justlogic-d67/seed-0" for r in progress)


async def test_outer_sample_deadline_cancels_the_inner_campaign(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # SILENT resource leak. The deadline only bounds SPEND because the inner campaign is awaited
    # directly, making it the awaiting coroutine's `_fut_waiter` so the timeout's cancellation
    # reaches it. Detach that await — `asyncio.shield`, `asyncio.wait`, a `gather` — and the
    # timed-out campaign keeps running, keeps calling the optimizer, and keeps billing tokens
    # against a sample nobody will read. Nothing errors; the run just costs more and ends later.
    # So this pins the PROPERTY (the campaign stops), not the shape of the code that achieves it.
    monkeypatch.setattr(heartbeat_mod, "HEARTBEAT_INTERVAL_S", 0.01)
    monkeypatch.setattr(inner_recursion, "OUTER_SAMPLE_WALL_S_PER_ROUND", 0.02)

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _hanging_inner(
        ctx: Any, spec: Any, overrides: Any, cycle_dir_box: dict[str, Path]
    ) -> CycleResult:
        started.set()
        try:
            await asyncio.sleep(30)  # far past the deadline
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return _fake_result()

    monkeypatch.setattr(inner_recursion, "_run_inner_campaign", _hanging_inner)
    _write_inner_tasks(tmp_path)
    inner_recursion._INNER_SPAWN.set(
        inner_recursion.InnerSpawnContext(
            inner_sandbox_root=tmp_path,
            dataset_config_dir=tmp_path,
            identity=None,  # type: ignore[arg-type]
            shared_root=tmp_path,
        )
    )
    llm_models._CYCLE_LEDGER.set(_RecordingLedger())  # type: ignore[arg-type]

    with pytest.raises(inner_recursion.InnerCycleUnscoreableError, match="wall-clock deadline"):
        await inner_recursion.run_inner_cycle("justlogic-d67/seed-0", {})

    assert started.is_set(), "the inner campaign never started — the deadline proved nothing"
    assert cancelled.is_set(), "the inner campaign outlived its deadline and kept spending"
