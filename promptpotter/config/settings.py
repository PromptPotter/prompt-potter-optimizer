"""
Application settings and configuration.
"""
import functools
import json
from pathlib import Path

from pydantic_settings import BaseSettings

APP_VERSION: str = "0.6.1"

# Defaults for backend connection (not env-driven — override via CLI args)
DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"
DEFAULT_BACKEND_ID = "local"
DEFAULT_EXPERIMENT_ID = "1_production_historical"


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
