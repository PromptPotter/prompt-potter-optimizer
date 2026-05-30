"""Server-held ``DraftCampaign`` + the in-memory registry that owns them.

Wire shape pinned in ``docs/specs/m12-api-openapi.yaml::DraftCampaign``;
prose at ``docs/specs/m13-chat-first-user-web.md § Draft-campaign object``.

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
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from promptpotter.domain.identity import TenantId, safe_name
from promptpotter.domain.origin_provenance import Provenance

DEFAULT_CONNECTOR = "termnorm"
"""Only registered connector today (per root CLAUDE.md); operator-editable."""

DEFAULT_SCORING_COMPOSITE = "exact_match"
"""Only universally-applicable scorer for ``(query, ground_truth)`` shape."""

DEFAULT_MAX_ROUNDS = 5
"""Matches the M10 prompt-iteration framework default."""

DEFAULT_OPTIMIZER_PROVIDER = "groq"
"""Mirrors :class:`OptimizerLLMConfig` so first ingest matches existing benchmark behaviour."""

DEFAULT_OPTIMIZER_MODEL = ""
"""Empty = fall back to ``settings.LLM_MODEL`` (currently ``openai/gpt-oss-120b``)."""

PREVIEW_ROWS = 10
"""Sample-preview head size returned alongside every mutation response."""


@dataclass(frozen=True, slots=True)
class DraftCampaign:
    """Server-held canonical state for an in-progress ingest. Frozen — mutate via :meth:`patch`."""

    draft_id: str
    tenant_id: TenantId
    slug: str
    n_samples: int
    sample_preview: tuple[dict[str, str], ...]
    connector: str
    scoring_composite: str
    max_rounds: int
    task_description: str
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
    # Per-field origin-readiness provenance, keyed by dotted field name
    # (`column.query`, `column.ground_truth`, …). The deterministic checklist
    # gates on it; no field reaches mint while UNSET or PROPOSED.
    resolved: dict[str, Provenance] = field(default_factory=dict)
    source_file: str = ""
    # Per-draft optimizer LLM config — what model runs the L1/L2/L3
    # meta-prompts. Distinct from the backend pipeline node's own LLM
    # (lives in ``pipeline_overlay::nodes.{name}.config``).
    optimizer_provider: str = DEFAULT_OPTIMIZER_PROVIDER
    optimizer_model: str = DEFAULT_OPTIMIZER_MODEL

    def to_wire(self) -> dict[str, Any]:
        """Wire shape matching the OpenAPI ``DraftCampaign`` schema.

        ``tenant_id`` is intentionally omitted — clients learn it from
        the session cookie (ADR-0002 no-drift gate #3: no per-record
        ``tenant_id`` on the wire).
        """
        return {
            "draft_id": self.draft_id,
            "slug": self.slug,
            "sample_preview": [dict(row) for row in self.sample_preview],
            "n_samples": self.n_samples,
            "connector": self.connector,
            "scoring_composite": self.scoring_composite,
            "max_rounds": self.max_rounds,
            "task_description": self.task_description,
            "pipeline_overlay": dict(self.pipeline_overlay),
            "optimizer_provider": self.optimizer_provider,
            "optimizer_model": self.optimizer_model,
            "headers": list(self.headers),
            "column_query": self.column_query,
            "column_ground_truth": self.column_ground_truth,
            "resolved": {field_name: prov.value for field_name, prov in self.resolved.items()},
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def patch(self, **changes: Any) -> DraftCampaign:
        """Return a copy with ``updated_at`` refreshed and any provided fields replaced."""
        return replace(self, updated_at=_now_iso(), **changes)

    def confirm_columns(
        self, *, query_col: str | None = None, ground_truth_col: str | None = None
    ) -> DraftCampaign:
        """Set + CONFIRM the input/target column mapping (operator-stated).

        Membership of each header in :attr:`headers` is the caller's
        wire-validation concern (422); this only records the confirmed value.
        """
        resolved = dict(self.resolved)
        changes: dict[str, Any] = {}
        if query_col is not None:
            changes["column_query"] = query_col
            resolved["column.query"] = Provenance.CONFIRMED
        if ground_truth_col is not None:
            changes["column_ground_truth"] = ground_truth_col
            resolved["column.ground_truth"] = Provenance.CONFIRMED
        return self.patch(resolved=resolved, **changes)


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
    ) -> DraftCampaign:
        """Mint a fresh draft and register it.

        The input/target column mapping is **not** silently assumed: it
        auto-confirms only when a header is literally named ``query`` /
        ``ground_truth`` (the unambiguous, deterministic case), and otherwise
        lands ``UNSET`` for the operator to confirm. The other config knobs
        keep their working operator-editable defaults this slice.
        """
        now = _now_iso()
        column_query, column_ground_truth, resolved = _auto_detect_columns(headers)
        draft = DraftCampaign(
            draft_id=_mint_draft_id(),
            tenant_id=tenant_id,
            slug=slug,
            n_samples=n_samples,
            sample_preview=tuple(dict(row) for row in sample_preview[:PREVIEW_ROWS]),
            connector=DEFAULT_CONNECTOR,
            scoring_composite=DEFAULT_SCORING_COMPOSITE,
            max_rounds=DEFAULT_MAX_ROUNDS,
            task_description="",
            pipeline_overlay={},
            headers=tuple(headers),
            column_query=column_query,
            column_ground_truth=column_ground_truth,
            resolved=resolved,
            optimizer_provider=DEFAULT_OPTIMIZER_PROVIDER,
            optimizer_model=DEFAULT_OPTIMIZER_MODEL,
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
    resolved = {
        "column.query": Provenance.CONFIRMED if query_col else Provenance.UNSET,
        "column.ground_truth": Provenance.CONFIRMED if ground_truth_col else Provenance.UNSET,
    }
    return query_col, ground_truth_col, resolved


def _mint_draft_id() -> str:
    """Stable, short, URL-safe draft id. ``d_`` prefix mirrors ``s_`` for sessions."""
    return f"d_{uuid.uuid4().hex[:16]}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


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
    "DEFAULT_OPTIMIZER_MODEL",
    "DEFAULT_OPTIMIZER_PROVIDER",
    "DEFAULT_SCORING_COMPOSITE",
    "PREVIEW_ROWS",
    "DraftCampaign",
    "DraftCampaignRegistry",
    "default_slug_from_filename",
]
