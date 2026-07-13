"""Server-held ``DraftCampaign`` — the mutable working state of a check-in-lifecycle campaign.

Wire shape pinned in ``docs/specs/m12-api-openapi.yaml::DraftCampaign``;
prose at ``docs/specs/roadmap.md § Draft-campaign object``.

A draft is the mutable target of both surfaces (chat tool-calls + the
panel "Apply" button). The Origin-shaped subset (slug, task_description,
pipeline_overlay) materializes into the four content-hashed files on
commit; the campaign-config subset (connector, scoring_composite,
max_rounds) materializes into the sibling ``campaign.json``.

Storage: a draft is the working state of a campaign in the ``checkin``
lifecycle. :class:`~promptpotter.infrastructure.store.CheckinDraftStore`
persists the lossless dict (:meth:`DraftCampaign.to_disk`) at
``campaigns/{campaign_id}/checkin/draft.json`` and the sample bank at
``checkin/cache.json``. The draft's ``draft_id`` IS the owning ``campaign_id``
(re-keyed at :func:`~promptpotter.application.jobs.launcher.create_checkin_campaign`),
so a multi-step ingest survives a restart and is resumable like any other
campaign — there is no in-memory registry. :func:`new_draft` builds the fresh
draft the first ingest action mints a check-in campaign from.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from promptpotter.application.config import MechanismConfig
from promptpotter.connectors import DEFAULT_CONNECTOR
from promptpotter.domain.identity import TenantId, safe_name
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.pipeline_parsing import merge_node_blocks
from promptpotter.shared.clock import utcnow_iso

if TYPE_CHECKING:
    from promptpotter.connectors.protocol import Connector

DEFAULT_SCORING_COMPOSITE = "exact_match"
"""Only universally-applicable scorer for ``(query, ground_truth)`` shape."""

DEFAULT_MAX_ROUNDS = 5
"""Matches the M10 prompt-iteration framework default."""

PREVIEW_ROWS = 10
"""Sample-preview head size returned alongside every mutation response."""


class OptimizationOverrides(BaseModel):
    """The campaign-config knobs a new-campaign draft carries, as one validated
    object — collapses what were three hand-threaded fields (``max_rounds`` /
    ``lock_model`` / ``mechanisms``) so a new knob is one field here, not six
    plumbing sites (draft → wire → edit-patch → webapp → OpenAPI). Operator-facing
    (UI) vocabulary; the commit builder maps ``lock_model`` → the committed
    ``campaign.json::optimization.forbidden_axes_strict``. The ``max_rounds`` bound
    gates the operator EDIT path; the trusted internal ``draft_from_dataset`` path
    builds the dict directly (a reused dataset's config may carry a higher ceiling)."""

    model_config = ConfigDict(extra="forbid")

    max_rounds: int = Field(
        DEFAULT_MAX_ROUNDS,
        ge=0,
        le=100,
        description="Round ceiling for the campaign. 0 = measure the origin and stop.",
    )
    lock_model: bool = Field(
        True,
        description="Bar the optimizer from mutating model/provider campaign-wide "
        "(commits as ``forbidden_axes_strict``).",
    )
    prompt_block_catalogue: Literal["guidance", "restrict", "off"] = Field(
        "guidance",
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
    """Stock campaign-config knobs (all defaults), as the JSON-shaped dict the
    draft stores and the wire emits."""
    return OptimizationOverrides().model_dump(mode="json")


def closed_answer_format(labels: tuple[str, ...]) -> str:
    """The canonical enumeration appended to a closed-answer-space prompt: every
    label, pipe-joined behind a pick-one directive, so the model can emit any one
    and the answer-space gate passes deterministically. The label set is a
    deterministic fact (the target column's distinct values), not the LLM's to
    transcribe — it reliably drops labels from a many-way set. Single source for
    this string (``committed_prompt_fields`` appends it)."""
    return "Choose exactly one of these labels: " + " | ".join(labels)


@dataclass(frozen=True, slots=True)
class DraftCampaign:
    """Server-held canonical state for an in-progress ingest. Frozen — mutate via :meth:`patch`.

    **It fuses two things on purpose.** The *dataset* half (``slug``,
    ``raw_task_description``, ``pipeline_overlay``, ``origin_prompt_fields``, the
    column mapping) materializes into the four dataset files; the *campaign-config*
    half (``connector``, ``scoring_composite``, ``optimization_overrides``)
    materializes into the sibling ``campaign.json``. The fusion is correct for the
    one-form ingest UX — the operator fills both in one pass.

    **The ``slug`` freezes at commit.** Before commit it's a mutable, operator-
    editable name; the moment :func:`~promptpotter.application.jobs.launcher.materialize_and_write_origin`
    (at check-in Start) writes ``datasets/{slug}/``, the slug becomes the dataset's
    filesystem identity *and* the pin every campaign resolves through. The only
    sanctioned post-commit identity change is the ``-vN`` suffix a *Replace* applies
    (``application/datasets/dataset_replace.py``).
    """

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
    # verbatim to ``prompts/default.json`` at mint. Empty until the check-in fills it.
    origin_prompt_fields: dict[str, Any] = field(default_factory=dict)
    # The check-in's decomposition half also authors the decomposed task context —
    # the 7-field domain framing (:class:`CheckinTaskContext`) that every optimizer
    # layer (L1/L1_CRITIQUE/L2/L3) reads via the ``task_context`` injection. It
    # rides the draft to commit, lands in ``{slug}/task_context.json``, and the
    # run reads it instead of re-decomposing at run-start (the second LLM call
    # that used to recompute exactly this). Empty until the check-in fills it.
    decomposed_task_context: dict[str, Any] = field(default_factory=dict)
    # The chosen active pipeline (``pipeline.json::pipelines.default``) — e.g. the
    # full ``cache_lookup → … → token_matching`` vs a bare ``llm_only``. Empty =
    # fall back to the connector's ``default_pipeline``. Carried so reusing an
    # existing dataset PRESERVES its pipeline through display + commit instead of
    # silently resetting to the connector default (the llm_only-on-reuse bug).
    pipeline_steps: list[str] = field(default_factory=list)
    # The campaign-config knobs, as one :class:`OptimizationOverrides`-shaped dict
    # (``max_rounds`` / ``lock_model`` / ``mechanisms``). Seeded with the stock
    # defaults, operator-editable in the new-campaign form, and materialized into
    # the committed ``campaign.json::optimization`` (``lock_model`` →
    # ``forbidden_axes_strict``). One object so a new knob is one field on
    # :class:`OptimizationOverrides`, not a fresh thread through every surface.
    optimization_overrides: dict[str, Any] = field(default_factory=_default_optimization_overrides)
    # The target library a ``candidate_source`` pipeline ranks each query against —
    # the "4th required input" the operator drops in the ingest UI when a node type
    # raises the dependency (see ``PipelineDependency``). Empty until dropped; on
    # commit it materializes to ``{slug}/candidate_library.txt`` and the run unions
    # it into the session's term index. NOT gated at the readiness checklist — a
    # surfaced-and-droppable dependency, not a hard mint block (the answers already
    # in the data are a degenerate-but-runnable pool).
    candidate_library: tuple[str, ...] = ()
    # Set when this draft was opened by reusing a prior origin (the picker's
    # "Reuse an origin" path) — the chosen origin's content id. When non-empty,
    # ``prepare_checkin_run`` passes ``origin_prompt_fields`` as the
    # ``origin_override`` seed, so C0 resolves via the ``seed`` branch and stamps
    # the ``campaign_origin`` lineage ("minted from a chosen prior origin"). Empty
    # for a fresh upload / plain dataset open (lineage stays ``origin``).
    reused_origin_id: str = ""

    def to_wire(self) -> dict[str, Any]:
        """Wire shape matching the OpenAPI ``DraftCampaign`` schema.

        ``tenant_id`` is intentionally omitted — clients learn it from
        the session cookie (ADR-0002 no-drift gate #3: no per-record
        ``tenant_id`` on the wire).
        """
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
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_disk(self) -> dict[str, Any]:
        """Lossless serialization for the durable check-in store (``checkin/draft.json``).

        Unlike :meth:`to_wire` (the lossy client projection), this carries EVERY
        field the readiness gate runs over — provenance enums (→ str), the column
        label sets, ``candidate_library``, ``decomposed_task_context`` — so
        :meth:`from_disk` reconstructs the exact draft. ``draft_id`` / ``tenant_id``
        are omitted: the campaign dir the check-in lives under IS the identity, so
        they're re-injected from campaign context on load."""
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
            "reused_origin_id": self.reused_origin_id,
        }

    @classmethod
    def from_disk(
        cls, data: dict[str, Any], *, draft_id: str, tenant_id: TenantId
    ) -> DraftCampaign:
        """Reconstruct a draft from :meth:`to_disk` output + the campaign-context identity.

        ``draft_id`` (= the owning ``campaign_id``) and ``tenant_id`` come from the
        campaign dir the check-in lives under, not the JSON — the dir IS the identity."""
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
            reused_origin_id=data.get("reused_origin_id", ""),
        )

    def answer_space(self) -> tuple[str, ...] | None:
        """The target column's closed label set, or ``None`` when the target is
        unresolved or open-ended.

        Keyed off the resolved/proposed ``column_ground_truth`` into
        :attr:`column_label_sets` (computed over the full upload at ingest). This
        is the campaign's enumerable answer space — the gate requires it land in
        the prompt, the proposer is handed it to enumerate.
        """
        if not self.column_ground_truth:
            return None
        return self.column_label_sets.get(self.column_ground_truth)

    def committed_prompt_fields(self) -> dict[str, Any]:
        """The prompt fields this draft commits at mint — the one encoding of
        "the prompt this draft commits," shared by the prompt writer
        (``launcher.materialize_and_write_origin``) and the answer-space gate
        (``origin_readiness._check_answer_space``) so they can't drift.

        The authored Layer-1 fields (``origin_prompt_fields``) win once present;
        otherwise we floor on ``instruction`` from the task description (a real
        ``PromptTemplate`` field — the prior ``task_description``/``instructions``
        keys were not, so the committed prompt loaded empty).

        A closed answer space is enumerated here deterministically: the label set is
        a fact (the target column's distinct values), not the LLM's to transcribe —
        the resolver reliably drops labels from a many-way set, which left every
        non-enumerated row unscoreable and the gate stuck. Code GUARANTEES the full
        enumeration rides the committed ``answer_format``, so ``_check_answer_space``
        is the safety that passes, not a tripwire. APPEND (never overwrite) so the
        resolver's extraction instruction — the bold/box the scorer reads — survives;
        skip when ``answer_format`` is still empty so the ``_check_commit_format``
        nudge to author it isn't masked."""
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
        """Return a copy with ``updated_at`` refreshed and any provided fields replaced."""
        return replace(self, updated_at=utcnow_iso(), **changes)

    def confirm_columns(
        self, *, query_col: str | None = None, ground_truth_col: str | None = None
    ) -> DraftCampaign:
        """Set + CONFIRM the input/target column mapping (operator-stated).

        Membership of each header in :attr:`headers` is the caller's
        wire-validation concern (422); this only records the confirmed value.
        The only caller is the ``edit-draft-campaign`` operator path.
        """
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
        """Apply resolver/operator field changes + their per-field provenance.

        ``values`` are draft-attribute kwargs (``raw_task_description=...``);
        ``provenance`` merges checklist field-id → tag onto :attr:`field_provenance`.
        The single mutation route the origin-resolution loop drives.
        """
        merged = dict(self.field_provenance)
        if provenance:
            merged.update(provenance)
        return self.patch(field_provenance=merged, **(values or {}))


def merge_pipeline_overlay(draft: DraftCampaign, connector: Connector) -> dict[str, Any]:
    """The draft's effective ``pipeline.json::nodes`` block: connector node-config
    seed (e.g. TermNorm's reasoning clamp + owned model) underneath, operator draft
    edits on top.

    The one place the draft's resolved node config is computed — shared by the
    committed pipeline.json builder (``draft_build``), the wire-side optimizer-locks
    block, and the origin readiness model gate — so the three never drift. Pure; the
    ``connector`` is passed in (this module stays connector-registry-free, like
    :meth:`DraftCampaign.to_wire`).

    The layering itself is :func:`merge_node_blocks`, shared with the dataset overlay
    in ``bootstrap/wiring.py``. This used to spell it a second time, merging dict
    sub-blocks by TYPE where the other merged by NAME — so an operator's partial
    ``output_schema`` edit shallow-merged here and could emit a schema whose
    ``required`` named a field its own ``properties`` no longer had."""
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
    """Build a fresh draft — the working state a check-in campaign is minted from.

    The first ingest action calls this, then
    :func:`~promptpotter.application.jobs.launcher.create_checkin_campaign` re-keys
    the draft's ``draft_id`` to the new ``campaign_id`` and persists it. The
    transient ``draft_id`` minted here is never stored (``to_disk`` omits it).

    The input/target column mapping is **not** silently assumed: it auto-confirms
    only when a header is literally named ``query`` / ``ground_truth`` (the
    unambiguous, deterministic case), and otherwise lands ``UNSET`` for the operator
    to confirm. The config knobs seed from our template defaults and auto-confirm
    (one sane default each, so the operator overrides rather than fills them);
    ``raw_task_description`` is the one knob with no default framing, so it lands
    ``UNSET`` — the operator (or the resolver, high-confidence) must state what the
    prompt does.
    """
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
    """Seed the gated-field provenance at mint: columns auto-detected (literal
    ``query`` / ``ground_truth`` headers confirm; else UNSET), ``task_description``
    UNSET (no default framing). Config is not gated — it carries a default the
    operator edits, so it gets no provenance entry.
    """
    query_col, ground_truth_col, provenance = _auto_detect_columns(headers)
    provenance["task_description"] = Provenance.UNSET
    return query_col, ground_truth_col, provenance


def _auto_detect_columns(
    headers: list[str],
) -> tuple[str, str, dict[str, Provenance]]:
    """Deterministic column auto-confirm — literal `query` / `ground_truth` only.

    The unambiguous case the spec's auto-confirm describes, resolved without
    an LLM: a header literally named ``query`` (resp. ``ground_truth``)
    confirms that column. Anything else stays ``UNSET`` for the operator to
    pick — no fuzzy guessing in the deterministic gate.
    """
    header_set = set(headers)
    query_col = "query" if "query" in header_set else ""
    ground_truth_col = "ground_truth" if "ground_truth" in header_set else ""
    provenance = {
        "column.query": Provenance.CONFIRMED if query_col else Provenance.UNSET,
        "column.ground_truth": Provenance.CONFIRMED if ground_truth_col else Provenance.UNSET,
    }
    return query_col, ground_truth_col, provenance


def _mint_draft_id() -> str:
    """Stable, short, URL-safe draft id. ``d_`` prefix mirrors ``s_`` for sessions."""
    return f"d_{uuid.uuid4().hex[:16]}"


# A draft built from an existing on-disk dataset (demo / benchmark / owned tenant
# dataset) records ``source_file = "dataset:{slug}"`` in ``draft_from_dataset``;
# a fresh CSV upload records the raw filename. The prefix is therefore the
# "derived-from-existing vs new-upload" discriminator the commit path branches on
# — no separate flag field. A derived draft mints against its canonical dataset
# instead of cloning a new ``datasets/{slug}/`` folder.
_DERIVED_PREFIX = "dataset:"


def dataset_source_of(source_file: str) -> str | None:
    """Source dataset slug iff the draft was derived from an existing dataset.

    Returns the slug after the ``dataset:`` prefix (the value
    ``mint_campaign_command(dataset_name=...)`` resolves), or ``None`` for a
    fresh CSV upload. Single source of truth for the prefix parse.
    """
    if source_file.startswith(_DERIVED_PREFIX):
        return source_file[len(_DERIVED_PREFIX) :] or None
    return None


def default_slug_from_filename(filename: str) -> str:
    """Derive a tentative slug from an uploaded filename's stem.

    Lowercased + stripped of extension + run through ``safe_name`` for
    validation. Trailing/leading separators are folded; an all-bad-chars
    name (e.g. ``__.csv``) falls back to ``upload``.
    """
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
