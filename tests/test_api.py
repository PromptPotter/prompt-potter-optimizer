"""Read-only webapp API endpoints — first real consumer of the per-cycle ledger.

Pins the contract for the per-cycle live-read endpoints. Each endpoint
round-trips a typed envelope from a seeded fixture campaign+cycle so the
ledger structure has at least one external consumer that fails loudly
when the record schema drifts.

A campaign is a forest of N sessions: routes carry both ``campaign_id``
and ``cycle_id``; ``dashboard.json`` is per-cycle (each cycle owns its own
file in its own cycle dir).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import (
    PhaseRecord,
    ResumeCheckpointKind,
    ResumeCheckpointRecord,
)
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store import (
    build_stores,
    campaign_root_dir_for,
    cycle_dir_for,
)
from promptpotter.main import WEBAPP_DIR, app
from promptpotter.presentation.api.deps import build_stores_from_identity
from promptpotter.shared.identity import default_identity

_CAMPAIGN_ID = "apitest__20260101-000000"


@pytest.fixture
def seeded_tenant(tmp_path: Path) -> Iterator[tuple[TestClient, str, str]]:
    """Spin up a tenant with one campaign + cycle (manifest, telemetry, ledger)."""
    projects_root = tmp_path / "projects"
    datasets_root = tmp_path / "datasets"
    cycle_id = "cycle_apitest_001"

    stores = build_stores(
        default_identity(), projects_root=projects_root, datasets_root=datasets_root
    )

    campaign_dir = campaign_root_dir_for(stores.base_dir, _CAMPAIGN_ID)
    cycle_dir = cycle_dir_for(stores.base_dir, _CAMPAIGN_ID, cycle_id)
    campaign_dir.mkdir(parents=True, exist_ok=True)
    cycle_dir.mkdir(parents=True, exist_ok=True)

    (campaign_dir / "campaign.json").write_text(
        json.dumps(
            {
                "campaign_id": _CAMPAIGN_ID,
                "dataset_name": "apitest",
                "label": "",
                "created_at": "2026-01-01T00:00:00+00:00",
                "status": "active",
                "root_cycle_id": cycle_id,
                "root_content_hash": "",
                "backend_id": "test_backend",
                "config": {},
            }
        ),
        encoding="utf-8",
    )
    # dashboard.json is per-cycle — it lives in this cycle's own dir.
    (cycle_dir / "dashboard.json").write_text(
        json.dumps({"phase": "l1_score", "round": 3, "best": 0.812}),
        encoding="utf-8",
    )
    (cycle_dir / "index.json").write_text(
        json.dumps(
            {
                "backend_id": "test_backend",
                "parent_session_id": "s_abc",
                "sibling_kind": "root",
                "status": "active",
                "rounds": [{"round": 0, "accuracy": 0.5, "label": "origin"}],
                "n_rounds": 1,
            }
        ),
        encoding="utf-8",
    )
    (cycle_dir / "log.md").write_text(
        "# Campaign log\n\n## Round 0\norigin=0.5\n",
        encoding="utf-8",
    )
    rounds_dir = cycle_dir / "rounds"
    rounds_dir.mkdir()
    (rounds_dir / "round_0000.json").write_text(
        json.dumps({"round": 0, "accuracy": 0.5}),
        encoding="utf-8",
    )

    ledger = CycleEventLog.open(CycleDir(cycle_dir))
    ledger.append(PhaseRecord(phase="round", event="enter", round=0))
    ledger.append(
        ResumeCheckpointRecord(
            kind=ResumeCheckpointKind.ROUND_WINNER,
            inputs_ref={"candidate_ids": ["c1"], "round_num": 0},
            outcome="c1",
            round=0,
        )
    )
    ledger.append(PhaseRecord(phase="round", event="complete", round=0, payload={"acc": 0.6}))
    ledger.append(
        ResumeCheckpointRecord(
            kind=ResumeCheckpointKind.FORK_CUT,
            inputs_ref={"from_round": 1},
            outcome="cycle_apitest_001_fork_abc",
            data={"forked_at": "2026-04-30T12:00:00+00:00"},
        )
    )

    def _override_stores():
        return stores

    app.dependency_overrides[build_stores_from_identity] = _override_stores
    try:
        yield TestClient(app), _CAMPAIGN_ID, cycle_id
    finally:
        app.dependency_overrides.clear()


def test_campaigns_list_returns_manifest(seeded_tenant: tuple[TestClient, str, str]) -> None:
    client, campaign_id, _ = seeded_tenant
    resp = client.get("/api/v1/campaigns")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["campaigns"][0]["campaign_id"] == campaign_id
    assert body["campaigns"][0]["dataset_name"] == "apitest"


def test_log_md_returns_markdown_envelope(seeded_tenant: tuple[TestClient, str, str]) -> None:
    client, campaign_id, cycle_id = seeded_tenant
    resp = client.get(f"/api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/log_md")
    assert resp.status_code == 200
    body = resp.json()
    assert body["scope_id"] == cycle_id
    assert "# Campaign log" in body["markdown"]


def test_ledger_returns_typed_records(seeded_tenant: tuple[TestClient, str, str]) -> None:
    client, campaign_id, cycle_id = seeded_tenant
    resp = client.get(f"/api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/ledger")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cycle_id"] == cycle_id
    assert body["next_offset"] == 4
    types = [r["record_type"] for r in body["records"]]
    assert types == ["phase", "decision", "phase", "decision"]


def test_ledger_since_offset_skips_earlier_records(
    seeded_tenant: tuple[TestClient, str, str],
) -> None:
    client, campaign_id, cycle_id = seeded_tenant
    resp = client.get(f"/api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/ledger?since=2")
    body = resp.json()
    assert [r["offset"] for r in body["records"]] == [2, 3]
    assert body["next_offset"] == 4


def test_decisions_filters_to_decision_records(
    seeded_tenant: tuple[TestClient, str, str],
) -> None:
    client, campaign_id, cycle_id = seeded_tenant
    resp = client.get(f"/api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/decisions")
    assert resp.status_code == 200
    body = resp.json()
    kinds = [d["kind"] for d in body["decisions"]]
    assert kinds == ["round_winner", "fork_cut"]


def test_forks_derives_from_fork_cut_records(seeded_tenant: tuple[TestClient, str, str]) -> None:
    client, campaign_id, cycle_id = seeded_tenant
    resp = client.get(f"/api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/forks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["parent_cycle_id"] == cycle_id
    assert len(body["forks"]) == 1
    fork = body["forks"][0]
    assert fork["fork_cycle_id"] == "cycle_apitest_001_fork_abc"
    assert fork["from_round"] == 1
    assert fork["forked_at"] == "2026-04-30T12:00:00+00:00"


def test_dashboard_is_per_cycle_not_session_root(
    seeded_tenant: tuple[TestClient, str, str],
) -> None:
    """Each cycle serves its OWN dashboard.json — a fork does not collapse to
    the session root's file (state-sync Phase 2)."""
    client, campaign_id, cycle_id = seeded_tenant

    # Root cycle's dashboard (seeded at round 3). A fork carries its own.
    stores = app.dependency_overrides[build_stores_from_identity]()
    fork_id = f"{cycle_id}_fork_abc123"
    fork_dir = cycle_dir_for(stores.base_dir, campaign_id, fork_id)
    fork_dir.mkdir(parents=True, exist_ok=True)
    (fork_dir / "dashboard.json").write_text(
        json.dumps({"phase": "l1_score", "round": 7, "best": 0.91}), encoding="utf-8"
    )

    root_resp = client.get(f"/api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/dashboard")
    fork_resp = client.get(f"/api/v1/campaigns/{campaign_id}/cycles/{fork_id}/dashboard")
    assert root_resp.json()["round"] == 3
    assert fork_resp.json()["round"] == 7  # the fork's own file, not the root's


# ===========================================================================
# Webapp preview — /sessions/active, /files, /file, root mount
# ===========================================================================


def test_active_returns_404_when_pointer_missing(
    seeded_tenant: tuple[TestClient, str, str],
) -> None:
    """No pointer file under the tenant's workspace dir → 404."""
    client, _, _ = seeded_tenant
    resp = client.get("/api/v1/sessions/active")
    assert resp.status_code == 404


def test_active_returns_pointer_when_present(
    seeded_tenant: tuple[TestClient, str, str],
) -> None:
    """Pointer written through the per-tenant API surfaces on /sessions/active."""
    from promptpotter.infrastructure.store import save_active_pointer

    client, campaign_id, cycle_id = seeded_tenant
    # The seeded_tenant fixture overrides build_stores_from_identity to a
    # Stores rooted at tmp_path/projects/default; save through the same root.
    stores = app.dependency_overrides[build_stores_from_identity]()
    save_active_pointer(
        stores.tenant_id,
        "s_abc",
        campaign_id,
        cycle_id,
        projects_root=stores.projects_root,
    )
    resp = client.get("/api/v1/sessions/active")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "tenant_id": "default",
        "session_id": "s_abc",
        "campaign_id": campaign_id,
        "cycle_id": cycle_id,
    }

    # /sessions/active/live-state is the stable façade: active pointer → live state.
    live = client.get("/api/v1/sessions/active/live-state")
    assert live.status_code == 200
    body = live.json()
    assert body["round"] == 3
    # Run-control facts the dashboard projection doesn't carry: default to
    # not-paused / uncapped when the runtime flags are absent.
    assert body["is_paused"] is False
    assert body["current_spend_cap_usd"] is None

    # With a pause.flag + spend_cap.json on the active cycle, /live-state reflects
    # both — the run controls read pause-state from here, not from telemetry
    # freshness (a paused runner emits no events).
    runtime = cycle_dir_for(stores.base_dir, campaign_id, cycle_id) / ".runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "pause.flag").write_text("requested_at=now\n", encoding="utf-8")
    (runtime / "spend_cap.json").write_text('{"max_usd": 4.5}', encoding="utf-8")
    live2 = client.get("/api/v1/sessions/active/live-state").json()
    assert live2["is_paused"] is True
    assert live2["current_spend_cap_usd"] == 4.5


def test_files_lists_cycle_and_campaign_artifacts(
    seeded_tenant: tuple[TestClient, str, str],
) -> None:
    client, campaign_id, cycle_id = seeded_tenant
    resp = client.get(f"/api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/files")
    assert resp.status_code == 200
    body = resp.json()
    assert body["campaign_id"] == campaign_id
    assert body["cycle_id"] == cycle_id
    cycle_paths = {e["path"] for e in body["entries"] if e["scope"] == "cycle"}
    campaign_paths = {e["path"] for e in body["entries"] if e["scope"] == "campaign"}
    # dashboard.json is per-cycle — it sits in this cycle's own dir, not
    # the campaign dir.
    assert {"index.json", "log.md", "rounds/round_0000.json", "dashboard.json"} <= cycle_paths
    assert {"campaign.json"} <= campaign_paths


def test_file_returns_json_content(seeded_tenant: tuple[TestClient, str, str]) -> None:
    client, campaign_id, cycle_id = seeded_tenant
    resp = client.get(
        f"/api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/file",
        params={"scope": "cycle", "path": "dashboard.json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["content_type"] == "json"
    assert json.loads(body["content"])["round"] == 3


def test_file_rejects_path_traversal(seeded_tenant: tuple[TestClient, str, str]) -> None:
    client, campaign_id, cycle_id = seeded_tenant
    for bad in ("../etc/passwd", "/abs/path", "rounds\\..\\x"):
        resp = client.get(
            f"/api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/file",
            params={"scope": "cycle", "path": bad},
        )
        assert resp.status_code == 400, f"expected 400 for {bad!r}, got {resp.status_code}"


def test_file_404_on_missing(seeded_tenant: tuple[TestClient, str, str]) -> None:
    client, campaign_id, cycle_id = seeded_tenant
    resp = client.get(
        f"/api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/file",
        params={"scope": "cycle", "path": "no_such.json"},
    )
    assert resp.status_code == 404


def test_file_oversize_returns_null_content(
    seeded_tenant: tuple[TestClient, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, campaign_id, cycle_id = seeded_tenant
    # Force the size threshold below dashboard.json's actual size.
    monkeypatch.setattr(
        "promptpotter.presentation.api.routers.campaigns.files._MAX_PREVIEW_BYTES", 1
    )
    resp = client.get(
        f"/api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/file",
        params={"scope": "cycle", "path": "dashboard.json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] is None
    assert body["content_type"] == "text"


def test_dataset_ingest_flows_through_draft_and_tenant_scope(
    seeded_tenant: tuple[TestClient, str, str],
) -> None:
    """``POST /datasets/ingest`` mints a tenant-scoped draft; sparse-edit lands
    on the same draft; cross-tenant lookup is 404 (existence-leak gate)."""
    from promptpotter.application.datasets.draft_campaign import DraftCampaignRegistry
    from promptpotter.domain.identity import TenantId

    client, *_ = seeded_tenant
    # The seeded_tenant fixture's stores ride on a tmp_path; TestClient runs
    # lifespan via __enter__, so use the context manager to seed the registry.
    with client:
        r = client.post(
            "/api/v1/datasets/ingest",
            files={
                "file": (
                    "tickets.csv",
                    b"query,ground_truth\nq1,refund\nq2,bug_report\n",
                    "text/csv",
                ),
            },
        )
        assert r.status_code == 200, r.text
        draft = r.json()
        assert draft["n_samples"] == 2
        assert draft["slug"] == "tickets"
        assert draft["connector"] == "termnorm"
        assert draft["max_rounds"] == 5
        # Literal `query` / `ground_truth` headers auto-confirm the column
        # mapping deterministically (no LLM, no operator click).
        assert draft["headers"] == ["query", "ground_truth"]
        assert draft["resolved"]["column.query"] == "confirmed"
        assert draft["resolved"]["column.ground_truth"] == "confirmed"
        # The auto-detected column + the template-default config knobs are
        # AUTO-sourced — once-hidden defaults made visible, not operator choices.
        assert draft["sources"]["column.query"] == "auto"
        assert draft["sources"]["connector"] == "auto"
        # Sparse edit lands a mutation; response is the full post-mutation shape.
        edit = client.post(
            "/api/v1/commands/edit-draft-campaign",
            headers={"Idempotency-Key": "test-k1"},
            json={
                "kind": "edit-draft-campaign",
                "payload": {
                    "draft_id": draft["draft_id"],
                    "patch": {"task_description": "label tickets", "max_rounds": 3},
                },
            },
        )
        assert edit.status_code == 200
        assert edit.json()["task_description"] == "label tickets"
        assert edit.json()["max_rounds"] == 3
        # Cross-tenant lookup: same draft_id, different tenant → 404, not 403.
        registry: DraftCampaignRegistry = client.app.state.draft_campaigns  # type: ignore[attr-defined]
        assert registry.get(draft["draft_id"], tenant_id=TenantId("other-tenant")) is None
        # Header-agnostic ingest: non-literal columns are accepted (the
        # literal-column gate is gone), but the mapping lands UNSET for the
        # operator to confirm before mint.
        agnostic = client.post(
            "/api/v1/datasets/ingest",
            files={"file": ("other.csv", b"foo,bar\n1,2\n", "text/csv")},
        )
        assert agnostic.status_code == 200, agnostic.text
        ag = agnostic.json()
        assert ag["headers"] == ["foo", "bar"]
        assert ag["resolved"]["column.query"] == "unset"
        assert ag["resolved"]["column.ground_truth"] == "unset"
        # Mint is gated: unset column mapping → 422 origin_incomplete (the
        # checklist fires before the backend preflight, so no network touch).
        blocked = client.post(
            "/api/v1/commands/mint-campaign-from-draft",
            headers={"Idempotency-Key": "test-gate-1"},
            json={
                "kind": "mint-campaign-from-draft",
                "payload": {"draft_id": ag["draft_id"]},
            },
        )
        assert blocked.status_code == 422, blocked.text
        gate = blocked.json()["detail"]
        assert gate["error"] == "origin_incomplete"
        # Closed set: unset columns + the once-hidden `task_description` (no
        # default framing) gate; the config knobs auto-confirm from templates.
        assert {g["field"] for g in gate["details"]["gaps"]} == {
            "column.query",
            "column.ground_truth",
            "task_description",
        }
        # Confirming the mapping via edit-draft-campaign flips those fields'
        # provenance (task_description still gates until stated).
        confirmed = client.post(
            "/api/v1/commands/edit-draft-campaign",
            headers={"Idempotency-Key": "test-gate-2"},
            json={
                "kind": "edit-draft-campaign",
                "payload": {
                    "draft_id": ag["draft_id"],
                    "patch": {"column_query": "foo", "column_ground_truth": "bar"},
                },
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["resolved"]["column.query"] == "confirmed"
        assert confirmed.json()["resolved"]["column.ground_truth"] == "confirmed"
        # An operator edit overrides the seed → STATED source (not auto).
        assert confirmed.json()["sources"]["column.query"] == "stated"


def test_mint_from_draft_503_preserves_draft_when_backend_unreachable(
    seeded_tenant: tuple[TestClient, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backend-down preflight on ``mint-campaign-from-draft`` must fail BEFORE
    ``commit_draft`` runs — so the operator can fix the backend and retry
    without re-uploading. Surfaces as HTTP 503 with the structured
    ``backend_unreachable`` error code (not 422 ``payload_invalid``).

    Post-R2: preflight is a ``Connector`` contract. We swap the registered
    termnorm connector for a sibling whose ``preflight`` raises
    :class:`BackendUnreachableError`, exercising the same code path the
    real probe would hit on a TCP failure."""
    from dataclasses import replace

    from promptpotter import connectors
    from promptpotter.connectors import BackendUnreachableError

    async def _unreachable(backend_url: str) -> None:
        raise BackendUnreachableError(
            backend_type="termnorm",
            backend_url=backend_url,
            detail="connection refused",
        )

    failing = replace(connectors.CONNECTORS["termnorm"], preflight=_unreachable)
    monkeypatch.setitem(connectors.CONNECTORS, "termnorm", failing)

    client, *_ = seeded_tenant
    with client:
        r = client.post(
            "/api/v1/datasets/ingest",
            files={
                "file": (
                    "tickets.csv",
                    b"query,ground_truth\nq1,refund\nq2,bug_report\n",
                    "text/csv",
                ),
            },
        )
        assert r.status_code == 200, r.text
        draft = r.json()

        # State the framing so the origin gate passes and we reach the backend
        # preflight (literal columns already auto-confirmed; config knobs seed
        # from templates). Without this, mint blocks at 422 before any network.
        client.post(
            "/api/v1/commands/edit-draft-campaign",
            headers={"Idempotency-Key": "test-mint-frame"},
            json={
                "kind": "edit-draft-campaign",
                "payload": {
                    "draft_id": draft["draft_id"],
                    "patch": {"task_description": "label support tickets"},
                },
            },
        )

        # First mint: backend down → 503 with the structured code, NOT a 422.
        mint = client.post(
            "/api/v1/commands/mint-campaign-from-draft",
            headers={"Idempotency-Key": "test-mint-1"},
            json={
                "kind": "mint-campaign-from-draft",
                "payload": {"draft_id": draft["draft_id"]},
            },
        )
        assert mint.status_code == 503, mint.text
        detail = mint.json()["detail"]
        assert detail["error"] == "backend_unreachable"
        assert detail["details"]["backend_type"] == "termnorm"
        assert detail["details"]["draft_id"] == draft["draft_id"]

        # Retry: draft is preserved (not 404 "draft not found") — operator can
        # fix the backend and retry without re-uploading. Still 503 here
        # because the patch keeps the backend unreachable, but the draft
        # lookup succeeded.
        retry = client.post(
            "/api/v1/commands/mint-campaign-from-draft",
            headers={"Idempotency-Key": "test-mint-2"},
            json={
                "kind": "mint-campaign-from-draft",
                "payload": {"draft_id": draft["draft_id"]},
            },
        )
        assert retry.status_code == 503, retry.text
        assert retry.json()["detail"]["error"] == "backend_unreachable"


def test_open_existing_origin_mints_canonical_without_cloning(
    seeded_tenant: tuple[TestClient, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opening an already-valid origin (demo / benchmark / owned) must mint a
    campaign against the canonical dataset_name — never uniquify into a
    ``{slug}-N`` clone, never materialize a new ``datasets/`` folder. Re-running
    is a sibling campaign under the one origin, not a fresh dataset.

    Guards the root fix: the draft is derived (``source_file = "dataset:{slug}"``),
    so ``mint-campaign-from-draft`` skips ``commit_draft`` and mints directly.
    """
    from dataclasses import replace
    from types import SimpleNamespace

    from promptpotter import connectors
    from promptpotter.application.jobs import launcher

    client, *_ = seeded_tenant
    stores = app.dependency_overrides[build_stores_from_identity]()

    # Seed a tenant-owned Origin — already complete on disk, so opening it is the
    # "existing origin" path (same branch demo/benchmark take).
    ds_dir = stores.tenant_datasets.dataset_dir("email-tagging")
    ds_dir.mkdir(parents=True, exist_ok=True)
    (ds_dir / "cache.json").write_text(
        json.dumps({"items": [{"query": "q1", "ground_truth": "a1"}]}), encoding="utf-8"
    )
    (ds_dir / "pipeline.json").write_text(
        json.dumps({"backend_type": "termnorm", "name": "email-tagging"}), encoding="utf-8"
    )
    (ds_dir / "campaign.json").write_text(
        json.dumps(
            {
                "campaign_config": {
                    "dataset_name": "email-tagging",
                    "optimization": {
                        "max_rounds": 5,
                        "improvement_threshold": 0.0,
                        "degradation_threshold": 0.0,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    (ds_dir / "task_description.md").write_text("tag emails", encoding="utf-8")
    assert stores.tenant_datasets.list_slugs() == ["email-tagging"]

    # Opt out of the network preflight + replace the heavy mint with a stub that
    # records the dataset_name it was asked to run.
    no_preflight = replace(connectors.CONNECTORS["termnorm"], preflight=None)
    monkeypatch.setitem(connectors.CONNECTORS, "termnorm", no_preflight)
    minted_against: list[str] = []

    async def _stub_mint(*, dataset_name: str, **_kw: object):
        minted_against.append(dataset_name)
        return f"{dataset_name}__deadbeef", "cycle_x", SimpleNamespace(job_id="job-x")

    monkeypatch.setattr(launcher, "mint_campaign_command", _stub_mint)

    with client:
        # Open the existing origin → canonical slug, NOT email-tagging-2.
        draft = client.post("/api/v1/datasets/email-tagging/draft")
        assert draft.status_code == 200, draft.text
        assert draft.json()["slug"] == "email-tagging"

        mint = client.post(
            "/api/v1/commands/mint-campaign-from-draft",
            headers={"Idempotency-Key": "test-derived-mint"},
            json={
                "kind": "mint-campaign-from-draft",
                "payload": {"draft_id": draft.json()["draft_id"]},
            },
        )
        assert mint.status_code == 200, mint.text
        assert mint.json()["campaign_id"] == "email-tagging__deadbeef"

    # The mint ran against the canonical origin, and NO clone folder appeared.
    assert minted_against == ["email-tagging"]
    assert stores.tenant_datasets.list_slugs() == ["email-tagging"]


def test_dataset_listing_gates_benchmarks_on_capability(
    seeded_tenant: tuple[TestClient, str, str],
) -> None:
    """``GET /datasets`` is identity-scoped — web tenants (no
    ``datasets.benchmarks.read`` capability) MUST NOT see the install's
    repo-root benchmarks. The bleed-through fix the M13 ingest arc closes."""
    client, *_ = seeded_tenant
    r = client.get("/api/v1/datasets")
    assert r.status_code == 200
    entries = r.json()["datasets"]
    tiers = {e["tier"] for e in entries}
    assert "benchmark" not in tiers, (
        "default identity must not see benchmarks unless PROMPTPOTTER_ADMIN=1"
    )


def test_optimizer_pipeline_returns_view_topology() -> None:
    """``/optimizer-pipeline`` must expose the bundled ``view`` block (nodes +
    edges) — what the webapp renders the workflow from."""
    client = TestClient(app)
    resp = client.get("/api/v1/optimizer-pipeline")
    assert resp.status_code == 200
    body = resp.json()
    assert "view" in body
    view = body["view"]
    assert {"nodes", "edges"} <= view.keys()
    node_ids = {n["id"] for n in view["nodes"]}
    # Must include the L1 inner-loop trio + scoring + IO endpoints.
    assert {"input", "l1_generate", "l1_score", "l1_critique", "output"} <= node_ids


def test_root_mount_serves_index_when_present() -> None:
    client = TestClient(app)
    resp = client.get("/")
    if WEBAPP_DIR.exists() and (WEBAPP_DIR / "index.html").exists():
        assert resp.status_code == 200
        assert "<html" in resp.text.lower()
    else:
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Connector protocol — wire payload + session lifecycle pluggability
# (merged from test_connector_protocol.py — same external-wire bucket)
# ---------------------------------------------------------------------------

from typing import Any  # noqa: E402

from promptpotter.connectors.termnorm import TermNormSession, termnorm_wire_adapter  # noqa: E402
from promptpotter.infrastructure.backend import BackendClient  # noqa: E402


def test_termnorm_wire_adapter_default_shape() -> None:
    """TermNorm adapter projects pipeline_params to {query, steps, node_config}."""
    payload = termnorm_wire_adapter(
        "what is X?",
        {
            "steps": ["fuzzy_matching", "entity_profiling"],
            "entity_profiling": {"prompt": "rank by relevance"},
            "fuzzy_matching": {"max_results": 10},
        },
    )
    assert payload["query"] == "what is X?"
    assert payload["steps"] == ["fuzzy_matching", "entity_profiling"]
    assert payload["node_config"] == {
        "entity_profiling": {"prompt": "rank by relevance"},
        "fuzzy_matching": {"max_results": 10},
    }


def test_termnorm_wire_adapter_drops_non_dict_pipeline_params() -> None:
    """Non-dict per-node values are dropped — backend contract is per-node config dict."""
    payload = termnorm_wire_adapter(
        "q",
        {"steps": ["x"], "x": {"k": "v"}, "garbage": "string-not-dict"},
    )
    assert payload["node_config"] == {"x": {"k": "v"}}


def test_pipeline_schema_to_params_is_sparse() -> None:
    """``to_pipeline_params`` emits ``{steps}`` only — no per-node defaults seeded."""
    from promptpotter.domain.pipeline_schema import (
        NodeOutputSchema,
        PipelineNode,
        PipelineSchema,
    )

    schema = PipelineSchema(
        name="t",
        version="v",
        nodes=[
            PipelineNode(
                name="llm_only",
                runtime="backend",
                node_role="ranker",
                param_keys=("provider", "model", "temperature"),
                output_schema=NodeOutputSchema(),
                current_config={"provider": "groq", "model": "x", "temperature": 0.0},
            )
        ],
    )
    pp = schema.to_pipeline_params()
    assert pp == {"steps": ["llm_only"]}, (
        f"to_pipeline_params must be sparse; per-node defaults are backend-owned. Got {pp!r}"
    )

    pp_with_override = {**pp, "llm_only": {"temperature": 0.7}}
    payload = termnorm_wire_adapter("q", pp_with_override)
    assert payload["node_config"] == {"llm_only": {"temperature": 0.7}}, (
        "Wire payload must carry only operator/optimizer overrides — not the "
        f"backend's defaults. Got {payload['node_config']!r}"
    )


def test_backend_client_uses_custom_wire_adapter() -> None:
    """BackendClient accepts a custom WireAdapter and uses it on run_query."""
    captured: dict[str, Any] = {}

    def alt_wire(query: str, pipeline_params: dict[str, Any] | None) -> dict[str, Any]:
        captured["query"] = query
        captured["pp"] = pipeline_params
        return {"prompt": query, "options": pipeline_params or {}}

    client = BackendClient(
        "http://example.invalid",
        wire_adapter=alt_wire,
        session=TermNormSession(),
    )
    payload = client._wire_adapter("hello", {"k": "v"})
    assert payload == {"prompt": "hello", "options": {"k": "v"}}
    assert captured == {"query": "hello", "pp": {"k": "v"}}


@pytest.mark.asyncio
async def test_termnorm_session_idempotent_set_terms() -> None:
    """Identical terms shouldn't re-POST /sessions — idempotency contract."""

    class _StubResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"status": "ok"}

    posted: list[Any] = []

    class _StubHttp:
        async def post(self, *args: Any, **kwargs: Any) -> _StubResponse:
            posted.append((args, kwargs))
            return _StubResponse()

    sess = TermNormSession()
    await sess.set_terms(_StubHttp(), "http://x", ["a", "b"])  # type: ignore[arg-type]
    assert len(posted) == 1
    result = await sess.set_terms(_StubHttp(), "http://x", ["a", "b"])  # type: ignore[arg-type]
    assert result["status"] == "already_initialized"
    assert len(posted) == 1
