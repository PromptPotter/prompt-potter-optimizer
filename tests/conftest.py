"""Shared test fixtures."""
import pytest

import api.services.llm_client as llm_mod
from api.services.campaign.models import CycleConfig
from api.services.obs.langfuse_client import LangfuseLogger
from api.services.project_store import ProjectStore
from tests.mock_llm_client import MockLLMClient


@pytest.fixture
def mock_llm_client():
    """Install a MockLLMClient as the global LLM client; restore after test."""
    client = MockLLMClient()
    prev = llm_mod._llm_client
    llm_mod._llm_client = client
    yield client
    llm_mod._llm_client = prev


@pytest.fixture
def tmp_store(tmp_path):
    """ProjectStore backed by a temporary directory."""
    return ProjectStore(base_dir=tmp_path)


@pytest.fixture
def store(tmp_store):
    """Alias for tmp_store — used by campaign and e2e tests."""
    return tmp_store


@pytest.fixture(autouse=True)
def _reset_langfuse():
    """Reset the LangfuseLogger singleton after every test."""
    yield
    LangfuseLogger.reset_instance()


@pytest.fixture
def eval_data():
    """Standard 3-query eval dataset used by feedback cycle tests."""
    return [
        {"query": "aspirin", "ground_truth": "Aspirin"},
        {"query": "ibuprofen", "ground_truth": "Ibuprofen"},
        {"query": "acetaminophen", "ground_truth": "Acetaminophen"},
    ]


@pytest.fixture
def cycle_config():
    """Standard CycleConfig for feedback cycle tests."""
    return CycleConfig(
        max_rounds=5,
        l1_patience=2,
        n_variants=3,
        creativity=0.5,
        improvement_threshold=0.01,
        backend_url="http://mock:8000",
        enable_critique=False,
        enable_l2=False,
    )
