"""
Application settings and global constants.

The ``Settings`` class holds env-driven configuration. Module-level constants
below are static and serve as the single source of truth for prompt field
lists, persistence versioning, and service-level defaults.
"""

from pydantic_settings import BaseSettings

APP_VERSION: str = "0.6.1"

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


# Persistence versioning
MEASUREMENTS_SCHEMA_VERSION = 1
DEFAULT_CONNECTOR_TYPE = "default"


class Settings(BaseSettings):
    """Application configuration settings."""

    # Environment
    ENVIRONMENT: str = "development"

    # CORS - stored as comma-separated string, parsed via property
    ALLOWED_ORIGINS: str = "*"

    @property
    def allowed_origins_list(self) -> list[str]:
        """Parse ALLOWED_ORIGINS as comma-separated list."""
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",")]

    # LLM provider keys. Provider selection is per-campaign on
    # ``CampaignConfig.optimizer_llm.provider`` — there is no env-var default.
    LLM_MODEL: str = "openai/gpt-oss-120b"
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""

    # Per-provider tier caps (rolling 60s window). Left as None → no throttle.
    # Example: Groq free tier is 5 req/min + 8000 tokens/min for gpt-oss-120b.
    GROQ_RPM: int | None = None
    GROQ_TPM: int | None = None
    OPENAI_RPM: int | None = None
    OPENAI_TPM: int | None = None
    ANTHROPIC_RPM: int | None = None
    ANTHROPIC_TPM: int | None = None
    OPENROUTER_RPM: int | None = None
    OPENROUTER_TPM: int | None = None

    # Warn when an optimizer meta-prompt (L1/L2/L3/critique/restructure) exceeds
    # this input-token count. Our in-house threshold — signals "tune the node
    # template"; not tied to any provider cap.
    OPTIMIZER_PROMPT_WARN_TOKENS: int = 8000

    # Langfuse Observability (cloud.langfuse.com)
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"
    LANGFUSE_ENABLED: bool = True
    LANGFUSE_PROMPTS_ENABLED: bool = False

    # File-based observability (traces, events.jsonl)
    OBS_ENABLED: bool = True

    # MLflow experiment tracking (opt-in; off by default). When enabled,
    # FileSink writes per-round MLflow runs to ``library/mlruns/`` via the
    # MLflow Python SDK.
    MLFLOW_ENABLED: bool = False

    model_config = {"env_file": ".env", "case_sensitive": True, "extra": "ignore"}


settings = Settings()
