"""Tests for campaign store and registry (WP 3.3).

Covers CampaignStore CRUD, lineage reconstruction, and API endpoints.
"""

import pytest

from api.models.opt_search_point import OptSearchPoint

BACKEND_ID = "test-backend"


@pytest.fixture
def baseline_ps():
    return OptSearchPoint(
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



def test_create_and_load(store):

    data = store.campaigns.create_campaign(BACKEND_ID, name="My Campaign")
    assert data["campaign_id"].startswith("campaign_")
    assert data["name"] == "My Campaign"
    assert data["status"] == "active"
    assert data["n_trials"] == 0

    loaded = store.campaigns.load(BACKEND_ID, data["campaign_id"])
    assert loaded is not None
    assert loaded["name"] == "My Campaign"


def test_add_trial_updates_index(store, campaign, baseline_ps):

    cid = campaign["campaign_id"]
    trial = store.campaigns.record_trial(
        BACKEND_ID, cid,
        round_num=0,
        prompt_fields=baseline_ps.model_dump(),
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
    assert loaded["trials"][0]["prompt_fields_id"] == baseline_ps.id


def test_multiple_trials_track_best(store, campaign, baseline_ps):

    cid = campaign["campaign_id"]

    store.campaigns.record_trial(
        BACKEND_ID, cid, round_num=0,
        prompt_fields=baseline_ps.model_dump(),
        accuracy=0.67, hits=2, total=3, label="baseline",
    )

    improved_ps = baseline_ps.derive_candidate(
        instruction="better", changes_description="round1",
    )
    store.campaigns.record_trial(
        BACKEND_ID, cid, round_num=1,
        prompt_fields=improved_ps.model_dump(),
        accuracy=0.80, hits=4, total=5, label="round1", improved=True,
    )

    worse_ps = improved_ps.derive_candidate(
        instruction="worse", changes_description="round2",
    )
    store.campaigns.record_trial(
        BACKEND_ID, cid, round_num=2,
        prompt_fields=worse_ps.model_dump(),
        accuracy=0.60, hits=3, total=5, label="round2", improved=False,
    )

    loaded = store.campaigns.load(BACKEND_ID, cid)
    assert loaded["n_trials"] == 3
    assert loaded["best_accuracy"] == 0.80
    assert loaded["baseline_accuracy"] == 0.67


def test_list_all(store):

    store.campaigns.create_campaign(BACKEND_ID, name="Campaign A")
    store.campaigns.create_campaign(BACKEND_ID, name="Campaign B")

    campaigns = store.campaigns.list_all(BACKEND_ID)
    assert len(campaigns) == 2
    names = {c["name"] for c in campaigns}
    assert names == {"Campaign A", "Campaign B"}


def test_delete(store, campaign):

    cid = campaign["campaign_id"]
    assert store.campaigns.delete(BACKEND_ID, cid) is True
    assert store.campaigns.load(BACKEND_ID, cid) is None
    assert store.campaigns.delete(BACKEND_ID, cid) is False




@pytest.fixture
def api_client(store):
    """FastAPI test client with store pointing at temp dir."""
    from fastapi.testclient import TestClient

    from api.dependencies import get_store
    from api.main import app

    app.dependency_overrides[get_store] = lambda: store
    client = TestClient(app)
    yield client
    app.dependency_overrides.pop(get_store, None)


def test_api_crud_lifecycle(api_client, store, baseline_ps):

    c = store.campaigns.create_campaign(BACKEND_ID, name="API Test")
    cid = c["campaign_id"]
    store.campaigns.record_trial(
        BACKEND_ID, cid, round_num=0,
        prompt_fields=baseline_ps.model_dump(),
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


