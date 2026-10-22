from __future__ import annotations

import logging
import re

from pydantic import SecretStr

from promptpotter.config.settings import settings

REDACTED = "***REDACTED***"

_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),
    re.compile(r"xoxb-[A-Za-z0-9-]+"),
    re.compile(r"pk-lf-[a-z0-9-]+"),
    re.compile(r"sk-lf-[a-z0-9-]+"),
)

_TRACEBACK = logging.Formatter()


def _snapshot_secret_values() -> tuple[str, ...]:
    # A setting is secret by its TYPE: a `SecretStr` field is covered the moment it is declared, whatever its name.
    return tuple(
        value
        for name in type(settings).model_fields
        if isinstance(held := getattr(settings, name), SecretStr)
        and (value := held.get_secret_value().strip())
    )


class SecretRedactionFilter(logging.Filter):
    """Snapshots the settings' secrets at construction: re-instantiate if they rotate mid-process."""

    def __init__(self) -> None:
        super().__init__()
        self._values: tuple[str, ...] = _snapshot_secret_values()

    def _redact(self, text: str) -> str:
        for v in self._values:
            if v in text:
                text = text.replace(v, REDACTED)
        for pat in _KEY_PATTERNS:
            text = pat.sub(REDACTED, text)
        return text

    def _redact_arg(self, arg: object) -> object:
        # Only an arg whose text carried a secret changes type: `%d` and uvicorn's access args survive.
        if arg is None or isinstance(arg, (int, float)):
            return arg
        text = str(arg)
        scrubbed = self._redact(text)
        return arg if scrubbed == text else scrubbed

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg is not None:
            msg = record.msg if isinstance(record.msg, str) else str(record.msg)
            scrubbed = self._redact(msg)
            if scrubbed != msg:
                record.msg = scrubbed
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._redact_arg(v) for k, v in record.args.items()}
            else:
                record.args = tuple(self._redact_arg(a) for a in record.args)
        # Every formatter reuses a cached `exc_text`: render it once here, scrubbed.
        if record.exc_info and not record.exc_text:
            record.exc_text = _TRACEBACK.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self._redact(record.exc_text)
        if record.stack_info:
            record.stack_info = self._redact(record.stack_info)
        return True
