"""Shared test helpers used across test files."""

import typing


class MockCompletion:
    """Fake OpenAI-compatible completion response for LLM tests."""

    class Choice:
        class Message:
            content = '{"result": "ok"}'
        message = Message()
        finish_reason = "stop"

    choices: typing.ClassVar = [Choice()]

    class Usage:
        prompt_tokens = 10
        completion_tokens = 5
        total_tokens = 15
    usage = Usage()
    model = "test-model"


class MockHTTPError(Exception):
    """Base class for mock HTTP errors used in tests."""


def make_http_error(status_code: int, message: str = "error"):
    """Create a mock HTTP error class with the given status_code."""
    return type(f"Mock{status_code}Error", (MockHTTPError,), {"status_code": status_code})(message)
