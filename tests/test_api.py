"""Read-only webapp API endpoints — first real consumer of the per-cycle ledger.

Pins the contract for the per-cycle live-read endpoints. Each endpoint
round-trips a typed envelope from a seeded fixture campaign+cycle so the
ledger structure has at least one external consumer that fails loudly
when the record schema drifts.

A campaign is a forest of N sessions: routes carry both ``campaign_id``
and ``cycle_id``; ``dashboard.json`` is per-session (in the session's
root cycle dir).
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

_CAMPAIGN_ID = "apitest__20260101-000000"


@pytest.fixture
def seeded_tenant(tmp_path: Path) -> Iterator[tuple[TestClient, str, str]]:
    """Spin up a tenant with one campaign + cycle (manifest, telemetry, ledger)."""
    projects_root = tmp_path / "projects"
    datasets_root = tmp_path / "datasets"
    cycle_id = "cycle_apitest_001"

    stores = build_stores(projects_root, datasets_root=datasets_root)

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
    # dashboard.json is per-session — it lives in the session's root cycle dir.
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

    app.dependency_overrides[build_stores] = _override_stores
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


def test_dashboard_resolves_at_session_family_root(
    seeded_tenant: tuple[TestClient, str, str],
) -> None:
    """The dashboard route resolves the session-family root server-side."""
    client, campaign_id, cycle_id = seeded_tenant
    resp = client.get(f"/api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/dashboard")
    assert resp.status_code == 200
    assert resp.json()["round"] == 3


# ===========================================================================
# Webapp preview — /active, /files, /file, /ui mount
# ===========================================================================


def test_active_returns_404_when_pointer_missing(
    seeded_tenant: tuple[TestClient, str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, _, _ = seeded_tenant
    monkeypatch.setattr(
        "promptpotter.infrastructure.store._ACTIVE_SESSION_PATH",
        tmp_path / "missing_active.json",
    )
    resp = client.get("/api/v1/active")
    assert resp.status_code == 404


def test_active_returns_pointer_when_present(
    seeded_tenant: tuple[TestClient, str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, campaign_id, cycle_id = seeded_tenant
    pointer_path = tmp_path / "active.json"
    pointer_path.write_text(
        json.dumps(
            {
                "tenant_id": "default",
                "session_id": "s_abc",
                "campaign_id": campaign_id,
                "cycle_id": cycle_id,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "promptpotter.infrastructure.store._ACTIVE_SESSION_PATH",
        pointer_path,
    )
    resp = client.get("/api/v1/active")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "tenant_id": "default",
        "session_id": "s_abc",
        "campaign_id": campaign_id,
        "cycle_id": cycle_id,
    }


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
    # dashboard.json is per-session — it sits in the (root) cycle dir, not
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
