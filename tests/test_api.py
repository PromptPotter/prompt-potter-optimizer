"""Read-only webapp API endpoints — first real consumer of the per-cycle ledger.

Pins the contract for the 7 per-cycle live-read endpoints introduced in
the Tier 3.2 cleanup. Each endpoint round-trips a typed envelope from a
seeded fixture cycle so the ledger structure has at least one external
consumer that fails loudly when the record schema drifts.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import Decision, DecisionKind, Phase
from promptpotter.infrastructure.ledger import RunLedger
from promptpotter.infrastructure.store import build_stores
from promptpotter.infrastructure.store.stores import campaign_dir_for, root_dir_for
from promptpotter.main import app


@pytest.fixture
def seeded_tenant(tmp_path: Path) -> Iterator[tuple[TestClient, str]]:
    """Spin up a tenant with one cycle dir containing dashboard / log.md / ledger."""
    projects_root = tmp_path / "projects"
    datasets_root = tmp_path / "datasets"
    cycle_id = "cycle_apitest_001"

    stores = build_stores(projects_root, datasets_root=datasets_root)

    root_dir = root_dir_for(stores.base_dir, cycle_id)
    cycle_dir = campaign_dir_for(stores.base_dir, cycle_id)
    root_dir.mkdir(parents=True, exist_ok=True)
    cycle_dir.mkdir(parents=True, exist_ok=True)

    (root_dir / "dashboard.json").write_text(
        json.dumps({"phase": "l1_score", "round": 3, "best": 0.812}),
        encoding="utf-8",
    )
    (cycle_dir / "log.md").write_text(
        "# Campaign log\n\n## Round 0\nbaseline=0.5\n",
        encoding="utf-8",
    )

    ledger = RunLedger.open(CycleDir(cycle_dir))
    ledger.append(Phase(phase="round", event="enter", round=0))
    ledger.append(
        Decision(
            kind=DecisionKind.ROUND_WINNER,
            inputs_ref={"candidate_ids": ["c1"], "round_num": 0},
            outcome="c1",
            round=0,
        )
    )
    ledger.append(Phase(phase="round", event="complete", round=0, payload={"acc": 0.6}))
    ledger.append(
        Decision(
            kind=DecisionKind.FORK_CUT,
            inputs_ref={"from_round": 1},
            outcome="cycle_apitest_001_fork_abc",
            data={"forked_at": "2026-04-30T12:00:00+00:00"},
        )
    )

    def _override_stores():
        return stores

    app.dependency_overrides[build_stores] = _override_stores
    try:
        yield TestClient(app), cycle_id
    finally:
        app.dependency_overrides.clear()


def test_dashboard_returns_verbatim_state(seeded_tenant: tuple[TestClient, str]) -> None:
    client, cycle_id = seeded_tenant
    resp = client.get(f"/api/v1/campaigns/{cycle_id}/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cycle_id"] == cycle_id
    assert body["data"]["phase"] == "l1_score"
    assert body["data"]["round"] == 3


def test_dashboard_404_on_missing_cycle(seeded_tenant: tuple[TestClient, str]) -> None:
    client, _ = seeded_tenant
    resp = client.get("/api/v1/campaigns/nonexistent_cycle/dashboard")
    assert resp.status_code == 404


def test_log_md_returns_markdown_envelope(seeded_tenant: tuple[TestClient, str]) -> None:
    client, cycle_id = seeded_tenant
    resp = client.get(f"/api/v1/campaigns/{cycle_id}/log_md")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cycle_id"] == cycle_id
    assert "# Campaign log" in body["markdown"]


def test_ledger_returns_typed_records(seeded_tenant: tuple[TestClient, str]) -> None:
    client, cycle_id = seeded_tenant
    resp = client.get(f"/api/v1/campaigns/{cycle_id}/ledger")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cycle_id"] == cycle_id
    assert body["next_offset"] == 4
    types = [r["record_type"] for r in body["records"]]
    assert types == ["phase", "decision", "phase", "decision"]


def test_ledger_since_offset_skips_earlier_records(seeded_tenant: tuple[TestClient, str]) -> None:
    client, cycle_id = seeded_tenant
    resp = client.get(f"/api/v1/campaigns/{cycle_id}/ledger?since=2")
    body = resp.json()
    assert [r["offset"] for r in body["records"]] == [2, 3]
    assert body["next_offset"] == 4


def test_decisions_filters_to_decision_records(seeded_tenant: tuple[TestClient, str]) -> None:
    client, cycle_id = seeded_tenant
    resp = client.get(f"/api/v1/campaigns/{cycle_id}/decisions")
    assert resp.status_code == 200
    body = resp.json()
    kinds = [d["kind"] for d in body["decisions"]]
    assert kinds == ["round_winner", "fork_cut"]


def test_forks_derives_from_fork_cut_records(seeded_tenant: tuple[TestClient, str]) -> None:
    client, cycle_id = seeded_tenant
    resp = client.get(f"/api/v1/campaigns/{cycle_id}/forks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["parent_cycle_id"] == cycle_id
    assert len(body["forks"]) == 1
    fork = body["forks"][0]
    assert fork["fork_cycle_id"] == "cycle_apitest_001_fork_abc"
    assert fork["from_round"] == 1
    assert fork["forked_at"] == "2026-04-30T12:00:00+00:00"
