"""
Logging configuration for PromptPotter Optimizer.

Call ``setup_logging()`` once at import time to configure the root logger.
All modules should use ``logger = logging.getLogger(__name__)``.
"""
import logging
import sys

LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logger with a stream handler to stderr."""
    root = logging.getLogger()
    if root.handlers:
        return  # already configured (e.g. by pytest)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    root.setLevel(level)
    root.addHandler(handler)


# Auto-configure on first import so all modules get consistent logging.
setup_logging()
