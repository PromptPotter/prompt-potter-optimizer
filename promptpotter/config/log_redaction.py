"""Secret-redaction filter for the root logging handler.

Last-mile defense before stderr: scrubs configured API keys and well-known
provider key prefixes out of every log record. The codebase already bans
``print()`` (CLAUDE.md), so any user-visible string passes through here.

The filter is defense-in-depth — no current code path stringifies an api_key
into a log record. It exists so a future careless ``logger.info("auth=%s",
key)`` cannot silently leak a credential.
"""

from __future__ import annotations

import logging
import re

from promptpotter.config.settings import settings

REDACTED = "***REDACTED***"

# Provider-key prefixes (defensive — catch credentials not in our settings).
_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),
    re.compile(r"xoxb-[A-Za-z0-9-]+"),
    re.compile(r"pk-lf-[a-z0-9-]+"),
    re.compile(r"sk-lf-[a-z0-9-]+"),
)

_SECRET_FIELDS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
)


def _snapshot_secret_values() -> tuple[str, ...]:
    """Non-empty current values of secret-bearing settings fields."""
    return tuple(v for f in _SECRET_FIELDS if (v := getattr(settings, f, "")))


class SecretRedactionFilter(logging.Filter):
    """Replaces configured api-key values + well-known prefixes with ``***REDACTED***``.

    Snapshots settings values at filter construction. Re-instantiate the
    filter if env keys are rotated mid-process.
    """

    def __init__(self) -> None:
        super().__init__()
        self._values: tuple[str, ...] = _snapshot_secret_values()

    def _redact(self, text: str) -> str:
        for v in self._values:
            if v and v in text:
                text = text.replace(v, REDACTED)
        for pat in _KEY_PATTERNS:
            text = pat.sub(REDACTED, text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: self._redact(v) if isinstance(v, str) else v for k, v in record.args.items()
                }
            else:
                record.args = tuple(
                    self._redact(a) if isinstance(a, str) else a for a in record.args
                )
        return True


__all__ = ["REDACTED", "SecretRedactionFilter"]
