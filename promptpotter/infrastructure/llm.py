"""
LLM client abstraction layer.

Providers: Groq, OpenAI, OpenRouter (via ``OpenAICompatibleClient`` over the
``openai`` SDK with ``base_url`` swap) and Anthropic (via ``AnthropicClient``
over the ``anthropic`` SDK). Chat completions, JSON mode, token tracking.
Retry and ``Retry-After`` honoring are delegated to the provider SDKs
(``max_retries`` kwarg on ``AsyncOpenAI`` / ``AsyncAnthropic``).

Provider selection is always explicit — caller passes
``get_llm_client(provider)``, typically sourced from
``CampaignConfig.optimizer_llm.provider``. There is no auto-detection or
env-var fallback.

Client-side tier throttling (RPM + TPM) is opt-in via ``*_RPM`` / ``*_TPM``
settings — see the rate-limiter section below.
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

# ``emit_token_usage`` warns on optimizer prompts that exceed
# ``OPTIMIZER_PROMPT_WARN_TOKENS``.

if TYPE_CHECKING:
    from openai import AsyncOpenAI

from pydantic import BaseModel, Field

from promptpotter.config.settings import settings
from promptpotter.shared.errors import RequestTooLargeError

logger = logging.getLogger(__name__)

__all__ = [
    "AnthropicClient",
    "LLMClientBase",
    "LLMResponse",
    "OpenAICompatibleClient",
    "TokenUsage",
    "emit_token_usage",
    "extract_parsed_json",
    "get_llm_client",
    "try_parse_json",
]


def try_parse_json(content: str, provider: str) -> Any | None:
    """Parse JSON from response content, return None on failure."""
    text = content.strip()
    # Strip markdown code fences (e.g. ```json ... ```)
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Best-effort repair of malformed JSON (Groq/kimi artifacts)
    try:
        repaired = text.replace("\\\\n", "\\n").replace("\\\\t", "\\t")
        repaired = re.sub(r"(?<=[{,\s])(\w+)\s*:", r'"\1":', repaired)
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        # Truncate after last balanced closing brace
        depth = 0
        last_valid = -1
        for i, ch in enumerate(repaired):
            if ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth == 0:
                    last_valid = i
        if 0 < last_valid < len(repaired) - 1:
            repaired = repaired[: last_valid + 1]
        result = json.loads(repaired)
        logger.info("%s JSON repaired successfully", provider)
        return result
    except json.JSONDecodeError:
        logger.debug("%s response not valid JSON: %s", provider, content[:200])
        return None


def extract_parsed_json(response: Any) -> Any:
    """Extract parsed JSON from an LLM response.

    Uses the pre-parsed ``response.parsed`` if available, otherwise falls
    back to ``json.loads(response.content)``.
    """
    if response.parsed:
        return response.parsed
    return json.loads(response.content)


# ---------------------------------------------------------------------------
# Token usage record + emission registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenUsage:
    """Per-call LLM token record.

    Attributes:
        node: Logical node name (optimizer: ``"l1_generate"``, ``"l1_critique"``,
            …; backend: ``"entity_profiling"``, ``"llm_ranking"``, …).
        kind: ``"optimizer"`` for meta-prompt calls, ``"backend"`` for
            in-pipeline LLM calls. Sinks use this to threshold separately.
        input_tokens: Prompt (input) tokens reported by the provider.
        output_tokens: Completion (output) tokens reported by the provider.
        duration_s: Wall-clock call duration in seconds.
    """

    node: str
    kind: Literal["optimizer", "backend"]
    input_tokens: int
    output_tokens: int
    duration_s: float

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def emit_token_usage(usage: TokenUsage) -> None:
    """Warn when an optimizer prompt exceeds ``OPTIMIZER_PROMPT_WARN_TOKENS``."""
    if usage.kind != "optimizer":
        return
    threshold = settings.OPTIMIZER_PROMPT_WARN_TOKENS
    if usage.input_tokens > threshold:
        logger.warning(
            "optimizer node %r prompt at %d tokens (threshold=%d) — tune the "
            "template or drop context; large meta-prompts reduce signal-to-noise "
            "for the meta-LLM and risk provider TPM caps",
            usage.node,
            usage.input_tokens,
            threshold,
        )


def _parse_tpm_overflow(err_str: str) -> tuple[int, int] | None:
    """Extract ``(limit, requested)`` from a Groq-style ``"Limit X, Requested Y"`` body."""
    m = re.search(r"Limit\s+(\d+),\s*Requested\s+(\d+)", err_str)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _parse_int_header(headers: Any, key: str) -> int | None:
    """Read ``key`` from a headers mapping and coerce to int, else ``None``."""
    if headers is None:
        return None
    val = headers.get(key) if hasattr(headers, "get") else None
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


async def _acquire_reservation(
    rate_limiter: RateLimiter | None,
    messages: list[dict[str, str]],
    max_tokens: int | None,
    provider_name: str,
) -> RateLimitReservation | None:
    """Block until ``messages`` fits the rolling window; returns ``None`` when no limiter is set."""
    if rate_limiter is None:
        return None
    return await rate_limiter.acquire_with_estimation(
        messages, max_tokens, provider_name=provider_name
    )


def _apply_discovered_caps(
    rate_limiter: RateLimiter | None,
    headers: Any,
    *,
    rpm_header: str,
    tpm_header: str,
) -> None:
    """Self-tune the limiter from the provider's rate-limit response headers (no-op without limiter)."""
    if rate_limiter is None:
        return
    rpm = _parse_int_header(headers, rpm_header)
    tpm = _parse_int_header(headers, tpm_header)
    rate_limiter.apply_discovered(rpm, tpm)


# Standard OpenAI/Groq rate-limit header keys.
_OPENAI_RPM_HEADER = "x-ratelimit-limit-requests"
_OPENAI_TPM_HEADER = "x-ratelimit-limit-tokens"
# Anthropic uses its own header naming.
_ANTHROPIC_RPM_HEADER = "anthropic-ratelimit-requests-limit"
_ANTHROPIC_TPM_HEADER = "anthropic-ratelimit-tokens-limit"


def _raise_if_request_too_large(exc: Exception, provider_name: str) -> None:
    """Translate a terminal 413/429 "Requested > Limit" into ``RequestTooLargeError``."""
    status = getattr(exc, "status_code", None)
    if status not in (413, 429):
        return
    parsed = _parse_tpm_overflow(str(exc))
    if parsed is None:
        return
    limit, requested = parsed
    if requested <= limit:
        return
    raise RequestTooLargeError(
        provider_name=provider_name,
        limit=limit,
        requested=requested,
    ) from exc


def _repair_json_validate_failure(err_str: str) -> tuple[str, Any] | None:
    """Salvage Groq's ``json_validate_failed`` 400 by re-parsing ``failed_generation``; ``None`` to fall through."""
    fg_key = "'failed_generation': '"
    fg_start = err_str.find(fg_key)
    if fg_start < 0:
        return None
    fg_text = err_str[fg_start + len(fg_key) :]
    fg_end = fg_text.rfind("'}")
    if fg_end <= 0:
        return None
    fg_text = fg_text[:fg_end].replace("\\n", "\n").replace("\\'", "'")
    parsed = try_parse_json(fg_text, "json_repair")
    if parsed is None:
        return None
    return fg_text, parsed


class LLMResponse(BaseModel):
    """Standardized response from LLM providers."""

    content: str = Field(..., description="Response content")
    model: str = Field(..., description="Model used")
    usage: dict[str, int] = Field(
        default_factory=dict,
        description="Token usage: prompt_tokens, completion_tokens, total_tokens",
    )
    finish_reason: str | None = Field(None, description="Why generation stopped")
    parsed: Any | None = Field(None, description="Parsed JSON if output_format='json'")


class LLMClientBase(ABC):
    """Abstract base class for LLM clients."""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        output_format: Literal["text", "json", "json_schema"] = "text",
        json_schema: dict | None = None,
        **kwargs,
    ) -> LLMResponse:
        """Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            model: Model identifier (uses default if not specified).
            temperature: Sampling temperature (0.0 = deterministic).
            max_tokens: Maximum response tokens. ``None`` = no cap (provider
                default — typically the model's output ceiling).
            output_format: "text", "json" (plain JSON mode), or "json_schema"
                (structured output with the provided ``json_schema``). When
                "json_schema" is selected, ``json_schema`` MUST be supplied
                and the provider must support ``response_format=json_schema``
                — no graceful fallback; a rejection surfaces as-is.
            json_schema: OpenAI-compatible JSON Schema dict. Required when
                ``output_format == "json_schema"``.
            **kwargs: Additional provider-specific parameters.

        Returns:
            LLMResponse with content and usage info.
        """
        ...


class OpenAICompatibleClient(LLMClientBase):
    """Client for any OpenAI-compatible API (OpenAI, Groq, etc.)."""

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        max_retries: int = 5,
        timeout: float | None = None,
        default_model: str | None = None,
        provider_name: str = "openai",
        rate_limiter: RateLimiter | None = None,
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._max_retries = max_retries
        self._timeout = timeout
        self._default_model = default_model or settings.LLM_MODEL
        self._provider_name = provider_name
        self._rate_limiter = rate_limiter
        self._client: AsyncOpenAI | None = None

    def _ensure_client(self) -> AsyncOpenAI:
        """Lazy-initialize the async OpenAI client."""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as err:
                raise ImportError("openai package not installed. Run: pip install openai") from err

            kwargs: dict[str, Any] = {
                "api_key": self._api_key,
                "max_retries": self._max_retries,
            }
            if self._base_url:
                kwargs["base_url"] = self._base_url
            if self._timeout:
                kwargs["timeout"] = self._timeout
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        output_format: Literal["text", "json", "json_schema"] = "text",
        json_schema: dict | None = None,
        **kwargs,
    ) -> LLMResponse:
        client = self._ensure_client()

        request_params: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            request_params["max_tokens"] = max_tokens
        if output_format == "json":
            request_params["response_format"] = {"type": "json_object"}
        elif output_format == "json_schema":
            if not json_schema:
                raise ValueError("output_format='json_schema' requires json_schema arg")
            request_params["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": json_schema.get("name", "response_schema"),
                    "schema": json_schema.get("schema", json_schema),
                    "strict": json_schema.get("strict", False),
                },
            }

        request_params.update(kwargs)

        # Proactive tier check + throttle. If the configured TPM cap can never
        # fit this request, fail fast before any network call. Otherwise block
        # until sending it stays within the rolling-window budget.
        reservation = await _acquire_reservation(
            self._rate_limiter, messages, max_tokens, self._provider_name
        )

        # SDK's own ``max_retries`` handles 408/409/429/5xx + Retry-After.
        # We only intercept for: terminal request-too-large (413/429 with
        # Requested > Limit), 404 model-not-found, and Groq's 400
        # json_validate_failed quirk. Use ``with_raw_response`` so we can
        # self-tune the limiter from ``x-ratelimit-limit-*`` headers.
        try:
            raw = await client.chat.completions.with_raw_response.create(**request_params)
            response = raw.parse()
        except Exception as exc:
            recovered = self._try_recover_from_chat_error(exc, request_params)
            if recovered is not None:
                return recovered
            raise

        _apply_discovered_caps(
            self._rate_limiter,
            raw.headers,
            rpm_header=_OPENAI_RPM_HEADER,
            tpm_header=_OPENAI_TPM_HEADER,
        )

        if not response.choices:
            raise ValueError(f"{self._provider_name} returned empty choices")
        content = response.choices[0].message.content or ""
        parsed = (
            try_parse_json(content, self._provider_name)
            if output_format in ("json", "json_schema") and content
            else None
        )

        usage = response.usage
        if reservation is not None and usage is not None:
            reservation.close(usage.total_tokens)
        return LLMResponse(
            content=content,
            model=response.model,
            usage={
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "total_tokens": usage.total_tokens if usage else 0,
            },
            finish_reason=response.choices[0].finish_reason,
            parsed=parsed,
        )

    def _try_recover_from_chat_error(
        self, exc: Exception, request_params: dict[str, Any]
    ) -> LLMResponse | None:
        """Translate known provider quirks into a salvaged response or a clearer raise.

        Returns a salvaged ``LLMResponse`` only for Groq's 400 json_validate_failed
        when the body carries a recoverable ``failed_generation``. Returns ``None``
        when the caller should re-raise the original exception.
        """
        _raise_if_request_too_large(exc, self._provider_name)
        status = getattr(exc, "status_code", None)
        if status == 404:
            model_name = request_params.get("model", "unknown")
            raise ValueError(
                f"Model '{model_name}' not found on {self._provider_name}. "
                f"Update campaign_config['optimizer_llm']['model'] or "
                f"set EXPERIMENT_ID = None to use current config."
            ) from exc
        if status == 400 and "json_validate_failed" in str(exc):
            repaired = _repair_json_validate_failure(str(exc))
            if repaired is not None:
                fg_text, parsed = repaired
                logger.info(
                    "%s: salvaged failed_generation via JSON repair",
                    self._provider_name,
                )
                return LLMResponse(
                    content=fg_text,
                    model=request_params.get("model", ""),
                    usage={"prompt_tokens": 0, "completion_tokens": 0},
                    parsed=parsed,
                )
        return None


class AnthropicClient(LLMClientBase):
    """Anthropic API client."""

    def __init__(
        self,
        api_key: str | None = None,
        rate_limiter: RateLimiter | None = None,
    ):
        self._api_key = api_key or settings.ANTHROPIC_API_KEY
        self._rate_limiter = rate_limiter
        self._client = None

    def _ensure_client(self):
        """Lazy-initialize the async Anthropic client."""
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError as err:
                raise ImportError(
                    "anthropic package not installed. "
                    'Install the anthropic extras: pip install -e ".[anthropic]"'
                ) from err
            self._client = AsyncAnthropic(api_key=self._api_key)
        return self._client

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        output_format: Literal["text", "json", "json_schema"] = "text",
        json_schema: dict | None = None,
        **kwargs,
    ) -> LLMResponse:
        if output_format == "json_schema":
            raise NotImplementedError(
                "Anthropic client does not support response_format=json_schema. "
                "Use a JSON-schema-capable provider (Groq/OpenAI) for structured output."
            )
        client = self._ensure_client()

        # Separate system message (Anthropic API convention)
        system_message = None
        anthropic_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                anthropic_messages.append({"role": msg["role"], "content": msg["content"]})

        model_name = model or settings.LLM_MODEL

        # Anthropic's API requires max_tokens. When the caller passes None
        # (project-wide default = uncapped), we still have to send a value.
        # 8192 is the per-request ceiling on most Claude models — enough
        # headroom for any realistic optimizer output; boundary-local only,
        # not a project default.
        anthropic_max_tokens = max_tokens if max_tokens is not None else 8192

        request_params: dict[str, Any] = {
            "model": model_name,
            "messages": anthropic_messages,
            "max_tokens": anthropic_max_tokens,
            "temperature": temperature,
        }
        if system_message:
            request_params["system"] = system_message

        reservation = await _acquire_reservation(
            self._rate_limiter, messages, max_tokens, "Anthropic"
        )

        raw = await client.messages.with_raw_response.create(**request_params)
        response = raw.parse()

        total = response.usage.input_tokens + response.usage.output_tokens
        if reservation is not None:
            reservation.close(total)
        _apply_discovered_caps(
            self._rate_limiter,
            raw.headers,
            rpm_header=_ANTHROPIC_RPM_HEADER,
            tpm_header=_ANTHROPIC_TPM_HEADER,
        )

        content = "".join(block.text for block in response.content if hasattr(block, "text"))
        parsed = (
            try_parse_json(content, "Anthropic") if output_format == "json" and content else None
        )

        return LLMResponse(
            content=content,
            model=response.model,
            usage={
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens": total,
            },
            finish_reason=response.stop_reason,
            parsed=parsed,
        )


def _build_rate_limiter(rpm: int | None, tpm: int | None) -> RateLimiter:
    """Build a limiter. Configured caps pin; unconfigured slots self-tune from
    ``x-ratelimit-limit-*`` response headers on the first successful call."""
    return RateLimiter(
        rpm=rpm,
        tpm=tpm,
        rpm_pinned=rpm is not None,
        tpm_pinned=tpm is not None,
    )


@dataclass(frozen=True)
class ProviderSpec:
    """Wiring for one OpenAI-compatible provider."""

    display_name: str  # e.g. "Groq" — used in error messages + logs
    api_key_attr: str  # settings field holding the API key
    rpm_attr: str  # settings field for rolling RPM cap
    tpm_attr: str  # settings field for rolling TPM cap
    base_url: str | None = None  # None ⇒ SDK default (OpenAI)
    max_retries: int = 5
    timeout: float | None = None


_OPENAI_COMPAT_SPECS: dict[str, ProviderSpec] = {
    "groq": ProviderSpec(
        "Groq",
        "GROQ_API_KEY",
        "GROQ_RPM",
        "GROQ_TPM",
        base_url="https://api.groq.com/openai/v1",
        max_retries=3,
        timeout=60.0,
    ),
    "openai": ProviderSpec(
        "OpenAI",
        "OPENAI_API_KEY",
        "OPENAI_RPM",
        "OPENAI_TPM",
    ),
    "openrouter": ProviderSpec(
        "OpenRouter",
        "OPENROUTER_API_KEY",
        "OPENROUTER_RPM",
        "OPENROUTER_TPM",
        base_url="https://openrouter.ai/api/v1",
    ),
}


def _make_openai_compat(spec: ProviderSpec) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        api_key=getattr(settings, spec.api_key_attr),
        base_url=spec.base_url,
        max_retries=spec.max_retries,
        timeout=spec.timeout,
        provider_name=spec.display_name,
        rate_limiter=_build_rate_limiter(
            getattr(settings, spec.rpm_attr),
            getattr(settings, spec.tpm_attr),
        ),
    )


def _make_anthropic_client() -> AnthropicClient:
    return AnthropicClient(
        rate_limiter=_build_rate_limiter(settings.ANTHROPIC_RPM, settings.ANTHROPIC_TPM),
    )


def _make_mock_client() -> LLMClientBase:
    """Lazy-load MockLLMClient (lives in tests package)."""
    try:
        from tests.mock_llm_client import MockLLMClient  # type: ignore[import-not-found]
    except ImportError as err:
        raise ValueError("Test mock unavailable outside the test environment.") from err
    return MockLLMClient()


def _bind_openai_compat(spec: ProviderSpec) -> Callable[[], LLMClientBase]:
    return lambda: _make_openai_compat(spec)


_PROVIDER_FACTORIES: dict[str, Callable[[], LLMClientBase]] = {
    **{name: _bind_openai_compat(spec) for name, spec in _OPENAI_COMPAT_SPECS.items()},
    "anthropic": _make_anthropic_client,
    "mock": _make_mock_client,
}


def get_llm_client(provider: str) -> LLMClientBase:
    """Construct the LLM client for ``provider``.

    Provider must be supplied explicitly — typically from
    ``CampaignConfig.optimizer_llm.provider``. There is no auto-detection
    or env-var fallback.
    """
    factory = _PROVIDER_FACTORIES.get(provider)
    if factory is None:
        valid = ", ".join(sorted(_PROVIDER_FACTORIES))
        raise ValueError(f"Unknown LLM provider: {provider!r}. Valid: {valid}.")
    return factory()


# ===========================================================================
# Rate limiter — rolling-window RPM + TPM + 429 Retry-After honoring
# ===========================================================================
#
# Proactive throttle to match provider tier caps (e.g. Groq free tier
# 5 req/min + 8000 tokens/min). Prevents 429 bursts by blocking *before*
# sending when either cap would be exceeded. Token estimation uses a rough
# ``chars // 4`` approximation; ``record_actual()`` corrects the reservation
# from server-reported usage. Also hosts the shared 429 ``Retry-After``
# parser + visible countdown (RFC 7231 §7.1.3).


import asyncio  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from collections import deque  # noqa: E402

MAX_429_ATTEMPTS: int = 5
# Brief visible cooldown between displaying a deprecated cache row and firing
# the fresh remeasurement. Not a throttle — a signal so the operator sees the
# retry happening instead of a 0.0s row jumping to a 20s call.
DEPR_RETRY_COOLDOWN_SEC: float = 1.0
_YELLOW = "\033[93m"
_RESET = "\033[0m"


def parse_retry_after(headers: object | None) -> float | None:
    """RFC 7231 §7.1.3 — read ``Retry-After`` (seconds) from response headers."""
    if headers is None:
        return None
    for key in ("Retry-After", "retry-after"):
        val = headers.get(key) if hasattr(headers, "get") else None
        if val is None:
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return None


async def wait_with_countdown(total_sec: float, label: str) -> None:
    """Sleep `total_sec` while emitting a yellow single-line countdown to stderr."""
    end = time.monotonic() + total_sec
    while True:
        remaining = max(0.0, end - time.monotonic())
        mins_total, secs = divmod(int(remaining + 0.5), 60)
        hours, mins = divmod(mins_total, 60)
        stamp = f"{hours:d}:{mins:02d}:{secs:02d}" if hours else f"{mins:02d}:{secs:02d}"
        sys.stderr.write(
            f"\r{_YELLOW}⚠ rate-limit ({label}): waiting {stamp}  (Ctrl+C to abort){_RESET}"
        )
        sys.stderr.flush()
        if remaining <= 0:
            break
        await asyncio.sleep(min(1.0, remaining))
    sys.stderr.write(f"\r{_YELLOW}⚠ rate-limit ({label}): resuming.{' ' * 30}{_RESET}\n")
    sys.stderr.flush()


def estimate_tokens(messages: list[dict[str, str]], max_output: int | None) -> int:
    """Rough prompt + output token estimate (~4 chars/token).

    When ``max_output`` is ``None`` (no caller-side cap), only the input
    side is counted. The TPM pre-check loses its output reservation, but
    the provider's own 429 still surfaces if the actual response overshoots.
    """
    char_count = sum(len(m.get("content", "")) for m in messages)
    return char_count // 4 + (max_output or 0)


@dataclass
class RateLimiter:
    """Rolling-window request/token limiter.

    Attributes:
        rpm: Requests per window. ``None`` disables the request cap.
        tpm: Tokens per window. ``None`` disables the token cap.
        window_s: Window length in seconds (default 60).
        rpm_pinned: When True, ``apply_discovered()`` won't override ``rpm``
            (caller explicitly configured it via settings).
        tpm_pinned: Same for ``tpm``.
    """

    rpm: int | None = None
    tpm: int | None = None
    window_s: float = 60.0
    rpm_pinned: bool = False
    tpm_pinned: bool = False

    _requests: deque[float] = field(default_factory=deque)
    _tokens: deque[tuple[float, int]] = field(default_factory=deque)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def apply_discovered(self, rpm: int | None, tpm: int | None) -> None:
        """Populate caps from server-reported rate-limit headers.

        Pinned slots (explicit user config) are never overwritten. Unpinned
        slots latch to the first non-``None`` value we see and update if the
        server later reports a different cap (tier change mid-run).
        """
        if rpm is not None and not self.rpm_pinned:
            self.rpm = rpm
        if tpm is not None and not self.tpm_pinned:
            self.tpm = tpm

    async def acquire(self, estimated_tokens: int) -> None:
        """Block until sending ``estimated_tokens`` fits within RPM + TPM caps.

        Reserves an RPM slot and a TPM allocation on return. Callers should
        follow up with :meth:`record_actual` once the server reports actual
        usage so the reservation reflects reality.
        """
        async with self._lock:
            while True:
                now = time.monotonic()
                self._prune(now)
                wait = self._wait_needed(now, estimated_tokens)
                if wait <= 0:
                    self._requests.append(now)
                    self._tokens.append((now, estimated_tokens))
                    return
                await asyncio.sleep(wait)

    def record_actual(self, estimated: int, actual: int) -> None:
        """Correct the most recent reservation with the response's actual tokens."""
        if self._tokens and self._tokens[-1][1] == estimated:
            ts, _ = self._tokens[-1]
            self._tokens[-1] = (ts, actual)

    async def acquire_with_estimation(
        self,
        messages: list[dict[str, str]],
        max_output: int | None,
        *,
        provider_name: str,
    ) -> RateLimitReservation:
        """Estimate, fail-fast on over-cap, throttle, and return a closeable reservation.

        Bundles the three-step dance every LLM call needs: estimate prompt
        size, raise ``RequestTooLargeError`` if the configured TPM cap can
        never fit it, and block until the rolling window has room. The
        returned reservation must be ``close()``-d with the server's actual
        token count after the response so the rolling window stays accurate.
        """
        estimated = estimate_tokens(messages, max_output)
        if self.tpm is not None and estimated > self.tpm:
            raise RequestTooLargeError(
                provider_name=provider_name,
                limit=self.tpm,
                requested=estimated,
            )
        await self.acquire(estimated)
        return RateLimitReservation(estimated=estimated, limiter=self)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_s
        while self._requests and self._requests[0] < cutoff:
            self._requests.popleft()
        while self._tokens and self._tokens[0][0] < cutoff:
            self._tokens.popleft()

    def _wait_needed(self, now: float, estimated_tokens: int) -> float:
        delays: list[float] = [0.0]
        if self.rpm is not None and len(self._requests) >= self.rpm:
            delays.append(self._requests[0] + self.window_s - now)
        if self.tpm is not None:
            current = sum(t for _, t in self._tokens)
            if current + estimated_tokens > self.tpm:
                needed = current + estimated_tokens - self.tpm
                shed = 0
                for ts, toks in self._tokens:
                    shed += toks
                    if shed >= needed:
                        delays.append(ts + self.window_s - now)
                        break
        return max(delays)


@dataclass(frozen=True)
class RateLimitReservation:
    """One outstanding throttle reservation — call ``close()`` after the response."""

    estimated: int
    limiter: RateLimiter

    def close(self, actual: int) -> None:
        """Reconcile the reservation with the server's actual token usage."""
        self.limiter.record_actual(self.estimated, actual)
