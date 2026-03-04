"""Tests for campaign store and registry (WP 3.3).

Covers CampaignStore CRUD, lineage reconstruction, and API endpoints.
"""

import pytest

from api.models.prompt_state import PromptState

BACKEND_ID = "test-backend"


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
    return store.campaigns.create_campaign(
        BACKEND_ID,
        name="Test Optimization",
        config={"n_variants": 5, "model": "test-model"},
    )


# ---------------------------------------------------------------------------
# CampaignStore CRUD
# ---------------------------------------------------------------------------


def test_create_and_load(store):
    """Create a campaign and load it back."""
    data = store.campaigns.create_campaign(BACKEND_ID, name="My Campaign")
    assert data["campaign_id"].startswith("campaign_")
    assert data["name"] == "My Campaign"
    assert data["status"] == "active"
    assert data["n_trials"] == 0

    loaded = store.campaigns.load(BACKEND_ID, data["campaign_id"])
    assert loaded is not None
    assert loaded["name"] == "My Campaign"


def test_add_trial_updates_index(store, campaign, baseline_ps):
    """add_trial writes detail file and updates campaign index."""
    cid = campaign["campaign_id"]
    trial = store.campaigns.record_trial(
        BACKEND_ID, cid,
        round_num=0,
        prompt_state=baseline_ps.model_dump(),
        accuracy=0.67,
        hits=2,
        total=3,
        label="baseline",
    )

    detail = store.campaigns.load_trial(BACKEND_ID, cid, 0)
    assert detail is not None
    assert detail["trial_id"] == trial["trial_id"]
    assert detail["accuracy"] == 0.67

    loaded = store.campaigns.load(BACKEND_ID, cid)
    assert loaded["n_trials"] == 1
    assert loaded["baseline_accuracy"] == 0.67
    assert loaded["best_accuracy"] == 0.67
    assert loaded["trials"][0]["prompt_state_id"] == baseline_ps.id


def test_multiple_trials_track_best(store, campaign, baseline_ps):
    """Best accuracy tracked across multiple trials."""
    cid = campaign["campaign_id"]

    store.campaigns.record_trial(
        BACKEND_ID, cid, round_num=0,
        prompt_state=baseline_ps.model_dump(),
        accuracy=0.67, hits=2, total=3, label="baseline",
    )

    improved_ps = baseline_ps.derive(
        instruction="better", changes_description="round1",
    )
    store.campaigns.record_trial(
        BACKEND_ID, cid, round_num=1,
        prompt_state=improved_ps.model_dump(),
        accuracy=0.80, hits=4, total=5, label="round1", improved=True,
    )

    worse_ps = improved_ps.derive(
        instruction="worse", changes_description="round2",
    )
    store.campaigns.record_trial(
        BACKEND_ID, cid, round_num=2,
        prompt_state=worse_ps.model_dump(),
        accuracy=0.60, hits=3, total=5, label="round2", improved=False,
    )

    loaded = store.campaigns.load(BACKEND_ID, cid)
    assert loaded["n_trials"] == 3
    assert loaded["best_accuracy"] == 0.80
    assert loaded["baseline_accuracy"] == 0.67


def test_list_all(store):
    """list_all returns summaries for all campaigns."""
    store.campaigns.create_campaign(BACKEND_ID, name="Campaign A")
    store.campaigns.create_campaign(BACKEND_ID, name="Campaign B")

    campaigns = store.campaigns.list_all(BACKEND_ID)
    assert len(campaigns) == 2
    names = {c["name"] for c in campaigns}
    assert names == {"Campaign A", "Campaign B"}


def test_export_includes_trial_details(store, campaign, baseline_ps):
    """export() includes full trial detail files."""
    cid = campaign["campaign_id"]

    store.campaigns.record_trial(
        BACKEND_ID, cid, round_num=0,
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

    trial_ids = store.campaigns.record_campaign_rounds(BACKEND_ID, cid, rounds)
    assert len(trial_ids) == 2

    loaded = store.campaigns.load(BACKEND_ID, cid)
    assert loaded["n_trials"] == 2
    assert loaded["best_accuracy"] == 0.90
    assert loaded["baseline_accuracy"] == 0.67


# ---------------------------------------------------------------------------
# Lineage reconstruction
# ---------------------------------------------------------------------------


def test_lineage_chain(store, campaign, baseline_ps):
    """get_lineage returns full parent chain."""
    cid = campaign["campaign_id"]

    r1_ps = baseline_ps.derive(instruction="r1", changes_description="round1")
    r2_ps = r1_ps.derive(instruction="r2", changes_description="round2")

    store.campaigns.record_trial(
        BACKEND_ID, cid, round_num=0,
        prompt_state=baseline_ps.model_dump(),
        accuracy=0.50, hits=1, total=2, label="baseline",
    )
    store.campaigns.record_trial(
        BACKEND_ID, cid, round_num=1,
        prompt_state=r1_ps.model_dump(),
        accuracy=0.75, hits=3, total=4, label="round1",
    )
    store.campaigns.record_trial(
        BACKEND_ID, cid, round_num=2,
        prompt_state=r2_ps.model_dump(),
        accuracy=1.0, hits=4, total=4, label="round2",
    )

    lineage = store.campaigns.get_lineage(BACKEND_ID, cid)
    assert len(lineage) == 3
    assert lineage[0]["parent_prompt_state_id"] is None
    assert lineage[1]["parent_prompt_state_id"] == baseline_ps.id
    assert lineage[2]["parent_prompt_state_id"] == r1_ps.id


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


def test_api_crud_lifecycle(api_client, store, baseline_ps):
    """Create, list, get detail, get trial via API."""
    c = store.campaigns.create_campaign(BACKEND_ID, name="API Test")
    cid = c["campaign_id"]
    store.campaigns.record_trial(
        BACKEND_ID, cid, round_num=0,
        prompt_state=baseline_ps.model_dump(),
        accuracy=0.67, hits=2, total=3, label="baseline",
        results=[{"query": "q1", "hit": True}],
    )

    # List
    resp = api_client.get(
        "/api/v1/campaigns", params={"backend_id": BACKEND_ID},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["campaigns"][0]["name"] == "API Test"

    # Detail
    resp = api_client.get(
        f"/api/v1/campaigns/{cid}", params={"backend_id": BACKEND_ID},
    )
    assert resp.status_code == 200
    assert resp.json()["n_trials"] == 1
    assert resp.json()["trials"][0]["accuracy"] == 0.67

    # Trial
    resp = api_client.get(
        f"/api/v1/campaigns/{cid}/trials/0",
        params={"backend_id": BACKEND_ID},
    )
    assert resp.status_code == 200
    assert resp.json()["accuracy"] == 0.67
    assert len(resp.json()["results"]) == 1


def test_api_delete(api_client, store):
    """Delete campaign via API."""
    c = store.campaigns.create_campaign(BACKEND_ID, name="Delete Test")
    cid = c["campaign_id"]

    resp = api_client.delete(
        f"/api/v1/campaigns/{cid}", params={"backend_id": BACKEND_ID},
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    resp = api_client.get(
        f"/api/v1/campaigns/{cid}", params={"backend_id": BACKEND_ID},
    )
    assert resp.status_code == 404
