from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal, NamedTuple

from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.infrastructure.store.io import (
    newest_mtime_ns,
    read_json_optional,
    validate_path_component,
)
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.identity import IdentityContext

logger = logging.getLogger(__name__)

_SIBLING_SEP_RE = re.compile(r"_(fork|diag)_")
_SIBLING_LAST_SEP_RE = re.compile(r"_(fork|diag)_(?!.*_(fork|diag)_)")
_DATASET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*(/[a-z0-9][a-z0-9_-]*)*$")


def validate_dataset_name(name: str) -> str:
    """The name IS the dataset's path under its tier, ``/``-separated; lowercase-only, since both
    shipped filesystems fold case."""
    if not name or not _DATASET_NAME_RE.match(name):
        raise ValueError(
            f"Invalid dataset name: {name!r}. `/`-separated segments of lowercase alphanumerics, "
            "hyphens and underscores, each starting with a letter or digit."
        )
    return name


def dataset_id_prefix(name: str) -> str:
    """The name where ONE path segment must hold it — a campaign id, which is a directory and a URL
    segment. Joined on ``.``, which no name may hold, so two names never share a prefix."""
    return validate_dataset_name(name).replace("/", ".")


def root_cycle_id(cycle_id: str) -> str:
    m = _SIBLING_SEP_RE.search(cycle_id)
    return cycle_id[: m.start()] if m else cycle_id


def sibling_kind(cycle_id: str) -> Literal["root", "fork", "diag"]:
    m = _SIBLING_LAST_SEP_RE.search(cycle_id)
    if m is None:
        return "root"
    return m.group(1)  # type: ignore[return-value]


def tenant_workspace(projects_root: Path, tenant_id: str) -> WorkspaceDir:
    return WorkspaceDir(projects_root / validate_path_component(tenant_id))


ArchiveHomeOwner = Literal["tenant", "install"]

# Beside `projects/`, never in it: no tenant walk can take an install's home for an account.
_ARCHIVE_HOMES_DIR = ".archives"


@dataclass(frozen=True)
class ArchiveHome:
    id: str
    root: Path
    owner: ArchiveHomeOwner


def archive_home(
    identity: IdentityContext, shared_archive: str, *, shared_root: Path
) -> ArchiveHome:
    if not shared_archive:
        tenant = identity.tenant_id
        return ArchiveHome(tenant, tenant_workspace(shared_root, tenant), "tenant")
    named = validate_path_component(shared_archive)
    return ArchiveHome(named, shared_root.parent / _ARCHIVE_HOMES_DIR / named, "install")


def campaigns_root_dir_for(tenant_root: WorkspaceDir) -> Path:
    return tenant_root / "campaigns"


def campaign_root_dir_for(tenant_root: WorkspaceDir, campaign_id: str) -> Path:
    return campaigns_root_dir_for(tenant_root) / validate_path_component(campaign_id)


DRAFTS_DIR = "drafts"


def drafts_root_dir_for(tenant_root: WorkspaceDir) -> Path:
    return tenant_root / DRAFTS_DIR


def draft_dir_for(tenant_root: WorkspaceDir, draft_id: str) -> Path:
    return drafts_root_dir_for(tenant_root) / validate_path_component(draft_id)


def draft_claim_path(tenant_root: WorkspaceDir, draft_id: str) -> Path:
    """Beside the draft dir, never in it: a discard removes the dir while holding the claim."""
    return drafts_root_dir_for(tenant_root) / f".{validate_path_component(draft_id)}.claim"


def draft_dirs(tenant_root: WorkspaceDir) -> list[Path]:
    """Every ledger billed before a campaign holds it; a claim file beside them is none."""
    root = drafts_root_dir_for(tenant_root)
    return sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


def head_to_head_path(tenant_root: WorkspaceDir, head_to_head_id: str) -> Path:
    return tenant_root / "head_to_heads" / f"{validate_path_component(head_to_head_id)}.json"


def campaign_cycles_dir(campaign_root: Path) -> Path:
    return campaign_root / "cycles"


def cycle_dir_for(tenant_root: WorkspaceDir, hop: CycleHop) -> Path:
    campaign_root = campaign_root_dir_for(tenant_root, hop.campaign_id)
    return campaign_cycles_dir(campaign_root) / validate_path_component(hop.cycle_id)


# Flat, off-registry and hash-keyed: nested in its owning cycle, a sandbox exceeds Windows' MAX_PATH.
INNER_SANDBOXES_DIR = ".inner"


def inner_sandboxes_dir(workspace_projects_root: Path) -> Path:
    """Takes the REAL workspace root: a sandboxed store's own ``projects_root`` IS ``.inner/<key>``."""
    return workspace_projects_root.parent / INNER_SANDBOXES_DIR


def in_inner_sandbox(path: Path) -> bool:
    return INNER_SANDBOXES_DIR in path.parts


def inner_sandbox_key(tenant_id: str, hop: CycleHop) -> str:
    """The FULL owner triple: ``cycle_id`` is content-addressed on the origin, so campaigns share it."""
    for part in (tenant_id, hop.campaign_id, hop.cycle_id):
        validate_path_component(part)
    # This hash IS the directory name: any change to the blob orphans every sandbox on disk.
    return "inner_" + stable_hash([tenant_id, hop.campaign_id, hop.cycle_id])


def inner_sandbox_dir(workspace_projects_root: Path, tenant_id: str, hop: CycleHop) -> Path:
    return inner_sandboxes_dir(workspace_projects_root) / inner_sandbox_key(tenant_id, hop)


def sandbox_owner_path(sandbox_dir: Path) -> Path:
    return sandbox_dir / "owner.json"


class SandboxOwner(NamedTuple):
    tenant_id: str
    campaign_id: str
    cycle_id: str


def read_sandbox_owner(sandbox_dir: Path) -> SandboxOwner | None:
    """``None`` = nothing names the owner of this hash-named dir: a caller KEEPS such a sandbox."""
    record = read_json_optional(sandbox_owner_path(sandbox_dir))
    if not isinstance(record, dict):
        return None
    try:
        return SandboxOwner(
            *(validate_path_component(str(record.get(name, ""))) for name in SandboxOwner._fields)
        )
    except ValueError as exc:
        logger.warning("inner sandbox %s names an unusable owner: %s", sandbox_dir, exc)
        return None


def round_basename(round_num: int) -> str:
    return f"round_{round_num:04d}.json"


ROUND_GLOB = "round_*.json"


def round_number(path: Path) -> int | None:
    stem = path.stem
    if path.suffix != ".json" or not stem.startswith("round_"):
        return None
    suffix = stem.removeprefix("round_")
    return int(suffix) if suffix.isdigit() else None


@dataclass(frozen=True, slots=True)
class CycleLayout:
    cycle_dir: Path

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
        return self.cycle_dir / "export.json"

    @property
    def optimized_surface(self) -> Path:
        return self.cycle_dir / "optimized.md"

    @property
    def readout(self) -> Path:
        return self.cycle_dir / "readout.log"

    @property
    def rounds(self) -> Path:
        return self.cycle_dir / "rounds"

    def round_file(self, round_num: int) -> Path:
        return self.rounds / round_basename(round_num)

    @property
    def runtime(self) -> Path:
        return self.cycle_dir / ".runtime"

    @property
    def ledger(self) -> Path:
        return self.runtime / "ledger.jsonl"

    @classmethod
    def of_ledger(cls, ledger: Path) -> CycleLayout:
        return cls(ledger.parent.parent)

    @property
    def streams(self) -> Path:
        return self.runtime / "streams"

    @property
    def audit_rounds(self) -> Path:
        return self.runtime / "cache" / "rounds"

    def audit_round_file(self, round_num: int) -> Path:
        return self.audit_rounds / round_basename(round_num)

    @property
    def producer_lock(self) -> Path:
        """Never unlinked: every launch reuses the path."""
        return self.runtime / "producer.lock"


@dataclass(frozen=True, slots=True)
class CampaignLayout:
    campaign_dir: Path

    @property
    def manifest(self) -> Path:
        return self.campaign_dir / "campaign.json"

    @property
    def log_md(self) -> Path:
        return self.campaign_dir / "log.md"

    def files(self) -> list[Path]:
        return _declared_files(self, self.campaign_dir)


def _declared_files(layout: CycleLayout | CampaignLayout, root: Path) -> list[Path]:
    """Derived: a hand-copied list skips the next file declared, and ``delete --keep-results`` deletes it."""
    return [
        p
        for name, attr in vars(type(layout)).items()
        if isinstance(attr, property)
        and isinstance(p := getattr(layout, name), Path)
        and p.parent == root
        and p.suffix
    ]


_PROBE = Path(".")

_REPORT_NAMES = frozenset(
    p.name
    for layout in (CycleLayout(_PROBE), CampaignLayout(_PROBE))
    for p in _declared_files(layout, _PROBE)
)


class FileKind(Enum):
    leaf: str
    keepsake: bool

    DATASET_MIRROR = ("dataset", False)
    CONNECTOR_CACHE = ("connector", False)
    ROUND_PUBLIC = (
        "state",
        False,
    )
    LEDGER = ("history", False)
    LOOP_TELEMETRY = ("trace", False)

    LANGFUSE_TRACE = ("trace", True)
    REPORT = ("reports", True)

    def __init__(self, leaf: str, keepsake: bool) -> None:
        self.leaf = leaf
        self.keepsake = keepsake


def classify(parts: Sequence[str]) -> FileKind:
    """``ROUND_PUBLIC``'s bytes straddle two leaves: the rollup splits it rather than reading ``.leaf``."""
    name = parts[-1]
    if "langfuse" in parts:
        i = parts.index("langfuse")
        sub = parts[i + 1] if i + 1 < len(parts) else ""
        return FileKind.DATASET_MIRROR if sub == "datasets" else FileKind.LANGFUSE_TRACE
    if ".runtime" in parts:
        j = parts.index(".runtime")
        sub = parts[j + 1] if j + 1 < len(parts) else ""
        if sub == "cache":
            return FileKind.CONNECTOR_CACHE
        if name == "ledger.jsonl":
            return FileKind.LEDGER
        return FileKind.LOOP_TELEMETRY
    if name in _REPORT_NAMES:
        return FileKind.REPORT
    if "rounds" in parts and name.startswith("round_") and name.endswith(".json"):
        return FileKind.ROUND_PUBLIC
    return FileKind.LOOP_TELEMETRY


def course_validator_ns(cycle_dir: Path) -> int | None:
    return newest_mtime_ns(CycleLayout(cycle_dir).ledger)
