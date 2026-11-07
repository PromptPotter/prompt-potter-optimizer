from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter import connectors
from promptpotter.application.datasets.authored import scorer_of
from promptpotter.application.datasets.draft_campaign import draft_criterion
from promptpotter.application.datasets.startability import checkin_rows
from promptpotter.application.datasets.tabular_ingest import (
    candidate_library_from_rows,
    parse_candidate_library,
)
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
from promptpotter.application.scoring.formula.compiler import (
    DIALS_KEY,
    campaign_terms,
    parse_dials,
    spell_dials,
)
from promptpotter.application.scoring.formula.matchers import MATCHERS
from promptpotter.domain.campaign_config import merge_config_layers
from promptpotter.domain.checkin_draft import DatasetBasis, UploadBasis
from promptpotter.domain.draft_edit import EditDraftPatch, NodeOutputEdit
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
from promptpotter.infrastructure.store.dataset_access import claim_own_definition
from promptpotter.shared.errors import PayloadInvalidError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from promptpotter.domain.cell_terms import CellTerm
    from promptpotter.domain.checkin_draft import DraftFile
    from promptpotter.infrastructure.store.stores import Stores


class DraftPatchPlan(NamedTuple):
    changes: dict[str, Any]
    provenance: dict[str, Provenance]
    column_query: str | None
    column_ground_truth: str | None
    # What the patched draft's connector serves, read once for the plan and the wire after it.
    served: ServedPipeline


async def plan_draft_patch(
    stores: Stores, draft: DraftFile, patch: EditDraftPatch
) -> DraftPatchPlan:
    changes: dict[str, Any] = {}
    provenance: dict[str, Provenance] = {}

    if patch.slug is not None and patch.slug != draft.slug:
        claim_own_definition(stores, patch.slug, new=True)
        changes["slug"] = patch.slug

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

    if patch.origin_prompts is not None:
        changes["origin_prompts"] = patch.origin_prompts
    if (library := _candidate_library(stores, draft, patch)) is not None:
        changes["candidate_library"] = library

    config = dict(draft.config)
    was = draft_pipeline(stores, draft, held)
    sent = {} if patch.config is None else patch.config.model_dump(mode="json", exclude_unset=True)
    if patch.config is not None:
        config = _stamp_optimizer(
            merge_config_layers(config, sent), was.config.optimization.optimizer
        )
        if "scoring" in sent:
            config["scoring"] = _folded_scoring(
                was.config.scoring, sent["scoring"], campaign_terms(served.connector)
            )
    if patch.node_output is not None:
        config = _authored_contract(config, was.params, patch.node_output)
    if patch.node_narrowing is not None:
        narrowed = _block(config.get("optimizer_narrowing"))
        for node, intents in patch.node_narrowing.items():
            try:
                narrowed[node] = narrowing_of(was.rows.get(node, []), intents).model_dump()
            except ValueError as exc:
                raise PayloadInvalidError(f"patch.node_narrowing.{node}: {exc}") from exc
        config["optimizer_narrowing"] = narrowed
    if config != draft.config:
        laid = draft_pipeline(stores, draft.patch(**changes, config=config), served)
        if "scoring" in sent:
            # Compiled here, so a criterion no cell could be graded by is refused, never stored.
            scorer_of(laid.config, verifier_graded=False)
        changes["config"] = _narrowing_follows_schema(
            config, _description_keys(was), _description_keys(laid)
        )

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

    if "config" in changes:
        # Only what THIS edit adds: a route already on the draft is the Start's to refuse.
        was_unpriced, was_outside = _refusals(was, fence=free_fence(stores, held.connector))
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


def _stamp_optimizer(config: dict[str, Any], running: str) -> dict[str, Any]:
    """An edit's ``optimization.nodes`` naming no optimizer addressed *running*, the one the draft
    ran before it. Named on the partial, since a partial cannot see its basis: an upload's connector
    switch moves the basis's optimizer beneath it, and a switch drops the nodes it addressed
    (`campaign_config.py::merge_config_layers`)."""
    optimization = _block(config.get("optimization"))
    if "nodes" not in optimization or "optimizer" in optimization:
        return config
    return {**config, "optimization": {**optimization, "optimizer": running}}


_SCORING_EDIT_KEYS = frozenset({"matcher", DIALS_KEY, "per_sample", "per_cell"})


def _folded_scoring(
    running: str | dict[str, str] | None, edit: object, terms: Mapping[str, CellTerm]
) -> object:
    """A string *edit* is the criterion whole; a mapping folds into *running*, the criterion the
    draft runs. Dials re-sent as the running criterion reads them move nothing, so a pinned
    formula's anchors survive a round trip; other dials replace it, to be anchored on the origin.
    *terms* are the patched draft's backend's: what its dials may weigh."""
    if not isinstance(edit, dict):
        return edit
    if unknown := sorted(set(edit) - _SCORING_EDIT_KEYS):
        raise PayloadInvalidError(
            f"patch.config.scoring names {unknown}; a scoring edit takes {sorted(_SCORING_EDIT_KEYS)}."
        )
    block = dict(running) if isinstance(running, dict) else {"per_sample": running}
    if "matcher" in edit:
        if edit["matcher"] not in MATCHERS:
            raise PayloadInvalidError(
                f"patch.config.scoring.matcher {edit['matcher']!r} is none of {sorted(MATCHERS)}."
            )
        block["per_sample"] = f"{edit['matcher']}(predicted, ground_truth)"
    if "per_sample" in edit:
        block["per_sample"] = edit["per_sample"]
    if "per_cell" in edit:
        block.pop(DIALS_KEY, None)
        block["per_cell"] = edit["per_cell"]
    if DIALS_KEY in edit:
        dials = spell_dials(parse_dials(str(edit[DIALS_KEY]), terms), terms)
        if dials != draft_criterion(running, terms).dials:
            block.pop("per_cell", None)
            block[DIALS_KEY] = dials
    # Canonical: two drafts declaring one criterion mint one block; empty dials are none.
    kept = {k: v for k, v in block.items() if v}
    return kept.get("per_sample") if set(kept) <= {"per_sample"} else kept


def _authored_contract(
    config: dict[str, Any], params: dict[str, Any], edit: NodeOutputEdit
) -> dict[str, Any]:
    """*params*, what the node runs before this edit, says whether it already declares a contract:
    a reused dataset's own counts."""
    overlay = _block(config.get("pipeline_overlay"))
    node = _block(overlay.get(edit.node))
    running = _block(params.get(edit.node))
    fields = list(_block(edit.output_schema.get("properties")))
    first = not _block(running.get(OUTPUT_SCHEMA_KEY))
    authored = [f for f in edit.answer_fields or () if f in fields]
    kept = [f for f in running.get(ANSWER_FIELDS_KEY) or () if f in fields]
    # The LAST field: fields generate in order, reasoning first.
    inferred = ["answer"] if "answer" in fields else fields[-1:]
    node[OUTPUT_SCHEMA_KEY] = edit.output_schema
    node.pop(ANSWER_FIELDS_KEY, None)
    if answers := authored or kept or inferred:
        node[ANSWER_FIELDS_KEY] = answers
    out = {**config, "pipeline_overlay": {**overlay, edit.node: node}}
    if not first:
        return out
    # A narrowing written before any schema existed could only tick `text`: re-open the axis.
    node[SCHEMA_TOGGLE_PARAM] = "json"
    narrowed = _block(config.get("optimizer_narrowing"))
    if edit.node in narrowed:
        narrowing = _block(narrowed[edit.node])
        allowed = _block(narrowing.get("param_allowed_values"))
        allowed.pop(SCHEMA_TOGGLE_PARAM, None)
        narrowing["param_allowed_values"] = allowed
        keys = narrowing.get("param_keys")
        if isinstance(keys, list) and SCHEMA_TOGGLE_PARAM not in keys:
            narrowing["param_keys"] = [*keys, SCHEMA_TOGGLE_PARAM]
        out["optimizer_narrowing"] = {**narrowed, edit.node: narrowing}
    return out


def _narrowing_follows_schema(
    config: dict[str, Any], before: dict[str, list[str]], after: dict[str, list[str]]
) -> dict[str, Any]:
    """A stored ``param_keys`` names description keys by PATH, so it follows an edited schema."""
    narrowed = _block(config.get("optimizer_narrowing"))
    out = dict(narrowed)
    for name, narrowing in narrowed.items():
        keys = narrowing.get("param_keys") if isinstance(narrowing, dict) else None
        if not isinstance(keys, list):
            continue
        old = set(before.get(name, []))
        new = after.get(name, [])
        if set(new) == old:
            continue
        kept = [k for k in keys if description_path(k) is None or k in new]
        for key in new:
            parent = (description_path(key) or "").rpartition(".")[0]
            if key not in old and (not parent or description_key(parent) in kept):
                kept.append(key)
        out[name] = {**narrowing, "param_keys": kept}
    return {**config, "optimizer_narrowing": out} if out != narrowed else config


_LIBRARY_SOURCES = ("candidate_library", "candidate_library_column", "candidate_library_file")


def _candidate_library(
    stores: Stores, draft: DraftFile, patch: EditDraftPatch
) -> tuple[str, ...] | None:
    """The library a patch sets, from the one source it sends: the terms, a draft column's
    distinct values, or a parsed file."""
    sent = [name for name in _LIBRARY_SOURCES if getattr(patch, name) is not None]
    if len(sent) > 1:
        raise PayloadInvalidError(f"patch sends {sent}; a library comes from one of them.")
    if patch.candidate_library_file is not None:
        upload = patch.candidate_library_file
        return parse_candidate_library(upload.blob, upload.filename)
    if (column := patch.candidate_library_column) is None:
        return None if patch.candidate_library is None else tuple(patch.candidate_library)
    if column not in draft.headers:
        raise PayloadInvalidError(
            f"patch.candidate_library_column {column!r} is not one of the dataset's columns "
            f"{list(draft.headers)}."
        )
    terms = candidate_library_from_rows(checkin_rows(stores, draft), column)
    if not terms:
        raise PayloadInvalidError(
            f"column {column!r} has no usable values.",
            code="ingest_failed",
            details={"reason": "empty"},
        )
    return terms


def apply_draft_patch(draft: DraftFile, plan: DraftPatchPlan) -> DraftFile:
    updated = draft.apply_resolution(values=plan.changes, provenance=plan.provenance)
    if plan.column_query is not None or plan.column_ground_truth is not None:
        updated = updated.confirm_columns(
            query_col=plan.column_query, ground_truth_col=plan.column_ground_truth
        )
    return updated
