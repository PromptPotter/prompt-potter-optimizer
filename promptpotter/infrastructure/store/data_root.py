"""Every file under the data root, by the one row that names it: what a reset does to it and the shape a landing proves."""

from __future__ import annotations

import functools
import glob
import re
from collections.abc import Callable
from enum import Enum
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

from pydantic import TypeAdapter, ValidationError

from promptpotter.config.paths import (
    ACCOUNTS_LOCK,
    ADMISSION_LOCK,
    BLOCK_PACKS_DIR,
    ENDED_JOBS_DIR,
    INSTALL_CACHE_DIR,
    JOBS_DIR,
    JUDGE_REUSE_DIR,
    LANDING_FILE,
    MACHINE_DIR,
    MEASUREMENTS_DIR,
    OPTIMIZER_REUSE_DIR,
    PRODUCERS_DIR,
    PROJECTS_DIR,
    READOUT_POINTER,
)
from promptpotter.config.prompt_blocks import PACK_FILE
from promptpotter.domain.campaign import Campaign, HeadToHeadRecord
from promptpotter.domain.checkin_draft import DraftFile, OriginResolution
from promptpotter.domain.command_events import COMMAND_LOG_ADAPTER
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.export import parse_prompt_export
from promptpotter.domain.free_tier import FREE_TIER_ADAPTER
from promptpotter.domain.job import JOB_ADAPTER
from promptpotter.domain.pipeline_manifest import AUTHORED_MANIFEST, CHECKIN_MANIFEST
from promptpotter.domain.prompt_block import BlockPack
from promptpotter.domain.ruler import RulerFold
from promptpotter.domain.run_records import RECORD_ADAPTER, ChargeLine
from promptpotter.domain.sample import ArchiveConfig
from promptpotter.infrastructure.identity.blocklist import Blocklist
from promptpotter.infrastructure.identity.grants import GrantsFile
from promptpotter.infrastructure.identity.paths import IDENTITY_DIR, IdentityPaths
from promptpotter.infrastructure.identity.provider_config import ProviderConfigBundle
from promptpotter.infrastructure.identity.session import SessionData
from promptpotter.infrastructure.llm.capabilities import CAPABILITIES_CACHE, CAPABILITY_FILE
from promptpotter.infrastructure.llm.pricing import ROUTE_ANSWER, ROUTE_ANSWERS_DIR
from promptpotter.infrastructure.llm.response import LLMResponse
from promptpotter.infrastructure.store.archive_queries import SampleFoldRow
from promptpotter.infrastructure.store.command_log import COMMAND_LOG_FILE
from promptpotter.infrastructure.store.dataset_access import (
    DEFINITION_FILE,
    FRAMING_FILE,
    MATERIALIZED_PANEL,
    MATERIALIZED_PANEL_FILE,
    ORIGIN_FILE,
    dataset_framing,
    definition_layer,
    origin_layer,
)
from promptpotter.infrastructure.store.draft_store import DRAFT_FILE, RESOLUTION_FILE
from promptpotter.infrastructure.store.io import (
    complete_lines,
    parse_yaml,
    read_bytes_optional,
    write_lock_name,
)
from promptpotter.infrastructure.store.layout import (
    ARCHIVE_HOMES_DIR,
    DIAGNOSTICS_DIR,
    DRAFTS_DIR,
    HEAD_TO_HEADS_DIR,
    INNER_SANDBOXES_DIR,
    ROUND_GLOB,
    WORKSPACE_STATE_DIR,
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
from promptpotter.infrastructure.store.source_scan import SOURCE_FACTS_FILE, TREATMENT_DIGESTS_FILE
from promptpotter.infrastructure.store.tenant_dataset_store import (
    BANKS_DIR,
    CUTS_FILE,
    LABELS_FILE,
    MATERIALIZED_DIR,
    TASK_CONTEXT_DIR,
    UPLOADS_DIR,
    BankCut,
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
from promptpotter.infrastructure.store.visit_records import VISIT_RECORDS_DIR, visit_record
from promptpotter.infrastructure.store.wallet_store import (
    API_KEYS_FILE,
    CHARGES_FILE,
    GRANT_FILE,
    HELD_KEYS,
    CouponGrant,
)
from promptpotter.shared.errors import validation_reason
from promptpotter.shared.hashing import SourceFacts
from promptpotter.shared.worded import Worded


class Fate(Enum):
    # Who an account is, what it holds, was charged and launched today, or who the host is: kept, and
    # one that does not load refuses a landing until a migration rewrites it.
    SURVIVOR = "SURVIVOR"
    # Kept while it loads, and dropped by a landing once it does not, which says so: what is
    # content-keyed and paid for, or a sign-in its person makes again.
    CACHE = "CACHE"
    # Dropped by a reset; one that does not load refuses a landing not asked to reset.
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


class Opaque(NamedTuple):
    """A row a landing proves by presence alone, and why no shape loads its bytes."""

    why: str


type Shape = TypeAdapter[Any] | Callable[[bytes], object] | Opaque

_HELD = Opaque("a lock or marker its process holds, never parsed")
_AUDIT = Opaque("an audit trail its writer appends and no reader parses")
_TREE = Opaque("a file of a tree another tool owns, read by that tool alone")
_WALKED = Opaque("its reader walks a dict, so no model holds its shape yet")
_FOR_A_PERSON = Opaque("written for a person to read, never read back")


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
    shape: Shape

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


# The key of the record schema the data root last landed under (`maintenance/landing.py`).
LANDED_SCHEMA_FILE = ".landed-schema"

_IDENTITY = IdentityPaths(root=Path(IDENTITY_DIR))
_BLOCK_PACK = f"{BLOCK_PACKS_DIR}/*/{PACK_FILE}"
_INSTALL, _SURVIVOR = Scope.INSTALL, Fate.SURVIVOR

# An install's shadows of a shipped manifest: their shape here, and their reader's proof in the
# landing (`maintenance/landing.py`), which reads them as the API's boot does.
OPTIMIZER_SHADOW = Kind(
    "optimizer manifest", _INSTALL, "optimizers/*/pipeline.yaml", _SURVIVOR, AUTHORED_MANIFEST
)
CHECKIN_SHADOW = Kind(
    "check-in manifest", _INSTALL, "checkin/pipeline.yaml", _SURVIVOR, CHECKIN_MANIFEST
)

INSTALL_KINDS: tuple[Kind, ...] = (
    Kind(
        "sign-in providers",
        _INSTALL,
        _IDENTITY.provider_config.as_posix(),
        _SURVIVOR,
        TypeAdapter(ProviderConfigBundle),
    ),
    Kind("blocklist", _INSTALL, _IDENTITY.blocklist.as_posix(), _SURVIVOR, TypeAdapter(Blocklist)),
    Kind("blocklist audit", _INSTALL, _IDENTITY.blocklist_audit.as_posix(), _SURVIVOR, _AUDIT),
    Kind("grants", _INSTALL, _IDENTITY.grants.as_posix(), _SURVIVOR, TypeAdapter(GrantsFile)),
    Kind("grants audit", _INSTALL, _IDENTITY.grants_audit.as_posix(), _SURVIVOR, _AUDIT),
    Kind(
        "session",
        _INSTALL,
        (_IDENTITY.sessions_dir / "*.json").as_posix(),
        Fate.CACHE,
        TypeAdapter(SessionData),
    ),
    # The app's perimeter log, 90 days deep: no account is in it, so a reset takes it.
    Kind("app visits", _INSTALL, f"{VISIT_RECORDS_DIR}/*.jsonl", Fate.RUN, visit_record),
    Kind("install block pack", _INSTALL, _BLOCK_PACK, _SURVIVOR, TypeAdapter(BlockPack)),
    Kind("environment", _INSTALL, ".env", _SURVIVOR, Opaque("dotenv lines the settings read")),
    Kind("free tier", _INSTALL, "free_tier.yaml", _SURVIVOR, FREE_TIER_ADAPTER),
    OPTIMIZER_SHADOW,
    CHECKIN_SHADOW,
    Kind("model rates", _INSTALL, "rates.json", Fate.CACHE, _WALKED),
    Kind("route ceilings", _INSTALL, f"{ROUTE_ANSWERS_DIR}/*.json", Fate.CACHE, ROUTE_ANSWER),
    # Under the data root where the install cache is its `.cache` (an install with no checkout).
    Kind(
        "source facts",
        _INSTALL,
        f"{INSTALL_CACHE_DIR}/{SOURCE_FACTS_FILE}",
        Fate.CACHE,
        TypeAdapter(dict[str, SourceFacts]),
    ),
    Kind(
        "treatment digests",
        _INSTALL,
        f"{INSTALL_CACHE_DIR}/{TREATMENT_DIGESTS_FILE}",
        Fate.CACHE,
        TypeAdapter(dict[str, str]),
    ),
    # `HF_HOME`, set by `deploy-linux/install-service.sh`: the only path its unit may write.
    Kind("dataset fetch cache", _INSTALL, f"{INSTALL_CACHE_DIR}/huggingface/**", Fate.CACHE, _TREE),
    # Today's launches are what the daily quota counts, so a reset keeps them.
    Kind("job", _INSTALL, f"{JOBS_DIR}/*.json", _SURVIVOR, JOB_ADAPTER),
    Kind("ended job", _INSTALL, f"{JOBS_DIR}/{ENDED_JOBS_DIR}/*.json", Fate.RUN, JOB_ADAPTER),
    Kind("landing", _INSTALL, f"{JOBS_DIR}/{LANDING_FILE}", Fate.MACHINE, _HELD),
    Kind(
        "landed schema",
        _INSTALL,
        f"{JOBS_DIR}/{LANDED_SCHEMA_FILE}",
        Fate.MACHINE,
        Opaque("the landing's own stamp: one this build cannot read lands the root again"),
    ),
    Kind("admission lock", _INSTALL, f"{JOBS_DIR}/{ADMISSION_LOCK}", Fate.MACHINE, _HELD),
    Kind("producer lock", _INSTALL, f"{JOBS_DIR}/{PRODUCERS_DIR}/*.lock", Fate.MACHINE, _HELD),
    Kind("machine slot", _INSTALL, f"{JOBS_DIR}/{MACHINE_DIR}/*/*.lock", Fate.MACHINE, _HELD),
    Kind("account opening lock", _INSTALL, f"{PROJECTS_DIR}/{ACCOUNTS_LOCK}", Fate.MACHINE, _HELD),
    Kind(
        "sandbox owner",
        _INSTALL,
        f"{INNER_SANDBOXES_DIR}/*/{sandbox_owner_path(Path()).name}",
        Fate.RUN,
        TypeAdapter(SandboxOwner),
    ),
    Kind("readout pointer", _INSTALL, READOUT_POINTER, Fate.RUN, _FOR_A_PERSON),
)


def _authored(name: str, glob: str, shape: Shape) -> tuple[Kind, Kind]:
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
    Kind("charges", Scope.ACCOUNT, CHARGES_FILE, _SURVIVOR, TypeAdapter(ChargeLine)),
    Kind("bank", Scope.ACCOUNT, f"{BANKS_DIR}/*.json", Fate.CACHE, read_bank),
    # An upload definition's `source:` reads it.
    Kind(
        "upload", Scope.ACCOUNT, f"{UPLOADS_DIR}/**", _SURVIVOR, Opaque("an uploaded file's bytes")
    ),
    # A file two rows name takes the first one's shape.
    *_authored("dataset definition", f"{_DATASETS}/**/{DEFINITION_FILE}", definition_layer),
    *_authored("dataset framing", f"{_DATASETS}/**/{FRAMING_FILE}", dataset_framing),
    *_authored("dataset origin", f"{_DATASETS}/**/{ORIGIN_FILE}", origin_layer),
    *_authored("dataset file", f"{_DATASETS}/**", Opaque("a dataset's own file, its connector's")),
    *_authored("task context", f"{TASK_CONTEXT_DIR}/*.yaml", read_task_context),
    *_authored("bank labels", f"{BANKS_DIR}/{LABELS_FILE}", TypeAdapter(BankLabel)),
    *_authored("account block pack", _BLOCK_PACK, TypeAdapter(BlockPack)),
    *_authored("model capability override", CAPABILITY_FILE, _WALKED),
)


class Leaf(Enum):
    """Where a campaign file's bytes count in the storage report."""

    CONNECTOR = "connector"
    # A round checkout less its per-sample rows, which count as `CONNECTOR`.
    STATE = "state"
    TRACE = "trace"
    HISTORY = "history"
    REPORTS = "reports"
    UNCLASSIFIED = "unclassified"


class LeafInfo(NamedTuple):
    label: str
    description: str
    # In the loop's subtotal: what the loop itself wrote, beside the data it read and the backend's.
    loop: bool


LEAF_INFO: Worded[Leaf, LeafInfo] = Worded(
    {
        Leaf.CONNECTOR: LeafInfo(
            "Connector", "What the backend produced: node-I/O cache and per-sample arrays.", False
        ),
        Leaf.STATE: LeafInfo("State", "Round checkouts, less their per-sample rows.", True),
        Leaf.TRACE: LeafInfo("Trace", "Loop telemetry: locks, a detached launch's log.", True),
        Leaf.HISTORY: LeafInfo("History", "The loop's event spine: its ledger.", True),
        Leaf.REPORTS: LeafInfo(
            "Reports", "Readable output: manifest, reports, hard samples.", True
        ),
        Leaf.UNCLASSIFIED: LeafInfo(
            "Unclassified",
            "A file no data-root row names: no leaf claims it, and land-data refuses it.",
            False,
        ),
    }
)
assert LEAF_INFO.keys() == set(Leaf)


class CampaignRow(NamedTuple):
    """A file under a campaign or draft directory, its glob from there."""

    name: str
    glob: str
    leaf: Leaf
    shape: Shape
    # What `delete --keep-results` keeps.
    keepsake: bool = False


_CAMPAIGN_SHAPE: Shape = TypeAdapter(Campaign)
_DRAFT_SHAPE: Shape = TypeAdapter(DraftFile)
# What a run IS: one of these that does not load takes its campaign or draft with it.
_RUN_RECORDS = (RECORD_ADAPTER, _CAMPAIGN_SHAPE, _DRAFT_SHAPE)


def _export(raw: bytes) -> object:
    return parse_prompt_export(raw.decode())


def _declared_rows(where: str, at: Path, layout: CycleLayout | CampaignLayout) -> list[CampaignRow]:
    probe = CycleLayout(Path())
    shapes: dict[str, Shape] = {
        CampaignLayout(Path()).manifest.name: _CAMPAIGN_SHAPE,
        probe.export.name: _export,
    }
    return [
        CampaignRow(
            f"{where} {p.name}",
            p.as_posix(),
            Leaf.REPORTS,
            shapes.get(p.name, _FOR_A_PERSON),
            keepsake=True,
        )
        for p in declared_files(layout, at)
    ]


def _cycle_rows(where: str, cycle: Path) -> tuple[CampaignRow, ...]:
    """Every file a cycle directory holds, a campaign's or a check-in draft's."""
    layout = CycleLayout(cycle)
    return (
        *_declared_rows(where, cycle, layout),
        CampaignRow(
            f"{where} round", f"{layout.rounds.as_posix()}/{ROUND_GLOB}", Leaf.STATE, _FOR_A_PERSON
        ),
        CampaignRow(f"{where} ledger", layout.ledger.as_posix(), Leaf.HISTORY, RECORD_ADAPTER),
        CampaignRow(f"{where} producer lock", layout.producer_lock.as_posix(), Leaf.TRACE, _HELD),
        CampaignRow(f"{where} pass lock", layout.pass_lock.as_posix(), Leaf.TRACE, _HELD),
        CampaignRow(f"{where} job log", layout.job_log.as_posix(), Leaf.TRACE, _FOR_A_PERSON),
    )


def _layout_paths(layout: CycleLayout | CampaignLayout) -> set[str]:
    return {
        path.as_posix()
        for name, attr in vars(type(layout)).items()
        if isinstance(attr, property) and isinstance(path := getattr(layout, name), Path)
    }


CAMPAIGN_ROWS: tuple[CampaignRow, ...] = (
    *_declared_rows("campaign", Path(), CampaignLayout(Path())),
    *_cycle_rows("cycle", Path("cycles/*")),
)

# A row is a path its writer takes from the layout, so a row no writer can reach fails here: a
# glob spelled beside the layout is the row a deleted writer leaves behind.
_WRITTEN = _layout_paths(CampaignLayout(Path())) | _layout_paths(CycleLayout(Path("cycles/*")))
assert all(
    any(row.glob == path or row.glob.startswith(f"{path}/") for path in _WRITTEN)
    for row in CAMPAIGN_ROWS
), "a campaign row names a path no layout declares, so nothing writes it"


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
    Kind("draft", _WS, f"{_DRAFT}/{DRAFT_FILE}", Fate.RUN, _DRAFT_SHAPE),
    Kind(
        "draft resolution",
        _WS,
        f"{_DRAFT}/{RESOLUTION_FILE}",
        Fate.RUN,
        TypeAdapter(OriginResolution),
    ),
    # Retired by the hold `reset` takes on it, with the draft it claims.
    Kind("draft claim", _WS, f"{DRAFTS_DIR}/.*.claim", Fate.RUN, _HELD),
    Kind(
        "active session",
        _WS,
        f"{WORKSPACE_STATE_DIR}/active_session.json",
        Fate.RUN,
        TypeAdapter(CycleHop),
    ),
    Kind("workspace ledger", _WS, f"{WORKSPACE_STATE_DIR}/events.jsonl", Fate.RUN, RECORD_ADAPTER),
    # What was asked, of every subject: a loop's inbox and the trail read it, no survivor fact.
    Kind(
        "command log",
        _WS,
        f"{WORKSPACE_STATE_DIR}/{COMMAND_LOG_FILE}",
        Fate.RUN,
        COMMAND_LOG_ADAPTER,
    ),
    Kind(
        "head-to-head",
        _WS,
        f"{HEAD_TO_HEADS_DIR}/*.json",
        Fate.RUN,
        TypeAdapter(HeadToHeadRecord),
    ),
    Kind("ruler", _WS, RULER_FILE, Fate.RUN, TypeAdapter(RulerFold)),
    Kind("diagnostic", _WS, f"{DIAGNOSTICS_DIR}/*.json", Fate.RUN, _FOR_A_PERSON),
    Kind(
        "materialized panel",
        _WS,
        f"{MATERIALIZED_DIR}/*/{MATERIALIZED_PANEL_FILE}",
        Fate.RUN,
        MATERIALIZED_PANEL,
    ),
    Kind("materialized dataset", _WS, f"{MATERIALIZED_DIR}/**", Fate.RUN, _TREE),
    Kind("bank cuts", _WS, f"{BANKS_DIR}/{CUTS_FILE}", Fate.RUN, TypeAdapter(BankCut)),
    Kind("model capabilities", _WS, CAPABILITIES_CACHE, Fate.RUN, _WALKED),
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
    ArchiveFile.CLAIM_LOCK: _HELD,
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


def _retired(_raw: bytes) -> object:
    raise ValueError("no row names it: run data a retired writer left")


KINDS: tuple[Kind, ...] = (
    # `io.py` names every atomic write's temporary `.<random>.tmp`, beside the file it replaces:
    # first, so a crashed write under a survivor's tree is never read as the survivor.
    Kind("atomic write", _INSTALL, "**/.*.tmp", Fate.RUN, Opaque("a write a crash cut short")),
    *INSTALL_KINDS,
    *ACCOUNT_KINDS,
    *WORKSPACE_KINDS,
    *ARCHIVE_KINDS,
)

# Asked after every row and its write lock: a file under a campaign or a draft that nothing above
# names is run data a retired writer left, which no reader takes, so a landing drops it.
RETIRED_KINDS: tuple[Kind, ...] = (
    Kind("retired run file", _WS, "campaigns/*/**", Fate.RUN, _retired),
    Kind("retired draft file", _WS, f"{_DRAFT}/**", Fate.RUN, _retired),
    # Its claim moved onto the host's account (`migrations/host_account.py`).
    Kind(
        "retired host claim", _INSTALL, f"{IDENTITY_DIR}/default_claimed.json", Fate.RUN, _retired
    ),
)


def run_owner(rel: PurePosixPath) -> PurePosixPath | None:
    """The campaign or draft directory a file under a workspace lies in, an inner sandbox's too."""
    owner = 3 if rel.parts[0] == INNER_SANDBOXES_DIR else 2
    if len(rel.parts) <= owner + 2 or rel.parts[owner] not in ("campaigns", DRAFTS_DIR):
        return None
    return PurePosixPath(*rel.parts[: owner + 2])


def run_unit(rel: PurePosixPath, kind: Kind) -> PurePosixPath:
    """What a run file that does not load takes with it: a campaign's or a draft's record its
    campaign or draft whole, so no husk is left that no reader can open; any other file itself."""
    if kind.shape not in _RUN_RECORDS or kind.scope is not Scope.WORKSPACE:
        return rel
    return run_owner(rel) or rel


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


class Staged(NamedTuple):
    """What a landing writes in a file's place, proven before anything is written."""

    path: Path
    data: bytes
    said: str


def classify(rel: PurePosixPath) -> Found | None:
    """``rel`` is from the data root; ``None`` is a file no row names."""
    if named := next((k for k in KINDS if k.matches(rel)), None):
        return Found(named, lock=False)
    if locked := next((k for k in KINDS if k.locks(rel)), None):
        return Found(locked, lock=True)
    retired = next((k for k in RETIRED_KINDS if k.matches(rel)), None)
    return None if retired is None else Found(retired, lock=False)


def _check(shape: TypeAdapter[Any] | Callable[[bytes], object], kind: Kind, raw: bytes) -> None:
    if not isinstance(shape, TypeAdapter):
        shape(raw)
    elif kind.authored:
        shape.validate_python(parse_yaml(raw))
    else:
        shape.validate_json(raw)


def load(path: Path, found: Found, staged: bytes | None) -> None:
    """Raises what the file's own reader would; ``staged`` is what a landing writes in its place."""
    kind, shape = found.kind, found.kind.shape
    if found.lock or isinstance(shape, Opaque):
        return
    raw = read_bytes_optional(path) if staged is None else staged
    # Gone since the listing.
    if raw is None:
        return
    if not kind.per_line:
        _check(shape, kind, raw)
        return
    for line in complete_lines(BytesIO(raw)):
        if line.strip():
            _check(shape, kind, line)


# What a reader raises on a file it cannot take; a gzip cut short is an `EOFError`.
LOAD_ERRORS = (OSError, ValueError, TypeError, KeyError, EOFError)


def unloadable_why(path: Path, found: Found, *, staged: bytes | None) -> str | None:
    """Names where and why, never the refused value: a survivor's may be a key."""
    try:
        load(path, found, staged)
    except ValidationError as exc:
        return f"ValidationError: {validation_reason(exc, shown=3)}"
    except LOAD_ERRORS as exc:
        return " ".join(f"{type(exc).__name__}: {exc}".split())
    return None


def _schema(shape: Shape) -> object:
    if isinstance(shape, TypeAdapter):
        return shape.json_schema()
    if isinstance(shape, Opaque):
        return None
    return {"reader": f"{shape.__module__}::{shape.__qualname__}"}


def row_schema(kind: Kind) -> dict[str, Any]:
    return {"fate": kind.fate.value, "patterns": list(kind.patterns), "shape": _schema(kind.shape)}


def pinned_schemas() -> dict[str, Any]:
    """The shapes a release must keep loading, or rewrite on every box first."""
    return {
        kind.name: row_schema(kind) for kind in KINDS if kind.fate in (Fate.SURVIVOR, Fate.CACHE)
    }
