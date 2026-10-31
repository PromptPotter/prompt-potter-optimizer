"""Every file under the data root, by the one row that names it: what a reset does to it and the shape a landing proves."""

from __future__ import annotations

import functools
import glob
import re
from collections.abc import Callable
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

from pydantic import TypeAdapter, ValidationError

from promptpotter.config.paths import (
    BLOCK_PACKS_DIR,
    INSTALL_CACHE_DIR,
    JUDGE_REUSE_DIR,
    MEASUREMENTS_DIR,
    OPTIMIZER_REUSE_DIR,
    PROJECTS_DIR,
)
from promptpotter.domain.campaign import Campaign, HeadToHeadRecord
from promptpotter.domain.free_tier import FREE_TIER_ADAPTER
from promptpotter.domain.job import JOB_ADAPTER
from promptpotter.domain.pipeline_manifest import AUTHORED_MANIFEST, CHECKIN_MANIFEST
from promptpotter.domain.round_audit import RoundAudit
from promptpotter.domain.ruler import RulerFold
from promptpotter.domain.run_records import RECORD_ADAPTER
from promptpotter.domain.sample import ArchiveConfig
from promptpotter.infrastructure.identity.blocklist import Blocklist
from promptpotter.infrastructure.identity.grants import GrantsFile
from promptpotter.infrastructure.identity.paths import IDENTITY_DIR, DefaultClaim, IdentityPaths
from promptpotter.infrastructure.identity.provider_config import ProviderConfigBundle
from promptpotter.infrastructure.identity.session import SessionData
from promptpotter.infrastructure.llm.response import LLMResponse
from promptpotter.infrastructure.store.archive_queries import SampleFoldRow
from promptpotter.infrastructure.store.io import parse_yaml, read_bytes_optional
from promptpotter.infrastructure.store.layout import (
    ARCHIVE_HOMES_DIR,
    DRAFTS_DIR,
    INNER_SANDBOXES_DIR,
    ROUND_GLOB,
    CampaignLayout,
    CycleLayout,
    SandboxOwner,
    declared_files,
    sandbox_owner_path,
)
from promptpotter.infrastructure.store.measurement_archive import (
    ArchiveFile,
    answer_of,
    claimed_row,
    decode_cold,
    index_row,
)
from promptpotter.infrastructure.store.ruler_store import RULER_FILE
from promptpotter.infrastructure.store.site_records import SITE_RECORDS_DIR, site_record
from promptpotter.infrastructure.store.source_scan import SOURCE_FACTS_FILE
from promptpotter.infrastructure.store.tenant_dataset_store import (
    BANKS_DIR,
    CUTS_FILE,
    LABELS_FILE,
    MATERIALIZED_DIR,
    UPLOADS_DIR,
    read_bank,
)
from promptpotter.infrastructure.store.user_store import USER_FILE, User
from promptpotter.infrastructure.store.wallet_store import (
    API_KEYS_FILE,
    CHARGES_FILE,
    GRANT_FILE,
    HELD_KEYS,
    ChargeRecord,
    CouponGrant,
)
from promptpotter.shared.hashing import SourceFacts


class Fate(Enum):
    # Says who an account is, what it holds and was charged, or who the host is.
    SURVIVOR = "SURVIVOR"
    # Content-keyed and paid for: kept while it loads.
    CACHE = "CACHE"
    RUN = "RUN"
    # A lock or marker the box's processes hold: kept, never parsed.
    MACHINE = "MACHINE"


class Scope(Enum):
    prefixes: tuple[str, ...]

    INSTALL = ("",)
    ACCOUNT = (f"{PROJECTS_DIR}/*",)
    WORKSPACE = (f"{PROJECTS_DIR}/*", f"{INNER_SANDBOXES_DIR}/*/*")
    ARCHIVE_HOME = (f"{PROJECTS_DIR}/*", f"{ARCHIVE_HOMES_DIR}/*")

    def __init__(self, *prefixes: str) -> None:
        self.prefixes = prefixes


# A `.jsonl` file is checked per non-blank line, a `.yaml` one decoded before its model.
type Shape = TypeAdapter[Any] | Callable[[bytes], object] | None


class Kind(NamedTuple):
    name: str
    scope: Scope
    glob: str
    fate: Fate
    shape: Shape

    @property
    def patterns(self) -> tuple[str, ...]:
        return tuple(f"{p}/{self.glob}" if p else self.glob for p in self.scope.prefixes)

    def names(self, rel: PurePosixPath) -> bool:
        return _compiled(self.patterns).match(rel.as_posix()) is not None

    def names_in_workspace(self, rel: PurePosixPath) -> bool:
        return _compiled((self.glob,)).match(rel.as_posix()) is not None


@functools.cache
def _compiled(patterns: tuple[str, ...]) -> re.Pattern[str]:
    """`PurePath.full_match`'s semantics, compiled once: it recompiles per call."""
    return re.compile(
        "|".join(glob.translate(p, recursive=True, include_hidden=True, seps="/") for p in patterns)
    )


def _install(name: str, glob: str, fate: Fate, shape: Shape = None) -> Kind:
    return Kind(name, Scope.INSTALL, glob, fate, shape)


_IDENTITY = IdentityPaths(root=Path(IDENTITY_DIR))
_JOBS = "jobs"


def _identity(name: str, path: Path, shape: Shape = None) -> Kind:
    return _install(name, path.as_posix(), Fate.SURVIVOR, shape)


INSTALL_KINDS: tuple[Kind, ...] = (
    _identity("sign-in providers", _IDENTITY.provider_config, TypeAdapter(ProviderConfigBundle)),
    _identity("blocklist", _IDENTITY.blocklist, TypeAdapter(Blocklist)),
    _identity("blocklist audit", _IDENTITY.blocklist_audit),
    _identity("grants", _IDENTITY.grants, TypeAdapter(GrantsFile)),
    _identity("grants audit", _IDENTITY.grants_audit),
    _identity("session", _IDENTITY.sessions_dir / "*.json", TypeAdapter(SessionData)),
    _identity("host claim", _IDENTITY.default_claim_marker, TypeAdapter(DefaultClaim)),
    _install("site records", f"{SITE_RECORDS_DIR}/*.jsonl", Fate.SURVIVOR, site_record),
    _install("install block packs", f"{BLOCK_PACKS_DIR}/**", Fate.SURVIVOR),
    _install("environment", ".env", Fate.SURVIVOR),
    _install("free tier", "free_tier.yaml", Fate.SURVIVOR, FREE_TIER_ADAPTER),
    _install("optimizer manifest", "optimizers/*/pipeline.yaml", Fate.SURVIVOR, AUTHORED_MANIFEST),
    _install("check-in manifest", "checkin/pipeline.yaml", Fate.SURVIVOR, CHECKIN_MANIFEST),
    _install("model rates", "rates.json", Fate.CACHE),
    # Under the data root where the install cache is its `.cache` (an install with no checkout).
    _install(
        "source facts",
        f"{INSTALL_CACHE_DIR}/{SOURCE_FACTS_FILE}",
        Fate.CACHE,
        TypeAdapter(dict[str, SourceFacts]),
    ),
    # `HF_HOME`, set by `deploy-linux/install-service.sh`: the only path its unit may write.
    _install("dataset fetch cache", f"{INSTALL_CACHE_DIR}/huggingface/**", Fate.CACHE),
    _install("job", f"{_JOBS}/*.json", Fate.RUN, JOB_ADAPTER),
    _install("job log", f"{_JOBS}/logs/*.log", Fate.RUN),
    _install("landing", f"{_JOBS}/.landing", Fate.MACHINE),
    _install("admission lock", f"{_JOBS}/.admission.lock", Fate.MACHINE),
    _install("producer lock", f"{_JOBS}/producers/*.lock", Fate.MACHINE),
    _install("machine slot", f"{_JOBS}/machine/*/*.lock", Fate.MACHINE),
    _install("account opening lock", f"{PROJECTS_DIR}/.accounts.lock", Fate.MACHINE),
    _install(
        "sandbox owner",
        f"{INNER_SANDBOXES_DIR}/*/{sandbox_owner_path(Path()).name}",
        Fate.RUN,
        TypeAdapter(SandboxOwner),
    ),
)


def _account(name: str, glob: str, fate: Fate, shape: Shape = None) -> Kind:
    return Kind(name, Scope.ACCOUNT, glob, fate, shape)


ACCOUNT_KINDS: tuple[Kind, ...] = (
    _account("user", USER_FILE, Fate.SURVIVOR, TypeAdapter(User)),
    _account("grant", GRANT_FILE, Fate.SURVIVOR, TypeAdapter(CouponGrant)),
    _account("api keys", API_KEYS_FILE, Fate.SURVIVOR, HELD_KEYS),
    _account("charges", CHARGES_FILE, Fate.SURVIVOR, TypeAdapter(ChargeRecord)),
    _account("bank", f"{BANKS_DIR}/*.json", Fate.CACHE, read_bank),
    _account("upload", f"{UPLOADS_DIR}/**", Fate.CACHE),
)


def _workspace(name: str, glob: str, shape: Shape = None, fate: Fate = Fate.RUN) -> Kind:
    return Kind(name, Scope.WORKSPACE, glob, fate, shape)


class Leaf(Enum):
    """Where a campaign file's bytes count in the storage report."""

    DATASET = "dataset"
    CONNECTOR = "connector"
    # A round checkout less its per-sample rows, which count as `CONNECTOR`.
    STATE = "state"
    TRACE = "trace"
    HISTORY = "history"
    REPORTS = "reports"


class CampaignRow(NamedTuple):
    kind: Kind
    leaf: Leaf
    # What `delete --keep-results` keeps.
    keepsake: bool


_DECLARED_SHAPES: dict[str, Shape] = {CampaignLayout(Path()).manifest.name: TypeAdapter(Campaign)}


def _row(
    name: str, glob: str, leaf: Leaf, shape: Shape = None, *, keep: bool = False
) -> CampaignRow:
    return CampaignRow(_workspace(name, glob, shape), leaf, keep)


def _declared_rows(where: str, at: str, layout: CycleLayout | CampaignLayout) -> list[CampaignRow]:
    return [
        _row(
            f"{where} {p.name}",
            f"{at}/{p.name}",
            Leaf.REPORTS,
            _DECLARED_SHAPES.get(p.name),
            keep=True,
        )
        for p in declared_files(layout, Path())
    ]


def _cycle_rows(where: str, cycle: str) -> tuple[CampaignRow, ...]:
    """Every file a cycle directory holds, a campaign's or a check-in draft's."""
    layout = CycleLayout(Path())

    def at(path: Path) -> str:
        return f"{cycle}/{path.as_posix()}"

    lf = f"{cycle}/langfuse"
    return (
        *_declared_rows(where, cycle, layout),
        _row(f"{where} round", f"{at(layout.rounds)}/{ROUND_GLOB}", Leaf.STATE),
        _row(f"{where} ledger", at(layout.ledger), Leaf.HISTORY, RECORD_ADAPTER),
        _row(f"{where} producer lock", at(layout.producer_lock), Leaf.TRACE),
        _row(f"{where} pass lock", at(layout.pass_lock), Leaf.TRACE),
        _row(f"{where} stream", f"{at(layout.streams)}/*.jsonl", Leaf.TRACE),
        _row(
            f"{where} round audit",
            f"{at(layout.audit_rounds)}/{ROUND_GLOB}",
            Leaf.CONNECTOR,
            TypeAdapter(RoundAudit),
        ),
        _row(f"{where} langfuse events", f"{lf}/events.jsonl", Leaf.TRACE, keep=True),
        _row(f"{where} langfuse state", f"{lf}/state.json", Leaf.TRACE, keep=True),
        _row(f"{where} langfuse trace", f"{lf}/traces/*.json", Leaf.TRACE, keep=True),
        _row(f"{where} langfuse observation", f"{lf}/observations/*/*.json", Leaf.TRACE, keep=True),
        _row(f"{where} langfuse score", f"{lf}/scores/*.jsonl", Leaf.TRACE, keep=True),
        _row(f"{where} dataset mirror", f"{lf}/datasets/**", Leaf.DATASET),
        _row(f"{where} prompt", f"{cycle}/prompts/**", Leaf.TRACE),
    )


_CAMPAIGN = "campaigns/*"

CAMPAIGN_ROWS: dict[Kind, CampaignRow] = {
    row.kind: row
    for row in (
        *_declared_rows("campaign", _CAMPAIGN, CampaignLayout(Path())),
        *_cycle_rows("cycle", f"{_CAMPAIGN}/cycles/*"),
    )
}

_DRAFT = f"{DRAFTS_DIR}/*"

WORKSPACE_KINDS: tuple[Kind, ...] = (
    *CAMPAIGN_ROWS,
    *(row.kind for row in _cycle_rows("draft", _DRAFT)),
    _workspace("draft", f"{_DRAFT}/draft.json"),
    _workspace("draft check-in", f"{_DRAFT}/checkin.json"),
    _workspace("draft claim", f"{DRAFTS_DIR}/.*.claim", fate=Fate.MACHINE),
    _workspace("active session", ".workspace/active_session.json"),
    _workspace("workspace ledger", ".workspace/events.jsonl", RECORD_ADAPTER),
    _workspace("head-to-head", "head_to_heads/*.json", TypeAdapter(HeadToHeadRecord)),
    _workspace("ruler", RULER_FILE, TypeAdapter(RulerFold)),
    _workspace("diagnostic", "diagnostics/*.json"),
    _workspace("diagnostic run", "diagnostics/runs/*.json"),
    _workspace("dataset", "datasets/**"),
    _workspace("materialized dataset", f"{MATERIALIZED_DIR}/**"),
    _workspace("task context", "task-context/*.yaml"),
    _workspace("bank labels", f"{BANKS_DIR}/{LABELS_FILE}"),
    _workspace("bank cuts", f"{BANKS_DIR}/{CUTS_FILE}"),
    _workspace("account block pack", f"{BLOCK_PACKS_DIR}/**"),
    _workspace("workspace cache", ".cache/**"),
)

_ARCHIVE_SHAPES: dict[ArchiveFile, Shape] = {
    ArchiveFile.INDEX: index_row,
    ArchiveFile.CONFIG: TypeAdapter(ArchiveConfig),
    ArchiveFile.CELLS: answer_of,
    ArchiveFile.COLD: decode_cold,
    ArchiveFile.CLAIM_LOCK: None,
    ArchiveFile.CLAIM_ROW: claimed_row,
    ArchiveFile.SAMPLE_FOLD: SampleFoldRow.model_validate_json,
}
assert set(_ARCHIVE_SHAPES) == set(ArchiveFile), "an archive file no row names"


def _archive_glob(file: ArchiveFile) -> str:
    name = file.file_name("") if not file.keyed else f"*{file.suffix}"
    return "/".join(part for part in (MEASUREMENTS_DIR, file.folder, name) if part)


ARCHIVE_KINDS: tuple[Kind, ...] = (
    *(
        Kind(
            f"archive {file.name.lower()}",
            Scope.ARCHIVE_HOME,
            _archive_glob(file),
            Fate.CACHE,
            shape,
        )
        for file, shape in _ARCHIVE_SHAPES.items()
    ),
    *(
        Kind(
            namespace,
            Scope.ARCHIVE_HOME,
            f"{namespace}/*.json",
            Fate.CACHE,
            TypeAdapter(LLMResponse),
        )
        for namespace in (OPTIMIZER_REUSE_DIR, JUDGE_REUSE_DIR)
    ),
)

KINDS: tuple[Kind, ...] = (*INSTALL_KINDS, *ACCOUNT_KINDS, *WORKSPACE_KINDS, *ARCHIVE_KINDS)

# Not a row: `io.py` names every atomic write's temporary `.<random>.tmp`, in whatever directory.
ATOMIC_TMP = Kind("atomic write", Scope.INSTALL, "**/.*.tmp", Fate.RUN, None)


class Found(NamedTuple):
    kind: Kind
    # `.X.lock` beside X: X's fate, and never parsed.
    lock: bool


def rows_matching(rel: PurePosixPath, kinds: tuple[Kind, ...] = KINDS) -> list[Kind]:
    return [k for k in kinds if k.names(rel)]


def _named_alone(kind: Kind, pattern: str) -> bool:
    rows = rows_matching(PurePosixPath(pattern.replace("**", "d/e").replace("*", "x")))
    return len(rows) == 1 and rows[0] is kind


assert all(_named_alone(kind, p) for kind in KINDS for p in kind.patterns), (
    "two rows name one file, so its fate is whichever comes first"
)


def _found(rel: PurePosixPath, match: Callable[[PurePosixPath], list[Kind]]) -> Found | None:
    if rows := match(rel):
        return Found(rows[0], lock=False)
    name = rel.name
    if name.startswith(".") and name.endswith(".lock"):
        locked = match(rel.with_name(name[1 : -len(".lock")]))
        return Found(locked[0], lock=True) if locked else None
    if name.startswith(".") and name.endswith(".tmp"):
        return Found(ATOMIC_TMP, lock=False)
    return None


def classify(rel: PurePosixPath) -> Found | None:
    """``rel`` is from the data root; ``None`` is a file no row names."""
    return _found(rel, rows_matching)


def classify_in_workspace(rel: PurePosixPath) -> Found | None:
    """``rel`` is from a workspace directory, wherever that workspace sits."""
    return _found(rel, lambda r: [k for k in WORKSPACE_KINDS if k.names_in_workspace(r)])


def _check(shape: Shape, raw: bytes, suffix: str) -> None:
    if isinstance(shape, TypeAdapter):
        if suffix == ".yaml":
            shape.validate_python(parse_yaml(raw))
        else:
            shape.validate_json(raw)
    elif shape is not None:
        shape(raw)


def load(path: Path, found: Found) -> None:
    """Raises what the file's own reader would."""
    shape = None if found.lock else found.kind.shape
    if shape is None or (raw := read_bytes_optional(path)) is None:
        return
    for one in raw.splitlines() if path.suffix == ".jsonl" else [raw]:
        if one.strip():
            _check(shape, one, path.suffix)


# What a reader raises on a file it cannot take; a gzip cut short is an `EOFError`.
LOAD_ERRORS = (OSError, ValueError, TypeError, KeyError, EOFError)


def unloadable_why(path: Path, found: Found) -> str | None:
    """Names where and why, never the refused value: a survivor's may be a key."""
    try:
        load(path, found)
    except ValidationError as exc:
        return "ValidationError: " + "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'the file'}: {e['msg']}"
            for e in exc.errors(include_input=False, include_url=False)
        )
    except LOAD_ERRORS as exc:
        return " ".join(f"{type(exc).__name__}: {exc}".split())
    return None


def _schema(shape: Shape) -> object:
    if isinstance(shape, TypeAdapter):
        return shape.json_schema()
    if shape is None:
        return None
    return {"reader": f"{shape.__module__}::{shape.__qualname__}"}


def pinned_schemas() -> dict[str, Any]:
    """The shapes a release must keep loading, or rewrite on every box first."""
    return {
        kind.name: {
            "fate": kind.fate.value,
            "patterns": list(kind.patterns),
            "shape": _schema(kind.shape),
        }
        for kind in KINDS
        if kind.fate in (Fate.SURVIVOR, Fate.CACHE)
    }
