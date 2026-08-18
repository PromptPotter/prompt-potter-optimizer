"""Campaign / cycle directory builders + cycle-id parsing. Pure — no I/O, no parent walk.
Every cycle is flat under one ``cycles/``; sibling kind and sweep batch ride ``index.json``."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
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
    """Lowercase-only because a dataset name IS a directory name and both shipped filesystems are
    case-insensitive. A leading digit is legal — ingest mints ``2024-sales`` slugs of its own."""
    if not name or not _DATASET_NAME_RE.match(name):
        raise ValueError(
            f"Invalid dataset name: {name!r}. Lowercase alphanumerics, hyphens and "
            "underscores only, starting with a letter or digit."
        )
    return name


def root_cycle_id(cycle_id: str) -> str:
    """Prefix before the FIRST sibling separator: ``cycle_X_fork_Y_sweep_b1_abc`` roots at
    ``cycle_X``."""
    m = _SIBLING_SEP_RE.search(cycle_id)
    return cycle_id[: m.start()] if m else cycle_id


def sibling_kind(cycle_id: str) -> Literal["root", "fork", "diag", "sweep"]:
    """Kind of THIS cycle (LAST separator); ``cycle_X_fork_Y_sweep_b1_abc`` ⇒ ``sweep``."""
    m = _SIBLING_LAST_SEP_RE.search(cycle_id)
    if m is None:
        return "root"
    return m.group(1)  # type: ignore[return-value]


def tenant_workspace(projects_root: Path, tenant_id: str) -> WorkspaceDir:
    """The ONE place ``projects_root/{tenant_id}`` is written down. It returns a ``WorkspaceDir`` so
    the type system separates it from ``projects_root``, one segment up and also a ``Path``."""
    return WorkspaceDir(projects_root / validate_path_component(tenant_id))


def campaigns_root_dir_for(tenant_root: WorkspaceDir) -> Path:
    """The tenant's ONE campaign parent. An archived campaign stays here and is hidden by
    ``campaign.json::lifecycle_status``, never by living somewhere else."""
    return tenant_root / "campaigns"


def campaign_root_dir_for(tenant_root: WorkspaceDir, campaign_id: str) -> Path:
    """Campaign dir — ``campaign.json`` + ``log.md`` + ``hard_samples.json`` + ``cycles/``. Per-session telemetry binds one level down."""
    return campaigns_root_dir_for(tenant_root) / validate_path_component(campaign_id)


def campaign_cycles_dir(campaign_root: Path) -> Path:
    """The ``cycles/`` dir under an ALREADY-RESOLVED campaign root — the sole owner
    of the literal, for every enumerator that walks a campaign's cycles."""
    return campaign_root / "cycles"


def cycle_dir_for(tenant_root: WorkspaceDir, hop: CycleHop) -> Path:
    """Per-cycle dir ``campaigns/{campaign_id}/cycles/{cycle_id}``; flat — sibling kind in ``index.json``, not the path."""
    campaign_root = campaign_root_dir_for(tenant_root, hop.campaign_id)
    return campaign_cycles_dir(campaign_root) / validate_path_component(hop.cycle_id)


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
    """Pass the REAL workspace projects root, invariant across recursion depth: a sandboxed store's
    own ``projects_root`` already IS ``.inner/<key>``, so anchoring there nests it again at L5."""
    return workspace_projects_root.parent / ".inner"


def inner_sandbox_key(tenant_id: str, hop: CycleHop) -> str:
    """Keyed on the FULL owner triple. ``cycle_id`` is content-addressed on the origin and so is
    shared by every campaign minted from it; keyed on the cycle alone, two campaigns share a sandbox."""
    for part in (tenant_id, hop.campaign_id, hop.cycle_id):
        validate_path_component(part)
    # The three names go in as they always did — this hash IS the sandbox directory name,
    # so serializing the carrier instead of its components would rename every sandbox on
    # disk and orphan what they hold, silently.
    blob = json.dumps([tenant_id, hop.campaign_id, hop.cycle_id], sort_keys=True)
    return "inner_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def inner_sandbox_dir(workspace_projects_root: Path, tenant_id: str, hop: CycleHop) -> Path:
    """The one sandbox owned by ``(tenant, campaign, cycle)`` — THE path, built once here."""
    return inner_sandboxes_dir(workspace_projects_root) / inner_sandbox_key(tenant_id, hop)


def sandbox_owner_path(sandbox_dir: Path) -> Path:
    """A material fact, so it lands on disk in readable form rather than only in the directory
    name. It is what makes the orphan reaper's ownership test exact."""
    return sandbox_dir / "owner.json"


def round_basename(round_num: int) -> str:
    """The 4-digit zero-padded round-file basename — shared by the public round
    tree, the candidate cache, and the audit cache. Owned once so all three agree."""
    return f"round_{round_num:04d}.json"


# The glob that finds what ``round_basename`` writes. Spelled beside it so the pair moves together.
ROUND_GLOB = "round_*.json"


def round_number(path: Path) -> int | None:
    """The reader-side INVERSE of :func:`round_basename`; ``None`` when the name is not a round file.

    It is owned here because there was no inverse for years, so four readers each invented one —
    and every one of them answers a plausible ZERO rather than raising on a name it cannot parse
    (a resume seeding a fresh trajectory, a rewind that deletes nothing and reports success). That
    is what made the naming claim above false: without this, a move on disk is silent, not one line.
    """
    stem = path.stem
    if path.suffix != ".json" or not stem.startswith("round_"):
        return None
    suffix = stem.removeprefix("round_")
    return int(suffix) if suffix.isdigit() else None


@dataclass(frozen=True, slots=True)
class CycleLayout:
    """Sole owner of the on-disk shape below ``campaigns/{c}/cycles/{cy}/`` — the ledger path, the
    ``round_{n:04d}`` naming and the flag names all derive here, so a move on disk is one line."""

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

    @property
    def export(self) -> Path:
        """The artifact a program that is not us reads — ``domain/export.py::PromptExport``."""
        return self.cycle_dir / "export.json"

    # --- resume state (heavy: dropped by ``delete --keep-results``) ---
    @property
    def rounds(self) -> Path:
        """Public resume-state round tree — hand-editable, the resume SoT."""
        return self.cycle_dir / "rounds"

    def round_file(self, round_num: int) -> Path:
        return self.rounds / round_basename(round_num)

    def round_files(self) -> list[Path]:
        """Every public round file, ascending — the zero-padding makes lexical order round order.
        Empty (never raising) on a cycle whose resume state was stripped by ``--keep-results``."""
        return sorted(self.rounds.glob(ROUND_GLOB)) if self.rounds.is_dir() else []

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
    def sample_lookahead(self) -> Path:
        # Not a `.flag`: it carries the COUNT the operator armed, so presence alone no longer
        # answers what the walk should do. Peer of `spend_cap` — same write / poll / consume
        # shape, same JSON body.
        return self.runtime / "sample_lookahead.json"

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
    """``leaf`` names the MECE size bucket; ``keepsake`` is whether ``delete --keep-results`` spares
    the file. ``trace`` straddles, which is why a bare 6-way leaf enum cannot answer both."""

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
    """First match wins; the final branch makes the partition exhaustive. ``ROUND_PUBLIC`` is the
    lone file whose bytes straddle two leaves, so the rollup splits it rather than reading ``.leaf``."""
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
    "campaign_root_dir_for",
    "campaigns_root_dir_for",
    "classify",
    "cycle_dir_for",
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
