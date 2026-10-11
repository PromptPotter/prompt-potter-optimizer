import asyncio
import logging
import re
import sys
from typing import Any, Literal, TextIO

from promptpotter.config.log_redaction import SecretRedactionFilter

LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class _CliFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if record.levelno >= logging.WARNING:
            line = f"{record.levelname}: {record.getMessage()}"
        else:
            line = record.getMessage()
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


class _QuietPolls(logging.Filter):
    _POLLED = re.compile(
        r"^/api/v1/(cycles|campaigns|machine-status|sessions/active|auth/(quota-status|me))(\?|$)"
    )
    _SUFFIX = ("/dashboard", "/health", "/tree", "/ray", "/cells")
    _QUIET_STATUS = frozenset({200, 304})

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not (isinstance(args, tuple) and len(args) >= 5):
            return True
        method, path, _http, status = args[1], args[2], args[3], args[4]
        if method != "GET" or status not in self._QUIET_STATUS:
            return True
        path_str = str(path)
        base = path_str.split("?", 1)[0]
        return not (self._POLLED.match(path_str) or base.endswith(self._SUFFIX))


def setup_logging(
    level: int = logging.INFO,
    *,
    style: Literal["full", "cli"] = "full",
) -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    handler: logging.StreamHandler[TextIO] = logging.StreamHandler(sys.stderr)
    if style == "cli":
        handler.setFormatter(_CliFormatter())
    else:
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    handler.addFilter(SecretRedactionFilter())
    root.setLevel(level)
    root.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if style == "cli":
        # Deep layers stay quiet: the presentation layer prints the summary.
        logging.getLogger("promptpotter.application").setLevel(logging.WARNING)
        logging.getLogger("promptpotter.infrastructure").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").addFilter(_QuietPolls())


def silence_proactor_disconnect_noise() -> None:
    """Swallows only the benign Windows Proactor teardown `ConnectionResetError` (bpo-39010)."""
    loop = asyncio.get_running_loop()
    prior = loop.get_exception_handler()

    def handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        if isinstance(exc, ConnectionResetError) and "_call_connection_lost" in repr(
            context.get("handle")
        ):
            return
        if prior is not None:
            prior(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(handler)


__all__ = ["setup_logging", "silence_proactor_disconnect_noise"]
