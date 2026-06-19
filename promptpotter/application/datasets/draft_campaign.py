"""Server-held ``DraftCampaign`` + the in-memory registry that owns them.

Wire shape pinned in ``docs/specs/m12-api-openapi.yaml::DraftCampaign``;
prose at ``docs/specs/roadmap.md § Draft-campaign object``.

A draft is the mutable target of both surfaces (chat tool-calls + the
panel "Apply" button). The Origin-shaped subset (slug, task_description,
pipeline_overlay) materializes into the four content-hashed files on
commit; the campaign-config subset (connector, scoring_composite,
max_rounds) materializes into the sibling ``campaign.json``.

Storage:
  * In-memory: ``DraftCampaignRegistry``, keyed by ``(tenant_id, draft_id)``
    — short-lived, restart-clears. Per ADR-0002 no-drift gate #3 the
    ``tenant_id`` is the trust-boundary key; cross-tenant lookups by
    bare ``draft_id`` return ``None`` even when the id collides.
  * On-disk: the parsed sample bank lives at
    ``projects/{tenant}/datasets/.drafts/{draft_id}/cache.json`` via
    :class:`TenantDatasetStore`, so a multi-step ingest survives a
    crash mid-conversation. The dataclass below carries only metadata —
    the bank is loaded on demand.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from promptpotter.application.config import MechanismConfig
from promptpotter.domain.identity import TenantId, safe_name
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.shared.clock import utcnow_iso

DEFAULT_CONNECTOR = "termnorm"
"""Only registered connector today (per root CLAUDE.md); operator-editable."""


def closed_answer_format(labels: Sequence[str]) -> str:
    """Canonical ``answer_format`` value for a closed label set: every label,
    pipe-joined, so the prompt can emit any of them and the answer_space gate
    passes deterministically. The single source for this string — both the
    resolver (origin_resolve) and the commit gate (launcher) call it."""
    return " | ".join(labels)


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
        DEFAULT_MAX_ROUNDS, ge=1, le=100, description="Round ceiling for the campaign."
    )
    lock_model: bool = Field(
        True,
        description="Bar the optimizer from mutating model/provider campaign-wide "
        "(commits as ``forbidden_axes_strict``).",
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
    editable name; the moment :func:`~promptpotter.application.jobs.launcher.commit_draft_to_dataset`
    writes ``datasets/{slug}/``, the slug becomes the dataset's filesystem
    identity *and* the pin every campaign resolves through. The only sanctioned
    post-commit identity change is the ``-vN`` suffix a *Replace* applies
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
    # Audit marker for WHO authored the origin decomposition. Empty = the real
    # ``checkin`` LLM node resolved it (the default ``resolve_origin_turn`` path).
    # Populated = a simulated check-in: an operator/agent (e.g. the ``/potter-run``
    # skill) authored the six prompt fields + task_context + column map by hand
    # instead of spending the LLM call, then set this via ``edit-draft-campaign``.
    # Shape ``{by, model, at}`` (free dict). Persisted in the draft resolution
    # block (``cache.json``) as an audit breadcrumb — material provenance, on
    # disk, human/agent-readable. NOT branched on in code (no consumer enforces
    # it); it documents who authored the origin, it doesn't gate anything.
    simulated_checkin: dict[str, Any] = field(default_factory=dict)
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
        (``launcher.commit_draft_to_dataset``) and the answer-space gate
        (``origin_readiness._check_answer_space``) so they can't drift.

        The authored Layer-1 fields (``origin_prompt_fields``) win once present;
        otherwise we floor on ``instruction`` from the task description (a real
        ``PromptTemplate`` field — the prior ``task_description``/``instructions``
        keys were not, so the committed prompt loaded empty)."""
        if self.origin_prompt_fields:
            return dict(self.origin_prompt_fields)
        return {"instruction": self.raw_task_description}

    def with_closed_answer_format(self) -> DraftCampaign:
        """Return a draft whose ``answer_format`` prompt field enumerates the full
        closed answer space. The label set is a deterministic fact (the target
        column's distinct values), not the LLM's to transcribe — so code owns this
        field and the answer_space gate becomes a safety that passes. No-op for an
        open-ended target or a draft without an authored prompt yet."""
        labels = self.answer_space()
        if not labels or not self.origin_prompt_fields:
            return self
        want = closed_answer_format(labels)
        if self.origin_prompt_fields.get("answer_format") == want:
            return self
        return self.patch(origin_prompt_fields={**self.origin_prompt_fields, "answer_format": want})

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


@dataclass
class DraftCampaignRegistry:
    """Thread-safe in-memory registry keyed by ``(tenant_id, draft_id)``.

    Lifecycle is per-process — restart wipes the table. The on-disk
    ``.drafts/{draft_id}/cache.json`` survives, but a draft without its
    in-memory metadata is treated as orphaned and discarded on a fresh
    ingest with the same slug.
    """

    _by_id: dict[str, DraftCampaign] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def create(
        self,
        *,
        tenant_id: TenantId,
        slug: str,
        n_samples: int,
        sample_preview: list[dict[str, str]],
        headers: list[str],
        source_file: str = "",
        column_label_sets: dict[str, tuple[str, ...]] | None = None,
    ) -> DraftCampaign:
        """Mint a fresh draft and register it.

        The input/target column mapping is **not** silently assumed: it
        auto-confirms only when a header is literally named ``query`` /
        ``ground_truth`` (the unambiguous, deterministic case), and otherwise
        lands ``UNSET`` for the operator to confirm. The config knobs seed from
        our template defaults and auto-confirm (one sane default each, so the
        operator overrides rather than fills them); ``raw_task_description`` is the
        one knob with no default framing, so it lands ``UNSET`` — the operator
        (or the resolver, high-confidence) must state what the prompt does.
        """
        now = utcnow_iso()
        column_query, column_ground_truth, provenance = _seed_provenance(headers)
        draft = DraftCampaign(
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
        with self._lock:
            self._by_id[draft.draft_id] = draft
        return draft

    def get(self, draft_id: str, *, tenant_id: TenantId) -> DraftCampaign | None:
        """Return the draft iff it exists *and* belongs to the supplied tenant.

        Cross-tenant id collision returns ``None`` — existence-leak gate
        per ADR-0001 (404 not 403 at the API layer).
        """
        with self._lock:
            draft = self._by_id.get(draft_id)
        if draft is None or draft.tenant_id != tenant_id:
            return None
        return draft

    def update(self, draft: DraftCampaign) -> DraftCampaign:
        """Overwrite the stored row with ``draft`` (same ``draft_id``)."""
        with self._lock:
            self._by_id[draft.draft_id] = draft
        return draft

    def discard(self, draft_id: str, *, tenant_id: TenantId) -> bool:
        """Tenant-scoped removal. Returns ``True`` when a row was deleted."""
        with self._lock:
            existing = self._by_id.get(draft_id)
            if existing is None or existing.tenant_id != tenant_id:
                return False
            del self._by_id[draft_id]
            return True


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
    "DEFAULT_CONNECTOR",
    "DEFAULT_MAX_ROUNDS",
    "DEFAULT_SCORING_COMPOSITE",
    "PREVIEW_ROWS",
    "DraftCampaign",
    "DraftCampaignRegistry",
    "OptimizationOverrides",
    "dataset_source_of",
    "default_slug_from_filename",
]
