"""No field reaches mint while ``UNSET`` or ``PROPOSED``: the ``origin_readiness`` checklist gates on it."""

from __future__ import annotations

from enum import StrEnum


class Provenance(StrEnum):
    UNSET = "unset"
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"


__all__ = ["Provenance"]
