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

from promptpotter.application.optimization.dispatch.llm_call import heartbeat as heartbeat_mod
from promptpotter.application.runner import inner_recursion
from promptpotter.domain.results import CycleResult, CycleSpend
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


def _fake_result() -> CycleResult:
    return CycleResult(
        rounds=[],
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
        spend=CycleSpend(),
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

    ctx = inner_recursion.InnerSpawnContext(
        inner_sandbox_root=tmp_path,
        dataset_config_dir=tmp_path,
        identity=None,  # type: ignore[arg-type]  # stubbed inner run never reads it
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
