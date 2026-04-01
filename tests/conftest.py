"""Shared test fixtures."""
import pytest

from api.services.obs.langfuse_client import LangfuseLogger


@pytest.fixture(autouse=True)
def _reset_langfuse():
    """Reset the LangfuseLogger singleton after every test."""
    yield
    LangfuseLogger.reset_instance()
