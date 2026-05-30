"""``origin_readiness`` — the deterministic gate between ingest and mint.

Pure function, no I/O. The proposer/gate split (spec:
``docs/specs/m10-origin-resolution-checkin.md``) puts *two* parties on
"is this origin complete": an LLM resolver *proposes* (lands later), and this
checklist *gates*. Mint is blocked until the checklist passes; a false
``ready`` from anything upstream is rejected and the open gaps fed back.

This slice gates the input/target **column mapping** — the field that was
genuinely broken before (ingest hard-required literally-named columns). The
remaining closed-set fields (task framing, connector / scorer / round-cap /
model provenance) join the gate when the LLM resolver lands and can propose +
auto-confirm them; until then they keep their operator-editable defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from promptpotter.application.datasets.draft_campaign import DraftCampaign
from promptpotter.domain.origin_provenance import Provenance


@dataclass(frozen=True, slots=True)
class FieldGap:
    """One origin field that still blocks mint.

    ``reason`` is ``"unset"`` (no value resolved) or ``"proposed_unconfirmed"``
    (an inference is waiting on operator confirmation). ``hint`` is one
    operator-facing line on how to close it.
    """

    field: str
    reason: str
    hint: str

    def to_wire(self) -> dict[str, str]:
        return {"field": self.field, "reason": self.reason, "hint": self.hint}


@dataclass(frozen=True, slots=True)
class OriginReadiness:
    """Checklist verdict: ``complete`` iff no field still blocks mint."""

    complete: bool
    gaps: tuple[FieldGap, ...]


def origin_readiness(draft: DraftCampaign) -> OriginReadiness:
    """Gate ``draft`` for mint. Pure; the checklist — not the operator — decides."""
    gaps: list[FieldGap] = []

    _check_column(
        draft,
        field_key="column.query",
        value=draft.column_query,
        label="input",
        gaps=gaps,
    )
    _check_column(
        draft,
        field_key="column.ground_truth",
        value=draft.column_ground_truth,
        label="target",
        gaps=gaps,
    )

    return OriginReadiness(complete=not gaps, gaps=tuple(gaps))


def _check_column(
    draft: DraftCampaign,
    *,
    field_key: str,
    value: str,
    label: str,
    gaps: list[FieldGap],
) -> None:
    """A column field is satisfied iff CONFIRMED *and* a member of the headers."""
    provenance = draft.resolved.get(field_key, Provenance.UNSET)
    if provenance is Provenance.CONFIRMED and value in draft.headers:
        return
    if provenance is Provenance.PROPOSED:
        gaps.append(
            FieldGap(
                field=field_key,
                reason="proposed_unconfirmed",
                hint=f"Confirm the {label} column (proposed {value!r}).",
            )
        )
        return
    headers = ", ".join(draft.headers) or "<none>"
    gaps.append(
        FieldGap(
            field=field_key,
            reason="unset",
            hint=f"Pick which uploaded column is the {label}. Available: {headers}.",
        )
    )


def resolution_block(draft: DraftCampaign) -> dict[str, Any]:
    """Serialize the draft's checklist state for the on-disk ``cache.json``.

    ``{complete, provenance: {field: tag}, gaps: [{field, reason, hint}]}`` —
    the AI-readable record of what blocks mint and why each field was set.
    """
    readiness = origin_readiness(draft)
    return {
        "complete": readiness.complete,
        "provenance": {field_name: prov.value for field_name, prov in draft.resolved.items()},
        "gaps": [gap.to_wire() for gap in readiness.gaps],
    }


__all__ = ["FieldGap", "OriginReadiness", "origin_readiness", "resolution_block"]
