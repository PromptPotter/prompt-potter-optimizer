"""
API tests for PromptPotter Optimizer
"""
import pytest
from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)


def test_health_check():
    """Test health endpoint"""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "timestamp" in data


def test_readiness_check():
    """Test readiness endpoint"""
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"


def test_optimize_endpoint():
    """Test optimization endpoint with sample data"""
    request_data = {
        "initial_prompt": "Classify the sentiment:",
        "dataset": [
            {"text": "I love this!", "expected": "positive"},
            {"text": "This is terrible", "expected": "negative"}
        ],
        "target_metric": "accuracy",
        "max_iterations": 2
    }

    response = client.post("/api/v1/optimize", json=request_data)
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert "optimized_prompt" in data
    assert "improvement" in data
    assert len(data["iterations"]) > 0


def test_optimize_invalid_dataset():
    """Test optimization with invalid dataset"""
    request_data = {
        "initial_prompt": "Test",
        "dataset": [],  # Empty dataset should fail
        "target_metric": "accuracy"
    }

    response = client.post("/api/v1/optimize", json=request_data)
    assert response.status_code == 422  # Validation error
