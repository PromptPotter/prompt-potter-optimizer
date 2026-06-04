"""Per-cycle ``index.json`` CRUD — create, update, rewind, enumerate."""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from promptpotter.config.settings import DEFAULT_CONNECTOR_TYPE
from promptpotter.infrastructure.runtime_flags import derive_run_phase
from promptpotter.infrastructure.store.base import read_json, read_json_optional, write_json
from promptpotter.infrastructure.store.campaign_store._kernel import CampaignStoreKernel
from promptpotter.infrastructure.store.campaign_store.index_helpers import round_summary
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_ledger_max_round_complete,
)
from promptpotter.infrastructure.store.paths import root_cycle_id, sibling_kind
from promptpotter.shared.clock import utcnow_iso

logger = logging.getLogger(__name__)


def _rmtree_robust(path: Path) -> None:
    """``shutil.rmtree`` with Windows-isms — long-path prefix, chmod-on-PermissionError, backoff."""
    target_str = str(path.resolve())
    if os.name == "nt" and not target_str.startswith("\\\\?\\"):
        target_str = "\\\\?\\" + target_str

    def _onexc(func: Callable[[str], object], target: str, exc: BaseException) -> None:
        if isinstance(exc, PermissionError):
            try:
                os.chmod(target, stat.S_IWRITE)
                func(target)
                return
            except OSError:
                pass
        raise exc

    for attempt in range(4):
        try:
            shutil.rmtree(target_str, onexc=_onexc)
            return
        except OSError as exc:
            if attempt == 3:
                raise
            logger.debug("rmtree retry %d for %s after %s", attempt + 1, path, exc)
            time.sleep(0.1 * (attempt + 1))


def _unit_kind(sibling_kind: str, fork_trigger: str | None) -> str:
    """Sidebar unit kind ∈ {``session``, ``divergent_resume``, ``user_fork``, ``l3_fork``}."""
    if sibling_kind == "root":
        return "session"
    if fork_trigger == "scoring_divergence":
        return "divergent_resume"
    if fork_trigger and fork_trigger.startswith("l3"):
        return "l3_fork"
    return "user_fork"


class CycleIndexMixin(CampaignStoreKernel):
    """Per-cycle ``index.json`` CRUD, rewind, and cross-campaign enumeration."""

    def load(self, campaign_id: str, cycle_id: str) -> dict[str, Any] | None:
        """Load a cycle's ``index.json``; ``cycle_id`` injected from dir name."""
        data: dict[str, Any] | None = read_json_optional(self._index_path(campaign_id, cycle_id))
        if data is None:
            return None
        data["cycle_id"] = cycle_id
        return data

    def create(
        self,
        campaign_id: str,
        cycle_id: str,
        metadata: dict[str, Any],
    ) -> Path:
        """Create/augment cycle ``index.json``; replay merges keys without clobbering rounds/best."""
        path = self._index_path(campaign_id, cycle_id)
        existing = read_json_optional(path) or {}
        # Identity is the directory name, never a stored field. Strip both
        # ids on every write so the dir name stays the sole source of truth
        # (self-heals any index.json minted under an older scheme).
        existing.pop("cycle_id", None)
        existing.pop("campaign_id", None)
        now = utcnow_iso()
        defaults: dict[str, Any] = {
            "created_at": existing.get("created_at", now),
            "updated_at": now,
            "status": "active",
            "type": "optimization_loop",
            "connector_type": metadata.get("connector_type", DEFAULT_CONNECTOR_TYPE),
            "parent_session_id": existing.get("parent_session_id", ""),
            "parent_cycle_id": None,
            "sibling_kind": sibling_kind(cycle_id),
            "n_rounds": 0,
            "best_accuracy": 0.0,
            "best_round_id": None,
            "origin_accuracy": 0.0,
            "rounds": [],
        }
        data = {**defaults, **existing, **metadata}
        data["updated_at"] = now
        write_json(path, data)
        return path

    def update(
        self,
        campaign_id: str,
        cycle_id: str,
        updates: dict[str, Any],
        *,
        remove: Sequence[str] = (),
    ) -> None:
        """Merge updates into ``index.json`` and write back (+ timestamp)."""
        path = self._index_path(campaign_id, cycle_id)
        data = read_json(path)
        for key in remove:
            data.pop(key, None)
        data.update(updates)
        data["updated_at"] = utcnow_iso()
        write_json(path, data)

    def rewind_to_round(
        self,
        campaign_id: str,
        cycle_id: str,
        after_round: int,
    ) -> None:
        """Archive round + candidate files for rounds > ``after_round`` into ``.runtime/archived/resumed_at_<ts>/``; rebuild the round index. Ledger-admissibility-gated."""
        cycle_dir = self.cycle_dir(campaign_id, cycle_id)
        rounds_dir = self._rounds_dir(campaign_id, cycle_id)
        candidates_dir = self._candidates_dir(campaign_id, cycle_id)

        ledger_path = cycle_dir / ".runtime" / "ledger.jsonl"
        if not ledger_path.exists():
            raise LookupError(f"cycle {cycle_id!r} has no ledger on disk")
        max_complete = scan_ledger_max_round_complete(ledger_path)
        if after_round > max_complete:
            raise LookupError(
                f"--from {after_round}: ledger only has completed rounds 0..{max_complete}"
            )

        if after_round >= 1:
            if not rounds_dir.exists():
                raise LookupError(
                    f"cycle {cycle_id!r}: ledger has rounds 0..{max_complete} but "
                    f"{rounds_dir} is missing — projection cache out of sync with ledger"
                )
            target = rounds_dir / f"round_{after_round:04d}.json"
            if not target.exists():
                raise LookupError(
                    f"--from {after_round}: round_{after_round:04d}.json not found in "
                    f"{rounds_dir} (ledger has completed rounds 0..{max_complete} — "
                    "projection cache out of sync)"
                )

        survivors: list[Path] = []
        to_archive_rounds: list[Path] = []
        to_archive_candidates: list[Path] = []
        for p in sorted(rounds_dir.glob("round_*.json")):
            try:
                n = int(p.stem.removeprefix("round_"))
            except ValueError:
                continue
            (to_archive_rounds if n > after_round else survivors).append(p)
        if candidates_dir.exists():
            for p in sorted(candidates_dir.glob("round_*.json")):
                try:
                    n = int(p.stem.removeprefix("round_"))
                except ValueError:
                    continue
                if n > after_round:
                    to_archive_candidates.append(p)

        archived_count = len(to_archive_rounds) + len(to_archive_candidates)
        if archived_count:
            ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            archive_root = cycle_dir / ".runtime" / "archived" / f"resumed_at_{ts}"
            if to_archive_rounds:
                (archive_root / "rounds").mkdir(parents=True, exist_ok=True)
                for p in to_archive_rounds:
                    p.rename(archive_root / "rounds" / p.name)
            if to_archive_candidates:
                (archive_root / "candidates").mkdir(parents=True, exist_ok=True)
                for p in to_archive_candidates:
                    p.rename(archive_root / "candidates" / p.name)
            logger.info(
                "Rewind cycle %s to round %d: archived %d file(s) → %s",
                cycle_id,
                after_round,
                archived_count,
                archive_root,
            )

        self._rebuild_round_index(campaign_id, cycle_id, survivors)

    def _rebuild_round_index(
        self,
        campaign_id: str,
        cycle_id: str,
        survivors: list[Path],
    ) -> None:
        index_path = self._index_path(campaign_id, cycle_id)
        data = read_json(index_path)

        rebuilt = [round_summary(read_json(p)) for p in sorted(survivors)]
        best = max(rebuilt, key=lambda s: s["accuracy"], default=None)

        data["rounds"] = rebuilt
        data["n_rounds"] = len(rebuilt)
        data["best_accuracy"] = best["accuracy"] if best else 0.0
        data["best_round_id"] = best["round_id"] if best else None
        data["updated_at"] = utcnow_iso()
        write_json(index_path, data)

    def mark_finished(
        self,
        campaign_id: str,
        cycle_id: str,
        *,
        status: str,
        stop_reason: str,
        best_accuracy: float,
        best_round: int,
        n_rounds: int,
        finished_at: str,
        interrupted_round: int | None = None,
        crash_traceback: str | None = None,
        final: dict[str, Any] | None = None,
    ) -> None:
        """Write terminal status/stop_reason + outcome summary to the cycle index."""
        from promptpotter.shared.errors import graceful

        # Monotonic round count — a degenerate finalize (n_rounds=0) must not regress real rounds.
        existing = read_json_optional(self._index_path(campaign_id, cycle_id)) or {}
        no_advance = n_rounds < int(existing.get("n_rounds") or 0)

        updates: dict[str, Any] = {
            "status": status,
            "stop_reason": stop_reason,
            "finished_at": finished_at,
        }
        if not no_advance:
            updates["best_accuracy"] = best_accuracy
            updates["best_round"] = best_round
            updates["n_rounds"] = n_rounds
            if final is not None:
                updates["final"] = final
        # Store partial-round / traceback markers based on what the caller computed
        # (halted_mid_round → interrupted_round; has_traceback → crash_traceback),
        # not by re-deriving from the status string — status is now the precise
        # StopReason value, decoupled from this storage decision.
        remove_keys: list[str] = []
        if interrupted_round is not None:
            updates["interrupted_round"] = interrupted_round
        else:
            remove_keys.append("interrupted_round")
        if crash_traceback:
            updates["crash_traceback"] = crash_traceback
        else:
            remove_keys.append("crash_traceback")
        with graceful("Cycle completion update failed"):
            self.update(campaign_id, cycle_id, updates, remove=remove_keys)

    def list_all(self, backend_id: str) -> list[dict[str, Any]]:
        results = []
        for index_path in self._index_files():
            data = read_json(index_path)
            header = data.get("header") if isinstance(data.get("header"), dict) else {}
            row_backend = header.get("backend_id", "")
            if backend_id and row_backend and row_backend != backend_id:
                continue
            campaign_id, cycle_id = self._ids_from_index_path(index_path)
            results.append(
                {
                    "campaign_id": campaign_id,
                    "cycle_id": cycle_id,
                    "name": data.get("name", ""),
                    "status": data["status"],
                    "n_rounds": data["n_rounds"],
                    "best_accuracy": data["best_accuracy"],
                    "origin_accuracy": data["origin_accuracy"],
                    "created_at": data["created_at"],
                    "updated_at": data["updated_at"],
                    "parent_session_id": data.get("parent_session_id", ""),
                }
            )
        return results

    def enumerate_cycles(self) -> list[dict[str, Any]]:
        """Every cycle on disk in the webapp-picker shape; unreadable → ``status='unreadable'`` stubs."""
        results: list[dict[str, Any]] = []
        for index_path in self._index_files():
            campaign_id, cycle_id = self._ids_from_index_path(index_path)
            try:
                data = read_json_optional(index_path)
            except Exception:
                data = None
            kind = sibling_kind(cycle_id)
            # The single run-phase derivation — running / paused / stopping /
            # detached / terminal. Lifecycle (terminal) comes from index
            # ``finished_at``; control + freshness from derive_run_phase. This is
            # the one computation the picker, both live dots, and the badge all
            # read — no surface re-derives "running" from its own inputs.
            cycle_dir = index_path.parent
            is_terminal = isinstance(data, dict) and bool(data.get("finished_at"))
            run_phase = str(derive_run_phase(cycle_dir, is_terminal=is_terminal))
            if not isinstance(data, dict):
                results.append(
                    {
                        "campaign_id": campaign_id,
                        "cycle_id": cycle_id,
                        "parent_session_id": "",
                        "parent_cycle_id": (None if kind == "root" else root_cycle_id(cycle_id)),
                        "dataset_name": "",
                        "backend_id": "",
                        "sibling_kind": kind,
                        "unit_kind": _unit_kind(kind, None),
                        "is_root": kind == "root",
                        "status": "unreadable",
                        "run_phase": run_phase,
                        "best_accuracy": None,
                        "n_rounds": 0,
                        "created_at": "",
                        "updated_at": "",
                    }
                )
                continue
            header_raw = data.get("header")
            header: dict[str, Any] = header_raw if isinstance(header_raw, dict) else {}
            sk = data.get("sibling_kind", kind)
            fork_raw = data.get("fork")
            fork_trigger = fork_raw.get("trigger") if isinstance(fork_raw, dict) else None
            results.append(
                {
                    "campaign_id": campaign_id,
                    "cycle_id": cycle_id,
                    "parent_session_id": data.get("parent_session_id", ""),
                    "parent_cycle_id": data.get("parent_cycle_id")
                    or (None if kind == "root" else root_cycle_id(cycle_id)),
                    "dataset_name": header.get("dataset_name", ""),
                    "backend_id": header.get("backend_id", ""),
                    "sibling_kind": sk,
                    "unit_kind": _unit_kind(sk, fork_trigger),
                    "is_root": kind == "root",
                    "status": data.get("status", ""),
                    "run_phase": run_phase,
                    "best_accuracy": data.get("best_accuracy"),
                    "n_rounds": data.get("n_rounds", 0),
                    "created_at": data.get("created_at", ""),
                    "updated_at": data.get("updated_at", ""),
                }
            )
        # Backfill dataset_name onto siblings from their campaign manifest.
        for e in results:
            if not e["dataset_name"]:
                campaign = self.load_campaign(e["campaign_id"])
                if campaign is not None:
                    e["dataset_name"] = campaign.dataset_name
        return results

    def try_delete_stub_cycle(self, campaign_id: str, cycle_id: str) -> tuple[bool, str]:
        """Delete a stub cycle dir → ``(deleted, reason)``. Guards: ``n_rounds == 0``, not a family root, no children."""
        cycle_dir = self.cycle_dir(campaign_id, cycle_id)
        index_path = cycle_dir / "index.json"
        if not index_path.is_file():
            return False, "not on disk"
        if root_cycle_id(cycle_id) == cycle_id:
            return False, "family root — deletion is for sibling stubs only"
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return False, f"index.json unreadable: {exc}"
        n_rounds = index.get("n_rounds", 0)
        if not isinstance(n_rounds, int) or n_rounds != 0:
            return False, f"n_rounds={n_rounds} — cycle ran real work"
        for other in self._index_files():
            other_campaign, other_cycle = self._ids_from_index_path(other)
            if other_campaign != campaign_id or other_cycle == cycle_id:
                continue
            other_data = read_json_optional(other)
            if isinstance(other_data, dict) and other_data.get("parent_cycle_id") == cycle_id:
                return False, f"has descendant {other_cycle}"
        _rmtree_robust(cycle_dir)
        return True, ""
