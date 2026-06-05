"""``Provenance`` — per-field origin-resolution tag.

Every gated origin field on a ``DraftCampaign`` carries a :class:`Provenance`:
the field has no value yet (``UNSET``), an inference is awaiting confirmation
(``PROPOSED``), or it is operator-stated / auto-confirmed-high-confidence
(``CONFIRMED``). No field reaches mint while ``UNSET`` or ``PROPOSED`` — the
deterministic ``origin_readiness`` checklist gates on it.

Frozen domain vocabulary, shared by the application-layer checklist + the
draft-campaign object. Spec: ``docs/specs/m10-origin-resolution-checkin.md``.
"""

from __future__ import annotations

from enum import StrEnum


class Provenance(StrEnum):
    """How settled an origin field's value is."""

    UNSET = "unset"
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"


__all__ = ["Provenance"]
