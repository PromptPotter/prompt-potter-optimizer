"""Operator-facing artifact writers — the orchestration-side log.md / review.md /
hard_samples.json renderers.

These compute artifacts and write disk (an orchestration job), so they live in
``application/`` next to the runner that drives them, not in ``presentation/``
(whose entry-point shells stay read-only over orchestration). Disk-side view
reconstruction (``from_disk_round`` / ``from_disk_log``) lives here too, next to
its single consumer.
"""

from __future__ import annotations

from promptpotter.application.output.writers import (
    from_disk_log,
    from_disk_round,
    write_hard_samples_artifacts,
    write_log_md,
    write_review_md,
)

__all__ = [
    "from_disk_log",
    "from_disk_round",
    "write_hard_samples_artifacts",
    "write_log_md",
    "write_review_md",
]
