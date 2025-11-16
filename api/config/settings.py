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

    # CORS
    ALLOWED_ORIGINS: List[str] = ["*"]

    # LLM Provider Settings
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""

    # Optimization Settings
    MAX_DATASET_SIZE: int = 1000  # Maximum number of examples in dataset
    MAX_ITERATIONS: int = 5  # Maximum optimization iterations
    DEFAULT_MODEL: str = "gpt-4"

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
