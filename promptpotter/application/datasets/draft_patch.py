"""The sparse mutation vocabulary for a :class:`DraftCampaign`, and the rules that apply one.

**Every ingress edits an origin through here** — the browser's Advanced block, the two
candidate-library uploads, and the CLI's ``--set``. It sits in ``application/`` because a rule
parked in ``presentation/api/routers/`` is one no CLI verb can import without dragging FastAPI in,
and the adapter that cannot reach it writes a narrower copy instead.

Appending the ``CommandRecord`` is ``commands/checkin_dispatch.py::dispatch_draft_patch``'s job.
This module owns only what an edit MEANS.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

from pydantic import Field

from promptpotter.application.datasets.draft_campaign import (
    OptimizationOverrides,
    rendered_pipeline_json,
)
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import description_key, description_path
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.errors import ConflictError, PayloadInvalidError

if TYPE_CHECKING:
    from promptpotter.application.datasets.draft_campaign import DraftCampaign
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.store.stores import Stores

__all__ = [
    "SETTABLE_SCALARS",
    "DraftPatchPlan",
    "EditDraftPatch",
    "apply_draft_patch",
    "plan_draft_patch",
]


class EditDraftPatch(StrictModel):
    """Sparse mutation payload — only declared fields ride through."""

    slug: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$"
    )
    connector: str | None = Field(default=None, min_length=1, max_length=64)
    scoring_composite: str | None = Field(default=None, min_length=1, max_length=64)
    raw_task_description: str | None = Field(default=None, min_length=1, max_length=16384)
    pipeline_overlay: dict[str, Any] | None = None
    # Written by the setup-panel mode toggle; read by commit's `_build_origin_pipeline_json`
    # and by `draft_active_steps`.
    pipeline_steps: list[str] | None = None
    column_query: str | None = Field(default=None, max_length=256)
    column_ground_truth: str | None = Field(default=None, max_length=256)
    # Replaces the draft's fields wholesale — the editor sends the full PromptTemplate object.
    origin_prompt_fields: dict[str, Any] | None = None
    # Shallow-merged onto the draft's current overrides then validated against
    # OptimizationOverrides, so the editor can send one knob or several; a nested
    # `mechanisms` replaces wholesale.
    optimization_overrides: dict[str, Any] | None = None
    # From the operator's upload or derived from one of the draft's own columns
    # (`routers/datasets/ingest.py`); both ride this patch.
    candidate_library: list[str] | None = Field(default=None, min_length=1)


# The fields a caller can carry as ONE raw token — what the CLI's `FIELD=VALUE` can express, and
# what a `--set` vocabulary is derived from rather than re-listed. Membership is decided by TYPE:
# a list or dict field has no flat form on a command line, and the browser sends those as JSON.
SETTABLE_SCALARS: frozenset[str] = frozenset(
    name for name, f in EditDraftPatch.model_fields.items() if f.annotation == (str | None)
)


class DraftPatchPlan(NamedTuple):
    """What one patch WILL do, resolved against the draft it targets but not yet written.

    Split from the write so the dispatcher can project the origin, record the command, and only
    then apply — and so every refusal (slug taken, column not uploaded, knob out of range) is
    raised before anything is recorded as having happened.
    """

    changes: dict[str, Any]
    provenance: dict[str, Provenance]
    column_query: str | None
    column_ground_truth: str | None


def plan_draft_patch(stores: Stores, draft: DraftCampaign, patch: EditDraftPatch) -> DraftPatchPlan:
    """Resolve *patch* against *draft*, raising rather than writing a half-applied edit."""
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

    # Config + the authored prompt are not gated — just set the value.
    for patch_val, draft_attr in (
        (patch.connector, "connector"),
        (patch.scoring_composite, "scoring_composite"),
        (patch.origin_prompt_fields, "origin_prompt_fields"),
        (patch.pipeline_steps, "pipeline_steps"),
        (patch.candidate_library, "candidate_library"),
    ):
        if patch_val is not None:
            changes[draft_attr] = patch_val

    # A declared output contract is checked by the one parser that reads it: an `answer_field`
    # outside its `output_schema` raises there, and unchecked here it failed the draft's own read.
    if patch.pipeline_overlay is not None:
        try:
            before = parse_pipeline_response(rendered_pipeline_json(draft))
            after = parse_pipeline_response(
                rendered_pipeline_json(draft.patch(pipeline_overlay=patch.pipeline_overlay))
            )
        except ValueError as exc:
            raise PayloadInvalidError(f"patch.pipeline_overlay: {exc}") from exc
        changes["pipeline_overlay"] = _narrowing_follows_schema(
            patch.pipeline_overlay, before, after
        )

    # Shallow-merge so one knob can change without resetting the rest, then validate the
    # result (rejects unknown keys / out-of-range max_rounds / malformed mechanisms).
    if patch.optimization_overrides is not None:
        merged = {**draft.optimization_overrides, **patch.optimization_overrides}
        changes["optimization_overrides"] = OptimizationOverrides.model_validate(merged).model_dump(
            mode="json"
        )

    # The task framing IS gated — an operator edit CONFIRMS it, which is what opens the
    # origin-readiness gate for a field left PROPOSED or UNSET.
    if patch.raw_task_description is not None:
        changes["raw_task_description"] = patch.raw_task_description
        provenance["task_description"] = Provenance.CONFIRMED

    # Each column must be a member of the uploaded headers; confirming flips its provenance so
    # the origin-readiness gate opens.
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


def _narrowing_follows_schema(
    overlay: dict[str, Any], before: PipelineSchema, after: PipelineSchema
) -> dict[str, Any]:
    """A stored ``param_keys`` list names each description key by PATH, so an edited output schema
    would leave it naming fields that are gone and holding every field that is new. It follows
    instead: a surviving path keeps its state, a removed one drops, a new top-level field opens —
    as on a node with no list — and a new nested one takes its parent's, the lock's inheritance.
    A rename is a removal plus an addition."""
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


def apply_draft_patch(draft: DraftCampaign, plan: DraftPatchPlan) -> DraftCampaign:
    """The planned edit, folded onto *draft*. Pure — the caller persists what comes back."""
    updated = draft.apply_resolution(values=plan.changes, provenance=plan.provenance)
    if plan.column_query is not None or plan.column_ground_truth is not None:
        updated = updated.confirm_columns(
            query_col=plan.column_query, ground_truth_col=plan.column_ground_truth
        )
    return updated
