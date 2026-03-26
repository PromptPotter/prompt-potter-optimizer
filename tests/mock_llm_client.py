"""Mock LLM client for testing — lives in tests, not production code."""
from typing import Literal

from api.services.llm_client import LLMClientBase, LLMResponse, _try_parse_json


class MockLLMClient(LLMClientBase):
    """Mock LLM client for testing — returns configurable responses."""

    def __init__(self, responses: list[str] | None = None):
        self.responses = responses or ["Mock LLM response"]
        self._call_count = 0

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1000,
        output_format: Literal["text", "json"] = "text",
        **kwargs,
    ) -> LLMResponse:
        content = self.responses[self._call_count % len(self.responses)]
        self._call_count += 1

        parsed = (
            _try_parse_json(content, "Mock")
            if output_format == "json"
            else None
        )

        return LLMResponse(
            content=content,
            model=model or "mock-model",
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
            finish_reason="stop",
            parsed=parsed,
        )
