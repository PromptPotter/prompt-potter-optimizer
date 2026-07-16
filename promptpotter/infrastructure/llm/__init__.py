"""LLM client abstraction layer.

Providers: Groq, OpenAI, OpenRouter (via :class:`OpenAICompatibleClient` over
the ``openai`` SDK with ``base_url`` swap) and Anthropic (via
:class:`AnthropicClient` over the ``anthropic`` SDK). Chat completions, JSON
mode, token tracking. Retry and ``Retry-After`` honoring are delegated to
the provider SDKs (``max_retries`` kwarg on ``AsyncOpenAI`` / ``AsyncAnthropic``).

Provider selection is always explicit — caller passes
:func:`get_llm_client` ``(provider)``, sourced from the optimizer node's
``config.provider`` (``datasets/_optimizer/pipeline.json``) inside ``llm_call``.
There is no auto-detection or env-var fallback.

Client-side tier throttling (RPM + TPM) is opt-in via the ``RATE_LIMITS``
setting (``{provider: [rpm, tpm]}``) — see :mod:`.rate_limit`.

Nothing is re-exported here — every consumer imports the leaf directly, e.g.
``from promptpotter.infrastructure.llm.registry import get_llm_client``.

CONCEPT MAP (by module):
* **registry** — :func:`get_llm_client` (the explicit provider seam) +
  :class:`ProviderSpec`; **base** — :class:`LLMClientBase`.
* **openai_compat** / **anthropic** — the two concrete clients above.
* **json_parse** — :func:`try_parse_json` / :func:`extract_parsed_json`
  (tolerant structured-output decoding for optimizer node responses).
* **rate_limit** — :class:`RateLimiter` + ``decide_429_wait`` /
  ``parse_retry_after`` / ``estimate_tokens`` / ``wait_with_countdown``.
* **models** — :class:`LLMResponse` + the per-call token-usage telemetry
  seam: :func:`emit_token_usage` reads the active ledger from the
  ``_CYCLE_LEDGER`` ContextVar (set via ``set_cycle_ledger`` /
  ``set_current_round``) and appends a ``TokenUsageRecord`` — no process
  global, no ``RunCallbacks`` hop.
"""
