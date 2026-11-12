from __future__ import annotations

import sys

from pydantic import SecretStr

from promptpotter.config.paths import env_file_path
from promptpotter.config.settings import settings
from promptpotter.infrastructure.store.io import write_text

_PROVIDER_KEYS = ("GROQ_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY")


def ensure_api_key() -> None:
    """The key it is given lands in the `.env` the settings read, and in this process's settings."""
    if any(getattr(settings, k).get_secret_value() for k in _PROVIDER_KEYS):
        return

    env_path = env_file_path()
    if env_path.exists():
        return

    print(
        f"\nNo API key found. {settings.BRAND_SHORT_NAME} routes the optimizer LLM through "
        "OpenRouter by default (the optimizer prompt is too large for "
        "Groq's free tier). Get a key at https://openrouter.ai/keys.",
        file=sys.stderr,
    )
    try:
        key = input("Paste your OpenRouter API key (or press Enter to skip): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("", file=sys.stderr)
        return
    if not key:
        print(
            "Skipping. Set OPENROUTER_API_KEY in .env or your shell before running again.",
            file=sys.stderr,
        )
        return
    write_text(env_path, f"OPENROUTER_API_KEY={key}\n")
    settings.OPENROUTER_API_KEY = SecretStr(key)
    print(f"Wrote {env_path}.", file=sys.stderr)
