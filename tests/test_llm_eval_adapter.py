"""Tests for LLM-only evaluation adapter."""

import asyncio

from promptpotter.services.llm_client import LLMClientBase, LLMResponse
from promptpotter.services.llm_eval_adapter import LLMOnlyAdapter


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
        """Prompt flows through pipeline_params[node]["prompt"] — same as TermNorm."""
        stub = _StubLLMClient()
        adapter = LLMOnlyAdapter(stub)
        _run(
            adapter.run_query(
                "What is 6 * 7?",
                pipeline_params={
                    "direct_llm": {"prompt": "You are a math tutor.", "temperature": 0.5}
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
            adapter.run_query(
                "What is 6 * 7?", pipeline_params={"direct_llm": {"temperature": 0.0}}
            )
        )
        # Only user message, no system
        assert len(stub.last_messages) == 1
        assert stub.last_messages[0]["role"] == "user"

    def test_check_status(self):
        adapter = LLMOnlyAdapter(_StubLLMClient())
        status = _run(adapter.check_status())
        assert status["status"] == "ok"
        assert status["mode"] == "llm-only"

    def test_init_session_noop(self):
        adapter = LLMOnlyAdapter(_StubLLMClient())
        result = _run(adapter.init_session(["term1", "term2"]))
        assert result["status"] == "skipped"

    def test_aclose(self):
        adapter = LLMOnlyAdapter(_StubLLMClient())
        _run(adapter.aclose())  # should not raise
