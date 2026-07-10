"""
Application settings and global constants.

The ``Settings`` class holds env-driven configuration. Module-level constants
below are static and serve as the single source of truth for prompt field
lists, persistence versioning, and service-level defaults.
"""

from pydantic_settings import BaseSettings

APP_VERSION: str = "0.8.2"

# Current legal-terms version the consent gate requires. Date-stamped: bumping it
# (when the Terms / Privacy text on the marketing site materially changes)
# re-prompts every user for fresh consent — the accepted version no longer
# matches. The accepted version + a server-stamped timestamp are the provable
# record persisted per-user in ``user.json`` (``User.terms_accepted``). Keep in
# sync with the version stamped on the marketing site's /terms + /privacy.
TERMS_VERSION: str = "2026-06-21"

# Defaults for backend connection (not env-driven — override via CLI args)
DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"
DEFAULT_BACKEND_ID = "local"
DEFAULT_EXPERIMENT_ID = "1_production_historical"

DATASET_NAME: str = "ground_truth"
NO_RESULT: str = "NO_RESULT"

# stores/measurement_archive — file lock parameters
LOCK_TIMEOUT: float = 5.0  # seconds before treating lock as stale


# Fields that render() assembles into the prompt string.
PROMPT_STRING_FIELDS: list[str] = [
    "persona",
    "task_intent",
    "problem_description",
    "instruction",
    "thinking_style",
    "answer_format",
]

# task_context sub-fields that L1 may emit alongside prompt/node overrides.
TASK_CONTEXT_OVERRIDES: frozenset[str] = frozenset({"upstream_context", "downstream_context"})

# JSON-schema types for universal LLM-call params + the PromptTemplate scheme.
# Used by the pipeline parser to populate ``PipelineNode.param_types`` without
# requiring every dataset overlay to spell them out. Dataset overlays may add
# backend-specific param types (e.g. ``threshold: integer`` on TermNorm's
# fuzzy_matching node) via the node's ``optimizer.param_types`` block; those
# override these defaults. Inference from ``node.config`` Python types is the
# last-resort fallback after both.
WELL_KNOWN_PARAM_TYPES: dict[str, str] = {
    # Universal LLM-call params — same shape across every provider.
    "temperature": "number",
    "top_p": "number",
    "max_tokens": "integer",
    "max_completion_tokens": "integer",
    "thinking_budget": "integer",
    "seed": "integer",
    "model": "string",
    "provider": "string",
    "reasoning_effort": "string",
    # PromptTemplate scheme — six string fields render() assembles.
    "persona": "string",
    "task_intent": "string",
    "problem_description": "string",
    "instruction": "string",
    "thinking_style": "string",
    "answer_format": "string",
}


DEFAULT_CONNECTOR_TYPE = "default"

# Optimizer-call reliability + size visibility.
#
# OPTIMIZER_CALL_DEADLINE_S — wall-clock ceiling on a single optimizer LLM
# round-trip. ``llm_call`` wraps each logical call in ``asyncio.timeout`` sized
# for the worst case (initial round-trip + one schema-repair retry — the client
# fires a full second call on schema-noncompliant output, ~2x latency; see
# ``_MAX_ROUND_TRIPS_PER_CALL``). The provider SDK's own timeout is a per-read-gap
# timeout, not a total one: a reasoning model streaming a large output slowly
# never trips it and the call can hang indefinitely. Healthy round-trips run
# 8-40s; a slow reasoning model on a heavy l2/l3 prompt runs ~150s, and a repair
# pushes the logical call to ~300s — so the per-call wall must budget BOTH
# round-trips or a healthy-but-slow call false-halts. One transient timeout is
# retried; a second halts the loop with ``StopReason.OPTIMIZER_TIMEOUT``.
OPTIMIZER_CALL_DEADLINE_S: float = 180.0

# OPTIMIZER_PROMPT_WARN_CHARS — soft size line. A composed optimizer prompt
# above this renders its CLI marker yellow with a ⚠ and logs at warn level.
# Set just above the real l1_generate floor: the distilled static body is
# ~3k, but the injected half (rendered_prompt + diagnostics + axis_memory +
# critique + …) carries a mid-campaign round to ~7.9k. 8,000 keeps healthy
# rounds quiet and flags only genuine bloat.
OPTIMIZER_PROMPT_WARN_CHARS: int = 8_000

# PoBB elimination — default posterior-of-being-best threshold ε. A
# candidate stops when its P(best) drops below ε. The runtime value is
# ``CampaignConfig.pobb_epsilon``; this is the single default every entry
# point (the config Field, PoBBConfig, the CLI)
# references so the number can't drift. 0.15 = abort a candidate once its
# P(reaching par with the current best) falls below 15% — empirically the
# most aggressive threshold before false-cuts of true winners climb (≈2% at
# 0.15 vs ≈9% at 0.20, measured on the archived round corpus).
POBB_DEFAULT_EPSILON: float = 0.15


class Settings(BaseSettings):
    """Application configuration settings."""

    # Environment
    ENVIRONMENT: str = "development"

    # CORS - comma-separated allowlist, parsed via property. Empty by default:
    # the webapp is same-origin (mounted at `/`) and the `npm run dev` proxy is
    # same-origin too, so cross-origin access is opt-in. A cross-origin API
    # client must set ALLOWED_ORIGINS explicitly — never `*`, since the app
    # serves credentialed requests (`allow_credentials=True` in main.py).
    ALLOWED_ORIGINS: str = ""

    @property
    def allowed_origins_list(self) -> list[str]:
        """Parse ALLOWED_ORIGINS as a comma-separated list, dropping blanks."""
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]

    # LLM provider keys. The optimizer's provider + model are install-global,
    # configured per node in ``datasets/_optimizer/pipeline.json`` and read at
    # ``llm_call`` — there is no env-var provider/model default. (Target/scoring
    # model is per-dataset, in the pipeline overlay.)
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""

    # Per-provider tier caps (rolling 60s window), keyed by provider → [rpm, tpm].
    # Omit a provider, or use null for a slot → no client-side throttle (the limiter
    # then self-tunes from response headers). Example (Groq free tier, 5 req/min +
    # 8000 tokens/min for gpt-oss-120b): RATE_LIMITS='{"groq": [5, 8000]}'
    RATE_LIMITS: dict[str, list[int | None]] = {}

    # Wire-level auth for backend connectors. Sent as
    # ``Authorization: Bearer <TERMNORM_TOKEN>`` on every BackendClient
    # request when non-empty. TermNorm side gates this behind its own
    # ``TERMNORM_REQUIRE_AUTH`` flag (see backend-api/config/middleware.py).
    TERMNORM_TOKEN: str = ""

    # Langfuse Observability (cloud.langfuse.com)
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"
    LANGFUSE_ENABLED: bool = True
    LANGFUSE_PROMPTS_ENABLED: bool = False

    # Security posture. When True, binary/Office uploads (.xlsx) are rejected at
    # ingest rather than parsed — xlsx is a macro / zip-bomb / XXE vector. Default
    # parses Excel; operators hardening a deployment set HARDENED_MODE=true. The
    # hook for future upload-surface hardening, matching the zero-trust posture in
    # docs/operations/secure-hosting.md.
    HARDENED_MODE: bool = False

    # Run-admission capacity — how many campaigns the server admits at once
    # (JobRegistry.reserve). 1 = strictly sequential (today's single-process
    # `--workers 1` reality); a new launch while a run is in flight gets 409
    # `machine_busy`. This is the concurrent-serving lever, but DO NOT raise it
    # above 1 until BYO per-user keys (Lane A2) + a per-tenant RateLimiter land —
    # N parallel runs on the shared key/rate-bucket would cross-bill and throttle.
    # See docs/specs/roadmap.md § Run admission + concurrent serving.
    MACHINE_RUN_CAPACITY: int = 1

    # File-based observability (traces, events.jsonl)
    OBS_ENABLED: bool = True

    # MLflow experiment tracking (opt-in; off by default). When enabled,
    # FileSink writes per-round MLflow runs to ``archive/mlruns/`` via the
    # MLflow Python SDK.
    MLFLOW_ENABLED: bool = False

    model_config = {"env_file": ".env", "case_sensitive": True, "extra": "ignore"}


settings = Settings()


__all__ = [
    "APP_VERSION",
    "DATASET_NAME",
    "DEFAULT_BACKEND_ID",
    "DEFAULT_BACKEND_URL",
    "DEFAULT_CONNECTOR_TYPE",
    "DEFAULT_EXPERIMENT_ID",
    "LOCK_TIMEOUT",
    "NO_RESULT",
    "OPTIMIZER_CALL_DEADLINE_S",
    "OPTIMIZER_PROMPT_WARN_CHARS",
    "POBB_DEFAULT_EPSILON",
    "PROMPT_STRING_FIELDS",
    "TASK_CONTEXT_OVERRIDES",
    "TERMS_VERSION",
    "WELL_KNOWN_PARAM_TYPES",
    "Settings",
    "settings",
]
