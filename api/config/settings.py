"""
Application settings and configuration.
"""
import json
from pathlib import Path

from pydantic_settings import BaseSettings

APP_VERSION: str = "0.6.0"


class Settings(BaseSettings):
    """Application configuration settings."""

    # Environment
    ENVIRONMENT: str = "development"

    # API Configuration — consumed by docker/entrypoint.sh, not by Python
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8001

    # CORS - stored as comma-separated string, parsed via property
    ALLOWED_ORIGINS: str = "*"

    @property
    def allowed_origins_list(self) -> list[str]:
        """Parse ALLOWED_ORIGINS as comma-separated list."""
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",")]

    # LLM Provider Settings
    LLM_PROVIDER: str = "groq"  # "groq", "openai", or "anthropic"
    LLM_MODEL: str = "meta-llama/llama-4-maverick-17b-128e-instruct"
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GROQ_API_KEY: str = ""

    # Langfuse Observability (cloud.langfuse.com)
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"
    LANGFUSE_ENABLED: bool = True

    # File-based observability (traces, experiments, events.jsonl)
    OBS_ENABLED: bool = True

    model_config = {"env_file": ".env", "case_sensitive": True, "extra": "ignore"}


settings = Settings()


def load_variant_library() -> dict:
    """Load the prompt variant library from ``prompt_variants.json``."""
    path = Path(__file__).parent / "prompt_variants.json"
    with open(path) as f:
        return json.load(f)
