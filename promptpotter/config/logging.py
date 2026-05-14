"""Logging configuration. Call ``setup_logging()`` once from entry points."""

import logging
import sys
from typing import Literal

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


class _TqdmStreamHandler(logging.StreamHandler):
    """Writes through ``tqdm.write`` so log lines don't trample an active progress bar."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from tqdm.auto import tqdm

            msg = self.format(record)
            tqdm.write(msg, file=self.stream)
            self.flush()
        except Exception:
            self.handleError(record)


class _QuietDashboardPoll(logging.Filter):
    """Drop successful 2 s dashboard/index polls from ``uvicorn.access``.

    The React webapp polls a small set of JSON files via the generic
    ``/api/v1/campaigns/{cycle_id}/file?scope=cycle&path=...`` reader.
    With a live campaign this is ~30 identical 200s per minute and crowds
    out real signal (errors, non-poll requests, startup). 4xx/5xx still
    log — only 200s on the polled paths are silenced.
    """

    _POLLED = ("path=dashboard.json", "path=index.json")

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not (isinstance(args, tuple) and len(args) >= 5):
            return True
        method, path, _http, status = args[1], args[2], args[3], args[4]
        if method != "GET" or status != 200:
            return True
        return not any(p in str(path) for p in self._POLLED)


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
    handler: logging.StreamHandler = (
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
    logging.getLogger("uvicorn.access").addFilter(_QuietDashboardPoll())


__all__ = ["LOG_DATE_FORMAT", "LOG_FORMAT", "setup_logging"]
