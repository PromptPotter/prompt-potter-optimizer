"""``Provenance`` — the per-field origin-resolution tag. No field reaches mint while ``UNSET`` or ``PROPOSED``; the
deterministic ``origin_readiness`` checklist is what gates on it."""

from __future__ import annotations

from enum import StrEnum


class Provenance(StrEnum):
    UNSET = "unset"
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"


__all__ = ["Provenance"]
