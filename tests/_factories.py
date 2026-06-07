"""Pure data builders + on-disk seeding for the test suite.

Every test rides these instead of hand-rolling campaign/cycle JSON or the
``build_stores`` + mkdir dance. Builders return plain dicts (override any field
via kwargs); ``seed_campaign_cycle`` lays a canonical campaign+cycle tree on
disk through the real store path helpers and returns a typed handle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import PhaseRecord
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.paths import campaign_root_dir_for, cycle_dir_for

DEFAULT_CAMPAIGN_ID = "testds__20260101-000000"
DEFAULT_CYCLE_ID = "cycle_test_0001"


def make_campaign_dict(
    campaign_id: str = DEFAULT_CAMPAIGN_ID,
    root_cycle_id: str = DEFAULT_CYCLE_ID,
    **overrides: Any,
) -> dict[str, Any]:
    """Canonical ``campaign.json`` body; override any field via kwargs."""
    base: dict[str, Any] = {
        "campaign_id": campaign_id,
        "dataset_name": campaign_id.split("__", 1)[0],
        "label": "",
        "created_at": "2026-01-01T00:00:00+00:00",
        "status": "active",
        "root_cycle_id": root_cycle_id,
        "root_content_hash": "",
        "backend_id": "test_backend",
        "config": {},
    }
    return base | overrides


def make_index_dict(**overrides: Any) -> dict[str, Any]:
    """Canonical cycle ``index.json`` body; override any field via kwargs."""
    base: dict[str, Any] = {
        "backend_id": "test_backend",
        "parent_session_id": "s_test",
        "sibling_kind": "root",
        "status": "active",
        "rounds": [],
        "n_rounds": 0,
    }
    return base | overrides


def make_dashboard_dict(**overrides: Any) -> dict[str, Any]:
    """Canonical per-cycle ``dashboard.json`` body."""
    return {"phase": "l1_score", "round": 0, "best": 0.5} | overrides


def make_round(round_num: int) -> dict[str, Any]:
    """One round-index entry / ``round_NNNN.json`` body."""
    return {
        "round_id": f"round_{round_num}",
        "round": round_num,
        "accuracy": round(0.5 + 0.1 * round_num, 4),
        "label": "origin" if round_num == 0 else f"r{round_num}",
    }


@dataclass(frozen=True)
class CycleHandle:
    """What ``seed_campaign_cycle`` hands back — paths + ids, no globals."""

    campaign_id: str
    cycle_id: str
    campaign_dir: Path
    cycle_dir: Path


def seed_campaign_cycle(
    tenant_root: Path,
    *,
    campaign_id: str = DEFAULT_CAMPAIGN_ID,
    cycle_id: str = DEFAULT_CYCLE_ID,
    n_rounds: int = 1,
    with_ledger: bool = False,
    index: dict[str, Any] | None = None,
    campaign: dict[str, Any] | None = None,
    dashboard: dict[str, Any] | None = None,
    with_candidates: bool = False,
) -> CycleHandle:
    """Lay a canonical campaign+cycle tree on disk; return a handle.

    Replaces every per-file scaffold (``seeded_tenant`` disk seed, ``_seed_cycle``,
    ``session_campaign_cycle_dirs``). ``tenant_root`` is ``stores.base_dir``.
    """
    campaign_dir = campaign_root_dir_for(tenant_root, campaign_id)
    cycle_dir = cycle_dir_for(tenant_root, campaign_id, cycle_id)
    rounds_dir = cycle_dir / "rounds"
    rounds_dir.mkdir(parents=True, exist_ok=True)

    rounds = [make_round(r) for r in range(n_rounds)]
    for entry in rounds:
        (rounds_dir / f"round_{entry['round']:04d}.json").write_text(
            json.dumps(entry), encoding="utf-8"
        )

    if with_candidates:
        candidates_dir = cycle_dir / ".runtime" / "cache" / "candidates"
        candidates_dir.mkdir(parents=True, exist_ok=True)
        for entry in rounds:
            (candidates_dir / f"round_{entry['round']:04d}.json").write_text("[]", encoding="utf-8")

    (campaign_dir / "campaign.json").write_text(
        json.dumps(make_campaign_dict(campaign_id, cycle_id, **(campaign or {}))),
        encoding="utf-8",
    )
    (cycle_dir / "index.json").write_text(
        json.dumps(make_index_dict(rounds=rounds, n_rounds=n_rounds, **(index or {}))),
        encoding="utf-8",
    )
    (cycle_dir / "dashboard.json").write_text(
        json.dumps(make_dashboard_dict(**(dashboard or {}))), encoding="utf-8"
    )
    (cycle_dir / "log.md").write_text("# Campaign log\n", encoding="utf-8")

    if with_ledger:
        ledger = CycleEventLog.open(CycleDir(cycle_dir))
        for entry in rounds:
            ledger.append(PhaseRecord(phase="round", event="enter", round=entry["round"]))
            ledger.append(
                PhaseRecord(
                    phase="round",
                    event="complete",
                    round=entry["round"],
                    payload={"accuracy": entry["accuracy"]},
                )
            )

    return CycleHandle(campaign_id, cycle_id, campaign_dir, cycle_dir)


def make_archive_item(
    sample_id: int = 0,
    *,
    hit: bool = True,
    fitness: float = 1.0,
    query: str | None = None,
    ground_truth: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """One ``measurements`` row for a saved archive run. ``**extra`` overrides or
    adds fields (e.g. ``predicted="ERROR"``, ``error=...``, ``error_category=...``)."""
    return {
        "sample_id": sample_id,
        "query": query if query is not None else f"q_{sample_id}",
        "ground_truth": ground_truth if ground_truth is not None else f"gt_{sample_id}",
        "predicted": "pred",
        "hit": hit,
        "fitness": fitness,
        "pipeline_data": {"terminated_at": "llm_only"},
    } | extra


def make_archive_run(
    run_id: str,
    *,
    items: list[dict[str, Any]] | None = None,
    node_configs: list[Any] | None = None,
    pipeline_params: dict[str, Any] | None = None,
    content_hash: str | None = None,
    scores: dict[str, Any] | None = None,
    created_at: str = "2026-01-01T00:00:00Z",
    **extra: Any,
) -> dict[str, Any]:
    """The ``MeasurementArchive.save`` envelope. Pass either ``node_configs`` or
    ``pipeline_params`` (the other is derived); ``**extra`` adds fields like
    ``dataset_name=``. Collapses the per-test archive seeders into one builder."""
    items = items or []
    if node_configs is None and pipeline_params is not None:
        node_configs = list(pipeline_params.items())
    if pipeline_params is None and node_configs is not None:
        pipeline_params = dict(node_configs)
    return {
        "run_id": run_id,
        "name": run_id,
        "content_hash": content_hash or f"hash_{run_id}",
        "prompt_fields_id": "pf_x",
        "item_count": len(items),
        "scores": scores or {"accuracy": 0.5, "total": len(items)},
        "node_configs": node_configs or [],
        "pipeline_params": pipeline_params or {},
        "created_at": created_at,
        "measurements": items,
    } | extra
