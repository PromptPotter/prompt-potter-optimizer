"""The deterministic gate between ingest and mint. An LLM resolver PROPOSES and this checklist
GATES; only what the operator must genuinely state is gated, since a default is not a gap."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from promptpotter import connectors
from promptpotter.application.datasets.draft_campaign import (
    DraftCampaign,
    merge_pipeline_overlay,
)
from promptpotter.application.scoring.formula.matchers import extraction_note_for_scoring
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS, PARAM_SCOPE_KEYS

# A node's resolved ``config`` block is an LLM call iff it carries any of these
# axes — model/provider + the per-call tunables. Such a node must own a model
# (see :func:`_check_node_models`).
_LLM_CALL_KEYS: frozenset[str] = PARAM_FORBIDDEN_KEYS | PARAM_SCOPE_KEYS


@dataclass(frozen=True, slots=True)
class FieldGap:
    """One origin field that still blocks mint. ``reason`` is ``"unset"`` or
    ``"proposed_unconfirmed"``; ``hint`` is one operator-facing line on how to close it."""

    field: str
    reason: str
    hint: str

    def to_wire(self) -> dict[str, str]:
        return {"field": self.field, "reason": self.reason, "hint": self.hint}


@dataclass(frozen=True, slots=True)
class OriginReadiness:
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
    _check_confirmed(
        draft,
        field_key="task_description",
        value=draft.raw_task_description,
        label="task framing",
        hint="Describe what the prompt should do.",
        gaps=gaps,
    )

    _check_answer_space(draft, gaps=gaps)
    _check_commit_format(draft, gaps=gaps)
    _check_node_models(draft, gaps=gaps)

    return OriginReadiness(complete=not gaps, gaps=tuple(gaps))


def _check_column(
    draft: DraftCampaign,
    *,
    field_key: str,
    value: str,
    label: str,
    gaps: list[FieldGap],
) -> None:
    provenance = draft.field_provenance.get(field_key, Provenance.UNSET)
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
    value: str,
    label: str,
    hint: str,
    gaps: list[FieldGap],
) -> None:
    """Satisfied by CONFIRMED provenance over a NON-EMPTY value: provenance records who settled a
    field, never that it holds anything, so a CONFIRMED blank is ``unset``, not a passing gate."""
    provenance = draft.field_provenance.get(field_key, Provenance.UNSET)
    if provenance is Provenance.CONFIRMED and value.strip():
        return
    reason = "proposed_unconfirmed" if provenance is Provenance.PROPOSED else "unset"
    extra = " (proposed — confirm or correct)." if provenance is Provenance.PROPOSED else ""
    gaps.append(FieldGap(field=field_key, reason=reason, hint=f"{hint} [{label}]{extra}"))


def _label_present(label: str, haystack: str) -> bool:
    """Whole-token membership, so the label ``other`` is not matched inside ``another``. Boundaries
    are non-alphanumeric, so multi-word and numeric labels still match cleanly."""
    return re.search(rf"(?<![a-z0-9]){re.escape(label.lower())}(?![a-z0-9])", haystack) is not None


def _check_answer_space(draft: DraftCampaign, *, gaps: list[FieldGap]) -> None:
    """A closed-label dataset stays open until every target label appears verbatim in the prompt
    that will be committed — otherwise the campaign mints a prompt that can never emit them."""
    labels = draft.answer_space()
    if not labels:
        return
    # Mirror the committed prompt exactly — checking the union of fields + the raw
    # description would pass a draft whose labels live only in a task description
    # that never reaches the prompt.
    haystack = " ".join(str(v) for v in draft.committed_prompt_fields().values()).lower()
    missing = [lab for lab in labels if not _label_present(lab, haystack)]
    if not missing:
        return
    gaps.append(
        FieldGap(
            field="answer_space",
            reason="unset" if not draft.origin_prompt_fields else "proposed_unconfirmed",
            hint=(
                "Enumerate every target label in the prompt so the model picks one: "
                f"{', '.join(labels)}. Missing: {', '.join(missing)}."
            ),
        )
    )


def _check_commit_format(draft: DraftCampaign, *, gaps: list[FieldGap]) -> None:
    """An extract-then-compare scorer needs a non-empty ``answer_format``: its sibling proves the
    labels appear somewhere, but a model never told to COMMIT one leaves the scorer nothing to cut."""
    if not extraction_note_for_scoring(draft.scoring_composite):
        return
    if str(draft.committed_prompt_fields().get("answer_format", "")).strip():
        return
    gaps.append(
        FieldGap(
            field="answer_format",
            reason="proposed_unconfirmed" if draft.origin_prompt_fields else "unset",
            hint=(
                f"Author answer_format with the commit instruction — the "
                f"{draft.scoring_composite!r} scorer extracts the answer from the "
                "output (e.g. the last bolded span), so an empty format leaves "
                "nothing to extract and every prediction misses."
            ),
        )
    )


def _check_node_models(draft: DraftCampaign, *, gaps: list[FieldGap]) -> None:
    """Every active LLM node must own a ``model`` before mint. Pre-mint the backend schema is not
    available, so this reads the connector-merged config for the exact pre-crash state."""
    try:
        connector = connectors.get(draft.connector)
    except KeyError:
        return
    active = set(draft.pipeline_steps or connector.default_pipeline)
    merged = merge_pipeline_overlay(draft, connector)
    for node_name in sorted(active):
        block = merged.get(node_name)
        config = block.get("config") if isinstance(block, dict) else None
        if not isinstance(config, dict) or not (_LLM_CALL_KEYS & config.keys()):
            continue
        if not config.get("model"):
            gaps.append(
                FieldGap(
                    field=f"node.{node_name}.model",
                    reason="unset",
                    hint=(
                        f"Set the model for LLM node {node_name!r} in "
                        f"backend.node_config — the dataset owns its task model."
                    ),
                )
            )


def field_values(draft: DraftCampaign) -> dict[str, Any]:
    """Current value of every closed-set field, surfaced into ``cache.json`` so the operator reads
    what each field is set to beside its provenance, without re-deriving from the draft."""
    getters: dict[str, Callable[[DraftCampaign], Any]] = {
        "column.query": lambda d: d.column_query,
        "column.ground_truth": lambda d: d.column_ground_truth,
        "task_description": lambda d: d.raw_task_description,
        "answer_space": lambda d: list(d.answer_space() or ()),
    }
    return {key: getter(draft) for key, getter in getters.items()}


def origin_projection(draft: DraftCampaign) -> dict[str, Any]:
    """What the draft WOULD commit — the gate above decides whether it may. **Not hash-equivalent to
    the committed origin**, so any content_hash assertion must run through the real commit path."""
    projection: dict[str, Any] = {
        "slug": draft.slug,
        "connector": draft.connector,
        "scoring_composite": draft.scoring_composite,
        "pipeline_steps": list(draft.pipeline_steps),
        "pipeline_overlay": draft.pipeline_overlay,
        "optimization_overrides": draft.optimization_overrides,
        "candidate_library": list(draft.candidate_library),
        "reused_origin_id": draft.reused_origin_id,
    }
    for field_key, value in (
        ("column.query", draft.column_query),
        ("column.ground_truth", draft.column_ground_truth),
        ("task_description", draft.raw_task_description),
    ):
        if draft.field_provenance.get(field_key) is Provenance.CONFIRMED:
            projection[field_key] = value
    for name, text in draft.committed_prompt_fields().items():
        projection[f"prompt.{name}"] = text
    return projection


def origin_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Projected fields that moved, as ``{field: {"from": x, "to": y}}``. A field absent on one side
    reads ``None`` there, so confirming a column is a change from ``None``."""
    return {
        key: {"from": before.get(key), "to": after.get(key)}
        for key in before.keys() | after.keys()
        if before.get(key) != after.get(key)
    }


def resolution_block(draft: DraftCampaign) -> dict[str, Any]:
    """Serialize the checklist state for the on-disk ``cache.json`` — the AI-readable record of what
    blocks mint, each gated field's value and provenance, and why each one blocks."""
    readiness = origin_readiness(draft)
    return {
        "complete": readiness.complete,
        "provenance": {
            field_name: prov.value for field_name, prov in draft.field_provenance.items()
        },
        "values": field_values(draft),
        "gaps": [gap.to_wire() for gap in readiness.gaps],
    }


__all__ = [
    "FieldGap",
    "OriginReadiness",
    "field_values",
    "origin_delta",
    "origin_projection",
    "origin_readiness",
    "resolution_block",
]
