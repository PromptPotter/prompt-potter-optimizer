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
    READOUT_POINTER,
)
from promptpotter.config.prompt_blocks import PACK_FILE
from promptpotter.domain.campaign import Campaign, HeadToHeadRecord
from promptpotter.domain.free_tier import FREE_TIER_ADAPTER
from promptpotter.domain.job import JOB_ADAPTER
from promptpotter.domain.pipeline_manifest import AUTHORED_MANIFEST, CHECKIN_MANIFEST
from promptpotter.domain.prompt_block import BlockPack
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
from promptpotter.infrastructure.store.dataset_access import (
    DEFINITION_FILE,
    FRAMING_FILE,
    ORIGIN_FILE,
    dataset_framing,
    definition_layer,
    origin_layer,
)
from promptpotter.infrastructure.store.io import parse_yaml, read_bytes_optional, write_lock_name
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
from promptpotter.infrastructure.store.read_model import read_jsonl_from
from promptpotter.infrastructure.store.ruler_store import RULER_FILE
from promptpotter.infrastructure.store.site_records import SITE_RECORDS_DIR, site_record
from promptpotter.infrastructure.store.source_scan import SOURCE_FACTS_FILE
from promptpotter.infrastructure.store.tenant_dataset_store import (
    BANKS_DIR,
    CUTS_FILE,
    LABELS_FILE,
    MATERIALIZED_DIR,
    TASK_CONTEXT_DIR,
    UPLOADS_DIR,
    BankLabel,
    read_bank,
    read_task_context,
)
from promptpotter.infrastructure.store.user_store import (
    CONSENT_FILE,
    USER_FILE,
    ConsentLine,
    User,
)
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
    # Says who an account is, what it holds, was charged and launched today, or who the host is.
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
    SANDBOX = (f"{INNER_SANDBOXES_DIR}/*/*",)
    WORKSPACE = (f"{PROJECTS_DIR}/*", f"{INNER_SANDBOXES_DIR}/*/*")
    ARCHIVE_HOME = (f"{PROJECTS_DIR}/*", f"{ARCHIVE_HOMES_DIR}/*")

    def __init__(self, *prefixes: str) -> None:
        self.prefixes = prefixes


type Shape = TypeAdapter[Any] | Callable[[bytes], object] | None


@functools.cache
def _compiled(patterns: tuple[str, ...]) -> re.Pattern[str]:
    """`PurePath.full_match`'s semantics, compiled once: it recompiles per call."""
    return re.compile(
        "|".join(glob.translate(p, recursive=True, include_hidden=True, seps="/") for p in patterns)
    )


class Kind(NamedTuple):
    name: str
    scope: Scope
    glob: str
    fate: Fate
    shape: Shape = None

    @property
    def patterns(self) -> tuple[str, ...]:
        return tuple(f"{p}/{self.glob}" if p else self.glob for p in self.scope.prefixes)

    @property
    def lock_patterns(self) -> tuple[str, ...]:
        """`io.py`'s write lock beside each file the row names; a subtree's rows name their own."""
        return tuple(
            f"{head}/{write_lock_name(tail)}" if head else write_lock_name(tail)
            for head, _, tail in (p.rpartition("/") for p in self.patterns)
            if tail != "**"
        )

    @property
    def per_line(self) -> bool:
        return self.glob.endswith(".jsonl")

    @property
    def authored(self) -> bool:
        """YAML, decoded before its model checks it."""
        return self.glob.endswith(".yaml")

    def matches(self, rel: PurePosixPath) -> bool:
        return _compiled(self.patterns).match(rel.as_posix()) is not None

    def locks(self, rel: PurePosixPath) -> bool:
        patterns = self.lock_patterns
        return bool(patterns) and _compiled(patterns).match(rel.as_posix()) is not None


_IDENTITY = IdentityPaths(root=Path(IDENTITY_DIR))
_JOBS = "jobs"
_BLOCK_PACK = f"{BLOCK_PACKS_DIR}/*/{PACK_FILE}"
_INSTALL, _SURVIVOR = Scope.INSTALL, Fate.SURVIVOR

INSTALL_KINDS: tuple[Kind, ...] = (
    Kind(
        "sign-in providers",
        _INSTALL,
        _IDENTITY.provider_config.as_posix(),
        _SURVIVOR,
        TypeAdapter(ProviderConfigBundle),
    ),
    Kind("blocklist", _INSTALL, _IDENTITY.blocklist.as_posix(), _SURVIVOR, TypeAdapter(Blocklist)),
    Kind("blocklist audit", _INSTALL, _IDENTITY.blocklist_audit.as_posix(), _SURVIVOR),
    Kind("grants", _INSTALL, _IDENTITY.grants.as_posix(), _SURVIVOR, TypeAdapter(GrantsFile)),
    Kind("grants audit", _INSTALL, _IDENTITY.grants_audit.as_posix(), _SURVIVOR),
    Kind(
        "session",
        _INSTALL,
        (_IDENTITY.sessions_dir / "*.json").as_posix(),
        _SURVIVOR,
        TypeAdapter(SessionData),
    ),
    Kind(
        "host claim",
        _INSTALL,
        _IDENTITY.default_claim_marker.as_posix(),
        _SURVIVOR,
        TypeAdapter(DefaultClaim),
    ),
    Kind("site records", _INSTALL, f"{SITE_RECORDS_DIR}/*.jsonl", _SURVIVOR, site_record),
    Kind("install block pack", _INSTALL, _BLOCK_PACK, _SURVIVOR, TypeAdapter(BlockPack)),
    Kind("environment", _INSTALL, ".env", _SURVIVOR),
    Kind("free tier", _INSTALL, "free_tier.yaml", _SURVIVOR, FREE_TIER_ADAPTER),
    Kind(
        "optimizer manifest", _INSTALL, "optimizers/*/pipeline.yaml", _SURVIVOR, AUTHORED_MANIFEST
    ),
    Kind("check-in manifest", _INSTALL, "checkin/pipeline.yaml", _SURVIVOR, CHECKIN_MANIFEST),
    Kind("model rates", _INSTALL, "rates.json", Fate.CACHE),
    # Under the data root where the install cache is its `.cache` (an install with no checkout).
    Kind(
        "source facts",
        _INSTALL,
        f"{INSTALL_CACHE_DIR}/{SOURCE_FACTS_FILE}",
        Fate.CACHE,
        TypeAdapter(dict[str, SourceFacts]),
    ),
    # `HF_HOME`, set by `deploy-linux/install-service.sh`: the only path its unit may write.
    Kind("dataset fetch cache", _INSTALL, f"{INSTALL_CACHE_DIR}/huggingface/**", Fate.CACHE),
    # Today's launches are what the daily quota counts, so a reset keeps them.
    Kind("job", _INSTALL, f"{_JOBS}/*.json", _SURVIVOR, JOB_ADAPTER),
    Kind("job log", _INSTALL, f"{_JOBS}/logs/*.log", Fate.RUN),
    Kind("landing", _INSTALL, f"{_JOBS}/.landing", Fate.MACHINE),
    Kind("admission lock", _INSTALL, f"{_JOBS}/.admission.lock", Fate.MACHINE),
    Kind("producer lock", _INSTALL, f"{_JOBS}/producers/*.lock", Fate.MACHINE),
    Kind("machine slot", _INSTALL, f"{_JOBS}/machine/*/*.lock", Fate.MACHINE),
    Kind("account opening lock", _INSTALL, f"{PROJECTS_DIR}/.accounts.lock", Fate.MACHINE),
    Kind(
        "sandbox owner",
        _INSTALL,
        f"{INNER_SANDBOXES_DIR}/*/{sandbox_owner_path(Path()).name}",
        Fate.RUN,
        TypeAdapter(SandboxOwner),
    ),
    Kind("readout pointer", _INSTALL, READOUT_POINTER, Fate.RUN),
)


def _authored(name: str, glob: str, shape: Shape = None) -> tuple[Kind, Kind]:
    """What an account authored outlives a release; an inner sandbox's copy is its run's."""
    return (
        Kind(name, Scope.ACCOUNT, glob, _SURVIVOR, shape),
        Kind(f"sandbox {name}", Scope.SANDBOX, glob, Fate.RUN, shape),
    )


_DATASETS = "datasets"

ACCOUNT_KINDS: tuple[Kind, ...] = (
    Kind("user", Scope.ACCOUNT, USER_FILE, _SURVIVOR, TypeAdapter(User)),
    Kind("consent trail", Scope.ACCOUNT, CONSENT_FILE, _SURVIVOR, TypeAdapter(ConsentLine)),
    Kind("grant", Scope.ACCOUNT, GRANT_FILE, _SURVIVOR, TypeAdapter(CouponGrant)),
    Kind("api keys", Scope.ACCOUNT, API_KEYS_FILE, _SURVIVOR, HELD_KEYS),
    Kind("charges", Scope.ACCOUNT, CHARGES_FILE, _SURVIVOR, TypeAdapter(ChargeRecord)),
    Kind("bank", Scope.ACCOUNT, f"{BANKS_DIR}/*.json", Fate.CACHE, read_bank),
    # An upload definition's `source:` reads it.
    Kind("upload", Scope.ACCOUNT, f"{UPLOADS_DIR}/**", _SURVIVOR),
    # A file two rows name takes the first one's shape.
    *_authored("dataset definition", f"{_DATASETS}/**/{DEFINITION_FILE}", definition_layer),
    *_authored("dataset framing", f"{_DATASETS}/**/{FRAMING_FILE}", dataset_framing),
    *_authored("dataset origin", f"{_DATASETS}/**/{ORIGIN_FILE}", origin_layer),
    *_authored("dataset file", f"{_DATASETS}/**"),
    *_authored("task context", f"{TASK_CONTEXT_DIR}/*.yaml", read_task_context),
    *_authored("bank labels", f"{BANKS_DIR}/{LABELS_FILE}", TypeAdapter(BankLabel)),
    *_authored("account block pack", _BLOCK_PACK, TypeAdapter(BlockPack)),
)


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
    """A file under a campaign or draft directory, its glob from there."""

    name: str
    glob: str
    leaf: Leaf
    shape: Shape = None
    # What `delete --keep-results` keeps.
    keepsake: bool = False


_DECLARED_SHAPES: dict[str, Shape] = {CampaignLayout(Path()).manifest.name: TypeAdapter(Campaign)}


def _declared_rows(where: str, at: Path, layout: CycleLayout | CampaignLayout) -> list[CampaignRow]:
    return [
        CampaignRow(
            f"{where} {p.name}",
            p.as_posix(),
            Leaf.REPORTS,
            _DECLARED_SHAPES.get(p.name),
            keepsake=True,
        )
        for p in declared_files(layout, at)
    ]


def _cycle_rows(where: str, cycle: Path) -> tuple[CampaignRow, ...]:
    """Every file a cycle directory holds, a campaign's or a check-in draft's."""
    layout = CycleLayout(cycle)

    def at(path: Path) -> str:
        return path.as_posix()

    lf = at(cycle / "langfuse")
    return (
        *_declared_rows(where, cycle, layout),
        CampaignRow(f"{where} round", f"{at(layout.rounds)}/{ROUND_GLOB}", Leaf.STATE),
        CampaignRow(f"{where} ledger", at(layout.ledger), Leaf.HISTORY, RECORD_ADAPTER),
        CampaignRow(f"{where} producer lock", at(layout.producer_lock), Leaf.TRACE),
        CampaignRow(f"{where} pass lock", at(layout.pass_lock), Leaf.TRACE),
        CampaignRow(f"{where} stream", f"{at(layout.streams)}/*.jsonl", Leaf.TRACE),
        CampaignRow(
            f"{where} round audit",
            f"{at(layout.audit_rounds)}/{ROUND_GLOB}",
            Leaf.CONNECTOR,
            TypeAdapter(RoundAudit),
        ),
        CampaignRow(f"{where} langfuse events", f"{lf}/events.jsonl", Leaf.TRACE, keepsake=True),
        CampaignRow(f"{where} langfuse state", f"{lf}/state.json", Leaf.TRACE, keepsake=True),
        CampaignRow(f"{where} langfuse trace", f"{lf}/traces/*.json", Leaf.TRACE, keepsake=True),
        CampaignRow(
            f"{where} langfuse observation",
            f"{lf}/observations/*/*.json",
            Leaf.TRACE,
            keepsake=True,
        ),
        CampaignRow(f"{where} langfuse score", f"{lf}/scores/*.jsonl", Leaf.TRACE, keepsake=True),
        CampaignRow(f"{where} dataset mirror", f"{lf}/datasets/**", Leaf.DATASET),
        CampaignRow(f"{where} prompt", f"{at(cycle / 'prompts')}/**", Leaf.TRACE),
    )


CAMPAIGN_ROWS: tuple[CampaignRow, ...] = (
    *_declared_rows("campaign", Path(), CampaignLayout(Path())),
    *_cycle_rows("cycle", Path("cycles/*")),
)


def campaign_row(rel: PurePosixPath) -> CampaignRow | None:
    """``rel`` is from a campaign directory."""
    return next((r for r in CAMPAIGN_ROWS if _compiled((r.glob,)).match(rel.as_posix())), None)


def _run_rows(owner: str, rows: tuple[CampaignRow, ...]) -> tuple[Kind, ...]:
    return tuple(
        Kind(r.name, Scope.WORKSPACE, f"{owner}/{r.glob}", Fate.RUN, r.shape) for r in rows
    )


_WS = Scope.WORKSPACE
_DRAFT = f"{DRAFTS_DIR}/*"
_CAMPAIGN_KINDS = _run_rows("campaigns/*", CAMPAIGN_ROWS)
_DRAFT_KINDS = _run_rows(_DRAFT, _cycle_rows("draft", Path()))

WORKSPACE_KINDS: tuple[Kind, ...] = (
    *_CAMPAIGN_KINDS,
    *_DRAFT_KINDS,
    Kind("draft", _WS, f"{_DRAFT}/draft.json", Fate.RUN),
    Kind("draft check-in", _WS, f"{_DRAFT}/checkin.json", Fate.RUN),
    # Retired by the hold `reset` takes on it, with the draft it claims.
    Kind("draft claim", _WS, f"{DRAFTS_DIR}/.*.claim", Fate.RUN),
    Kind("active session", _WS, ".workspace/active_session.json", Fate.RUN),
    Kind("workspace ledger", _WS, ".workspace/events.jsonl", Fate.RUN, RECORD_ADAPTER),
    Kind("head-to-head", _WS, "head_to_heads/*.json", Fate.RUN, TypeAdapter(HeadToHeadRecord)),
    Kind("ruler", _WS, RULER_FILE, Fate.RUN, TypeAdapter(RulerFold)),
    Kind("diagnostic", _WS, "diagnostics/*.json", Fate.RUN),
    Kind("diagnostic run", _WS, "diagnostics/runs/*.json", Fate.RUN),
    Kind("materialized dataset", _WS, f"{MATERIALIZED_DIR}/**", Fate.RUN),
    Kind("bank cuts", _WS, f"{BANKS_DIR}/{CUTS_FILE}", Fate.RUN),
    Kind("workspace cache", _WS, ".cache/**", Fate.RUN),
)

# What `delete` guards a cycle by: a live producer holds it.
PRODUCER_LOCKS = frozenset(
    kind for kind in (*_CAMPAIGN_KINDS, *_DRAFT_KINDS) if kind.name.endswith(" producer lock")
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

KINDS: tuple[Kind, ...] = (
    *INSTALL_KINDS,
    *ACCOUNT_KINDS,
    *WORKSPACE_KINDS,
    *ARCHIVE_KINDS,
    # `io.py` names every atomic write's temporary `.<random>.tmp`, beside the file it replaces.
    Kind("atomic write", _INSTALL, "**/.*.tmp", Fate.RUN),
)


def _named_first(kind: Kind, pattern: str) -> bool:
    rel = PurePosixPath(pattern.replace("**", "d/e").replace("*", "x"))
    rows = [k for k in KINDS if k.matches(rel)]
    return rows[0] is kind and all(k.fate is kind.fate for k in rows)


assert all(_named_first(kind, p) for kind in KINDS for p in kind.patterns), (
    "a file two rows name takes the first one's fate, so a later row naming it is never read"
)


class Found(NamedTuple):
    kind: Kind
    # The row's write lock: its fate, and never parsed.
    lock: bool


def classify(rel: PurePosixPath) -> Found | None:
    """``rel`` is from the data root; ``None`` is a file no row names."""
    if named := next((k for k in KINDS if k.matches(rel)), None):
        return Found(named, lock=False)
    locked = next((k for k in KINDS if k.locks(rel)), None)
    return None if locked is None else Found(locked, lock=True)


def _check(kind: Kind, raw: bytes) -> None:
    if not isinstance(kind.shape, TypeAdapter):
        assert kind.shape is not None
        kind.shape(raw)
    elif kind.authored:
        kind.shape.validate_python(parse_yaml(raw))
    else:
        kind.shape.validate_json(raw)


def load(path: Path, found: Found) -> None:
    """Raises what the file's own reader would; an unterminated last line is an append in flight,
    which every line reader stops before."""
    kind = found.kind
    if found.lock or kind.shape is None:
        return
    if kind.per_line:
        read_jsonl_from(path, functools.partial(_check, kind), 0)
    elif (raw := read_bytes_optional(path)) is not None:
        _check(kind, raw)


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
