from __future__ import annotations

from pydantic import SecretStr

from promptpotter.config.paths import env_file_path
from promptpotter.config.settings import settings
from promptpotter.infrastructure.store.io import write_text


def keep_api_key(key: str) -> None:
    """The key lands in the `.env` the settings read, and in this process's settings."""
    write_text(env_file_path(), f"OPENROUTER_API_KEY={key}\n")
    settings.OPENROUTER_API_KEY = SecretStr(key)
