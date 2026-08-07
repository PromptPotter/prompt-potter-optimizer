"""Server-held working state of a check-in-lifecycle campaign, persisted under the owning
campaign's ``checkin/`` — no in-memory registry, which a restart would silently lose."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from pydantic import Field

from promptpotter.application.campaign_config import (
    MechanismConfig,
    OptimizationConfig,
    PromptBlockCatalogue,
)
from promptpotter.connectors import DEFAULT_CONNECTOR
from promptpotter.domain.identity import TenantId, safe_name
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.pipeline_parsing import merge_node_blocks
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.clock import utcnow_iso

if TYPE_CHECKING:
    from promptpotter.connectors.protocol import Connector

DEFAULT_SCORING_COMPOSITE = "exact_match"
"""Only universally-applicable scorer for ``(query, ground_truth)`` shape."""

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
    prompt_block_catalogue: PromptBlockCatalogue = Field(
        # The config field's own default — the draft never re-spells it.
        OptimizationConfig.model_fields["prompt_block_catalogue"].default,
        description="How the reusable prompt building-block library reaches the "
        "optimizer: ``guidance`` (suggest blocks, it may still invent), "
        "``restrict`` (blocks only), ``off`` (no library).",
    )
    mechanisms: MechanismConfig = Field(
        default_factory=MechanismConfig,
        description="Pluggable orchestration mechanism toggles "
        "(sorting/selection + early-abort groups).",
    )


def _default_optimization_overrides() -> dict[str, Any]:
    return OptimizationOverrides().model_dump(mode="json")


def closed_answer_format(labels: tuple[str, ...]) -> str:
    """The label set is a deterministic fact (the target column's distinct values), not the
    LLM's to transcribe — it reliably drops labels from a many-way set."""
    return "Choose exactly one of these labels: " + " | ".join(labels)


@dataclass(frozen=True, slots=True)
class DraftCampaign:
    """Frozen — mutate via :meth:`patch`. The ``slug`` freezes at commit: from the moment
    ``datasets/{slug}/`` is written, only a *Replace*'s ``-vN`` suffix may change it."""

    draft_id: str
    tenant_id: TenantId
    slug: str
    n_samples: int
    sample_preview: tuple[dict[str, str], ...]
    connector: str
    scoring_composite: str
    raw_task_description: str
    pipeline_overlay: dict[str, Any]
    created_at: str
    updated_at: str
    # Uploaded column headers + the input/target column mapping. The mapping
    # is resolved on the draft (auto-confirmed for literal `query` /
    # `ground_truth` headers; operator-confirmed otherwise) and gated at mint
    # — ingest no longer requires literally-named columns.
    headers: tuple[str, ...] = ()
    column_query: str = ""
    column_ground_truth: str = ""
    # Per-column closed label set, computed ONCE over the full upload at ingest
    # (``closed_label_set`` in ``csv_ingest``) and keyed by header name — only
    # columns that read as a fixed taxonomy carry an entry. The target column's
    # entry is the campaign's *answer space* (see :meth:`answer_space`); the
    # origin gate uses it to require the labels be enumerated in the prompt, and
    # the check-in proposer is handed it so it stops collapsing the taxonomy to
    # "(e.g., X)". Empty for an open-ended target (numeric/free-text, where
    # distinct ≈ n_rows).
    column_label_sets: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Per-field origin-readiness provenance, keyed by dotted field name
    # (`column.query`, `column.ground_truth`, `task_description`). The
    # deterministic checklist gates on it; no field reaches mint while UNSET or
    # PROPOSED. Only the three genuinely-stated fields carry an entry — config
    # is not gated.
    field_provenance: dict[str, Provenance] = field(default_factory=dict)
    source_file: str = ""
    # The campaign's origin prompt — a ``PromptTemplate.prompt_field_dict()``
    # shape (the six string fields + optional ``few_shot_examples``). This is the
    # origin OSP's prompt fields: seeded by the check-in node's decomposition half
    # (``CheckinOutput`` carries it), or from an authored dataset's prompt on the
    # ``draft_from_dataset`` path; operator-editable before commit, and written
    # verbatim to ``prompts/default.yaml`` at mint. Empty until the check-in fills it.
    origin_prompt_fields: dict[str, Any] = field(default_factory=dict)
    # The check-in's decomposition half also authors the decomposed task context —
    # the 7-field domain framing (:class:`CheckinTaskContext`) that every optimizer
    # layer (L1/L1_CRITIQUE/L2/L3) reads via the ``task_context`` injection. It
    # rides the draft to commit, lands in ``{slug}/task_context.yaml``, and the run reads it
    # instead of paying a second LLM call to re-decompose at run-start. Empty until check-in.
    decomposed_task_context: dict[str, Any] = field(default_factory=dict)
    # The chosen active pipeline (``pipeline.yaml::pipelines.default``) — e.g. the
    # full ``cache_lookup → … → token_matching`` vs a bare ``llm_only``. Empty =
    # fall back to the connector's ``default_pipeline``. Carried so reusing an
    # existing dataset PRESERVES its pipeline through display + commit instead of
    # silently resetting to the connector default (the llm_only-on-reuse bug).
    pipeline_steps: list[str] = field(default_factory=list)
    # The campaign-config knobs, as one :class:`OptimizationOverrides`-shaped dict
    # (``max_rounds`` / ``mechanisms``). Seeded with the stock defaults,
    # operator-editable in the new-campaign form, and materialized into the
    # committed ``campaign.json::optimization``. One object so a new knob is one
    # field on :class:`OptimizationOverrides`, not a fresh thread through every surface.
    optimization_overrides: dict[str, Any] = field(default_factory=_default_optimization_overrides)
    # The target library a ``candidate_source`` pipeline ranks each query against —
    # the "4th required input" the operator drops in the ingest UI when a node type
    # raises the dependency (see ``PipelineDependency``). Empty until dropped; on
    # commit it materializes to ``{slug}/candidate_library.txt`` and the run unions
    # it into the session's term index. NOT gated at the readiness checklist — a
    # surfaced-and-droppable dependency, not a hard mint block (the answers already
    # in the data are a degenerate-but-runnable pool).
    candidate_library: tuple[str, ...] = ()
    # The origin's sanctioned inner-optimizer model set (`CampaignConfig.allowed_models`).
    # Ticked in the check-in pipeline setup; materializes into `campaign.json::config.
    # allowed_models` (top-level, a CampaignConfig field — NOT under `optimization`).
    # Empty = nothing sanctioned = restrictive default (any human model steer taints).
    # The allow-list a human fork may steer the inner model to without a babysit warning.
    allowed_models: tuple[str, ...] = ()
    # Set when this draft was opened by reusing a prior origin (the picker's
    # "Reuse an origin" path) — the chosen origin's content id. When non-empty,
    # ``prepare_checkin_run`` passes ``origin_prompt_fields`` as the
    # ``origin_override`` seed, so C0 resolves via the ``seed`` branch and stamps
    # the ``campaign_origin`` lineage ("minted from a chosen prior origin"). Empty
    # for a fresh upload / plain dataset open (lineage stays ``origin``).
    reused_origin_id: str = ""

    def to_wire(self) -> dict[str, Any]:
        """``tenant_id`` is omitted on purpose — clients learn it from the session cookie
        (ADR-0002 no-drift gate #3: no per-record ``tenant_id`` on the wire)."""
        return {
            "draft_id": self.draft_id,
            "slug": self.slug,
            # Project to the declared wire contract ({query, ground_truth}) using
            # the resolved column mapping. The internal rows stay keyed by raw
            # header names (the resolver reads those as sample_rows); only the
            # wire boundary normalizes. Blank until the columns are confirmed.
            "sample_preview": [
                {
                    "query": row.get(self.column_query, ""),
                    "ground_truth": row.get(self.column_ground_truth, ""),
                }
                for row in self.sample_preview
            ],
            "n_samples": self.n_samples,
            "connector": self.connector,
            "scoring_composite": self.scoring_composite,
            "optimization_overrides": dict(self.optimization_overrides),
            "raw_task_description": self.raw_task_description,
            "pipeline_overlay": dict(self.pipeline_overlay),
            "headers": list(self.headers),
            "column_query": self.column_query,
            "column_ground_truth": self.column_ground_truth,
            "field_provenance": {
                field_name: prov.value for field_name, prov in self.field_provenance.items()
            },
            "origin_prompt_fields": dict(self.origin_prompt_fields),
            # Count, not the full list — a library can run to tens of thousands of
            # entries; the UI needs only "is it fulfilled, and how big". The
            # per-dependency ``fulfilled`` flag rides ``optimizer_locks``' sibling
            # ``dependencies`` block (added at the wire boundary, which has the
            # connector's node types).
            "candidate_library_size": len(self.candidate_library),
            "allowed_models": list(self.allowed_models),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_disk(self) -> dict[str, Any]:
        """Lossless, unlike :meth:`to_wire`: every field the readiness gate runs over survives.
        ``draft_id`` / ``tenant_id`` are omitted — the campaign dir the check-in lives under IS them."""
        return {
            "slug": self.slug,
            "n_samples": self.n_samples,
            "sample_preview": [dict(row) for row in self.sample_preview],
            "connector": self.connector,
            "scoring_composite": self.scoring_composite,
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
            "allowed_models": list(self.allowed_models),
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
            scoring_composite=data["scoring_composite"],
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
            optimization_overrides=dict(data.get("optimization_overrides", {})),
            candidate_library=tuple(data.get("candidate_library", ())),
            allowed_models=tuple(data.get("allowed_models", ())),
            reused_origin_id=data.get("reused_origin_id", ""),
        )

    def answer_space(self) -> tuple[str, ...] | None:
        """The campaign's enumerable answer space, computed over the full upload at ingest — the
        gate requires it land in the prompt, and the proposer is handed it to enumerate."""
        if not self.column_ground_truth:
            return None
        return self.column_label_sets.get(self.column_ground_truth)

    def committed_prompt_fields(self) -> dict[str, Any]:
        """The one encoding of "the prompt this draft commits", shared with the answer-space gate so
        they cannot drift. APPEND, never overwrite: the resolver's extraction instruction survives."""
        fields = (
            dict(self.origin_prompt_fields)
            if self.origin_prompt_fields
            else {"instruction": self.raw_task_description}
        )
        labels = self.answer_space()
        fmt = str(fields.get("answer_format", "")).strip()
        if labels and fmt:
            enumeration = closed_answer_format(labels)
            if enumeration not in fmt:
                fields["answer_format"] = f"{fmt}\n{enumeration}"
        return fields

    def patch(self, **changes: Any) -> DraftCampaign:
        return replace(self, updated_at=utcnow_iso(), **changes)

    def confirm_columns(
        self, *, query_col: str | None = None, ground_truth_col: str | None = None
    ) -> DraftCampaign:
        """Membership of each header in :attr:`headers` is the caller's wire-validation concern
        (422); this only records the confirmed value."""
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
        """The single mutation route the origin-resolution loop drives. ``values`` are draft
        attribute kwargs; ``provenance`` merges field-id → tag onto :attr:`field_provenance`."""
        merged = dict(self.field_provenance)
        if provenance:
            merged.update(provenance)
        return self.patch(field_provenance=merged, **(values or {}))


def merge_pipeline_overlay(draft: DraftCampaign, connector: Connector) -> dict[str, Any]:
    """The one place the draft's resolved node config is computed — shared by the committed
    ``pipeline.yaml`` builder, the wire optimizer-locks block and the origin model gate."""
    return merge_node_blocks(dict(connector.default_node_config), draft.pipeline_overlay or {})


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
    """``create_checkin_campaign`` re-keys ``draft_id`` to the new ``campaign_id``; the transient
    one minted here is never stored. ``raw_task_description`` has no default, so it lands UNSET."""
    now = utcnow_iso()
    column_query, column_ground_truth, provenance = _seed_provenance(headers)
    return DraftCampaign(
        draft_id=_mint_draft_id(),
        tenant_id=tenant_id,
        slug=slug,
        n_samples=n_samples,
        sample_preview=tuple(dict(row) for row in sample_preview[:PREVIEW_ROWS]),
        connector=DEFAULT_CONNECTOR,
        scoring_composite=DEFAULT_SCORING_COMPOSITE,
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
    """Deterministic auto-confirm: a header literally named ``query`` / ``ground_truth`` confirms
    that column, anything else stays UNSET for the operator — no fuzzy guessing in the gate."""
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


# A draft built from an existing on-disk dataset (demo / benchmark / owned tenant
# dataset) records ``source_file = "dataset:{slug}"`` in ``draft_from_dataset``;
# a fresh CSV upload records the raw filename. The prefix is therefore the
# "derived-from-existing vs new-upload" discriminator the commit path branches on
# — no separate flag field. A derived draft mints against its canonical dataset
# instead of cloning a new ``datasets/{slug}/`` folder.
_DERIVED_PREFIX = "dataset:"


def dataset_source_of(source_file: str) -> str | None:
    """Single source of truth for the ``dataset:`` prefix parse; ``None`` for a fresh CSV upload."""
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
    safe_name(cleaned)  # raises if still invalid (shouldn't happen)
    return cleaned


__all__ = [
    "DEFAULT_MAX_ROUNDS",
    "DEFAULT_SCORING_COMPOSITE",
    "PREVIEW_ROWS",
    "DraftCampaign",
    "OptimizationOverrides",
    "dataset_source_of",
    "default_slug_from_filename",
    "merge_pipeline_overlay",
    "new_draft",
]
