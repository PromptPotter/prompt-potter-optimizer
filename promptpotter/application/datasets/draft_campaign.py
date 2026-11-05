from __future__ import annotations

import uuid
from dataclasses import dataclass, field, fields, replace
from typing import TYPE_CHECKING, Any

from promptpotter import connectors
from promptpotter.application.scoring.formula.compiler import (
    DIALS_KEY,
    parse_dials,
    spell_dials,
    split_scoring_block,
)
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.checkin_draft import DatasetBasis, DraftBasis, DraftFile, UploadBasis
from promptpotter.domain.draft_edit import OptimizationOverrides
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.pipeline_parsing import merge_node_blocks
from promptpotter.domain.pipeline_schema import DEFAULT_PIPELINE, NodeSearchNarrowing
from promptpotter.domain.scoring import anchored_criterion_dials
from promptpotter.infrastructure.store.stores import Stores, owned_draft_data
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.identity import TenantId, safe_name

if TYPE_CHECKING:
    from collections.abc import Mapping

    from promptpotter.connectors.protocol import Connector
    from promptpotter.domain.cell_terms import CellTerm
    from promptpotter.domain.pipeline_schema import PipelineSchema

DEFAULT_SCORING_MATCHER = "label_match"
"""Only universally-applicable matcher for ``(query, ground_truth)`` shape."""

PREVIEW_ROWS = 10
"""Sample-preview head size returned alongside every mutation response."""


def _default_optimization_overrides() -> dict[str, Any]:
    return OptimizationOverrides().model_dump(mode="json")


def closed_answer_format(labels: tuple[str, ...]) -> str:
    return "Choose exactly one of these labels: " + " | ".join(labels)


@dataclass(frozen=True, slots=True)
class DraftCampaign:
    draft_id: str
    tenant_id: TenantId
    # Another user's draft is not found, as their campaign is.
    owner_user_id: str
    slug: str
    n_samples: int
    sample_preview: tuple[dict[str, str], ...]
    basis: DraftBasis
    scoring_matcher: str
    raw_task_description: str | None
    pipeline_overlay: dict[str, Any]
    created_at: str
    updated_at: str
    headers: tuple[str, ...] = ()
    column_query: str | None = None
    column_ground_truth: str | None = None
    column_label_sets: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Per header, the first row (1-based, file order) whose cell is blank: that column cuts no bank.
    first_blank_row: dict[str, int] = field(default_factory=dict)
    field_provenance: dict[str, Provenance] = field(default_factory=dict)
    source_file: str = ""
    origin_prompts: dict[str, dict[str, str]] = field(default_factory=dict)
    decomposed_task_context: dict[str, Any] = field(default_factory=dict)
    pipeline_steps: list[str] = field(default_factory=list)
    optimization_overrides: dict[str, Any] = field(default_factory=_default_optimization_overrides)
    candidate_library: tuple[str, ...] = ()
    # Non-empty routes the check-in's Start through the ``origin_override`` seed.
    reused_origin_id: str = ""
    scoring_dials: str = ""
    # A resolver turn has run on it: an edit keeps it, so a reopen reviews, never re-resolves.
    resolver_ran: bool = False

    def to_disk(self) -> DraftFile:
        return DraftFile.model_validate(
            {name: getattr(self, name) for name in DraftFile.model_fields}
        )

    @classmethod
    def from_disk(cls, file: DraftFile, *, draft_id: str, tenant_id: TenantId) -> DraftCampaign:
        return cls(draft_id=draft_id, tenant_id=tenant_id, **dict(file))

    @property
    def dataset_name(self) -> str:
        """The dataset it runs on: the one it was cut from, or an upload's, minted under its slug."""
        return self.basis.dataset if isinstance(self.basis, DatasetBasis) else self.slug

    def resolution_pending(self) -> bool:
        """The task is on file and no resolver turn ever set it up: a reopen resolves, while an
        origin's or the operator's authored prompts are never re-proposed over."""
        return (
            self.raw_task_description is not None
            and not self.resolver_ran
            and not self.authored_prompts()
        )

    def confirmed_task(self) -> str:
        """The task a Start commits: the gate refuses every Start before one is confirmed."""
        assert self.raw_task_description is not None, "a Start passed a gate holding no task"
        return self.raw_task_description

    def answer_space(self) -> tuple[str, ...] | None:
        if self.column_ground_truth is None:
            return None
        return self.column_label_sets.get(self.column_ground_truth)

    def authored_prompts(self) -> bool:
        # Wiped strings count as blank, so the fallback is not keyed on the map being empty.
        return any(
            text.strip() for fields in self.origin_prompts.values() for text in fields.values()
        )

    def committed_prompts(self, chain: PipelineSchema) -> dict[str, dict[str, str]]:
        """Over the *chain* `pipeline_resolve.draft_pipeline` runs. The raw task and the label
        enumeration seed a field only on a chain of ONE prompt node."""
        prompt_nodes = chain.prompt_nodes()
        alone = len(prompt_nodes) == 1
        return {
            node.name: self._committed_fields(node.name, node.prompt_fields if alone else ())
            for node in prompt_nodes
        }

    def _committed_fields(self, step: str, seeded: tuple[str, ...]) -> dict[str, str]:
        authored = self.origin_prompts.get(step, {})
        fields = dict(authored)
        if "instruction" in seeded and not any(text.strip() for text in authored.values()):
            fields = {"instruction": self.confirmed_task()}
        # Appended even when answer_format is blank: the optimizer prompts promise the labels.
        labels = self.answer_space()
        if labels and "answer_format" in seeded:
            enumeration = closed_answer_format(labels)
            fmt = fields.get("answer_format", "").strip()
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


assert {f.name for f in fields(DraftCampaign)} - {"draft_id", "tenant_id"} == set(
    DraftFile.model_fields
), "a DraftCampaign field draft.json does not hold, or one it holds no field reads"


def merge_pipeline_overlay(draft: DraftCampaign, connector: Connector) -> dict[str, Any]:
    """No narrowing: ``optimizer_narrowing`` holds it."""
    declared = {
        node: {k: v for k, v in block.items() if k != "optimizer"}
        for node, block in (draft.pipeline_overlay or {}).items()
        if isinstance(block, dict)
    }
    return merge_node_blocks(dict(connector.default_node_config), declared)


def owned_draft(stores: Stores, draft_id: str) -> DraftCampaign:
    data = owned_draft_data(stores, draft_id)
    return DraftCampaign.from_disk(data, draft_id=draft_id, tenant_id=stores.identity.tenant_id)


def owned_drafts(stores: Stores) -> list[DraftCampaign]:
    user = str(stores.identity.user_id)
    return [
        DraftCampaign.from_disk(data, draft_id=draft_id, tenant_id=stores.identity.tenant_id)
        for draft_id in stores.drafts.draft_ids()
        if (data := stores.drafts.read_draft(draft_id)) is not None and data.owner_user_id == user
    ]


def committed_pipeline_json(draft: DraftCampaign, upload: UploadBasis) -> dict[str, Any]:
    """An upload's ``dataset.yaml::pipeline``, which its Start writes."""
    connector = connectors.CONNECTORS.get(upload.connector)
    pipeline: dict[str, Any] = {
        "name": draft.slug,
        "backend_type": connector.name,
        "backend_name": connector.name,
        "nodes": merge_pipeline_overlay(draft, connector),
    }
    if steps := draft.pipeline_steps or list(connector.default_pipeline):
        pipeline["pipelines"] = {DEFAULT_PIPELINE: steps}
    if connector.available_models:
        pipeline["available_models"] = list(connector.available_models)
    return pipeline


def draft_scoring_block(draft: DraftCampaign) -> str | dict[str, str]:
    per_sample = f"{draft.scoring_matcher}(predicted, ground_truth)"
    if not draft.scoring_dials:
        return per_sample
    return {"per_sample": per_sample, DIALS_KEY: draft.scoring_dials}


def scoring_choice(
    scoring: str | dict[str, str] | None, terms: Mapping[str, CellTerm]
) -> tuple[str, str]:
    """The matcher and dials a draft holds of a scoring block; a pinned formula's anchors are lost."""
    spec = split_scoring_block(scoring, judge_instrument=None)
    matcher = (spec.per_sample or "").split("(", 1)[0].strip() or DEFAULT_SCORING_MATCHER
    declared = parse_dials(scoring.get(DIALS_KEY, ""), terms) if isinstance(scoring, dict) else {}
    pinned = anchored_criterion_dials(spec.per_cell) if spec.per_cell else None
    weights = declared or {name: d.weight for name, d in (pinned or {}).items()}
    return matcher, spell_dials(weights, terms)


def default_campaign_config(draft: DraftCampaign, upload: UploadBasis) -> CampaignConfig:
    connector = connectors.CONNECTORS.get(upload.connector)
    overrides = draft.optimization_overrides
    optimization: dict[str, Any] = {
        "budget": overrides["budget"],
        "halt_at_accuracy": overrides["halt_at_accuracy"],
    }
    optimization.update(dict(connector.default_optimization))
    optimization["optimizer"] = overrides["optimizer"]
    optimization["nodes"] = dict(overrides["nodes"])
    _, narrowing = split_overlay(draft.pipeline_overlay)
    return CampaignConfig.model_validate(
        {
            "scoring": draft_scoring_block(draft),
            "optimization": optimization,
            "optimizer_narrowing": {n: v.model_dump() for n, v in narrowing.items()},
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
                prompt_fields_held=optimizer.get("prompt_fields_held", []),
                param_allowed_values=optimizer.get("param_allowed_values", {}),
            )
    return overrides, narrowing


def new_draft(
    *,
    tenant_id: TenantId,
    owner_user_id: str,
    slug: str,
    basis: DraftBasis,
    n_samples: int,
    sample_preview: list[dict[str, str]],
    headers: list[str],
    source_file: str = "",
    column_label_sets: dict[str, tuple[str, ...]] | None = None,
    first_blank_row: dict[str, int] | None = None,
) -> DraftCampaign:
    now = utcnow_iso()
    column_query, column_ground_truth, provenance = _seed_provenance(headers)
    return DraftCampaign(
        draft_id=mint_draft_id(),
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        slug=slug,
        n_samples=n_samples,
        sample_preview=tuple(dict(row) for row in sample_preview[:PREVIEW_ROWS]),
        basis=basis,
        scoring_matcher=DEFAULT_SCORING_MATCHER,
        raw_task_description=None,
        pipeline_overlay={},
        headers=tuple(headers),
        column_query=column_query,
        column_ground_truth=column_ground_truth,
        column_label_sets=dict(column_label_sets or {}),
        first_blank_row=dict(first_blank_row or {}),
        field_provenance=provenance,
        created_at=now,
        updated_at=now,
        source_file=source_file,
    )


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
    cleaned = "".join(c if c.isalnum() or c in "-_" else "-" for c in stem.lower()).strip("-_")
    if not cleaned:
        cleaned = "upload"
    safe_name(cleaned)
    return cleaned
