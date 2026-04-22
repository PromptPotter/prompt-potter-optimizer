"""Token-usage chokepoint invariants.

The contract is narrow: one record type, one emission API, pluggable sinks.
Tests guard:

1. The built-in sink warns when an optimizer prompt exceeds
   ``OPTIMIZER_PROMPT_WARN_TOKENS``.
2. The built-in sink ignores backend-kind calls (different policy later).
3. Custom sinks receive every event; one faulty sink cannot break emission
   for the others.
"""

import logging

import pytest

from promptpotter.infrastructure.llm.token_usage import (
    TokenUsage,
    emit_token_usage,
    register_token_sink,
    reset_token_sinks_for_tests,
)


@pytest.fixture(autouse=True)
def _clean_sinks():
    """Ensure each test starts with only the built-in sink installed."""
    reset_token_sinks_for_tests()
    yield
    reset_token_sinks_for_tests()


def test_warn_when_optimizer_over_threshold(caplog, monkeypatch):
    monkeypatch.setattr(
        "promptpotter.infrastructure.llm.token_usage.settings.OPTIMIZER_PROMPT_WARN_TOKENS",
        100,
    )
    with caplog.at_level(logging.WARNING, logger="promptpotter.infrastructure.llm.token_usage"):
        emit_token_usage(
            TokenUsage(
                node="l1_generate",
                kind="optimizer",
                input_tokens=200,
                output_tokens=10,
                duration_s=0.1,
            )
        )
    assert any("l1_generate" in r.message and "200 tokens" in r.message for r in caplog.records)


def test_no_warn_when_optimizer_under_threshold(caplog, monkeypatch):
    monkeypatch.setattr(
        "promptpotter.infrastructure.llm.token_usage.settings.OPTIMIZER_PROMPT_WARN_TOKENS",
        1000,
    )
    with caplog.at_level(logging.WARNING, logger="promptpotter.infrastructure.llm.token_usage"):
        emit_token_usage(
            TokenUsage(
                node="critique",
                kind="optimizer",
                input_tokens=500,
                output_tokens=50,
                duration_s=0.1,
            )
        )
    assert caplog.records == []


def test_no_warn_for_backend_kind(caplog, monkeypatch):
    """Backend LLM nodes use a separate policy (TBD); default sink ignores them."""
    monkeypatch.setattr(
        "promptpotter.infrastructure.llm.token_usage.settings.OPTIMIZER_PROMPT_WARN_TOKENS",
        100,
    )
    with caplog.at_level(logging.WARNING, logger="promptpotter.infrastructure.llm.token_usage"):
        emit_token_usage(
            TokenUsage(
                node="entity_profiling",
                kind="backend",
                input_tokens=9000,
                output_tokens=100,
                duration_s=1.0,
            )
        )
    assert caplog.records == []


def test_custom_sink_receives_events():
    received: list[TokenUsage] = []
    register_token_sink(received.append)
    usage = TokenUsage(
        node="l2_context", kind="optimizer", input_tokens=10, output_tokens=5, duration_s=0.1
    )
    emit_token_usage(usage)
    assert received == [usage]


def test_faulty_sink_does_not_block_others(caplog):
    received: list[TokenUsage] = []

    def boom(_u: TokenUsage) -> None:
        raise RuntimeError("sink exploded")

    register_token_sink(boom)
    register_token_sink(received.append)
    usage = TokenUsage(
        node="l3_plan", kind="optimizer", input_tokens=10, output_tokens=5, duration_s=0.1
    )
    with caplog.at_level(logging.ERROR, logger="promptpotter.infrastructure.llm.token_usage"):
        emit_token_usage(usage)
    assert received == [usage]
    assert any("sink" in r.message and "failed" in r.message for r in caplog.records)


def test_total_tokens_property():
    u = TokenUsage(node="x", kind="optimizer", input_tokens=42, output_tokens=58, duration_s=0.1)
    assert u.total_tokens == 100
