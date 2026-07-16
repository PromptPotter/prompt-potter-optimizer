"""Operator-facing artifact writers — the orchestration-side log.md / review.md /
hard_samples.json renderers.

These compute artifacts and write disk (an orchestration job), so they live in
``application/`` next to the runner that drives them, not in ``presentation/``
(whose entry-point shells stay read-only over orchestration). Disk-side view
reconstruction (``from_disk_log``) lives here too, next to
its single consumer.
"""
