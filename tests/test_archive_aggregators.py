"""Cross-cycle aggregators over the MeasurementArchive — data-shape contracts.

Guards three things:

1. ``StopReason.DIAG_COMPLETE`` is a named member (M10 diag mode wire shape).
2. ``build_archive_observations`` keys candidates by ``content_hash[:12]``
   and skips error/missing-id rows (the input contract Rasch fitting relies on).
3. ``build_individuals_rows`` aggregates per-sample score correctly across
   archive entries (the numerical contract behind ``individuals.md``).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from promptpotter.application.intelligence.hard_sample_archive import (
    build_archive_observations,
)
from promptpotter.application.leaderboard import (
    build_individuals_rows,
)
from promptpotter.domain.phases import StopReason
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive


def _seed(
    archive: MeasurementArchive,
    *,
    run_id: str,
    content_hash: str,
    items: list[dict[str, Any]],
) -> None:
    archive.save(
        "bk",
        run_id,
        {
            "run_id": run_id,
            "name": run_id,
            "content_hash": content_hash,
            "prompt_fields_id": "pf_x",
            "item_count": len(items),
            "scores": {"accuracy": 0.5},
            "node_configs": [["llm_only", {"model": "stub"}]],
            "pipeline_params": {"llm_only": {"model": "stub"}},
            "created_at": "2026-04-30T00:00:00Z",
            "measurements": items,
        },
    )


def _item(sid: int, *, hit: bool, score: float = 1.0, error: bool = False) -> dict[str, Any]:
    return {
        "sample_id": sid,
        "query": f"q_{sid}",
        "ground_truth": f"gt_{sid}",
        "predicted": "ERROR" if error else "pred",
        "hit": hit,
        "score": score,
        "error": error,
        "pipeline_data": {"terminated_at": "llm_only"},
    }


@pytest.fixture
def archive(tmp_path: Path) -> MeasurementArchive:
    return MeasurementArchive(tmp_path)


def test_diag_complete_is_a_named_stop_reason() -> None:
    """Diag mode wire shape: the new stop reason must exist for the runner +
    dashboard to recognize a diag-mode halt."""
    assert StopReason.DIAG_COMPLETE.value == "diag_complete"


def test_archive_observations_use_content_hash_prefix(archive: MeasurementArchive) -> None:
    """Candidate IDs are ``content_hash[:12]`` so the cross-cycle Rasch fit
    can identify the same JSP across cycles, sessions, and forks. Error
    rows and rows missing ``sample_id`` must be dropped before fitting."""
    long_hash = "abcdef0123456789aaaa"
    _seed(
        archive,
        run_id="run_1",
        content_hash=long_hash,
        items=[
            _item(1, hit=True),
            _item(2, hit=False),
            _item(3, hit=False, error=True),  # dropped: error row
            {"hit": True},  # dropped: missing sample_id
        ],
    )
    obs = build_archive_observations(archive, "bk")
    assert len(obs) == 2
    assert all(o.candidate_id == long_hash[:12] for o in obs)
    assert {o.sample_id for o in obs} == {1, 2}


def test_individuals_rows_aggregate_and_filter_errors(archive: MeasurementArchive) -> None:
    """One row per ``content_hash``; ``mean_score`` computed from non-error
    items only. The numerical contract behind ``individuals.md``."""
    _seed(
        archive,
        run_id="run_a",
        content_hash="hash_aaaaaaaaaaaaa",
        items=[
            _item(1, hit=True, score=1.0),
            _item(2, hit=True, score=0.5),
            _item(3, hit=False, score=0.0),
            _item(4, hit=False, error=True),  # dropped
        ],
    )
    _seed(
        archive,
        run_id="run_b",
        content_hash="hash_bbbbbbbbbbbbb",
        items=[
            _item(10, hit=False, score=0.0),
            _item(11, hit=False, score=0.0),
        ],
    )
    stores = SimpleNamespace(archive=archive)
    rows = build_individuals_rows(stores, "bk")
    by_hash = {r.jsp_hash: r for r in rows}
    a = by_hash["hash_aaa"]
    b = by_hash["hash_bbb"]
    assert a.n_samples == 3
    assert a.mean_score == pytest.approx((1.0 + 0.5 + 0.0) / 3)
    assert b.n_samples == 2
    assert b.mean_score == 0.0
