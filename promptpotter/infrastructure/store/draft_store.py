from __future__ import annotations

from pathlib import Path
from typing import Any

from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.run_records import BankedSubject
from promptpotter.infrastructure.store.account_trail import bank_trail
from promptpotter.infrastructure.store.io import (
    HeldLock,
    read_json_optional,
    rmtree_robust,
    take_lock,
    write_json,
)
from promptpotter.infrastructure.store.layout import (
    CycleLayout,
    draft_claim_path,
    draft_dir_for,
    draft_dirs,
    drafts_root_dir_for,
)
from promptpotter.infrastructure.store.read_model import file_sig
from promptpotter.shared.errors import ConflictError, NotFoundError


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
    """A draft's one owner is its CLAIM: a write, a discard and a second Start refuse while it is held."""

    def __init__(self, base_dir: WorkspaceDir):
        self._base_dir = base_dir

    def draft_dir(self, draft_id: str) -> Path:
        return draft_dir_for(self._base_dir, draft_id)

    def ledger(self, draft_id: str) -> Path:
        return CycleLayout(self.draft_dir(draft_id)).ledger

    def draft_ids(self) -> list[str]:
        return [p.name for p in draft_dirs(self._base_dir) if (p / "draft.json").is_file()]

    def version(self) -> tuple[object, ...]:
        """Stats only: moves on an open, an edit, a check-in's spend, a Start and a discard."""
        return (
            file_sig(drafts_root_dir_for(self._base_dir)),
            *(
                (p.name, file_sig(p / "draft.json"), file_sig(CycleLayout(p).ledger))
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

    def write_draft(self, draft_id: str, draft: dict[str, Any]) -> Path:
        path = self.draft_dir(draft_id) / "draft.json"
        write_json(path, draft)
        return path

    def read_draft(self, draft_id: str) -> dict[str, Any] | None:
        return read_json_optional(self.draft_dir(draft_id) / "draft.json")

    def _patch_checkin(self, draft_id: str, **fields: Any) -> None:
        path = self.draft_dir(draft_id) / "checkin.json"
        write_json(path, {**(read_json_optional(path) or {}), **fields})

    def _checkin_field(self, draft_id: str, key: str) -> Any:
        return (read_json_optional(self.draft_dir(draft_id) / "checkin.json") or {}).get(key)

    def write_upload(self, draft_id: str, upload: str) -> None:
        """The kept file's name under ``TenantDatasetStore.upload_path``."""
        self._patch_checkin(draft_id, upload=upload)

    def upload_of(self, draft_id: str) -> str | None:
        upload = self._checkin_field(draft_id, "upload")
        return upload if isinstance(upload, str) and upload else None

    def write_resolution(self, draft_id: str, resolution: dict[str, Any]) -> None:
        self._patch_checkin(draft_id, resolution=resolution)

    def load_resolution(self, draft_id: str) -> dict[str, Any] | None:
        resolution = self._checkin_field(draft_id, "resolution")
        return resolution if isinstance(resolution, dict) else None

    def close(self, draft_id: str) -> None:
        """After a mint moved its ledger out, by whoever holds the draft: a working copy nothing reads."""
        rmtree_robust(self.draft_dir(draft_id))

    def discard(self, claim: DraftClaim) -> None:
        """A destroyer, under the claim it releases: what was commanded on it outlives it, under its id."""
        with claim:
            draft_dir = self.draft_dir(claim.draft_id)
            bank_trail(
                workspace=self._base_dir,
                cycle_dirs=[draft_dir],
                subject=BankedSubject(draft_id=claim.draft_id),
            )
            rmtree_robust(draft_dir)
