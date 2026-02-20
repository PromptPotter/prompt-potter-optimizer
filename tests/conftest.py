"""Shared test fixtures."""
import pytest

import api.services.llm_client as llm_mod
from api.services.llm_client import MockLLMClient
from api.services.langfuse_client import LangfuseLogger
from api.services.project_store import ProjectStore


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


@pytest.fixture(autouse=True)
def _reset_langfuse():
    """Reset the LangfuseLogger singleton after every test."""
    yield
    LangfuseLogger.reset_instance()
