"""``origin_readiness`` — the deterministic gate between ingest and mint.

Pure function, no I/O. The proposer/gate split (spec:
``docs/specs/m10-origin-resolution-checkin.md``) puts *two* parties on
"is this origin complete": an LLM resolver *proposes* (the ``checkin`` node,
origin-aware version), and this checklist *gates*. Mint is blocked until the
checklist passes; a false ``ready`` from the resolver is rejected and the open
gaps are fed back.

The checklist closed set is every origin knob that was a **hidden default**
before — the field a campaign could mint with that the operator never stated.
Two field kinds:

* **Column mapping** (``column.query`` / ``column.ground_truth``) — satisfied
  only when CONFIRMED *and* a member of the uploaded headers.
* **Config knobs** (task framing, connector, scorer, round cap, optimizer
  provider/model, backend node overlay) — satisfied when CONFIRMED. They seed
  from our ``pipeline.json`` / ``campaign.json`` template defaults and
  auto-confirm at ``create()`` so the operator never has to click through a
  value with one sane default; ``task_description`` is the one that lands
  UNSET (no default framing — the operator must state what the prompt does).

Out of this slice's closed set: ``optimizer.reasoning_floor`` /
``reasoning_ceiling`` / ``model_locked`` — they need new draft fields + campaign
wiring and join the gate in a follow-up.
"""

from __future__ import annotations

from collections.abc import Callable
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


# The config-knob closed set: (field_key, label, hint-when-unset). Each is
# satisfied by CONFIRMED provenance alone (its value seeds from a template
# default). The column mapping is checked separately — it also gates on header
# membership.
_CONFIG_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("task_description", "task framing", "Describe what the prompt should do."),
    ("connector", "backend connector", "Confirm which backend runs the pipeline."),
    ("scoring_composite", "success metric", "Confirm how a prediction is scored."),
    ("max_rounds", "round cap", "Confirm the optimization round cap."),
    ("optimizer.provider", "optimizer provider", "Confirm the optimizer LLM provider."),
    ("optimizer.model", "optimizer model", "Confirm the optimizer LLM model."),
    ("backend.node_config", "backend node config", "Confirm the backend node overlay."),
)


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
    for field_key, label, hint in _CONFIG_FIELDS:
        _check_confirmed(draft, field_key=field_key, label=label, hint=hint, gaps=gaps)

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


def _check_confirmed(
    draft: DraftCampaign,
    *,
    field_key: str,
    label: str,
    hint: str,
    gaps: list[FieldGap],
) -> None:
    """A config knob is satisfied by CONFIRMED provenance — its value is whatever
    template default or operator/resolver choice currently sits on the draft."""
    provenance = draft.resolved.get(field_key, Provenance.UNSET)
    if provenance is Provenance.CONFIRMED:
        return
    reason = "proposed_unconfirmed" if provenance is Provenance.PROPOSED else "unset"
    extra = " (proposed — confirm or correct)." if provenance is Provenance.PROPOSED else ""
    gaps.append(FieldGap(field=field_key, reason=reason, hint=f"{hint} [{label}]{extra}"))


def field_values(draft: DraftCampaign) -> dict[str, Any]:
    """Current value of every closed-set field, keyed by the checklist field id.

    Surfaced into ``cache.json`` so the operator (and the AI) reads *what* each
    field is set to alongside its provenance, without re-deriving from the draft.
    """
    getters: dict[str, Callable[[DraftCampaign], Any]] = {
        "column.query": lambda d: d.column_query,
        "column.ground_truth": lambda d: d.column_ground_truth,
        "task_description": lambda d: d.task_description,
        "connector": lambda d: d.connector,
        "scoring_composite": lambda d: d.scoring_composite,
        "max_rounds": lambda d: d.max_rounds,
        "optimizer.provider": lambda d: d.optimizer_provider,
        "optimizer.model": lambda d: d.optimizer_model,
        "backend.node_config": lambda d: dict(d.pipeline_overlay),
    }
    return {key: getter(draft) for key, getter in getters.items()}


def resolution_block(draft: DraftCampaign) -> dict[str, Any]:
    """Serialize the draft's checklist state for the on-disk ``cache.json``.

    ``{complete, provenance: {field: tag}, sources: {field: auto|stated},
    values: {field: value}, gaps: [{field, reason, hint}]}`` — the AI-readable
    record of what blocks mint, the current value + provenance + source of every
    gated field, and why each blocks. The resolver additionally stamps its last
    ``OriginResolution`` alongside this block.
    """
    readiness = origin_readiness(draft)
    return {
        "complete": readiness.complete,
        "provenance": {field_name: prov.value for field_name, prov in draft.resolved.items()},
        "sources": {field_name: src.value for field_name, src in draft.sources.items()},
        "values": field_values(draft),
        "gaps": [gap.to_wire() for gap in readiness.gaps],
    }


__all__ = [
    "FieldGap",
    "OriginReadiness",
    "field_values",
    "origin_readiness",
    "resolution_block",
]
