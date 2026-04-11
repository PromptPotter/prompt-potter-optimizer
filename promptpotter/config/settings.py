"""
Application settings and configuration.
"""

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

    # Local scoring gate — admin secret for LLM-only adapter access
    # Empty = disabled (all scoring goes through backend). Set in .env to allow local scoring.
    LOCAL_SCORING_SECRET: str = ""

    # File-based observability (traces, experiments, events.jsonl)
    OBS_ENABLED: bool = True

    model_config = {"env_file": ".env", "case_sensitive": True, "extra": "ignore"}


settings = Settings()
