"""
Logging configuration for PromptPotter Optimizer.

Call ``setup_logging()`` explicitly from entry points (``promptpotter.main``,
notebook ``init_services()``) to configure the root logger.
All modules should use ``logger = logging.getLogger(__name__)``.
"""

import logging
import sys
from typing import Literal

LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class _CliFormatter(logging.Formatter):
    """Minimal format for interactive CLI: bare message at INFO, ``LEVEL: message`` above."""

    def format(self, record: logging.LogRecord) -> str:
        if record.levelno >= logging.WARNING:
            return f"{record.levelname}: {record.getMessage()}"
        return record.getMessage()


class _TqdmStreamHandler(logging.StreamHandler):
    """StreamHandler that writes through ``tqdm.write`` so log lines don't
    trample an active progress bar. Falls back to plain stream writes if
    tqdm isn't importable for some reason.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from tqdm.auto import tqdm

            msg = self.format(record)
            tqdm.write(msg, file=self.stream)
            self.flush()
        except Exception:
            self.handleError(record)


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
    root.setLevel(level)
    root.addHandler(handler)
    # Suppress noisy httpx request logging
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if style == "cli":
        # Deep layers stay quiet — campaign_runner (presentation) prints the summary.
        logging.getLogger("promptpotter.application").setLevel(logging.WARNING)
        logging.getLogger("promptpotter.infrastructure").setLevel(logging.WARNING)
