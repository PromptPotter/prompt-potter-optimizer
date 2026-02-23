"""
LLM inference node.

Executes a single LLM call with configurable model, temperature, and prompts.
"""
from typing import Any, Type
from pydantic import BaseModel, Field

from .base import NodeBase


class LLMInput(BaseModel):
    """Input model for LLM node."""

    messages: list[dict[str, str]] = Field(
        ...,
        description="Chat messages as [{role: 'user'|'system'|'assistant', content: '...'}]"
    )
    system_prompt: str | None = Field(
        None,
        description="System prompt (prepended to messages if provided)"
    )
    variables: dict[str, Any] = Field(
        default_factory=dict,
        description="Template variables for {{variable}} substitution in messages"
    )


class LLMOutput(BaseModel):
    """Output model for LLM node."""

    content: str = Field(..., description="LLM response content")
    model: str = Field(..., description="Model used for inference")
    usage: dict[str, int] = Field(
        default_factory=dict,
        description="Token usage: prompt_tokens, completion_tokens, total_tokens"
    )
    parsed: Any | None = Field(
        None,
        description="Parsed JSON if output_format='json'"
    )


class LLMNode(NodeBase[LLMInput, LLMOutput]):
    """
    Execute LLM inference.

    A general-purpose node for running LLM completions with support for
    template variables, JSON output mode, and multiple providers.

    Config options:
        model: Model identifier (default: from settings.LLM_MODEL)
        temperature: Sampling temperature 0.0-2.0 (default: 0.0)
        max_tokens: Maximum response tokens (default: 1000)
        output_format: "text" or "json" (default: "text")
        provider: "openai", "anthropic", or "mock" (default: auto-detect)
    """

    @classmethod
    def get_input_model(cls) -> Type[LLMInput]:
        return LLMInput

    @classmethod
    def get_output_model(cls) -> Type[LLMOutput]:
        return LLMOutput

    def format_user_prompt(self, input_data: LLMInput) -> str:
        """Format messages into a single prompt string for logging."""
        return "\n".join(
            f"{m['role']}: {m['content']}" for m in input_data.messages
        )

    def _apply_variables(self, text: str, variables: dict[str, Any]) -> str:
        """
        Apply template variables to text.

        Supports {{variable}} syntax for substitution.
        """
        result = text
        for key, value in variables.items():
            # Convert value to string
            if isinstance(value, (dict, list)):
                import json
                str_value = json.dumps(value, indent=2)
            else:
                str_value = str(value)

            # Replace both {{key}} and {{ key }} patterns
            result = result.replace(f"{{{{{key}}}}}", str_value)
            result = result.replace(f"{{{{ {key} }}}}", str_value)

        return result

    async def _execute(self, input_data: LLMInput) -> LLMOutput:
        from api.services.llm_client import get_llm_client

        # Get config values with defaults
        model = self.config.get("model")
        temperature = self.config.get("temperature", 0.0)
        max_tokens = self.config.get("max_tokens", 1000)
        output_format = self.config.get("output_format", "text")
        provider = self.config.get("provider")

        # Get LLM client
        client = get_llm_client(provider)

        # Apply template variables to messages
        messages = []
        for msg in input_data.messages:
            content = self._apply_variables(msg["content"], input_data.variables)
            messages.append({"role": msg["role"], "content": content})

        # Add system prompt if provided
        if input_data.system_prompt:
            system_content = self._apply_variables(
                input_data.system_prompt, input_data.variables
            )
            messages.insert(0, {"role": "system", "content": system_content})

        # Call LLM
        response = await client.chat(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            output_format=output_format
        )

        # Update metrics with token counts
        if self._last_metrics:
            self._last_metrics.input_tokens = response.usage.get("prompt_tokens")
            self._last_metrics.output_tokens = response.usage.get("completion_tokens")
            self._last_metrics.model = response.model

        return LLMOutput(
            content=response.content,
            model=response.model,
            usage=response.usage,
            parsed=response.parsed
        )
