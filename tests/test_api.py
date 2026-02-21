"""API smoke tests."""
from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)


def test_health_and_ready():
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"

    resp = client.get("/api/v1/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"
