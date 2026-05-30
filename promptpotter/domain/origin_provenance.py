"""``Provenance`` + ``ProvenanceSource`` — per-field origin-resolution tags.

Every origin-readiness field on a ``DraftCampaign`` carries a :class:`Provenance`:
the field has no value yet (``UNSET``), an inference is awaiting confirmation
(``PROPOSED``), or it is operator-stated / auto-confirmed-high-confidence
(``CONFIRMED``). No field reaches mint while ``UNSET`` or ``PROPOSED`` — the
deterministic ``origin_readiness`` checklist gates on it.

A field that has a value (``PROPOSED`` / ``CONFIRMED``) also carries a
:class:`ProvenanceSource` answering *who* set it: a machine derivation (``AUTO``
— a template default auto-confirmed at ``create()``, a deterministic column
auto-detect, or a resolver finding) versus an explicit operator statement
(``STATED`` — an ``edit-draft-campaign`` patch or a confirmed column pick). The
two axes are orthogonal: ``CONFIRMED + AUTO`` is a once-hidden default now made
visible; ``CONFIRMED + STATED`` is an operator choice. The audit trail this
distinction feeds lands in the draft ``cache.json``.

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


class ProvenanceSource(StrEnum):
    """Who set an origin field's value — a machine derivation or the operator."""

    AUTO = "auto"
    STATED = "stated"


__all__ = ["Provenance", "ProvenanceSource"]
