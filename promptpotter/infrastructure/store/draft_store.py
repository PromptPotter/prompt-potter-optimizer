from __future__ import annotations

from pathlib import Path

from promptpotter.domain.checkin_draft import DraftFile, OriginResolution
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.run_records import BankedSubject
from promptpotter.infrastructure.store.account_trail import bank_trail
from promptpotter.infrastructure.store.command_index import CommandIndex
from promptpotter.infrastructure.store.io import HeldLock, rmtree_robust, take_lock, write_bytes
from promptpotter.infrastructure.store.layout import (
    CycleLayout,
    draft_claim_path,
    draft_dir_for,
    draft_dirs,
    drafts_root_dir_for,
)
from promptpotter.infrastructure.store.read_model import file_sig, read_file
from promptpotter.shared.errors import ConflictError, NotFoundError

DRAFT_FILE = "draft.json"
RESOLUTION_FILE = "resolution.json"


class DraftClaim:
    """An OS lock, so a dead process holds none; ``release`` is idempotent for a refusal before the hold."""

    def __init__(self, draft_id: str, lock: HeldLock) -> None:
        self.draft_id = draft_id
        self._lock = lock

    def release(self) -> None:
        self._lock.retire()

    def __enter__(self) -> DraftClaim:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


class DraftStore:
    """A draft's one owner is its CLAIM: a write, a discard and a second Start refuse while it is
    held. *commands* is the account's command-id index, which every close and discard files into
    before the draft goes."""

    def __init__(self, base_dir: WorkspaceDir, commands: CommandIndex):
        self._base_dir = base_dir
        self._commands = commands

    @classmethod
    def of_account(cls, account: WorkspaceDir) -> DraftStore:
        """An account's own drafts: its workspace holds its index."""
        return cls(account, CommandIndex(account))

    def draft_dir(self, draft_id: str) -> Path:
        return draft_dir_for(self._base_dir, draft_id)

    def ledger(self, draft_id: str) -> Path:
        return CycleLayout(self.draft_dir(draft_id)).ledger

    def draft_ids(self) -> list[str]:
        return [p.name for p in draft_dirs(self._base_dir) if (p / DRAFT_FILE).is_file()]

    def version(self) -> tuple[object, ...]:
        """Stats only: moves on an open, an edit, a check-in's spend, a Start and a discard."""
        return (
            file_sig(drafts_root_dir_for(self._base_dir)),
            *(
                (p.name, file_sig(p / DRAFT_FILE), file_sig(CycleLayout(p).ledger))
                for p in draft_dirs(self._base_dir)
            ),
        )

    def hold(self, draft_id: str) -> DraftClaim:
        lock = take_lock(draft_claim_path(self._base_dir, draft_id), wait_s=0)
        if lock is None:
            raise ConflictError(
                f"draft {draft_id!r} is held by a Start or a command on it; retry once that ends.",
                code="draft_held",
            )
        return DraftClaim(draft_id, lock)

    def claim(self, draft_id: str) -> DraftClaim:
        """Taken first, then the draft asked for: a discard that won the race leaves nothing to mint."""
        claim = self.hold(draft_id)
        if self.read_draft(draft_id) is None:
            claim.release()
            raise NotFoundError(f"draft {draft_id!r} not found.", code="command_target_not_found")
        return claim

    def write_draft(self, draft_id: str, draft: DraftFile) -> Path:
        path = self.draft_dir(draft_id) / DRAFT_FILE
        write_bytes(path, draft.model_dump_json(indent=2).encode("utf-8"))
        return path

    def read_draft(self, draft_id: str) -> DraftFile | None:
        return read_file(self.draft_dir(draft_id) / DRAFT_FILE, DraftFile.model_validate_json)

    def write_resolution(self, draft_id: str, resolution: OriginResolution) -> None:
        path = self.draft_dir(draft_id) / RESOLUTION_FILE
        write_bytes(path, resolution.model_dump_json(indent=2).encode("utf-8"))

    def load_resolution(self, draft_id: str) -> OriginResolution | None:
        path = self.draft_dir(draft_id) / RESOLUTION_FILE
        return read_file(path, OriginResolution.model_validate_json)

    def close(self, draft_id: str) -> None:
        """After a mint moved its ledger out, by whoever holds the draft: a working copy nothing
        reads. A destroyer too: what it files reads deleted unless its ledger moved before."""
        draft_dir = self.draft_dir(draft_id)
        self._commands.removing(draft_dir)
        rmtree_robust(draft_dir)

    def discard(self, claim: DraftClaim) -> None:
        """A destroyer, under the claim it releases: what was commanded on it outlives it, under its id."""
        with claim:
            bank_trail(
                workspace=self._base_dir,
                cycle_dirs=[self.draft_dir(claim.draft_id)],
                subject=BankedSubject(draft_id=claim.draft_id),
            )
            self.close(claim.draft_id)
