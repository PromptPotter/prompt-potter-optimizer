from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from pydantic import Field

from promptpotter import connectors
from promptpotter.application.campaign_config import (
    CampaignConfig,
    OptimizationConfig,
    load_campaign_config,
)
from promptpotter.application.scoring.formula import DIALS_KEY
from promptpotter.connectors import DEFAULT_CONNECTOR
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.pipeline_parsing import merge_node_blocks
from promptpotter.domain.pipeline_schema import (
    ManifestNodeOverlay,
    NodeSearchNarrowing,
    ParamIntent,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.identity import TenantId, safe_name

if TYPE_CHECKING:
    from promptpotter.connectors.protocol import Connector

DEFAULT_SCORING_MATCHER = "label_match"
"""Only universally-applicable matcher for ``(query, ground_truth)`` shape."""

DEFAULT_MAX_ROUNDS = 5
"""Matches the M10 prompt-iteration framework default."""

PREVIEW_ROWS = 10
"""Sample-preview head size returned alongside every mutation response."""


class OptimizationOverrides(StrictModel):
    """The ``max_rounds`` bound gates the operator EDIT path only; the trusted internal
    ``draft_from_dataset`` builds the dict directly, so a reused dataset may exceed it."""

    max_rounds: int = Field(
        DEFAULT_MAX_ROUNDS,
        ge=0,
        le=100,
        description="Round ceiling for the campaign. 0 = measure the origin and stop.",
    )
    optimizer: str = Field(
        OptimizationConfig.model_fields["optimizer"].default,
        min_length=1,
        description=OptimizationConfig.model_fields["optimizer"].description,
    )
    nodes: dict[str, ManifestNodeOverlay] = Field(
        default_factory=dict,
        description=OptimizationConfig.model_fields["nodes"].description,
    )


def _default_optimization_overrides() -> dict[str, Any]:
    return OptimizationOverrides().model_dump(mode="json")


class NodeOutputEdit(StrictModel):
    """One node's authored output contract: the schema whole, and the field carrying the answer
    where the operator named one."""

    node: str = Field(min_length=1)
    output_schema: dict[str, Any]
    answer_field: str | None = None


class EditDraftPatch(StrictModel):
    """Sparse mutation payload — only declared fields ride through. The one origin-edit vocabulary
    every ingress shares; the rules that apply one are ``draft_patch.py``'s, and stay out of this
    module so a command payload naming the type loads no resolver."""

    slug: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$"
    )
    connector: str | None = Field(default=None, min_length=1, max_length=64)
    scoring_matcher: str | None = Field(default=None, min_length=1, max_length=64)
    # The empty string clears the dials back to correctness alone.
    scoring_dials: str | None = Field(default=None, max_length=512)
    raw_task_description: str | None = Field(default=None, min_length=1, max_length=16384)
    pipeline_overlay: dict[str, Any] | None = None
    node_narrowing: dict[str, list[ParamIntent]] | None = None
    node_output: NodeOutputEdit | None = None
    pipeline_steps: list[str] | None = None
    column_query: str | None = Field(default=None, max_length=256)
    column_ground_truth: str | None = Field(default=None, max_length=256)
    origin_prompt_fields: dict[str, Any] | None = None
    # Merged onto the draft's current overrides, `nodes` key by key.
    optimization_overrides: dict[str, Any] | None = None
    candidate_library: list[str] | None = Field(default=None, min_length=1)


SETTABLE_SCALARS: frozenset[str] = frozenset(
    name for name, f in EditDraftPatch.model_fields.items() if f.annotation == (str | None)
)


def closed_answer_format(labels: tuple[str, ...]) -> str:
    return "Choose exactly one of these labels: " + " | ".join(labels)


@dataclass(frozen=True, slots=True)
class DraftCampaign:
    draft_id: str
    tenant_id: TenantId
    slug: str
    n_samples: int
    sample_preview: tuple[dict[str, str], ...]
    connector: str
    scoring_matcher: str
    raw_task_description: str
    pipeline_overlay: dict[str, Any]
    created_at: str
    updated_at: str
    headers: tuple[str, ...] = ()
    column_query: str = ""
    column_ground_truth: str = ""
    column_label_sets: dict[str, tuple[str, ...]] = field(default_factory=dict)
    field_provenance: dict[str, Provenance] = field(default_factory=dict)
    source_file: str = ""
    origin_prompt_fields: dict[str, Any] = field(default_factory=dict)
    decomposed_task_context: dict[str, Any] = field(default_factory=dict)
    pipeline_steps: list[str] = field(default_factory=list)
    optimization_overrides: dict[str, Any] = field(default_factory=_default_optimization_overrides)
    candidate_library: tuple[str, ...] = ()
    # Empty = never fetched or unreachable: served as `schema_source: unreachable`, never "locked".
    backend_nodes: dict[str, Any] = field(default_factory=dict)
    # Non-empty routes the check-in's Start through the ``origin_override`` seed.
    reused_origin_id: str = ""
    scoring_dials: str = ""

    def to_disk(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "n_samples": self.n_samples,
            "sample_preview": [dict(row) for row in self.sample_preview],
            "connector": self.connector,
            "scoring_matcher": self.scoring_matcher,
            "scoring_dials": self.scoring_dials,
            "raw_task_description": self.raw_task_description,
            "pipeline_overlay": dict(self.pipeline_overlay),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "headers": list(self.headers),
            "column_query": self.column_query,
            "column_ground_truth": self.column_ground_truth,
            "column_label_sets": {k: list(v) for k, v in self.column_label_sets.items()},
            "field_provenance": {k: v.value for k, v in self.field_provenance.items()},
            "source_file": self.source_file,
            "origin_prompt_fields": dict(self.origin_prompt_fields),
            "decomposed_task_context": dict(self.decomposed_task_context),
            "pipeline_steps": list(self.pipeline_steps),
            "optimization_overrides": dict(self.optimization_overrides),
            "candidate_library": list(self.candidate_library),
            "backend_nodes": dict(self.backend_nodes),
            "reused_origin_id": self.reused_origin_id,
        }

    @classmethod
    def from_disk(
        cls, data: dict[str, Any], *, draft_id: str, tenant_id: TenantId
    ) -> DraftCampaign:
        return cls(
            draft_id=draft_id,
            tenant_id=tenant_id,
            slug=data["slug"],
            n_samples=data["n_samples"],
            sample_preview=tuple(dict(row) for row in data.get("sample_preview", [])),
            connector=data["connector"],
            scoring_matcher=data["scoring_matcher"],
            scoring_dials=data["scoring_dials"],
            raw_task_description=data.get("raw_task_description", ""),
            pipeline_overlay=dict(data.get("pipeline_overlay", {})),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            headers=tuple(data.get("headers", ())),
            column_query=data.get("column_query", ""),
            column_ground_truth=data.get("column_ground_truth", ""),
            column_label_sets={k: tuple(v) for k, v in data.get("column_label_sets", {}).items()},
            field_provenance={
                k: Provenance(v) for k, v in data.get("field_provenance", {}).items()
            },
            source_file=data.get("source_file", ""),
            origin_prompt_fields=dict(data.get("origin_prompt_fields", {})),
            decomposed_task_context=dict(data.get("decomposed_task_context", {})),
            pipeline_steps=list(data.get("pipeline_steps", [])),
            optimization_overrides={
                **_default_optimization_overrides(),
                **data.get("optimization_overrides", {}),
            },
            candidate_library=tuple(data.get("candidate_library", ())),
            backend_nodes=dict(data.get("backend_nodes", {})),
            reused_origin_id=data.get("reused_origin_id", ""),
        )

    def answer_space(self) -> tuple[str, ...] | None:
        if not self.column_ground_truth:
            return None
        return self.column_label_sets.get(self.column_ground_truth)

    def committed_prompt_fields(self) -> dict[str, Any]:
        # Wiped strings count as blank, so the fallback is not keyed on the dict being empty.
        authored = any(str(value).strip() for value in self.origin_prompt_fields.values())
        fields = (
            dict(self.origin_prompt_fields)
            if authored
            else {"instruction": self.raw_task_description}
        )
        # Appended even when answer_format is blank: the optimizer prompts promise the labels.
        labels = self.answer_space()
        if labels:
            enumeration = closed_answer_format(labels)
            fmt = str(fields.get("answer_format", "")).strip()
            if enumeration not in fmt:
                fields["answer_format"] = f"{fmt}\n{enumeration}" if fmt else enumeration
        return fields

    def patch(self, **changes: Any) -> DraftCampaign:
        return replace(self, updated_at=utcnow_iso(), **changes)

    def confirm_columns(
        self, *, query_col: str | None = None, ground_truth_col: str | None = None
    ) -> DraftCampaign:
        provenance = dict(self.field_provenance)
        changes: dict[str, Any] = {}
        if query_col is not None:
            changes["column_query"] = query_col
            provenance["column.query"] = Provenance.CONFIRMED
        if ground_truth_col is not None:
            changes["column_ground_truth"] = ground_truth_col
            provenance["column.ground_truth"] = Provenance.CONFIRMED
        return self.patch(field_provenance=provenance, **changes)

    def apply_resolution(
        self,
        *,
        values: dict[str, Any] | None = None,
        provenance: dict[str, Provenance] | None = None,
    ) -> DraftCampaign:
        merged = dict(self.field_provenance)
        if provenance:
            merged.update(provenance)
        return self.patch(field_provenance=merged, **(values or {}))


def merge_pipeline_overlay(draft: DraftCampaign, connector: Connector) -> dict[str, Any]:
    """What is WRITTEN: without ``backend_nodes``, whose ``param_keys`` would pin the search space."""
    return merge_node_blocks(dict(connector.default_node_config), draft.pipeline_overlay or {})


def resolved_node_schema(draft: DraftCampaign, connector: Connector) -> dict[str, Any]:
    return merge_node_blocks(dict(draft.backend_nodes), merge_pipeline_overlay(draft, connector))


def load_checkin_draft(stores: Stores, campaign_id: str) -> DraftCampaign | None:
    data = stores.checkin.read_draft(campaign_id)
    if data is None:
        return None
    return DraftCampaign.from_disk(data, draft_id=campaign_id, tenant_id=stores.identity.tenant_id)


def draft_pipeline_json(draft: DraftCampaign, nodes: dict[str, Any]) -> dict[str, Any]:
    pipeline: dict[str, Any] = {
        "name": draft.slug,
        "backend_type": draft.connector,
        "backend_name": draft.connector,
    }
    connector = connectors.get(draft.connector)
    steps = draft.pipeline_steps or list(connector.default_pipeline)
    if steps:
        pipeline["pipelines"] = {"default": list(steps)}
    if connector.available_models:
        pipeline["available_models"] = list(connector.available_models)

    if nodes:
        pipeline["nodes"] = nodes
    return pipeline


def committed_pipeline_json(draft: DraftCampaign) -> dict[str, Any]:
    return draft_pipeline_json(
        draft, merge_pipeline_overlay(draft, connectors.get(draft.connector))
    )


def rendered_pipeline_json(draft: DraftCampaign) -> dict[str, Any]:
    return draft_pipeline_json(draft, resolved_node_schema(draft, connectors.get(draft.connector)))


def declared_pipeline_json(draft: DraftCampaign) -> dict[str, Any]:
    """Without the operator's layer: ``narrow`` REPLACES allowed values, so unticking needs this."""
    connector = connectors.get(draft.connector)
    return draft_pipeline_json(
        draft, merge_node_blocks(dict(draft.backend_nodes), dict(connector.default_node_config))
    )


def draft_scoring_block(draft: DraftCampaign) -> str | dict[str, str]:
    per_sample = f"{draft.scoring_matcher}(predicted, ground_truth)"
    if not draft.scoring_dials:
        return per_sample
    return {"per_sample": per_sample, DIALS_KEY: draft.scoring_dials}


def default_campaign_config(draft: DraftCampaign) -> CampaignConfig:
    connector = connectors.get(draft.connector)
    overrides = draft.optimization_overrides
    optimization: dict[str, Any] = {"max_rounds": overrides["max_rounds"]}
    optimization.update(dict(connector.default_optimization))
    optimization["optimizer"] = overrides["optimizer"]
    optimization["nodes"] = dict(overrides["nodes"])
    return load_campaign_config(
        {
            "dataset_name": draft.slug,
            "scoring": draft_scoring_block(draft),
            "optimization": optimization,
        }
    )


def draft_campaign_config(draft: DraftCampaign) -> CampaignConfig:
    base = default_campaign_config(draft)
    overrides, narrowing = split_overlay(draft.pipeline_overlay or {})
    return base.model_copy(
        update={
            "pipeline_overlay": {**base.pipeline_overlay, **overrides},
            "optimizer_narrowing": {**base.optimizer_narrowing, **narrowing},
        }
    )


def split_overlay(
    pipeline_overlay: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, NodeSearchNarrowing]]:
    overrides: dict[str, Any] = {}
    narrowing: dict[str, NodeSearchNarrowing] = {}
    for node, block in pipeline_overlay.items():
        if not isinstance(block, dict):
            continue
        config = block.get("config")
        if isinstance(config, dict) and config:
            overrides[node] = dict(config)
        optimizer = block.get("optimizer")
        if isinstance(optimizer, dict) and optimizer:
            narrowing[node] = NodeSearchNarrowing(
                param_keys=optimizer.get("param_keys"),
                param_allowed_values=optimizer.get("param_allowed_values", {}),
            )
    return overrides, narrowing


def new_draft(
    *,
    tenant_id: TenantId,
    slug: str,
    n_samples: int,
    sample_preview: list[dict[str, str]],
    headers: list[str],
    source_file: str = "",
    column_label_sets: dict[str, tuple[str, ...]] | None = None,
) -> DraftCampaign:
    now = utcnow_iso()
    column_query, column_ground_truth, provenance = _seed_provenance(headers)
    return DraftCampaign(
        draft_id=_mint_draft_id(),
        tenant_id=tenant_id,
        slug=slug,
        n_samples=n_samples,
        sample_preview=tuple(dict(row) for row in sample_preview[:PREVIEW_ROWS]),
        connector=DEFAULT_CONNECTOR,
        scoring_matcher=DEFAULT_SCORING_MATCHER,
        raw_task_description="",
        pipeline_overlay={},
        headers=tuple(headers),
        column_query=column_query,
        column_ground_truth=column_ground_truth,
        column_label_sets=dict(column_label_sets or {}),
        field_provenance=provenance,
        created_at=now,
        updated_at=now,
        source_file=source_file,
    )


def _seed_provenance(
    headers: list[str],
) -> tuple[str, str, dict[str, Provenance]]:
    query_col, ground_truth_col, provenance = _auto_detect_columns(headers)
    provenance["task_description"] = Provenance.UNSET
    return query_col, ground_truth_col, provenance


def _auto_detect_columns(
    headers: list[str],
) -> tuple[str, str, dict[str, Provenance]]:
    header_set = set(headers)
    query_col = "query" if "query" in header_set else ""
    ground_truth_col = "ground_truth" if "ground_truth" in header_set else ""
    provenance = {
        "column.query": Provenance.CONFIRMED if query_col else Provenance.UNSET,
        "column.ground_truth": Provenance.CONFIRMED if ground_truth_col else Provenance.UNSET,
    }
    return query_col, ground_truth_col, provenance


def _mint_draft_id() -> str:
    return f"d_{uuid.uuid4().hex[:16]}"


_DERIVED_PREFIX = "dataset:"


def dataset_source_of(source_file: str) -> str | None:
    if source_file.startswith(_DERIVED_PREFIX):
        return source_file[len(_DERIVED_PREFIX) :] or None
    return None


def default_slug_from_filename(filename: str) -> str:
    stem = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    cleaned = "".join(c if c.isalnum() or c in "-_" else "-" for c in stem.lower()).strip("-_")
    if not cleaned:
        cleaned = "upload"
    safe_name(cleaned)
    return cleaned


__all__ = [
    "DEFAULT_MAX_ROUNDS",
    "DEFAULT_SCORING_MATCHER",
    "PREVIEW_ROWS",
    "SETTABLE_SCALARS",
    "DraftCampaign",
    "EditDraftPatch",
    "NodeOutputEdit",
    "OptimizationOverrides",
    "dataset_source_of",
    "default_slug_from_filename",
    "merge_pipeline_overlay",
    "new_draft",
]
