"""
Application settings and configuration
"""
from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    """Application configuration settings"""

    # Environment
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    # API Configuration
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    # CORS - stored as comma-separated string, parsed via property
    ALLOWED_ORIGINS: str = "*"

    @property
    def allowed_origins_list(self) -> List[str]:
        """Parse ALLOWED_ORIGINS as comma-separated list."""
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",")]

    # LLM Provider Settings
    LLM_PROVIDER: str = "groq"  # "groq", "openai", or "anthropic"
    LLM_MODEL: str = ""  # Model to use (provider-specific default if empty)
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GROQ_API_KEY: str = ""

    # Langfuse Observability (cloud.langfuse.com)
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"
    LANGFUSE_ENABLED: bool = True

    # Optimization Settings
    MAX_DATASET_SIZE: int = 1000  # Maximum number of examples in dataset
    MAX_ITERATIONS: int = 5  # Maximum optimization iterations
    DEFAULT_MODEL: str = "gpt-4"

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
