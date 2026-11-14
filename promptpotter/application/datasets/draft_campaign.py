from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from pydantic import Field

from promptpotter import connectors
from promptpotter.application.scoring.formula.compiler import (
    DIALS_KEY,
    parse_dials,
    spell_dials,
    split_scoring_block,
)
from promptpotter.application.scoring.formula.matchers import matcher_of
from promptpotter.domain.cell_terms import CellTerm
from promptpotter.domain.checkin_draft import DraftBasis, DraftFile, UploadBasis
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.pipeline_params import NodeCall
from promptpotter.domain.pipeline_parsing import merge_node_blocks
from promptpotter.domain.pipeline_schema import DEFAULT_PIPELINE
from promptpotter.domain.scoring import anchored_criterion_dials
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import NotFoundError

PREVIEW_ROWS = 10
"""Sample-preview head size returned alongside every mutation response."""


UPLOAD_DEFINITION_KEYS = frozenset({"pipeline_overlay"})
"""The keys of an upload's draft that are its definition's half (`dataset.yaml::pipeline`: the
node overlay), never its campaign's."""


def upload_pipeline_json(upload: UploadBasis, overlay: Mapping[str, NodeCall]) -> dict[str, Any]:
    """An upload's ``dataset.yaml::pipeline``: its connector's upload chain, *overlay* (per node,
    its params) laid on as each node's ``config``. The one definition its Start writes, the sole
    route for a backend tunable (`assets/datasets/CLAUDE.md`); a read lays the node templates."""
    connector = connectors.CONNECTORS.get(upload.connector)
    uploads = connector.uploads
    assert uploads is not None, "`draft_patch.py` and ingest name only a connector serving uploads"
    declared = {node: {"config": call.laid_keys()} for node, call in overlay.items()}
    return {
        "connector": connector.name,
        "nodes": merge_node_blocks({node: {} for node in uploads.chain}, declared),
        "pipelines": {DEFAULT_PIPELINE: list(uploads.chain)},
        "available_models": list(uploads.available_models),
    }


class DraftCriterion(StrictModel):
    """The criterion a draft runs, read once off its laid ``config.scoring``: what a check-in
    picks from. An edit sends these keys back as ``EditDraftPatch.config.scoring``."""

    matcher: str | None = Field(
        description="The matcher the per-sample formula calls; null where it calls none or several."
    )
    dials: str = Field(
        description="The composite's weights, `term=weight` joined by commas: the declared dials, "
        "or a pinned formula's own; empty scores correctness alone."
    )


def draft_criterion(
    scoring: str | dict[str, str] | None, terms: Mapping[str, CellTerm]
) -> DraftCriterion:
    """*terms*: what the draft's backend lets a dial weigh (`compiler.py::campaign_terms`)."""
    spec = split_scoring_block(scoring, judge_instrument=None)
    if isinstance(scoring, dict) and DIALS_KEY in scoring:
        weights = parse_dials(scoring[DIALS_KEY], terms)
    else:
        pinned = anchored_criterion_dials(spec.per_cell) if spec.per_cell else None
        weights = {name: dial.weight for name, dial in (pinned or {}).items()}
    return DraftCriterion(matcher=matcher_of(spec.per_sample), dials=spell_dials(weights, terms))


def owned_draft(stores: Stores, draft_id: str) -> DraftFile:
    """Asked before a draft's claim is taken; the tenant directory is the owner."""
    data = stores.drafts.read_draft(draft_id)
    if data is None:
        raise NotFoundError(f"draft {draft_id!r} not found.", code="command_target_not_found")
    return data


def owned_drafts(stores: Stores) -> list[DraftFile]:
    return [
        data
        for draft_id in stores.drafts.draft_ids()
        if (data := stores.drafts.read_draft(draft_id)) is not None
    ]


def new_draft(
    *,
    slug: str,
    basis: DraftBasis,
    n_samples: int,
    sample_preview: list[dict[str, str]],
    headers: list[str],
    column_label_sets: dict[str, tuple[str, ...]] | None = None,
    first_blank_row: dict[str, int] | None = None,
) -> DraftFile:
    now = utcnow_iso()
    column_query, column_ground_truth, provenance = _seed_provenance(headers)
    return DraftFile(
        slug=slug,
        n_samples=n_samples,
        sample_preview=tuple(dict(row) for row in sample_preview[:PREVIEW_ROWS]),
        basis=basis,
        config={},
        raw_task_description=None,
        created_at=now,
        updated_at=now,
        headers=tuple(headers),
        column_query=column_query,
        column_ground_truth=column_ground_truth,
        column_label_sets=dict(column_label_sets or {}),
        first_blank_row=dict(first_blank_row or {}),
        field_provenance=provenance,
        origin_prompts={},
        decomposed_task_context={},
        candidate_library=(),
        resolver_ran=False,
    ).at(mint_draft_id())


def _seed_provenance(
    headers: list[str],
) -> tuple[str | None, str | None, dict[str, Provenance]]:
    query_col = "query" if "query" in headers else None
    ground_truth_col = "ground_truth" if "ground_truth" in headers else None
    provenance: dict[str, Provenance] = {}
    for field_key, column in (
        ("column.query", query_col),
        ("column.ground_truth", ground_truth_col),
    ):
        if column is not None:
            provenance[field_key] = Provenance.CONFIRMED
    return query_col, ground_truth_col, provenance


def mint_draft_id() -> str:
    return f"d_{uuid.uuid4().hex[:16]}"


def default_slug_from_filename(filename: str) -> str:
    stem = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    # The dataset-name charset, so a derived slug is refused only where a typed one would be.
    cleaned = "".join(
        c if c.isascii() and (c.isalnum() or c in "-_") else "-" for c in stem.lower()
    )
    return cleaned.strip("-_") or "upload"
