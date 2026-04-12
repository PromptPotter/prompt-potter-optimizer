"""Tests for LLM-only backend adapter and auth gate."""

import asyncio

from promptpotter.application.campaign.campaign_setup import _validate_local_access
from promptpotter.infrastructure.llm.client import LLMClientBase, LLMOnlyAdapter, LLMResponse


class _StubLLMClient(LLMClientBase):
    """Stub that returns a fixed response."""

    def __init__(self, content: str = "Let me solve this.\n#### 42") -> None:
        self._content = content

    async def chat(self, messages, **kwargs) -> LLMResponse:
        self.last_messages = messages
        self.last_kwargs = kwargs
        return LLMResponse(
            content=self._content,
            model="stub-model",
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            finish_reason="stop",
        )


def _run(coro):
    return asyncio.run(coro)


class TestLLMOnlyAdapter:
    def test_run_query_returns_backend_format(self):
        adapter = LLMOnlyAdapter(_StubLLMClient())
        result = _run(adapter.run_query("What is 6 * 7?"))

        assert "data" in result
        data = result["data"]
        assert "final_ranking" in data
        assert len(data["final_ranking"]) == 1
        assert data["final_ranking"][0]["candidate"] == "Let me solve this.\n#### 42"
        assert data["final_ranking"][0]["score"] == 1.0
        assert data["terminated_at"] == "llm_call"

    def test_prompt_from_pipeline_params(self):
        """Prompt flows through pipeline_params[node]["prompt"] — same as pipeline backends."""
        stub = _StubLLMClient()
        adapter = LLMOnlyAdapter(stub)
        _run(
            adapter.run_query(
                "What is 6 * 7?",
                pipeline_params={
                    "llm_only": {"prompt": "You are a math tutor.", "temperature": 0.5}
                },
            )
        )
        # System prompt extracted from pipeline_params
        assert stub.last_messages[0] == {"role": "system", "content": "You are a math tutor."}
        assert stub.last_messages[1] == {"role": "user", "content": "What is 6 * 7?"}
        assert stub.last_kwargs["temperature"] == 0.5

    def test_no_prompt_no_system_message(self):
        """When pipeline_params has no prompt key, no system message is sent."""
        stub = _StubLLMClient()
        adapter = LLMOnlyAdapter(stub)
        _run(
            adapter.run_query("What is 6 * 7?", pipeline_params={"llm_only": {"temperature": 0.0}})
        )
        # Only user message, no system
        assert len(stub.last_messages) == 1
        assert stub.last_messages[0]["role"] == "user"


class TestLocalAuthGate:
    """Tests for _validate_local_access() security gate.

    Returns True (authorized for local LLM) or False (fall back to backend).
    """

    def test_denies_when_no_secret_configured(self, monkeypatch):
        """Empty LOCAL_SCORING_SECRET -> False (route to backend)."""
        monkeypatch.setattr("promptpotter.config.settings.settings.LOCAL_SCORING_SECRET", "")
        assert _validate_local_access("any-token") is False

    def test_denies_missing_token(self, monkeypatch):
        """Secret set but no token provided -> False (route to backend)."""
        monkeypatch.setattr(
            "promptpotter.config.settings.settings.LOCAL_SCORING_SECRET", "correct-secret"
        )
        assert _validate_local_access(None) is False

    def test_denies_wrong_token(self, monkeypatch):
        """Token doesn't match secret -> False (route to backend)."""
        monkeypatch.setattr(
            "promptpotter.config.settings.settings.LOCAL_SCORING_SECRET", "correct-secret"
        )
        assert _validate_local_access("wrong-token") is False

    def test_accepts_correct_token(self, monkeypatch):
        """Matching token -> True (local LLM authorized)."""
        monkeypatch.setattr(
            "promptpotter.config.settings.settings.LOCAL_SCORING_SECRET", "my-secret"
        )
        assert _validate_local_access("my-secret") is True
