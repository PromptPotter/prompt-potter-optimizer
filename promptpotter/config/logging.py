"""Logging configuration. Call ``setup_logging()`` once from entry points."""

import asyncio
import logging
import re
import sys
from typing import Any, Literal, TextIO

from promptpotter.config.log_redaction import SecretRedactionFilter

LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class _CliFormatter(logging.Formatter):
    """Bare message at INFO; ``LEVEL: message`` above. Appends traceback when ``exc_info`` is set."""

    def format(self, record: logging.LogRecord) -> str:
        if record.levelno >= logging.WARNING:
            line = f"{record.levelname}: {record.getMessage()}"
        else:
            line = record.getMessage()
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


class _QuietPolls(logging.Filter):
    """Drop successful high-frequency webapp polls from ``uvicorn.access``.

    The dashboard polls ``/…/dashboard`` (2 s) and the workspace polls
    ``/active`` + ``/cycles`` + ``/campaigns`` + per-backend ``/health``.
    These are ~100+ identical 200/304s per minute and crowd out real signal
    (errors, commands, startup). Only successful (200/304) GETs on those
    routes are silenced — 4xx/5xx (incl. the unauthenticated-tab 401s),
    POSTs, and one-shot reads still log.
    """

    _LIST = re.compile(r"^/api/v1/(active|cycles|campaigns)(\?|$)")
    _SUFFIX = ("/dashboard", "/health")
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
        return not (self._LIST.match(path_str) or base.endswith(self._SUFFIX))


def setup_logging(
    level: int = logging.INFO,
    *,
    style: Literal["full", "cli"] = "full",
) -> None:
    """Configure root logger with a stream handler to stderr.

    ``style="full"`` — timestamped, module-tagged format (API server, notebook).
    ``style="cli"`` — bare messages for interactive CLI; deep-layer INFO is
    suppressed so the presentation layer owns the user-facing summary.
    """
    root = logging.getLogger()
    if root.handlers:
        return  # already configured (e.g. by pytest)
    handler: logging.StreamHandler[TextIO] = logging.StreamHandler(sys.stderr)
    if style == "cli":
        handler.setFormatter(_CliFormatter())
    else:
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    handler.addFilter(SecretRedactionFilter())
    root.setLevel(level)
    root.addHandler(handler)
    # Suppress noisy httpx request logging
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if style == "cli":
        # Deep layers stay quiet — campaign_runner (presentation) prints the summary.
        logging.getLogger("promptpotter.application").setLevel(logging.WARNING)
        logging.getLogger("promptpotter.infrastructure").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").addFilter(_QuietPolls())


def silence_proactor_disconnect_noise() -> None:
    """Swallow the benign Windows ``ProactorEventLoop`` teardown error.

    When a browser drops a kept-alive socket, ``_ProactorBasePipeTransport.
    _call_connection_lost`` calls ``sock.shutdown(SHUT_RDWR)`` on an
    already-reset socket and raises ``ConnectionResetError`` [WinError 10054],
    which the loop dumps via its exception handler (Python bpo-39010). The
    connection is already gone — nothing failed, it's pure log noise.

    Install a loop exception handler that swallows *exactly* that case — a
    ``ConnectionResetError`` raised inside ``_call_connection_lost`` — and
    delegates everything else to the prior/default handler so real loop errors
    still surface. Call once, from inside the running loop (lifespan startup).
    """
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


__all__ = [
    "LOG_DATE_FORMAT",
    "LOG_FORMAT",
    "setup_logging",
    "silence_proactor_disconnect_noise",
]
