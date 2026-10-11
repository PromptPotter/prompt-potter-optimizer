from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

from pydantic import ValidationError

from promptpotter.application.campaign_config import merge_config_layers
from promptpotter.application.datasets.csv_ingest import candidate_library_from_rows
from promptpotter.application.datasets.draft_campaign import (
    EditDraftPatch,
    NodeOutputEdit,
    OptimizationOverrides,
    load_checkin_draft,
    rendered_pipeline_json,
)
from promptpotter.application.optimizer_manifest import resolve_optimizer
from promptpotter.application.pipeline_resolve import draft_config_rows
from promptpotter.application.scoring.formula import SCORING_FUNCTIONS, parse_dials, spell_dials
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import (
    OUTPUT_SCHEMA_KEY,
    SCHEMA_TOGGLE_PARAM,
    description_key,
    description_path,
    narrowing_of,
)
from promptpotter.shared.errors import ConflictError, NotFoundError, PayloadInvalidError

if TYPE_CHECKING:
    from promptpotter.application.datasets.draft_campaign import DraftCampaign
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.store.stores import Stores

__all__ = [
    "DraftPatchPlan",
    "apply_draft_patch",
    "candidate_library_from_column",
    "plan_draft_patch",
]


class DraftPatchPlan(NamedTuple):
    changes: dict[str, Any]
    provenance: dict[str, Provenance]
    column_query: str | None
    column_ground_truth: str | None


def plan_draft_patch(stores: Stores, draft: DraftCampaign, patch: EditDraftPatch) -> DraftPatchPlan:
    changes: dict[str, Any] = {}
    provenance: dict[str, Provenance] = {}

    if patch.slug is not None and patch.slug != draft.slug:
        if stores.tenant_datasets.slug_exists(patch.slug):
            raise ConflictError(
                f"Slug '{patch.slug}' already exists in your collection.",
                code="slug_collision",
                details={"suggested_slug": stores.tenant_datasets.suggest_free_slug(patch.slug)},
            )
        changes["slug"] = patch.slug

    if patch.scoring_matcher is not None and patch.scoring_matcher not in SCORING_FUNCTIONS:
        raise PayloadInvalidError(
            f"patch.scoring_matcher {patch.scoring_matcher!r} is not one of the matchers "
            f"{sorted(SCORING_FUNCTIONS)}."
        )
    if patch.scoring_dials is not None:
        # Canonical spelling, so two drafts declaring the same criterion mint the same block.
        changes["scoring_dials"] = spell_dials(parse_dials(patch.scoring_dials))

    for patch_val, draft_attr in (
        (patch.connector, "connector"),
        (patch.scoring_matcher, "scoring_matcher"),
        (patch.origin_prompt_fields, "origin_prompt_fields"),
        (patch.pipeline_steps, "pipeline_steps"),
        (patch.candidate_library, "candidate_library"),
    ):
        if patch_val is not None:
            changes[draft_attr] = patch_val

    overlay = patch.pipeline_overlay
    if patch.node_output is not None:
        overlay = _authored_contract(
            draft.pipeline_overlay if overlay is None else overlay, patch.node_output
        )
    if patch.node_narrowing is not None:
        rows = draft_config_rows(draft, workspace=stores.base_dir)
        overlay = dict(draft.pipeline_overlay if overlay is None else overlay)
        for node, intents in patch.node_narrowing.items():
            try:
                narrowing = narrowing_of(rows.get(node, []), intents)
            except ValueError as exc:
                raise PayloadInvalidError(f"patch.node_narrowing.{node}: {exc}") from exc
            overlay[node] = {**_block(overlay.get(node)), "optimizer": narrowing.model_dump()}
    if overlay is not None:
        try:
            before = parse_pipeline_response(rendered_pipeline_json(draft))
            after = parse_pipeline_response(
                rendered_pipeline_json(draft.patch(pipeline_overlay=overlay))
            )
        except ValueError as exc:
            raise PayloadInvalidError(f"patch.pipeline_overlay: {exc}") from exc
        changes["pipeline_overlay"] = _narrowing_follows_schema(overlay, before, after)

    if patch.optimization_overrides is not None:
        merged = merge_config_layers(
            {"optimization": dict(draft.optimization_overrides)},
            {"optimization": patch.optimization_overrides},
        )
        try:
            overrides = OptimizationOverrides.model_validate(merged["optimization"])
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first["loc"])
            raise PayloadInvalidError(
                f"patch.optimization_overrides.{where}: {first['msg']}"
            ) from exc
        resolve_optimizer(overrides.optimizer, overrides.nodes)
        changes["optimization_overrides"] = overrides.model_dump(mode="json")

    if patch.raw_task_description is not None:
        changes["raw_task_description"] = patch.raw_task_description
        provenance["task_description"] = Provenance.CONFIRMED

    for label, col in (
        ("column_query", patch.column_query),
        ("column_ground_truth", patch.column_ground_truth),
    ):
        if col is not None and col not in draft.headers:
            raise PayloadInvalidError(
                f"patch.{label} {col!r} is not one of the uploaded headers {list(draft.headers)}."
            )

    return DraftPatchPlan(
        changes=changes,
        provenance=provenance,
        column_query=patch.column_query,
        column_ground_truth=patch.column_ground_truth,
    )


def _block(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _authored_contract(overlay: dict[str, Any], edit: NodeOutputEdit) -> dict[str, Any]:
    prev = _block(overlay.get(edit.node))
    config = _block(prev.get("config"))
    fields = list(_block(edit.output_schema.get("properties")))
    first = not _block(config.get(OUTPUT_SCHEMA_KEY))
    named = [f for f in (edit.answer_field, config.get("answer_field")) if f in fields]
    # The LAST field: fields generate in order, reasoning first.
    fallback = ["answer"] if "answer" in fields else fields[-1:]
    config[OUTPUT_SCHEMA_KEY] = edit.output_schema
    config.pop("answer_field", None)
    for field in (named or fallback)[:1]:
        config["answer_field"] = field
    block = {**prev, "config": config}
    if first:
        # A narrowing written before any schema existed could only tick `text`: re-open the axis.
        config[SCHEMA_TOGGLE_PARAM] = "json"
        if "optimizer" in prev:
            optimizer = _block(prev["optimizer"])
            allowed = _block(optimizer.get("param_allowed_values"))
            allowed.pop(SCHEMA_TOGGLE_PARAM, None)
            optimizer["param_allowed_values"] = allowed
            keys = optimizer.get("param_keys")
            if isinstance(keys, list) and SCHEMA_TOGGLE_PARAM not in keys:
                optimizer["param_keys"] = [*keys, SCHEMA_TOGGLE_PARAM]
            block["optimizer"] = optimizer
    return {**overlay, edit.node: block}


def _narrowing_follows_schema(
    overlay: dict[str, Any], before: PipelineSchema, after: PipelineSchema
) -> dict[str, Any]:
    """A stored ``param_keys`` names description keys by PATH, so it follows an edited schema."""
    out = dict(overlay)
    for name, block in overlay.items():
        opt = block.get("optimizer") if isinstance(block, dict) else None
        if not isinstance(opt, dict) or not isinstance(opt.get("param_keys"), list):
            continue
        was, now = before.get_node(name), after.get_node(name)
        old = set(was.description_keys) if was else set()
        new = now.description_keys if now else []
        if set(new) == old:
            continue
        kept = [k for k in opt["param_keys"] if description_path(k) is None or k in new]
        for key in new:
            parent = (description_path(key) or "").rpartition(".")[0]
            if key not in old and (not parent or description_key(parent) in kept):
                kept.append(key)
        out[name] = {**block, "optimizer": {**opt, "param_keys": kept}}
    return out


def candidate_library_from_column(stores: Stores, draft_id: str, column: str) -> tuple[str, ...]:
    draft = load_checkin_draft(stores, draft_id)
    if draft is None:
        raise NotFoundError(f"draft {draft_id!r} not found.", code="command_target_not_found")
    if column not in draft.headers:
        raise PayloadInvalidError(
            f"column {column!r} is not one of the dataset's columns {list(draft.headers)}."
        )
    bank = stores.checkin.load_bank(draft_id)
    if bank is None:
        raise PayloadInvalidError("draft has no cached rows to build from.")
    terms = candidate_library_from_rows(bank["items"], column)
    if not terms:
        raise PayloadInvalidError(
            f"column {column!r} has no usable values.",
            code="ingest_failed",
            details={"reason": "empty"},
        )
    return terms


def apply_draft_patch(draft: DraftCampaign, plan: DraftPatchPlan) -> DraftCampaign:
    updated = draft.apply_resolution(values=plan.changes, provenance=plan.provenance)
    if plan.column_query is not None or plan.column_ground_truth is not None:
        updated = updated.confirm_columns(
            query_col=plan.column_query, ground_truth_col=plan.column_ground_truth
        )
    return updated
