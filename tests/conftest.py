"""Shared test fixtures."""

import pytest

from promptpotter.infrastructure.tracing.langfuse_client import LangfuseLogger


@pytest.fixture(autouse=True)
def _reset_langfuse():
    """Reset the LangfuseLogger singleton after every test."""
    yield
    LangfuseLogger.reset_instance()
