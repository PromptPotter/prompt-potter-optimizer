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


def test_record_campaign_rounds(store, campaign, baseline_ps):

    cid = campaign["campaign_id"]

    improved_ps = baseline_ps.derive_candidate(
        instruction="better", changes_description="improved",
    )

    rounds = [
        {
            "round": 0, "label": "baseline",
            "prompt_fields": baseline_ps,
            "accuracy": 0.67, "hits": 2, "total": 3,
            "results": [], "improved": False,
        },
        {
            "round": 1, "label": "improved",
            "prompt_fields": improved_ps,
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



def test_lineage_chain(store, campaign, baseline_ps):

    cid = campaign["campaign_id"]

    r1_ps = baseline_ps.derive_candidate(instruction="r1", changes_description="round1")
    r2_ps = r1_ps.derive_candidate(instruction="r2", changes_description="round2")

    store.campaigns.record_trial(
        BACKEND_ID, cid, round_num=0,
        prompt_fields=baseline_ps.model_dump(),
        accuracy=0.50, hits=1, total=2, label="baseline",
    )
    store.campaigns.record_trial(
        BACKEND_ID, cid, round_num=1,
        prompt_fields=r1_ps.model_dump(),
        accuracy=0.75, hits=3, total=4, label="round1",
    )
    store.campaigns.record_trial(
        BACKEND_ID, cid, round_num=2,
        prompt_fields=r2_ps.model_dump(),
        accuracy=1.0, hits=4, total=4, label="round2",
    )

    lineage = store.campaigns.get_lineage(BACKEND_ID, cid)
    assert len(lineage) == 3
    assert lineage[0]["parent_prompt_fields_id"] is None
    assert lineage[1]["parent_prompt_fields_id"] == baseline_ps.id
    assert lineage[2]["parent_prompt_fields_id"] == r1_ps.id



@pytest.fixture
def api_client(store, monkeypatch):
    """FastAPI test client with store pointing at temp dir."""
    monkeypatch.setattr(
        "api.dependencies.get_store",
        lambda: store,
    )
    from fastapi.testclient import TestClient

    from api.main import app
    return TestClient(app)


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


def test_api_delete_removed(api_client, store):

    c = store.campaigns.create_campaign(BACKEND_ID, name="Delete Test")
    cid = c["campaign_id"]

    resp = api_client.delete(
        f"/api/v1/campaigns/{cid}", params={"backend_id": BACKEND_ID},
    )
    assert resp.status_code == 405

    # Store-level delete still works
    assert store.campaigns.delete(BACKEND_ID, cid) is True
    assert store.campaigns.load(BACKEND_ID, cid) is None



def _apply_e2e_mocks(monkeypatch):
    """Apply all service mocks for E2E feedback cycle testing."""
    from _helpers import build_eval_results

    async def mock_restructure(context_input, llm_client, **kwargs):
        return {
            "persona": "You are a pharmacology expert.",
            "task_intent": "Match drug names to canonical terms.",
            "problem_description": "Drug name normalization.",
            "instruction": "Rank candidates by relevance to the query.",
            "thinking_style": "Compare each candidate systematically.",
            "answer_format": "Return the best match.",
        }

    monkeypatch.setattr(
        "api.services.search.context.restructure_context",
        mock_restructure,
    )

    async def mock_generate(osp, accuracy, results, n, creativity,
                            llm_client, **kwargs):
        return [
            osp.derive_candidate(
                instruction=f"Match query to canonical drug name (variant {i})",
                changes_description=f"e2e_candidate_{i}",
            ).prompt_field_dict()
            for i in range(n)
        ]

    monkeypatch.setattr(
        "api.services.l1_optimizer.l1_generate",
        mock_generate,
    )

    async def mock_eval(search_point, data, ctx=None, **kwargs):
        label = kwargs.get("label", "")
        if label == "candidate_0":
            results, scores = build_eval_results(data, hits=3)
        else:
            results, scores = build_eval_results(data, hits=0)
        return results, scores, False

    monkeypatch.setattr(
        "api.services.prompt_eval.evaluate_prompt_cached",
        mock_eval,
    )

    from tests.mock_llm_client import MockLLMClient
    monkeypatch.setattr(
        "api.services.llm_client.get_llm_client",
        lambda provider=None: MockLLMClient(),
    )


@pytest.mark.slow
@pytest.mark.asyncio
async def test_e2e_optimization_with_registry(
    monkeypatch, store, eval_data,
):

    _apply_e2e_mocks(monkeypatch)

    from api.services.campaign.models import CycleConfig
    from api.services.campaign.optimization_loop import run_optimization

    config = CycleConfig(
        max_rounds=3,
        patience=2,
        n_variants=2,
        backend_url="http://mock:8000",
    )

    result = await run_optimization(
        instruction="Normalize drug names.",
        eval_data=eval_data,
        config=config,
        baseline_prompt_fields=OptSearchPoint(instruction="Normalize drug names.").model_dump(),
        baseline_accuracy=0.0,
        baseline_results=[],
    )

    assert result.n_rounds > 0

    # Record to campaign store
    campaign = store.campaigns.create_campaign(
        BACKEND_ID,
        name="E2E Feedback Cycle",
        config={"max_rounds": 3},
    )
    campaign_id = campaign["campaign_id"]

    rounds_for_registry = []
    for rd in result.rounds:
        ps = OptSearchPoint(**rd.prompt_fields)
        rounds_for_registry.append({
            "round": rd.round,
            "label": rd.label,
            "prompt_fields": ps,
            "accuracy": rd.accuracy,
            "hits": rd.hits,
            "total": rd.total,
            "improved": rd.improved,
            "candidates_evaluated": rd.candidates_evaluated,
        })

    trial_ids = store.campaigns.record_campaign_rounds(
        BACKEND_ID, campaign_id, rounds_for_registry,
    )
    store.campaigns.complete(BACKEND_ID, campaign_id)

    assert len(trial_ids) == result.n_rounds

    loaded = store.campaigns.load(BACKEND_ID, campaign_id)
    assert loaded["status"] == "completed"
    assert loaded["n_trials"] == result.n_rounds
