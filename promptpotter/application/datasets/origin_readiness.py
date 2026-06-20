"""``origin_readiness`` — the deterministic gate between ingest and mint.

Pure function, no I/O. The proposer/gate split (spec:
``docs/specs/roadmap.md``) puts *two* parties on
"is this origin complete": an LLM resolver *proposes* (the ``checkin`` node,
origin-aware version), and this checklist *gates*. Mint is blocked until the
checklist passes; a false ``ready`` from the resolver is rejected and the open
gaps are fed back.

The checklist gates only what the operator (or the resolver) must genuinely
state — not config that has a sane default:

* **Column mapping** (``column.query`` / ``column.ground_truth``) — satisfied
  only when CONFIRMED *and* a member of the uploaded headers.
* **Task framing** (``task_description``) — satisfied when CONFIRMED. It's the
  one field with no default; the operator states it or the resolver proposes it
  high-confidence.
* **Answer space** — a closed-label target must enumerate every label in the
  committed prompt. Code owns this (``with_closed_answer_format`` writes the
  labels into ``answer_format``), so it's a safety that passes — kept because it
  is the real post-condition that a closed-label campaign can emit each label.
* **Node model** — every active node configured as an LLM call (its connector-
  merged ``config`` block carries an LLM-call axis) must own a ``model``. The one
  config axis that IS gated, because a missing model has no sane default — the
  dataset owns its task model, and the absence crashes at mint rather than
  defaulting. Model-only (provider is connector-derivable). See
  :func:`_check_node_models`.

Other config (connector, scorer, round cap, optimizer provider/model, the rest of
the backend node overlay) is NOT gated: each carries a sane default the operator
edits in the optional Advanced block. A default is not a hidden gap — it's a
default.
"""

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
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS, PARAM_SCOPE_KEYS

# A node's resolved ``config`` block is an LLM call iff it carries any of these
# axes — model/provider + the per-call tunables. Such a node must own a model
# (see :func:`_check_node_models`).
_LLM_CALL_KEYS: frozenset[str] = PARAM_FORBIDDEN_KEYS | PARAM_SCOPE_KEYS


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
    _check_confirmed(
        draft,
        field_key="task_description",
        label="task framing",
        hint="Describe what the prompt should do.",
        gaps=gaps,
    )

    _check_answer_space(draft, gaps=gaps)
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
    """A column field is satisfied iff CONFIRMED *and* a member of the headers."""
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
    label: str,
    hint: str,
    gaps: list[FieldGap],
) -> None:
    """``task_description`` is satisfied by CONFIRMED provenance — the operator
    stated it, or the resolver proposed it high-confidence. PROPOSED (low
    confidence) blocks until the operator confirms or corrects the framing."""
    provenance = draft.field_provenance.get(field_key, Provenance.UNSET)
    if provenance is Provenance.CONFIRMED:
        return
    reason = "proposed_unconfirmed" if provenance is Provenance.PROPOSED else "unset"
    extra = " (proposed — confirm or correct)." if provenance is Provenance.PROPOSED else ""
    gaps.append(FieldGap(field=field_key, reason=reason, hint=f"{hint} [{label}]{extra}"))


def _label_present(label: str, haystack: str) -> bool:
    """Whole-token membership — so the label ``other`` isn't matched inside
    ``another``/``mother``. Boundaries are non-alphanumeric, so multi-word
    labels (``technical support``) and numeric labels still match cleanly."""
    return re.search(rf"(?<![a-z0-9]){re.escape(label.lower())}(?![a-z0-9])", haystack) is not None


def _check_answer_space(draft: DraftCampaign, *, gaps: list[FieldGap]) -> None:
    """Closed-label datasets must enumerate every target label in the prompt.

    When the target column is a fixed taxonomy (:meth:`DraftCampaign.answer_space`),
    the origin stays open until each label appears verbatim in the prompt that
    will be committed — :meth:`DraftCampaign.committed_prompt_fields`, the one
    encoding the prompt writer also uses. The deterministic gate; the check-in
    proposer (handed the same answer space) is what authors the enumeration. A
    proposer that drops a label surfaces here as an open gap rather than minting a
    campaign whose prompt can never emit it (the failure that left every
    non-``financial`` row unscoreable). No-op for an open-ended target — there's
    no small fixed set to enumerate."""
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


def _check_node_models(draft: DraftCampaign, *, gaps: list[FieldGap]) -> None:
    """Every active node configured as an LLM call must own a ``model`` before mint.

    The dataset owns its task model (``application/CLAUDE.md § Backend overlay``);
    a missing one crashes loudly at mint (``config.py`` LLM-node model check). Pre-
    mint the backend schema (``is_llm``) isn't available, so this reasons over the
    connector-merged config instead: a node whose resolved ``config`` block carries
    any LLM-call axis (model/provider/reasoning_effort/…) but no non-empty ``model``
    is the exact pre-crash state — surfaced here as a check-in nudge, model-only
    (provider is derivable / connector-defaulted). CANDIDATE_SOURCE and other
    non-LLM nodes carry no such block and are not flagged; an LLM node with no seed
    config at all (rarer) the mint crash still backstops. Connector lookup is an
    in-memory registry read (no I/O); an unregistered connector is left for the
    commit path to reject."""
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
    """Current value of every closed-set field, keyed by the checklist field id.

    Surfaced into ``cache.json`` so the operator (and the AI) reads *what* each
    field is set to alongside its provenance, without re-deriving from the draft.
    """
    getters: dict[str, Callable[[DraftCampaign], Any]] = {
        "column.query": lambda d: d.column_query,
        "column.ground_truth": lambda d: d.column_ground_truth,
        "task_description": lambda d: d.raw_task_description,
        "answer_space": lambda d: list(d.answer_space() or ()),
    }
    return {key: getter(draft) for key, getter in getters.items()}


def resolution_block(draft: DraftCampaign) -> dict[str, Any]:
    """Serialize the draft's checklist state for the on-disk ``cache.json``.

    ``{complete, provenance: {field: tag}, values: {field: value},
    gaps: [{field, reason, hint}], simulated_checkin: {...}}`` — the AI-readable
    record of what blocks mint, the current value + provenance of every gated
    field, why each blocks, and (when non-empty) the audit marker for an origin
    whose check-in was simulated by an agent rather than the LLM node. The
    resolver additionally stamps its last resolution alongside this block.
    """
    readiness = origin_readiness(draft)
    return {
        "complete": readiness.complete,
        "provenance": {
            field_name: prov.value for field_name, prov in draft.field_provenance.items()
        },
        "values": field_values(draft),
        "gaps": [gap.to_wire() for gap in readiness.gaps],
        # Empty unless an agent simulated the check-in (authored the origin by
        # hand). Records WHO/WHAT/WHEN so a simulated origin is auditable on disk.
        "simulated_checkin": dict(draft.simulated_checkin),
    }


__all__ = [
    "FieldGap",
    "OriginReadiness",
    "field_values",
    "origin_readiness",
    "resolution_block",
]
