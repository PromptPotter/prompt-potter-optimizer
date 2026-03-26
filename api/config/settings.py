"""
Application settings and configuration.
"""
import functools
import json
from pathlib import Path

from pydantic_settings import BaseSettings

APP_VERSION: str = "0.6.1"

# -- Shared constants ---------------------------------------------------------

DATASET_NAME: str = "termnorm_ground_truth"
NO_RESULT: str = "NO_RESULT"
DISPLAY_TRUNCATE: int = 60
DEFAULT_DIAGNOSTIC_QUERIES: int = 6

# -- Service constants --------------------------------------------------------

# backend_client — HTTP timeout for /matches endpoint
MATCH_TIMEOUT: float = 120.0

# llm_client — provider defaults
OPENAI_MAX_RETRIES: int = 5
GROQ_MAX_RETRIES: int = 3
GROQ_TIMEOUT: float = 60.0
GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"

# llm_client — app-level retry (beyond SDK's own retry)
LLM_RETRY_STATUSES: frozenset[int] = frozenset({429, 502, 503})
LLM_MAX_APP_RETRIES: int = 3
LLM_BASE_DELAY: float = 1.0  # seconds

# pipeline_discovery — TTL for cached GET /pipeline responses
PIPELINE_CACHE_TTL: float = 30.0

# search/smart_search — diagnostic set thresholds
MIN_DIAGNOSTIC_QUERIES: int = 3
DIAGNOSTIC_HIT_RATIO: float = 0.75

# stores/dataset_run_store — file lock parameters
LOCK_RETRY_INTERVAL: float = 0.05  # seconds between retries
LOCK_TIMEOUT: float = 5.0  # seconds before treating lock as stale


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

    # LLM Provider Settings
    LLM_PROVIDER: str = "groq"  # "groq", "openai", or "anthropic"
    LLM_MODEL: str = "openai/gpt-oss-120b"
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GROQ_API_KEY: str = ""

    # Langfuse Observability (cloud.langfuse.com)
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"
    LANGFUSE_ENABLED: bool = True
    LANGFUSE_PROMPTS_ENABLED: bool = False

    # File-based observability (traces, experiments, events.jsonl)
    OBS_ENABLED: bool = True

    model_config = {"env_file": ".env", "case_sensitive": True, "extra": "ignore"}


settings = Settings()


@functools.lru_cache(maxsize=1)
def _load_variant_library_raw() -> dict:
    path = Path(__file__).parent / "prompt_variants.json"
    with open(path) as f:
        return json.load(f)


def _extract_text(variants: list) -> list[str]:
    return [v["text"] if isinstance(v, dict) else v for v in variants]


def load_variant_library() -> dict:
    """Load the prompt variant library, returning flat ``{field: [str]}`` shape."""
    raw = _load_variant_library_raw()
    return {
        section: {field: _extract_text(vals) for field, vals in axes.items()}
        for section, axes in raw.items()
    }


def load_variant_library_rich() -> dict:
    """Load the prompt variant library with provenance metadata intact."""
    return _load_variant_library_raw()
