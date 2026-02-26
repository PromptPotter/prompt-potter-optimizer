"""Tests for campaign registry (WP 3.3).

Covers:
- CampaignStore CRUD (create, add_trial, load, list, export, delete)
- campaign_registry service functions
- Campaign lineage reconstruction
- API endpoints via FastAPI test client
"""

import pytest

from api.models.prompt_state import PromptState
from api.services.project_store import ProjectStore
from api.services.campaign.campaign_registry import (
    complete_campaign,
    create_campaign,
    generate_campaign_id,
    generate_trial_id,
    get_campaign_lineage,
    record_campaign_rounds,
    record_trial,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BACKEND_ID = "test-backend"


@pytest.fixture
def store(tmp_path):
    """ProjectStore pointing at a temp directory."""
    return ProjectStore(base_dir=tmp_path)


@pytest.fixture
def baseline_ps():
    return PromptState(
        instruction="Rank candidates by relevance.",
        persona="You are a domain expert.",
        changes_description="baseline",
    )


@pytest.fixture
def campaign(store):
    """A freshly created campaign."""
    return create_campaign(
        store, BACKEND_ID,
        name="Test Optimization",
        config={"n_variants": 5, "model": "test-model"},
    )


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


def test_campaign_id_format():
    cid = generate_campaign_id()
    assert cid.startswith("campaign_")
    assert len(cid) > 20


def test_trial_id_format():
    tid = generate_trial_id(3)
    assert tid.startswith("trial_0003_")
    assert len(tid) > 15


# ---------------------------------------------------------------------------
# CampaignStore CRUD
# ---------------------------------------------------------------------------


def test_create_and_load(store):
    """Create a campaign and load it back."""
    data = create_campaign(store, BACKEND_ID, name="My Campaign")
    assert data["campaign_id"].startswith("campaign_")
    assert data["name"] == "My Campaign"
    assert data["status"] == "active"
    assert data["n_trials"] == 0

    loaded = store.campaigns.load(BACKEND_ID, data["campaign_id"])
    assert loaded is not None
    assert loaded["name"] == "My Campaign"


def test_create_duplicate_raises(store, campaign):
    """Creating a campaign with the same ID raises FileExistsError."""
    with pytest.raises(FileExistsError):
        store.campaigns.create(
            BACKEND_ID, campaign["campaign_id"], {"name": "dup"},
        )


def test_load_nonexistent(store):
    """Loading a missing campaign returns None."""
    assert store.campaigns.load(BACKEND_ID, "campaign_nope") is None


def test_add_trial_updates_index(store, campaign, baseline_ps):
    """add_trial writes detail file and updates campaign index."""
    cid = campaign["campaign_id"]
    trial = record_trial(
        store, BACKEND_ID, cid,
        round_num=0,
        prompt_state=baseline_ps.model_dump(),
        accuracy=0.67,
        hits=2,
        total=3,
        label="baseline",
    )

    # Detail file exists
    detail = store.campaigns.load_trial(BACKEND_ID, cid, 0)
    assert detail is not None
    assert detail["trial_id"] == trial["trial_id"]
    assert detail["accuracy"] == 0.67

    # Campaign index updated
    loaded = store.campaigns.load(BACKEND_ID, cid)
    assert loaded["n_trials"] == 1
    assert loaded["baseline_accuracy"] == 0.67
    assert loaded["best_accuracy"] == 0.67
    assert loaded["trials"][0]["prompt_state_id"] == baseline_ps.id


def test_multiple_trials_track_best(store, campaign, baseline_ps):
    """Best accuracy tracked across multiple trials."""
    cid = campaign["campaign_id"]

    # Round 0: baseline 0.67
    record_trial(
        store, BACKEND_ID, cid, round_num=0,
        prompt_state=baseline_ps.model_dump(),
        accuracy=0.67, hits=2, total=3, label="baseline",
    )

    # Round 1: improved to 0.80
    improved_ps = baseline_ps.derive(
        instruction="better", changes_description="round1",
    )
    record_trial(
        store, BACKEND_ID, cid, round_num=1,
        prompt_state=improved_ps.model_dump(),
        accuracy=0.80, hits=4, total=5, label="round1", improved=True,
    )

    # Round 2: regressed to 0.60
    worse_ps = improved_ps.derive(
        instruction="worse", changes_description="round2",
    )
    record_trial(
        store, BACKEND_ID, cid, round_num=2,
        prompt_state=worse_ps.model_dump(),
        accuracy=0.60, hits=3, total=5, label="round2", improved=False,
    )

    loaded = store.campaigns.load(BACKEND_ID, cid)
    assert loaded["n_trials"] == 3
    assert loaded["best_accuracy"] == 0.80
    assert loaded["baseline_accuracy"] == 0.67


def test_list_all(store):
    """list_all returns summaries for all campaigns."""
    create_campaign(store, BACKEND_ID, name="Campaign A")
    create_campaign(store, BACKEND_ID, name="Campaign B")

    campaigns = store.campaigns.list_all(BACKEND_ID)
    assert len(campaigns) == 2
    names = {c["name"] for c in campaigns}
    assert names == {"Campaign A", "Campaign B"}


def test_list_empty(store):
    """list_all returns empty list when no campaigns exist."""
    assert store.campaigns.list_all(BACKEND_ID) == []


def test_export_includes_trial_details(store, campaign, baseline_ps):
    """export() includes full trial detail files."""
    cid = campaign["campaign_id"]

    record_trial(
        store, BACKEND_ID, cid, round_num=0,
        prompt_state=baseline_ps.model_dump(),
        accuracy=0.67, hits=2, total=3, label="baseline",
        results=[{"query": "q1", "hit": True}],
    )

    export = store.campaigns.export(BACKEND_ID, cid)
    assert export is not None
    assert len(export["trials_detail"]) == 1
    assert export["trials_detail"][0]["results"] == [{"query": "q1", "hit": True}]


def test_delete(store, campaign):
    """delete() removes campaign file and trial directory."""
    cid = campaign["campaign_id"]
    assert store.campaigns.delete(BACKEND_ID, cid) is True
    assert store.campaigns.load(BACKEND_ID, cid) is None
    assert store.campaigns.delete(BACKEND_ID, cid) is False


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------


def test_complete_campaign(store, campaign):
    """complete_campaign sets status to completed."""
    cid = campaign["campaign_id"]
    complete_campaign(store, BACKEND_ID, cid)
    loaded = store.campaigns.load(BACKEND_ID, cid)
    assert loaded["status"] == "completed"


def test_record_campaign_rounds(store, campaign, baseline_ps):
    """record_campaign_rounds bridges legacy round format to registry."""
    cid = campaign["campaign_id"]

    improved_ps = baseline_ps.derive(
        instruction="better", changes_description="improved",
    )

    rounds = [
        {
            "round": 0, "label": "baseline",
            "prompt_state": baseline_ps,
            "accuracy": 0.67, "hits": 2, "total": 3,
            "results": [], "improved": False,
        },
        {
            "round": 1, "label": "improved",
            "prompt_state": improved_ps,
            "accuracy": 0.90, "hits": 9, "total": 10,
            "results": [], "improved": True,
            "candidates_evaluated": 5,
        },
    ]

    trial_ids = record_campaign_rounds(store, BACKEND_ID, cid, rounds)
    assert len(trial_ids) == 2

    loaded = store.campaigns.load(BACKEND_ID, cid)
    assert loaded["n_trials"] == 2
    assert loaded["best_accuracy"] == 0.90
    assert loaded["baseline_accuracy"] == 0.67


# ---------------------------------------------------------------------------
# Lineage reconstruction
# ---------------------------------------------------------------------------


def test_lineage_chain(store, campaign, baseline_ps):
    """get_campaign_lineage returns full parent chain."""
    cid = campaign["campaign_id"]

    # Build lineage: baseline → r1 → r2
    r1_ps = baseline_ps.derive(
        instruction="r1", changes_description="round1",
    )
    r2_ps = r1_ps.derive(
        instruction="r2", changes_description="round2",
    )

    record_trial(
        store, BACKEND_ID, cid, round_num=0,
        prompt_state=baseline_ps.model_dump(),
        accuracy=0.50, hits=1, total=2, label="baseline",
    )
    record_trial(
        store, BACKEND_ID, cid, round_num=1,
        prompt_state=r1_ps.model_dump(),
        accuracy=0.75, hits=3, total=4, label="round1",
    )
    record_trial(
        store, BACKEND_ID, cid, round_num=2,
        prompt_state=r2_ps.model_dump(),
        accuracy=1.0, hits=4, total=4, label="round2",
    )

    lineage = get_campaign_lineage(store, BACKEND_ID, cid)
    assert len(lineage) == 3

    # Round 0 has no parent
    assert lineage[0]["parent_prompt_state_id"] is None
    assert lineage[0]["prompt_state_id"] == baseline_ps.id

    # Round 1 parent is baseline
    assert lineage[1]["parent_prompt_state_id"] == baseline_ps.id
    assert lineage[1]["prompt_state_id"] == r1_ps.id

    # Round 2 parent is r1
    assert lineage[2]["parent_prompt_state_id"] == r1_ps.id
    assert lineage[2]["prompt_state_id"] == r2_ps.id


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client(store, monkeypatch):
    """FastAPI test client with store pointing at temp dir."""
    monkeypatch.setattr(
        "api.routers.campaigns._get_store",
        lambda: store,
    )
    from fastapi.testclient import TestClient
    from api.main import app
    return TestClient(app)


def test_api_list_empty(api_client):
    resp = api_client.get(
        "/api/v1/campaigns", params={"backend_id": BACKEND_ID},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["campaigns"] == []


def test_api_create_and_list(api_client, store, baseline_ps):
    """Create campaigns via service, list via API."""
    create_campaign(store, BACKEND_ID, name="API Test")

    resp = api_client.get(
        "/api/v1/campaigns", params={"backend_id": BACKEND_ID},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["campaigns"][0]["name"] == "API Test"


def test_api_get_detail(api_client, store, baseline_ps):
    """Get campaign detail via API."""
    c = create_campaign(store, BACKEND_ID, name="Detail Test")
    cid = c["campaign_id"]
    record_trial(
        store, BACKEND_ID, cid, round_num=0,
        prompt_state=baseline_ps.model_dump(),
        accuracy=0.67, hits=2, total=3, label="baseline",
    )

    resp = api_client.get(
        f"/api/v1/campaigns/{cid}", params={"backend_id": BACKEND_ID},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["n_trials"] == 1
    assert data["trials"][0]["accuracy"] == 0.67


def test_api_get_trial(api_client, store, baseline_ps):
    """Get trial detail via API."""
    c = create_campaign(store, BACKEND_ID, name="Trial Test")
    cid = c["campaign_id"]
    record_trial(
        store, BACKEND_ID, cid, round_num=0,
        prompt_state=baseline_ps.model_dump(),
        accuracy=0.67, hits=2, total=3, label="baseline",
        results=[{"query": "q1", "hit": True}],
    )

    resp = api_client.get(
        f"/api/v1/campaigns/{cid}/trials/0",
        params={"backend_id": BACKEND_ID},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["accuracy"] == 0.67
    assert len(data["results"]) == 1


def test_api_export(api_client, store, baseline_ps):
    """Export campaign via API."""
    c = create_campaign(store, BACKEND_ID, name="Export Test")
    cid = c["campaign_id"]
    record_trial(
        store, BACKEND_ID, cid, round_num=0,
        prompt_state=baseline_ps.model_dump(),
        accuracy=0.67, hits=2, total=3, label="baseline",
    )

    resp = api_client.get(
        f"/api/v1/campaigns/{cid}/export",
        params={"backend_id": BACKEND_ID},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "trials_detail" in data
    assert len(data["trials_detail"]) == 1


def test_api_lineage(api_client, store, baseline_ps):
    """Get lineage via API."""
    c = create_campaign(store, BACKEND_ID, name="Lineage Test")
    cid = c["campaign_id"]
    r1 = baseline_ps.derive(instruction="r1", changes_description="r1")

    record_trial(
        store, BACKEND_ID, cid, round_num=0,
        prompt_state=baseline_ps.model_dump(),
        accuracy=0.50, hits=1, total=2, label="baseline",
    )
    record_trial(
        store, BACKEND_ID, cid, round_num=1,
        prompt_state=r1.model_dump(),
        accuracy=0.80, hits=4, total=5, label="r1",
    )

    resp = api_client.get(
        f"/api/v1/campaigns/{cid}/lineage",
        params={"backend_id": BACKEND_ID},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["lineage"]) == 2
    assert data["lineage"][1]["parent_prompt_state_id"] == baseline_ps.id


def test_api_delete(api_client, store):
    """Delete campaign via API."""
    c = create_campaign(store, BACKEND_ID, name="Delete Test")
    cid = c["campaign_id"]

    resp = api_client.delete(
        f"/api/v1/campaigns/{cid}", params={"backend_id": BACKEND_ID},
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    # Verify gone
    resp = api_client.get(
        f"/api/v1/campaigns/{cid}", params={"backend_id": BACKEND_ID},
    )
    assert resp.status_code == 404


def test_api_not_found(api_client):
    """404 for missing campaign."""
    resp = api_client.get(
        "/api/v1/campaigns/campaign_nope",
        params={"backend_id": BACKEND_ID},
    )
    assert resp.status_code == 404
