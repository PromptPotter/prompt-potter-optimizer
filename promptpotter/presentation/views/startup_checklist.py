"""Pre-flight check-in checklist — the operator-facing startup display.

``cmd_new`` runs an ordered sequence of startup steps (campaign mint,
backend reachability, dataset + pipeline load, task check-in, origin
launch). Each step emits one :func:`checkin_line` so the operator watches
the sequence advance instead of staring at an opaque gap before origin
scoring.

Display-only: every fact echoed here is already persisted to disk
(``campaign.json``, ``dashboard.json``, ``task_context.yaml``, …) — this
is a progress echo, not a new source of truth.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("promptpotter.presentation.cli")

__all__ = ["checkin_line"]


def checkin_line(step: str, detail: str, *, ok: bool = True) -> None:
    """Emit one pre-flight check-in line — ``✓ {step} — {detail}``."""
    mark = "✓" if ok else "✗"
    logger.info("%s %-13s — %s", mark, step, detail)
