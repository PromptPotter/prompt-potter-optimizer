"""Integration tests for FastAPI router endpoints."""

import pytest
from fastapi.testclient import TestClient

from api.dependencies import get_store
from api.main import app
from api.services.project_store import ProjectStore


@pytest.fixture
def api_client(tmp_store):
    """TestClient with store pointing at temp dir."""
    app.dependency_overrides[get_store] = lambda: tmp_store
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def tmp_store(tmp_path):
    return ProjectStore(base_dir=tmp_path)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health_returns_200(api_client):
    resp = api_client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert "version" in body
    assert "timestamp" in body


# ---------------------------------------------------------------------------
# Backend CRUD
# ---------------------------------------------------------------------------

REGISTER_PAYLOAD = {
    "name": "Test Backend",
    "backend_type": "termnorm",
    "base_url": "http://localhost:9000",
}


def test_register_backend(api_client):
    resp = api_client.post("/api/v1/backends", json=REGISTER_PAYLOAD)
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "test-backend"
    assert body["name"] == "Test Backend"
    assert body["backend_type"] == "termnorm"
    assert body["base_url"] == "http://localhost:9000"
    assert "created_at" in body


def test_register_duplicate_409(api_client):
    api_client.post("/api/v1/backends", json=REGISTER_PAYLOAD)
    resp = api_client.post("/api/v1/backends", json=REGISTER_PAYLOAD)
    assert resp.status_code == 409


def test_register_custom_id(api_client):
    payload = {**REGISTER_PAYLOAD, "id": "my-custom-id"}
    resp = api_client.post("/api/v1/backends", json=payload)
    assert resp.status_code == 201
    assert resp.json()["id"] == "my-custom-id"


def test_list_backends_empty(api_client):
    resp = api_client.get("/api/v1/backends")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_backends_after_register(api_client):
    api_client.post("/api/v1/backends", json=REGISTER_PAYLOAD)
    resp = api_client.get("/api/v1/backends")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["id"] == "test-backend"


def test_get_backend(api_client):
    api_client.post("/api/v1/backends", json=REGISTER_PAYLOAD)
    resp = api_client.get("/api/v1/backends/test-backend")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Test Backend"


def test_get_backend_404(api_client):
    resp = api_client.get("/api/v1/backends/nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Experiments (requires sync)
# ---------------------------------------------------------------------------


def test_list_experiments_no_sync_404(api_client):
    api_client.post("/api/v1/backends", json=REGISTER_PAYLOAD)
    resp = api_client.get("/api/v1/backends/test-backend/experiments")
    assert resp.status_code == 404
    assert "sync" in resp.json()["detail"].lower()


def test_get_experiment_not_synced_404(api_client):
    api_client.post("/api/v1/backends", json=REGISTER_PAYLOAD)
    resp = api_client.get("/api/v1/backends/test-backend/experiments/exp-1")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------


def test_list_campaigns_empty(api_client, tmp_store):
    api_client.post("/api/v1/backends", json=REGISTER_PAYLOAD)
    resp = api_client.get("/api/v1/campaigns", params={"backend_id": "test-backend"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.parametrize("path", [
    "/api/v1/campaigns/nonexistent",
    "/api/v1/campaigns/nonexistent/trials/0",
])
def test_campaign_404(api_client, path):
    resp = api_client.get(path, params={"backend_id": "test-backend"})
    assert resp.status_code == 404
