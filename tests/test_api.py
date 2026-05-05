"""Read-only webapp API endpoints — first real consumer of the per-cycle ledger.

Pins the contract for the 7 per-cycle live-read endpoints introduced in
the Tier 3.2 cleanup. Each endpoint round-trips a typed envelope from a
seeded fixture cycle so the ledger structure has at least one external
consumer that fails loudly when the record schema drifts.

Also covers the M11 webapp-preview surface: ``/active`` pointer, the
``/campaigns/{cycle_id}/files`` recursive listing, the
``/campaigns/{cycle_id}/file`` content read, and the static ``/ui``
mount.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import DecisionKind, DecisionRecord, PhaseRecord
from promptpotter.infrastructure.ledger import CycleLedger
from promptpotter.infrastructure.store import build_stores, campaign_dir_for, root_dir_for
from promptpotter.main import WEBAPP_DIR, app


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
    rounds_dir = cycle_dir / "rounds"
    rounds_dir.mkdir()
    (rounds_dir / "round_0000.json").write_text(
        json.dumps({"round": 0, "accuracy": 0.5}),
        encoding="utf-8",
    )

    ledger = CycleLedger.open(CycleDir(cycle_dir))
    ledger.append(PhaseRecord(phase="round", event="enter", round=0))
    ledger.append(
        DecisionRecord(
            kind=DecisionKind.ROUND_WINNER,
            inputs_ref={"candidate_ids": ["c1"], "round_num": 0},
            outcome="c1",
            round=0,
        )
    )
    ledger.append(PhaseRecord(phase="round", event="complete", round=0, payload={"acc": 0.6}))
    ledger.append(
        DecisionRecord(
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


# ===========================================================================
# M11 webapp preview — /active, /files, /file, /ui mount
# ===========================================================================


def test_active_returns_404_when_pointer_missing(
    seeded_tenant: tuple[TestClient, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, _ = seeded_tenant
    monkeypatch.setattr(
        "promptpotter.infrastructure.store.active_pointer._ACTIVE_SESSION_PATH",
        tmp_path / "missing_active.json",
    )
    resp = client.get("/api/v1/active")
    assert resp.status_code == 404


def test_active_returns_pointer_when_present(
    seeded_tenant: tuple[TestClient, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, cycle_id = seeded_tenant
    pointer_path = tmp_path / "active.json"
    pointer_path.write_text(
        json.dumps({"tenant_id": "default", "session_id": "s_abc", "cycle_id": cycle_id}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "promptpotter.infrastructure.store.active_pointer._ACTIVE_SESSION_PATH",
        pointer_path,
    )
    resp = client.get("/api/v1/active")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"tenant_id": "default", "session_id": "s_abc", "cycle_id": cycle_id}


def test_files_lists_cycle_artifacts(seeded_tenant: tuple[TestClient, str]) -> None:
    client, cycle_id = seeded_tenant
    resp = client.get(f"/api/v1/campaigns/{cycle_id}/files")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cycle_id"] == cycle_id
    assert body["is_fork"] is False  # root cycle, not a fork
    paths = {e["path"] for e in body["entries"]}
    assert {"dashboard.json", "log.md", "rounds/round_0000.json"} <= paths
    # All entries on a non-fork cycle must be scope=cycle.
    assert all(e["scope"] == "cycle" for e in body["entries"])


def test_file_returns_json_content(seeded_tenant: tuple[TestClient, str]) -> None:
    client, cycle_id = seeded_tenant
    resp = client.get(
        f"/api/v1/campaigns/{cycle_id}/file",
        params={"scope": "cycle", "path": "dashboard.json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["content_type"] == "json"
    assert json.loads(body["content"])["round"] == 3


def test_file_rejects_path_traversal(seeded_tenant: tuple[TestClient, str]) -> None:
    client, cycle_id = seeded_tenant
    for bad in ("../etc/passwd", "/abs/path", "rounds\\..\\x"):
        resp = client.get(
            f"/api/v1/campaigns/{cycle_id}/file",
            params={"scope": "cycle", "path": bad},
        )
        assert resp.status_code == 400, f"expected 400 for {bad!r}, got {resp.status_code}"


def test_file_404_on_missing(seeded_tenant: tuple[TestClient, str]) -> None:
    client, cycle_id = seeded_tenant
    resp = client.get(
        f"/api/v1/campaigns/{cycle_id}/file",
        params={"scope": "cycle", "path": "no_such.json"},
    )
    assert resp.status_code == 404


def test_file_oversize_returns_null_content(
    seeded_tenant: tuple[TestClient, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, cycle_id = seeded_tenant
    # Force the size threshold below dashboard.json's actual size.
    monkeypatch.setattr("promptpotter.presentation.api._MAX_PREVIEW_BYTES", 1)
    resp = client.get(
        f"/api/v1/campaigns/{cycle_id}/file",
        params={"scope": "cycle", "path": "dashboard.json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] is None
    assert body["content_type"] == "text"


def test_optimizer_pipeline_returns_view_topology() -> None:
    """``/optimizer/pipeline`` must expose the bundled ``view`` block (nodes +
    edges) — what the webapp renders the workflow from."""
    client = TestClient(app)
    resp = client.get("/api/v1/optimizer/pipeline")
    assert resp.status_code == 200
    body = resp.json()
    assert "view" in body
    view = body["view"]
    assert {"nodes", "edges"} <= view.keys()
    node_ids = {n["id"] for n in view["nodes"]}
    # Must include the L1 inner-loop trio + scoring + IO endpoints.
    assert {"input", "l1_generate", "l1_score", "l1_critique", "output"} <= node_ids


def test_ui_mount_serves_index_when_present() -> None:
    client = TestClient(app)
    resp = client.get("/ui/")
    if WEBAPP_DIR.exists() and (WEBAPP_DIR / "index.html").exists():
        assert resp.status_code == 200
        assert "<html" in resp.text.lower()
    else:
        assert resp.status_code == 404
