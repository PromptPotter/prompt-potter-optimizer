"""Server-held working state of a check-in-lifecycle campaign, persisted under the owning
campaign's ``checkin/`` — no in-memory registry, which a restart would silently lose."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from pydantic import Field

from promptpotter import connectors
from promptpotter.application.campaign_config import (
    CampaignConfig,
    MechanismConfig,
    OptimizationConfig,
    PromptBlockCatalogue,
    load_campaign_config,
)
from promptpotter.connectors import DEFAULT_CONNECTOR
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.pipeline_parsing import merge_node_blocks
from promptpotter.domain.pipeline_schema import NodeSearchNarrowing
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.identity import TenantId, safe_name

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
        description="How the reusable prompt block library reaches the "
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
    # Auto-confirmed for literal `query` / `ground_truth` headers, operator-confirmed
    # otherwise, and gated at mint — ingest does not require literally-named columns.
    headers: tuple[str, ...] = ()
    column_query: str = ""
    column_ground_truth: str = ""
    # Computed ONCE over the full upload at ingest (``csv_ingest::closed_label_set``), keyed
    # by header — only columns reading as a fixed taxonomy carry an entry. The target
    # column's entry is the campaign's *answer space* (:meth:`answer_space`). Empty for an
    # open-ended target, where distinct ≈ n_rows.
    column_label_sets: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Keyed by dotted field name (`column.query`, `column.ground_truth`,
    # `task_description`) — the three genuinely-stated fields; config is not gated. No
    # field reaches mint while UNSET or PROPOSED.
    field_provenance: dict[str, Provenance] = field(default_factory=dict)
    source_file: str = ""
    # ``PromptTemplate.prompt_field_dict()`` shape. Seeded by the check-in node's
    # decomposition half or an authored dataset's prompt, operator-editable before commit,
    # written verbatim to ``prompts/default.yaml`` at mint.
    origin_prompt_fields: dict[str, Any] = field(default_factory=dict)
    # The check-in's other decomposition half — the 7-field domain framing every optimizer
    # layer reads via the ``task_context`` injection. Lands in ``{slug}/task_context.yaml``,
    # so the run does not pay a second LLM call to re-decompose at start.
    decomposed_task_context: dict[str, Any] = field(default_factory=dict)
    # ``pipeline.yaml::pipelines.default``; empty falls back to the connector's
    # ``default_pipeline``. Carried so reusing a dataset PRESERVES its pipeline through
    # display + commit instead of resetting to the connector default.
    pipeline_steps: list[str] = field(default_factory=list)
    # One :class:`OptimizationOverrides`-shaped dict, materialized into the committed
    # ``campaign.json::optimization`` — so a new knob is one field there, not a fresh
    # thread through every surface.
    optimization_overrides: dict[str, Any] = field(default_factory=_default_optimization_overrides)
    # The target library a ``candidate_source`` pipeline ranks each query against, dropped in
    # the ingest UI when a node type raises the dependency (``PipelineDependency``);
    # materializes to ``{slug}/candidate_library.txt`` and unions into the session's term
    # index. NOT gated — the answers already in the data are a degenerate-but-runnable pool.
    candidate_library: tuple[str, ...] = ()
    # The backend's OWN ``GET /pipeline::nodes``, captured once when this draft was created.
    # Without it a check-in cannot know which params are search AXES: the connector seed carries
    # narrowing (`param_allowed_values`) but never `param_keys`, so every axis derived as
    # unmovable and the setup screen drew a lock the engine does not enforce — while the run,
    # reading the live schema, had the axis open. Captured rather than fetched per response
    # because it is a material fact about THIS check-in, and one an operator can read back off
    # disk. Empty = never fetched or the backend was unreachable, which is why the wire carries
    # `backend_reachable` beside the schema rather than letting empty mean "locked".
    backend_nodes: dict[str, Any] = field(default_factory=dict)
    # The chosen origin's content id when this draft reused a prior origin. Non-empty routes
    # ``prepare_checkin_run`` through the ``origin_override`` seed, so C0 resolves via the
    # ``seed`` branch and stamps the ``campaign_origin`` lineage.
    reused_origin_id: str = ""

    def to_wire(self) -> dict[str, Any]:
        """``tenant_id`` is omitted on purpose — clients learn it from the session cookie
        (ADR-0002 no-drift gate #3: no per-record ``tenant_id`` on the wire)."""
        return {
            "draft_id": self.draft_id,
            "slug": self.slug,
            # Raw header-keyed rows, same as ``to_disk`` and the resolver's ``sample_rows``.
            # This used to project through ``column_query``/``column_ground_truth``, which
            # are "" until the operator confirms them — so every row served blank on any CSV
            # whose headers are not literally query/ground_truth, and the preview an operator
            # would read to CHOOSE the mapping was erased by the mapping being unchosen. The
            # browser has ``headers`` beside this and renders the columns itself.
            "sample_preview": [dict(row) for row in self.sample_preview],
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
            # Count, not the list — a library runs to tens of thousands of entries and the UI
            # needs only "is it fulfilled, and how big". The per-dependency ``fulfilled`` flag
            # rides the wire's own ``dependencies`` block.
            "candidate_library_size": len(self.candidate_library),
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
            backend_nodes=dict(data.get("backend_nodes", {})),
            reused_origin_id=data.get("reused_origin_id", ""),
        )

    def answer_space(self) -> tuple[str, ...] | None:
        """The campaign's enumerable answer space, computed over the full upload at ingest —
        ``committed_prompt_fields`` lands it in the prompt deterministically."""
        if not self.column_ground_truth:
            return None
        return self.column_label_sets.get(self.column_ground_truth)

    def committed_prompt_fields(self) -> dict[str, Any]:
        """The one encoding of "the prompt this draft commits". Any field may be blank — the
        optimizer evolves them — so only an ENTIRELY blank prompt (wiped strings included, hence
        not keyed on the dict) falls back to the task description."""
        authored = any(str(value).strip() for value in self.origin_prompt_fields.values())
        fields = (
            dict(self.origin_prompt_fields)
            if authored
            else {"instruction": self.raw_task_description}
        )
        # Appended even when answer_format is blank: the optimizer prompts forbid the
        # LLMs from re-typing labels on the promise that the system supplies them.
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
    """What this check-in WRITES — the committed ``pipeline.yaml`` builder, the wire
    optimizer-locks block and the origin model gate. Deliberately does NOT fold in
    :attr:`DraftCampaign.backend_nodes`: the committed file is an OVERLAY on a schema the
    backend still owns at run time, and snapshotting the backend's ``param_keys`` into it would
    turn ``split_overlay`` into a narrowing that pins the search space to whatever the service
    happened to declare on the day of the check-in."""
    return merge_node_blocks(dict(connector.default_node_config), draft.pipeline_overlay or {})


def resolved_node_schema(draft: DraftCampaign, connector: Connector) -> dict[str, Any]:
    """What this check-in SHOWS — the same three layers the runner resolves, in the same order:
    the backend declares the node, the connector narrows it, the operator overrides.

    The twin of :func:`merge_pipeline_overlay` and the reason they are two functions. Display
    needs the backend layer or it cannot answer "may the optimizer move this?" — that answer
    lives in ``optimizer.param_keys``, which only the backend declares. Reading the overlay
    alone derived every axis as unmovable, so the setup screen drew locks the engine never
    enforced. Persisting that same merge would be the opposite error, which is why the write
    path above stops one layer short."""
    return merge_node_blocks(dict(draft.backend_nodes), merge_pipeline_overlay(draft, connector))


def load_checkin_draft(stores: Stores, campaign_id: str) -> DraftCampaign | None:
    """Rehydrate the durable check-in draft, or ``None``. The campaign dir IS the identity, so
    ``draft_id`` / ``tenant_id`` come from the store's tenant scope — a cross-tenant id isn't found.

    Beside the draft rather than beside the launcher: the resolver reads a draft to answer for a
    check-in campaign, and it cannot import a module that starts runs."""
    data = stores.checkin.read_draft(campaign_id)
    if data is None:
        return None
    return DraftCampaign.from_disk(data, draft_id=campaign_id, tenant_id=stores.identity.tenant_id)


def draft_pipeline_json(draft: DraftCampaign, nodes: dict[str, Any]) -> dict[str, Any]:
    """A draft as the SAME raw shape ``datasets/{slug}/pipeline.yaml`` holds, so one parser reads
    both and a check-in is not a second kind of pipeline.

    *nodes* is the caller's choice of layer depth, and the two callers deliberately differ — see
    the two wrappers below. Passing it in rather than branching inside is what keeps "what we
    write" and "what we draw" from collapsing into one flag nobody can read.
    ``pipelines.default`` overrides the pipeline order."""
    pipeline: dict[str, Any] = {
        "name": draft.slug,
        "backend_type": draft.connector,
        "backend_name": draft.connector,
    }
    connector = connectors.get(draft.connector)
    steps = draft.pipeline_steps or list(connector.default_pipeline)
    if steps:
        pipeline["pipelines"] = {"default": list(steps)}
    # The model MENU, from the one function that feeds both the committed file and the
    # pre-commit render — so a check-in dataset gets the same catalogue a hand-authored
    # benchmark declares, instead of the empty list that leaves its model list with nothing
    # to offer. The ADMIN's catalogue and nothing else: a model the operator typed rides
    # `nodes.{n}.optimizer.param_allowed_values.model`, which is what BOUNDS the run
    # (`PipelineSchema.model_options` prefers it), and folding it in here would erase the one
    # difference that lets a surface say which values are theirs. Absent when the connector
    # declares none: no menu is a real answer.
    if connector.available_models:
        pipeline["available_models"] = list(connector.available_models)

    if nodes:
        pipeline["nodes"] = nodes
    return pipeline


def committed_pipeline_json(draft: DraftCampaign) -> dict[str, Any]:
    """What gets WRITTEN as ``datasets/{slug}/pipeline.yaml`` — the overlay depth, so the backend
    still owns the schema at run time."""
    return draft_pipeline_json(
        draft, merge_pipeline_overlay(draft, connectors.get(draft.connector))
    )


def rendered_pipeline_json(draft: DraftCampaign) -> dict[str, Any]:
    """What the RESOLVER reads for a check-in campaign — the backend's declaration underneath the
    overlay, because ``optimizer.param_keys`` lives nowhere else and a surface without it draws
    locks the run never enforces."""
    return draft_pipeline_json(draft, resolved_node_schema(draft, connectors.get(draft.connector)))


def declared_pipeline_json(draft: DraftCampaign) -> dict[str, Any]:
    """The same pipeline with the OPERATOR's layer left off — what was on offer before this draft
    narrowed anything.

    The resolver needs both: ``narrow`` REPLACES ``param_allowed_values``, so a value the operator
    unticked is gone from the narrowed schema, and a menu built from that could never offer it
    back. Union the two and unticking stays reversible (``pipeline_resolve::_enum_menu``). Reading
    it off :func:`rendered_pipeline_json` cannot work — that one has already folded the narrowing
    in, which is exactly the layer this omits."""
    connector = connectors.get(draft.connector)
    return draft_pipeline_json(
        draft, merge_node_blocks(dict(draft.backend_nodes), dict(connector.default_node_config))
    )


def default_campaign_config(draft: DraftCampaign) -> CampaignConfig:
    """The campaign config a draft mints WITHOUT its node overlay — the floor the split below
    layers onto, and the same one ``_campaign_config_for_launch`` starts from at Start."""
    connector = connectors.get(draft.connector)
    overrides = draft.optimization_overrides
    optimization: dict[str, Any] = {"max_rounds": overrides["max_rounds"]}
    optimization.update(dict(connector.default_optimization))
    optimization["prompt_block_catalogue"] = overrides["prompt_block_catalogue"]
    optimization["mechanisms"] = dict(overrides["mechanisms"])
    return load_campaign_config(
        {
            "dataset_name": draft.slug,
            "scoring": f"{draft.scoring_composite}(predicted, ground_truth)",
            "exclude_nodes": list(connector.default_exclude_nodes),
            "optimization": optimization,
        }
    )


def draft_campaign_config(draft: DraftCampaign) -> CampaignConfig:
    """What this draft WOULD freeze at Start — the floor plus its own node overlay, split into the
    two flat fields a campaign carries.

    The resolver reads this so the answer an operator sees while authoring IS the answer their
    campaign runs; ``mint_and_start._campaign_config_for_launch`` performs the same merge onto the
    committed snapshot. The two agreeing is the point — a setup screen showing something the mint
    will not reproduce is the defect this whole seam exists to close."""
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
    """The draft's nested overlay as the two flat fields a ``CampaignConfig`` carries.

    A reused dataset's mint applies this split onto the per-campaign snapshot, so the shared,
    immutable dataset is never mutated; a fresh upload folds the whole overlay into its own file.
    The RESOLVER applies it too, which is what makes the answer an operator reads while authoring
    the same one their campaign runs — the split is the campaign layer, before it is frozen."""
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


# ``source_file`` carries `dataset:{slug}` for a draft built off an existing on-disk dataset
# and the raw filename for a fresh upload, so this prefix IS the discriminator commit branches
# on — no separate flag. A derived draft mints against its canonical dataset rather than
# cloning a new ``datasets/{slug}/``.
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
