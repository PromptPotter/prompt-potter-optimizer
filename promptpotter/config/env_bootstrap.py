"""First-run helper: prompt for a provider API key when none is set.

Lives in `config/` so the write target (`.env` in CWD) is owned by the
config layer rather than the CLI entry point — the entry-point invariant
test forbids `write_text` from `campaign_runner.py`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_PROVIDER_KEYS = ("GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY")


def ensure_api_key() -> None:
    """Prompt for a Groq key if no provider key is set anywhere.

    Returns immediately if any of the four supported provider env vars is
    set or if `.env` exists in CWD. Otherwise reads one line from stdin
    and writes `.env` in CWD; cancellation (EOF/Ctrl-C/empty input) is
    graceful — the caller proceeds and the provider call will error
    later with the provider's own message.
    """
    if any(os.environ.get(k) for k in _PROVIDER_KEYS):
        return

    env_path = Path.cwd() / ".env"
    if env_path.exists():
        return

    print(
        "\nNo API key found. PromptPotter routes optimizer LLM calls through "
        "Groq by default (free tier available at https://console.groq.com).",
        file=sys.stderr,
    )
    try:
        key = input("Paste your Groq API key (or press Enter to skip): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("", file=sys.stderr)
        return
    if not key:
        print(
            "Skipping. Set GROQ_API_KEY in .env or your shell before running again.",
            file=sys.stderr,
        )
        return
    env_path.write_text(f"GROQ_API_KEY={key}\n", encoding="utf-8")
    os.environ["GROQ_API_KEY"] = key
    print(f"Wrote {env_path}.", file=sys.stderr)
