"""First-run helper: prompt for a provider API key when none is set. It lives in ``config/`` so the write target
(``.env`` in CWD) is owned by the config layer — the entry-point invariant test forbids a write from the CLI shell."""

from __future__ import annotations

import os
import sys

from promptpotter.config.paths import env_file_path
from promptpotter.config.settings import settings

_PROVIDER_KEYS = ("GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY")


def ensure_api_key() -> None:
    """Prompt for an OpenRouter key when no provider key is set anywhere. Cancellation is GRACEFUL — the caller proceeds and
    the provider call errors later with the provider's own message."""
    if any(os.environ.get(k) for k in _PROVIDER_KEYS):
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
    env_path.write_text(f"OPENROUTER_API_KEY={key}\n", encoding="utf-8")
    os.environ["OPENROUTER_API_KEY"] = key
    print(f"Wrote {env_path}.", file=sys.stderr)
