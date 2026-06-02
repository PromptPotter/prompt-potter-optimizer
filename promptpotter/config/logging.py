"""Logging configuration. Call ``setup_logging()`` once from entry points."""

import logging
import re
import sys
from typing import Literal, TextIO

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


class _TqdmStreamHandler(logging.StreamHandler[TextIO]):
    """Writes through ``tqdm.write`` so log lines don't trample an active progress bar."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from tqdm.auto import tqdm

            msg = self.format(record)
            tqdm.write(msg, file=self.stream)
            self.flush()
        except Exception:
            self.handleError(record)


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
    handler: logging.StreamHandler[TextIO] = (
        _TqdmStreamHandler(sys.stderr) if style == "cli" else logging.StreamHandler(sys.stderr)
    )
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


__all__ = ["LOG_DATE_FORMAT", "LOG_FORMAT", "setup_logging"]
