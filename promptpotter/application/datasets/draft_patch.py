from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter import connectors
from promptpotter.application.datasets.draft_campaign import owned_draft
from promptpotter.application.datasets.startability import checkin_rows
from promptpotter.application.datasets.tabular_ingest import candidate_library_from_rows
from promptpotter.application.free_tier import (
    FreeFence,
    free_fence,
    outside_free_tier,
    refuse_outside_free_tier,
)
from promptpotter.application.pipeline_resolve import (
    DraftPipeline,
    ServedPipeline,
    draft_pipeline,
    draft_served,
    resolved_output_schemas,
)
from promptpotter.application.preflight import refuse_unpriced, unpriced_routes
from promptpotter.application.scoring.formula.compiler import parse_dials, spell_dials
from promptpotter.application.scoring.formula.matchers import MATCHERS
from promptpotter.domain.campaign_config import merge_config_layers
from promptpotter.domain.checkin_draft import DatasetBasis, UploadBasis
from promptpotter.domain.draft_edit import EditDraftPatch, NodeOutputEdit, OptimizationOverrides
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.pipeline_schema import (
    ANSWER_FIELDS_KEY,
    OUTPUT_SCHEMA_KEY,
    SCHEMA_TOGGLE_PARAM,
    description_key,
    description_path,
    description_paths,
    narrowing_of,
)
from promptpotter.shared.errors import ConflictError, PayloadInvalidError

if TYPE_CHECKING:
    from promptpotter.application.datasets.draft_campaign import DraftCampaign
    from promptpotter.infrastructure.store.stores import Stores


class DraftPatchPlan(NamedTuple):
    changes: dict[str, Any]
    provenance: dict[str, Provenance]
    column_query: str | None
    column_ground_truth: str | None
    # What the patched draft's connector serves, read once for the plan and the wire after it.
    served: ServedPipeline


async def plan_draft_patch(
    stores: Stores, draft: DraftCampaign, patch: EditDraftPatch
) -> DraftPatchPlan:
    changes: dict[str, Any] = {}
    provenance: dict[str, Provenance] = {}

    if patch.slug is not None and patch.slug != draft.slug:
        if stores.tenant_datasets.slug_exists(patch.slug):
            raise ConflictError(
                f"Slug '{patch.slug}' already exists in your collection. Suggested slug: "
                f"{stores.tenant_datasets.suggest_free_slug(patch.slug)}.",
                code="slug_collision",
            )
        changes["slug"] = patch.slug

    if patch.scoring_matcher is not None and patch.scoring_matcher not in MATCHERS:
        raise PayloadInvalidError(
            f"patch.scoring_matcher {patch.scoring_matcher!r} is not one of the matchers "
            f"{sorted(MATCHERS)}."
        )
    if patch.connector is not None and not (
        (chosen := connectors.CONNECTORS.table.get(patch.connector)) and chosen.serves_uploads
    ):
        raise PayloadInvalidError(
            f"patch.connector {patch.connector!r} does not serve an uploaded table; one of "
            f"{sorted(n for n, c in connectors.CONNECTORS.table.items() if c.serves_uploads)}."
        )
    if patch.connector is not None and isinstance(draft.basis, DatasetBasis):
        raise PayloadInvalidError(
            f"patch.connector: {draft.basis.dataset!r} runs the backend its definition names."
        )
    held = await draft_served(stores, draft)
    if (
        patch.connector is not None
        and isinstance(draft.basis, UploadBasis)
        and patch.connector != draft.basis.connector
    ):
        changes["basis"] = draft.basis.model_copy(update={"connector": patch.connector})
    served = (
        await draft_served(stores, draft.patch(basis=changes["basis"]))
        if "basis" in changes
        else held
    )
    if patch.scoring_dials is not None:
        # Canonical spelling, so two drafts declaring the same criterion mint the same block.
        changes["scoring_dials"] = spell_dials(parse_dials(patch.scoring_dials))

    for patch_val, draft_attr in (
        (patch.scoring_matcher, "scoring_matcher"),
        (patch.origin_prompts, "origin_prompts"),
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
        rows = draft_pipeline(stores, draft, held).rows
        overlay = dict(draft.pipeline_overlay if overlay is None else overlay)
        for node, intents in patch.node_narrowing.items():
            try:
                narrowing = narrowing_of(rows.get(node, []), intents)
            except ValueError as exc:
                raise PayloadInvalidError(f"patch.node_narrowing.{node}: {exc}") from exc
            overlay[node] = {**_block(overlay.get(node)), "optimizer": narrowing.model_dump()}
    if overlay is not None:
        before = _description_keys(draft_pipeline(stores, draft, held))
        patched = draft.patch(pipeline_overlay=overlay, **changes)
        after = _description_keys(draft_pipeline(stores, patched, served))
        changes["pipeline_overlay"] = _narrowing_follows_schema(overlay, before, after)

    if patch.optimization_overrides is not None:
        prior = dict(draft.optimization_overrides)
        # A node knob is its optimizer's: a switch starts the new one at its own defaults.
        if patch.optimization_overrides.get("optimizer", prior.get("optimizer")) != prior.get(
            "optimizer"
        ):
            prior.pop("nodes", None)
        merged = merge_config_layers(
            {"optimization": prior},
            {"optimization": patch.optimization_overrides},
        )
        overrides = PayloadInvalidError.validating(
            OptimizationOverrides,
            merged["optimization"],
            under=("patch", "optimization_overrides"),
        )
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

    if changes.keys() & {"pipeline_overlay", "optimization_overrides"}:
        # Only what THIS edit adds: a route already on the draft is the Start's to refuse.
        was_unpriced, was_outside = _refusals(
            draft_pipeline(stores, draft, held), fence=free_fence(stores, held.connector)
        )
        unpriced, outside = _refusals(
            draft_pipeline(stores, draft.patch(**changes), served),
            fence=free_fence(stores, served.connector),
        )
        refuse_unpriced([r for r in unpriced if r not in was_unpriced])
        refuse_outside_free_tier([o for o in outside if o not in was_outside])

    return DraftPatchPlan(
        changes=changes,
        provenance=provenance,
        column_query=patch.column_query,
        column_ground_truth=patch.column_ground_truth,
        served=served,
    )


def _description_keys(pipeline: DraftPipeline) -> dict[str, list[str]]:
    """Per node, the description params of the output schema the draft's config folds."""
    return {
        node: [description_key(p) for p in description_paths(schema.json_schema)]
        for node, schema in resolved_output_schemas(pipeline.chain, pipeline.params).items()
        if schema is not None
    }


def _refusals(pipeline: DraftPipeline, *, fence: FreeFence | None) -> tuple[list[str], list[str]]:
    config, params, schema, selected = (
        pipeline.config,
        pipeline.params,
        pipeline.chain,
        pipeline.optimizer,
    )
    outside = [] if fence is None else outside_free_tier(config, selected, params, schema, fence)
    return unpriced_routes(config, selected, params, schema), outside


def _block(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _authored_contract(overlay: dict[str, Any], edit: NodeOutputEdit) -> dict[str, Any]:
    prev = _block(overlay.get(edit.node))
    config = _block(prev.get("config"))
    fields = list(_block(edit.output_schema.get("properties")))
    first = not _block(config.get(OUTPUT_SCHEMA_KEY))
    authored = [f for f in edit.answer_fields or () if f in fields]
    kept = [f for f in config.get(ANSWER_FIELDS_KEY) or () if f in fields]
    # The LAST field: fields generate in order, reasoning first.
    fallback = ["answer"] if "answer" in fields else fields[-1:]
    config[OUTPUT_SCHEMA_KEY] = edit.output_schema
    config.pop(ANSWER_FIELDS_KEY, None)
    if answers := authored or kept or fallback:
        config[ANSWER_FIELDS_KEY] = answers
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
    overlay: dict[str, Any], before: dict[str, list[str]], after: dict[str, list[str]]
) -> dict[str, Any]:
    """A stored ``param_keys`` names description keys by PATH, so it follows an edited schema."""
    out = dict(overlay)
    for name, block in overlay.items():
        opt = block.get("optimizer") if isinstance(block, dict) else None
        if not isinstance(opt, dict) or not isinstance(opt.get("param_keys"), list):
            continue
        old = set(before.get(name, []))
        new = after.get(name, [])
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
    draft = owned_draft(stores, draft_id)
    if column not in draft.headers:
        raise PayloadInvalidError(
            f"column {column!r} is not one of the dataset's columns {list(draft.headers)}."
        )
    terms = candidate_library_from_rows(checkin_rows(stores, draft), column)
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
