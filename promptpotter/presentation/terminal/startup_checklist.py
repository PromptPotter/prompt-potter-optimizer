from __future__ import annotations

import logging

logger = logging.getLogger("promptpotter.presentation.cli")

__all__ = ["checkin_line"]


def checkin_line(step: str, detail: str, *, ok: bool = True) -> None:
    mark = "✓" if ok else "✗"
    logger.info("%s %-13s — %s", mark, step, detail)
