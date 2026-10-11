from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from promptpotter import connectors
from promptpotter.application.bench.task_context import OriginNextAction
from promptpotter.application.commands.payloads import EditDraftCampaignPayload
from promptpotter.application.datasets.draft_campaign import (
    DraftCampaign,
    merge_pipeline_overlay,
)
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.search_point import PARAM_SCOPE_KEYS, WHO_ANSWERS_KEYS
from promptpotter.domain.strict_model import StrictModel

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.stores import Stores

_LLM_CALL_KEYS: frozenset[str] = WHO_ANSWERS_KEYS | PARAM_SCOPE_KEYS


class FieldGap(StrictModel):
    """One origin field that still blocks mint — also the ``422 origin_incomplete`` body's ``details.gaps``."""

    field: str = Field(description="Checklist field id, e.g. `column.query`.")
    reason: Literal["unset", "proposed_unconfirmed"]
    hint: str = Field(description="One operator-facing line on how to close it.")


class OriginReadiness(StrictModel):
    """The mint gate's verdict; a surface gates Start on it and never re-derives it."""

    complete: bool = Field(description="True iff no field still blocks mint.")
    gaps: tuple[FieldGap, ...]


class OriginLastResolution(StrictModel):
    """One resolver turn's own output. Its findings ride ``OriginResolution.raised`` as commands."""

    assessment: str
    next_action: OriginNextAction
    recap: str = Field(description="Set only on a `ready` turn.")


class RaisedCommand(StrictModel):
    """One proposal, already the command a click fires. The assistant offers it and never fires it."""

    kind: Literal["edit-draft-campaign"] = "edit-draft-campaign"
    payload: EditDraftCampaignPayload
    evidence: str = Field(description="The citation backing the proposal.")


class OriginResolution(StrictModel):
    """The checklist state ``cache.json::resolution`` holds, and the last resolver turn beside it."""

    complete: bool
    provenance: dict[str, Provenance]
    values: dict[str, Any] = Field(description="Each gated field's current value, by field id.")
    gaps: tuple[FieldGap, ...]
    last_resolution: OriginLastResolution | None = Field(
        default=None, description="Null until a resolver turn ran, and again after a later edit."
    )
    raised: list[RaisedCommand] = Field(
        default_factory=list, description="Proposals the last turn left unclicked."
    )
    degraded_cause: str | None = Field(
        default=None,
        description="Why the last turn came back thin (a paid repair retry); null where it did not.",
    )


def origin_readiness(draft: DraftCampaign) -> OriginReadiness:
    """Ready is not mintable: a node's required ``{{template vars}}`` are checked only at mint."""
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
    provenance = draft.field_provenance.get(field_key, Provenance.UNSET)
    if provenance is Provenance.CONFIRMED and value.strip():
        return
    proposed = provenance is Provenance.PROPOSED
    extra = " (proposed — confirm or correct)." if proposed else ""
    gaps.append(
        FieldGap(
            field=field_key,
            reason="proposed_unconfirmed" if proposed else "unset",
            hint=f"{hint} [{label}]{extra}",
        )
    )


def _check_node_models(draft: DraftCampaign, *, gaps: list[FieldGap]) -> None:
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
    getters: dict[str, Callable[[DraftCampaign], Any]] = {
        "column.query": lambda d: d.column_query,
        "column.ground_truth": lambda d: d.column_ground_truth,
        "task_description": lambda d: d.raw_task_description,
        "answer_space": lambda d: list(d.answer_space() or ()),
    }
    return {key: getter(draft) for key, getter in getters.items()}


def origin_projection(draft: DraftCampaign) -> dict[str, Any]:
    """Not hash-equivalent to the committed origin: a content_hash check runs the real commit."""
    projection: dict[str, Any] = {
        "slug": draft.slug,
        "connector": draft.connector,
        "scoring_matcher": draft.scoring_matcher,
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
    return {
        key: {"from": before.get(key), "to": after.get(key)}
        for key in before.keys() | after.keys()
        if before.get(key) != after.get(key)
    }


def resolution_block(draft: DraftCampaign) -> OriginResolution:
    readiness = origin_readiness(draft)
    return OriginResolution(
        complete=readiness.complete,
        provenance=dict(draft.field_provenance),
        values=field_values(draft),
        gaps=readiness.gaps,
    )


def save_checkin_draft(
    stores: Stores, draft: DraftCampaign, *, resolution: OriginResolution | None = None
) -> DraftCampaign:
    stores.checkin.write_draft(draft.draft_id, draft.to_disk())
    block = resolution or resolution_block(draft)
    stores.checkin.write_resolution(draft.draft_id, block.model_dump(mode="json"))
    return draft


def load_resolution(stores: Stores, draft: DraftCampaign) -> OriginResolution:
    block = (stores.checkin.load_bank(draft.draft_id) or {}).get("resolution")
    return OriginResolution.model_validate(block) if block else resolution_block(draft)


__all__ = [
    "FieldGap",
    "OriginLastResolution",
    "OriginReadiness",
    "OriginResolution",
    "RaisedCommand",
    "field_values",
    "load_resolution",
    "origin_delta",
    "origin_projection",
    "origin_readiness",
    "resolution_block",
    "save_checkin_draft",
]
