"""Campaign / cycle directory builders + cycle-id parsing + the per-cycle
on-disk layout. Pure — no I/O, no parent walk.

On-disk shape: ``campaigns/{campaign_id}/`` holds ``campaign.json`` + ``log.md`` +
``cycles/{cycle_id}/`` (every cycle flat under one ``cycles/``). New campaigns
mint ``campaign_id = {dataset}__{rand6_hex}`` per ``new``; root cycle is
``cycle_{target_hash[:12]}``. Sibling kind + sweep batch id live in
``cycles/{cycle_id}/index.json``, not the path.

:class:`CycleLayout` is the single owner of everything *inside* a cycle dir —
the ``.runtime/`` durability tree, the public round files, the manifest, the
rendered prompts, the langfuse mirror. Every reader/writer derives its paths
from a ``CycleLayout``; to move a file on disk, change it here and nowhere else.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.infrastructure.store.io import validate_path_component

# The roots (``REPO_ROOT`` + its two derivatives) moved to
# ``promptpotter/config/paths.py``. They were a ``parents[3]`` walk, which is I/O
# about the install and a parent walk — the two things this module's own docstring
# says it does not do. Import them from ``config.paths``; the name ``REPO_ROOT`` is
# gone deliberately, because there is not one root.


_SIBLING_SEP_RE = re.compile(r"_(fork|diag|sweep)_")
_SIBLING_LAST_SEP_RE = re.compile(r"_(fork|diag|sweep)_(?!.*_(fork|diag|sweep)_)")
_DATASET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def validate_dataset_name(name: str) -> str:
    """**The** dataset-name rule — every entry point asks here, wire included.

    Stricter than :func:`validate_path_component` (no dots) and lowercase-only,
    because a dataset name IS a directory name and the two filesystems this ships
    on are case-insensitive: ``Foo`` and ``foo`` would be one directory while
    :meth:`~...TenantDatasetStore.slug_exists` reported two. Ingest already
    lowercases, so nothing legal is lost.

    A leading digit is allowed on purpose — ``2024-sales.csv`` is an ordinary upload, and
    a wire rule that rejects it rejects the slug its own ingest just minted, so a dataset
    can be created and never minted against. That is what a second rule for this field
    buys (it was ``_DATASET_NAME_PATTERN`` in the commands router); there is one rule
    because two can disagree.

    Raises ``ValueError`` on invalid; returns *name* unchanged otherwise.
    """
    if not name or not _DATASET_NAME_RE.match(name):
        raise ValueError(
            f"Invalid dataset name: {name!r}. Lowercase alphanumerics, hyphens and "
            "underscores only, starting with a letter or digit."
        )
    return name


def root_cycle_id(cycle_id: str) -> str:
    """Family-root cycle id — prefix before the FIRST sibling separator (``_fork_``/``_diag_``/``_sweep_``).

    ``cycle_X_fork_Y_sweep_b1_abc`` roots at ``cycle_X``.
    """
    m = _SIBLING_SEP_RE.search(cycle_id)
    return cycle_id[: m.start()] if m else cycle_id


def sibling_kind(cycle_id: str) -> Literal["root", "fork", "diag", "sweep"]:
    """Kind of THIS cycle (LAST separator); ``cycle_X_fork_Y_sweep_b1_abc`` ⇒ ``sweep``."""
    m = _SIBLING_LAST_SEP_RE.search(cycle_id)
    if m is None:
        return "root"
    return m.group(1)  # type: ignore[return-value]


def tenant_workspace(projects_root: Path, tenant_id: str) -> WorkspaceDir:
    """``projects_root/{tenant_id}`` — the tenant's workspace root, and the ONE
    place that relation is written down.

    Every ``tenant_root`` argument below is one of these. It is a
    :data:`~promptpotter.domain.cycle_paths.WorkspaceDir` rather than a bare ``Path``
    so the type system can tell it apart from the ``projects_root`` one level up:
    the two are both ``Path``, differ by exactly one segment, and every function that
    took the wrong one still compiled. That was not hypothetical — the active-session
    pointer accepted an optional root and defaulted to the process-global workspace, so
    an L4 inner cycle retargeted the OPERATOR's pointer at a campaign living under
    ``.inner/`` and blanked their dashboard mid-run.
    """
    return WorkspaceDir(projects_root / validate_path_component(tenant_id))


def campaign_root_dir_for(tenant_root: WorkspaceDir, campaign_id: str) -> Path:
    """Campaign dir — ``campaign.json`` + ``log.md`` + ``hard_samples.json`` + ``cycles/``. Per-session telemetry binds one level down."""
    return tenant_root / "campaigns" / validate_path_component(campaign_id)


def archive_root_dir_for(tenant_root: WorkspaceDir, campaign_id: str) -> Path:
    """Recycle-bin home for an archived campaign — ``archive/{campaign_id}/``. The
    ``archive`` verb MOVES the campaign tree here; ``unarchive`` moves it back to
    ``campaigns/``. Recoverability is its only feature; it sits beside the
    measurement store (``measurements/``, the DB core), which is NOT trash."""
    return tenant_root / "archive" / validate_path_component(campaign_id)


def campaign_cycles_dir(campaign_root: Path) -> Path:
    """The ``cycles/`` dir under an ALREADY-RESOLVED campaign root — the sole owner
    of the literal, for every enumerator that walks a campaign's cycles."""
    return campaign_root / "cycles"


def cycle_dir_under(campaign_root: Path, cycle_id: str) -> Path:
    """Per-cycle dir under an ALREADY-RESOLVED campaign root — the sole owner of the
    ``cycles/{cycle_id}`` layout. ``CampaignStore`` passes an archive-aware root here;
    :func:`cycle_dir_for` passes the ``campaigns/``-only one."""
    return campaign_cycles_dir(campaign_root) / validate_path_component(cycle_id)


def cycle_dir_for(tenant_root: WorkspaceDir, campaign_id: str, cycle_id: str) -> Path:
    """Per-cycle dir ``campaigns/{campaign_id}/cycles/{cycle_id}``; flat — sibling kind in ``index.json``, not the path."""
    return cycle_dir_under(campaign_root_dir_for(tenant_root, campaign_id), cycle_id)


def sweep_batch_dir_for(tenant_root: WorkspaceDir, campaign_id: str, batch_id: str) -> Path:
    """``campaigns/{campaign_id}/sweeps/{batch_id}`` — batch ``index.json`` + ``summary.md``; fork *cycles* live flat under ``cycles/``."""
    validate_path_component(batch_id)
    return campaign_root_dir_for(tenant_root, campaign_id) / "sweeps" / batch_id


def session_dir_for(tenant_root: WorkspaceDir, session_id: str) -> Path:
    validate_path_component(session_id)
    return tenant_root / "sessions" / session_id


# -- L4 inner sandboxes -------------------------------------------------------
#
# The natural home for a cycle's inner campaigns is inside that cycle's own directory,
# where ownership would be structural and deletion would cascade for free. They live in a
# flat off-registry registry instead for ONE reason: physical nesting blows Windows'
# 260-char MAX_PATH at depth 1 and is hopeless at L5+. That constraint is also why the key
# below is a fixed-width hash rather than the owner's three names joined — this tree already
# runs within ~30 chars of the limit, so the key has to stay the width it always was.


def inner_sandboxes_dir(workspace_projects_root: Path) -> Path:
    """The workspace's sandbox home, ``.inner/`` — a sibling of ``projects/``.

    Pass the REAL workspace projects root (``Stores.shared_root``), which is invariant
    across recursion depth. A sandboxed store's own ``projects_root`` already IS
    ``.inner/<key>``, so anchoring on it would nest ``.inner/.inner/…`` at L5 — the exact
    path-length trap the flat layout exists to avoid. The two coincide at depth 0.
    """
    return workspace_projects_root.parent / ".inner"


def inner_sandbox_key(tenant_id: str, campaign_id: str, cycle_id: str) -> str:
    """Directory name of one cycle's inner sandbox: ``inner_<16 hex>``.

    Keyed on the FULL owner identity, because no part of it identifies the owner alone.
    ``cycle_id`` is a content hash of the origin and is therefore SHARED by every campaign
    minted from that origin — deliberately, since resume and cache reuse depend on it — and
    ``campaign_id`` is unique only within a tenant. Keyed on the cycle alone, as it was, two
    campaigns on one origin shared a single sandbox: a fresh ``new`` swept away the other's
    live inner tree, ``delete_campaign`` cascaded into it, ``reclaim_orphan_sandboxes``
    resolved its owner through a cross-tenant glob, and ``?descend=`` served one campaign's
    inner fan-out under the other's id. Observed, not hypothetical — a second
    ``new promptpotter-self`` destroyed 39 banked inner campaigns belonging to the only outer
    run that had ever finished.

    The three names are not lost to the hash: the sandbox records them in ``owner.json``
    (:func:`sandbox_owner_path`), which is also what lets a reader resolve ownership exactly
    instead of guessing at it from a path.
    """
    for part in (tenant_id, campaign_id, cycle_id):
        validate_path_component(part)
    blob = json.dumps([tenant_id, campaign_id, cycle_id], sort_keys=True)
    return "inner_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def inner_sandbox_dir(
    workspace_projects_root: Path, tenant_id: str, campaign_id: str, cycle_id: str
) -> Path:
    """The one sandbox owned by ``(tenant, campaign, cycle)`` — THE path, built once here."""
    return inner_sandboxes_dir(workspace_projects_root) / inner_sandbox_key(
        tenant_id, campaign_id, cycle_id
    )


def sandbox_owner_path(sandbox_dir: Path) -> Path:
    """``owner.json`` — which ``(tenant, campaign, cycle)`` this sandbox belongs to.

    A material fact, so it lands on disk in human-readable form rather than only in the
    directory name. It is what makes the orphan reaper's ownership test exact.
    """
    return sandbox_dir / "owner.json"


def round_basename(round_num: int) -> str:
    """The 4-digit zero-padded round-file basename — shared by the public round
    tree, the candidate cache, and the audit cache. Owned once so all three agree."""
    return f"round_{round_num:04d}.json"


@dataclass(frozen=True, slots=True)
class CycleLayout:
    """Every path *inside* one cycle dir, named once — the sole owner of the
    on-disk shape below ``campaigns/{c}/cycles/{cy}/``.

    ``.runtime/ledger.jsonl``, the ``round_{n:04d}`` file naming, and the flag
    names (``pause.flag`` / ``skip.flag`` / …) all derive from here, so a move
    on disk (e.g. a ``.runtime/`` durability-class split) is a one-line change.
    """

    cycle_dir: Path

    # --- top-level per-cycle surfaces (human-readable; survive keep-results) ---
    @property
    def manifest(self) -> Path:
        return self.cycle_dir / "index.json"

    @property
    def dashboard(self) -> Path:
        return self.cycle_dir / "dashboard.json"

    @property
    def log_md(self) -> Path:
        return self.cycle_dir / "log.md"

    @property
    def review_md(self) -> Path:
        return self.cycle_dir / "review.md"

    @property
    def hard_samples(self) -> Path:
        return self.cycle_dir / "hard_samples.json"

    # --- resume state (heavy: dropped by ``delete --keep-results``) ---
    @property
    def rounds(self) -> Path:
        """Public resume-state round tree — hand-editable, the resume SoT."""
        return self.cycle_dir / "rounds"

    def round_file(self, round_num: int) -> Path:
        return self.rounds / round_basename(round_num)

    # --- .runtime/ durability classes (spine / cache / control / rewind) ---
    @property
    def runtime(self) -> Path:
        return self.cycle_dir / ".runtime"

    @property
    def ledger(self) -> Path:
        """The append-only per-cycle event spine — the persistence SoT."""
        return self.runtime / "ledger.jsonl"

    @property
    def streams(self) -> Path:
        return self.runtime / "streams"

    @property
    def audit_rounds(self) -> Path:
        """Deep per-round audit cache (rebuildable from the ledger)."""
        return self.runtime / "cache" / "rounds"

    def audit_round_file(self, round_num: int) -> Path:
        return self.audit_rounds / round_basename(round_num)

    @property
    def candidates_cache(self) -> Path:
        return self.runtime / "cache" / "candidates"

    def candidate_file(self, round_num: int) -> Path:
        return self.candidates_cache / round_basename(round_num)

    # --- control-local flags (polled per checkpoint; transient) ---
    @property
    def pause_flag(self) -> Path:
        return self.runtime / "pause.flag"

    @property
    def skip_flag(self) -> Path:
        return self.runtime / "skip.flag"

    @property
    def checkin_flag(self) -> Path:
        return self.runtime / "checkin.flag"

    @property
    def gate_decision(self) -> Path:
        return self.runtime / "gate_decision.json"

    @property
    def spend_cap(self) -> Path:
        return self.runtime / "spend_cap.json"


# Readable-output files (anywhere in a campaign tree) → the ``reports`` keepsake.
_REPORT_NAMES = frozenset(
    {"campaign.json", "dashboard.json", "index.json", "review.md", "log.md", "hard_samples.json"}
)


class FileKind(Enum):
    """The logical role of one file in a campaign tree — the single taxonomy behind
    BOTH the storage rollup's size leaf (:attr:`leaf`) and ``delete --keep-results``'
    keepsake decision (:attr:`keepsake`).

    :attr:`leaf` names the MECE size bucket (a human-facing figure — the sidebar cake).
    :attr:`keepsake` is whether ``delete --keep-results`` spares the file. ``trace`` is
    the one leaf that straddles keepsake, so it carries two kinds — the langfuse loop
    trace survives, loop telemetry does not — which is why a bare 6-way leaf enum can't
    also answer keepsake."""

    leaf: str
    keepsake: bool

    # Heavy tiers — dropped by ``delete --keep-results``.
    DATASET_MIRROR = ("dataset", False)  # langfuse/datasets/** — the ground-truth input copy
    CONNECTOR_CACHE = ("connector", False)  # .runtime/cache/** — backend node-I/O + audit rounds
    ROUND_PUBLIC = (
        "state",
        False,
    )  # rounds/round_*.json — bytes split connector/state by the rollup
    LEDGER = ("history", False)  # .runtime/ledger.jsonl — the event spine (carries the cycle seed)
    LOOP_TELEMETRY = ("trace", False)  # .runtime/streams, prompts/, sweeps/, residual

    # Keepsake — spared by ``delete --keep-results``.
    LANGFUSE_TRACE = ("trace", True)  # langfuse/{traces,observations,scores} — the loop trace
    REPORT = ("reports", True)  # manifest + dashboard/index/log/review + hard_samples

    def __init__(self, leaf: str, keepsake: bool) -> None:
        self.leaf = leaf
        self.keepsake = keepsake


def classify(rel: Path) -> FileKind:
    """Classify one file (path relative to the campaign root) into its :class:`FileKind`.
    First match wins; the final branch makes the partition exhaustive.

    ``ROUND_PUBLIC`` (``rounds/round_*.json``) is the lone file whose bytes straddle two
    leaves — the backend's per-sample arrays roll into ``connector``, the searchpoint
    remainder into ``state`` — so the rollup splits it rather than reading ``.leaf``. The
    ``.runtime/cache`` audit copy carries ``.runtime`` and is caught above as
    ``CONNECTOR_CACHE``, so it never reaches the round branch."""
    parts = rel.parts
    if "langfuse" in parts:
        i = parts.index("langfuse")
        sub = parts[i + 1] if i + 1 < len(parts) else ""
        return FileKind.DATASET_MIRROR if sub == "datasets" else FileKind.LANGFUSE_TRACE
    if ".runtime" in parts:
        j = parts.index(".runtime")
        sub = parts[j + 1] if j + 1 < len(parts) else ""
        if sub == "cache":
            return FileKind.CONNECTOR_CACHE
        if rel.name == "ledger.jsonl":
            return FileKind.LEDGER
        return FileKind.LOOP_TELEMETRY  # streams/ + anything else under .runtime
    if rel.name in _REPORT_NAMES:
        return FileKind.REPORT
    if "rounds" in parts and rel.name.startswith("round_") and rel.suffix == ".json":
        return FileKind.ROUND_PUBLIC
    return FileKind.LOOP_TELEMETRY  # prompts/, sweeps/summary.md, residual → loop telemetry


__all__ = [
    "CycleLayout",
    "FileKind",
    "archive_root_dir_for",
    "campaign_root_dir_for",
    "classify",
    "cycle_dir_for",
    "cycle_dir_under",
    "inner_sandbox_dir",
    "inner_sandbox_key",
    "inner_sandboxes_dir",
    "root_cycle_id",
    "round_basename",
    "sandbox_owner_path",
    "session_dir_for",
    "sibling_kind",
    "sweep_batch_dir_for",
    "tenant_workspace",
    "validate_dataset_name",
]
