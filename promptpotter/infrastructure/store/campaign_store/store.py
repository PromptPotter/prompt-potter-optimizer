"""Campaign + cycle artifacts under ``campaigns/{campaign_id}/``.

A campaign is a **forest**: it holds N *session* roots (one per ``new`` on
the same origin declaration) plus their fork descendants, all flat under
``cycles/``. :meth:`list_sessions` enumerates the forest roots.

Two surfaces:

* **Campaign-level** — ``campaign.json`` manifest CRUD
  (:meth:`create_campaign` / :meth:`load_campaign` / :meth:`update_campaign`
  / :meth:`list_campaigns`). The campaign owns the frozen ``CampaignConfig``
  snapshot and the forest's identity (``campaign_id = {dataset}__{hash}``).
* **Cycle-level** — per-cycle ``index.json`` + round/candidate files under
  ``campaigns/{campaign_id}/cycles/{cycle_id}/``. Every cycle method takes a
  leading ``campaign_id`` because a cycle id is unique only within its
  campaign — the path spine is ``(campaign_id, cycle_id)``.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from promptpotter.config.settings import DEFAULT_CONNECTOR_TYPE
from promptpotter.domain.campaign import Campaign
from promptpotter.infrastructure.store.base import (
    EntityStore,
    read_json,
    read_json_optional,
    validate_path_component,
    write_json,
)
from promptpotter.infrastructure.store.campaign_store.index_helpers import (
    fresh_sibling_index_blob,
    round_summary,
)
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_ledger_max_round_complete,
)
from promptpotter.infrastructure.store.paths import (
    campaign_root_dir_for,
    cycle_dir_for,
    root_cycle_id,
    session_index,
    sibling_kind,
)

logger = logging.getLogger(__name__)


def _rmtree_robust(path: Path) -> None:
    """``shutil.rmtree`` that survives Windows-isms.

    Three failure modes handled: long paths (>260 chars) via the ``\\\\?\\``
    prefix, read-only files (chmod + retry), and transient handles
    (backoff + retry). Linux/macOS take the same path; the prefix is a no-op.
    """
    target_str = str(path.resolve())
    if os.name == "nt" and not target_str.startswith("\\\\?\\"):
        target_str = "\\\\?\\" + target_str

    def _onexc(func, target, exc):
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
    """Operator-facing unit kind — the time-horizon taxonomy for the sidebar.

    Folds the on-disk ``(sibling_kind, fork trigger)`` into four kinds that
    align with how an operator remembers their work over time:

    - ``session`` — the root run; ``resume`` (incl. Ctrl+C → resume)
      extends it, never branches.
    - ``divergent_resume`` — a ``resume --fork-on-divergence`` branch.
    - ``user_fork`` — any operator-initiated branch: a HITL fork, a
      diagnostic-BFS sibling, or a sweep-batch fork.
    - ``l3_fork`` — reserved for L3 auto-forking. No on-disk trigger emits
      it yet (L3 fork-proposals are observation-only today), so it never
      appears until that ships.
    """
    if sibling_kind == "root":
        return "session"
    if fork_trigger == "scoring_divergence":
        return "divergent_resume"
    if fork_trigger and fork_trigger.startswith("l3"):
        return "l3_fork"
    return "user_fork"


class CampaignStore(EntityStore):
    """File I/O for the ``campaigns/{campaign_id}/`` tree."""

    def __init__(self, base_dir: Path):
        # base_dir is tenant root; campaigns nest directly under it
        super().__init__(base_dir, "campaigns")

    # -- path helpers ---------------------------------------------------------

    def campaign_root_dir(self, campaign_id: str) -> Path:
        """Campaign dir — ``campaign.json`` / ``log.md`` / ``cycles/`` (the forest)."""
        return campaign_root_dir_for(self._base_dir, campaign_id)

    def cycle_dir(self, campaign_id: str, cycle_id: str) -> Path:
        """Per-cycle dir — ``campaigns/{campaign_id}/cycles/{cycle_id}``."""
        return cycle_dir_for(self._base_dir, campaign_id, cycle_id)

    def _manifest_path(self, campaign_id: str) -> Path:
        return self.campaign_root_dir(campaign_id) / "campaign.json"

    def _index_path(self, campaign_id: str, cycle_id: str) -> Path:
        return self.cycle_dir(campaign_id, cycle_id) / "index.json"

    def _rounds_dir(self, campaign_id: str, cycle_id: str) -> Path:
        return self.cycle_dir(campaign_id, cycle_id) / "rounds"

    def _candidates_dir(self, campaign_id: str, cycle_id: str) -> Path:
        return self.cycle_dir(campaign_id, cycle_id) / ".runtime" / "cache" / "candidates"

    # -- Campaign manifest CRUD ----------------------------------------------

    def create_campaign(self, campaign: Campaign) -> Path:
        """Write the ``campaign.json`` manifest. The single config-snapshot writer."""
        path = self._manifest_path(campaign.campaign_id)
        write_json(path, campaign.model_dump(mode="json"))
        return path

    def load_campaign(self, campaign_id: str) -> Campaign | None:
        """Load the ``campaign.json`` manifest; ``None`` when absent."""
        data = read_json_optional(self._manifest_path(campaign_id))
        if data is None:
            return None
        return Campaign.model_validate(data)

    def update_campaign(self, campaign_id: str, updates: dict[str, Any]) -> None:
        """Merge *updates* into ``campaign.json`` and write back."""
        path = self._manifest_path(campaign_id)
        data = read_json(path)
        data.update(updates)
        write_json(path, data)

    def list_campaign_ids(self) -> list[str]:
        """Every campaign id on disk (dir with a ``campaign.json``), sorted."""
        campaigns_dir = self._base_dir / "campaigns"
        if not campaigns_dir.exists():
            return []
        return sorted(
            p.name
            for p in campaigns_dir.iterdir()
            if p.is_dir() and (p / "campaign.json").is_file()
        )

    def list_campaigns(self, dataset_name: str | None = None) -> list[Campaign]:
        """Every campaign manifest, optionally filtered to one dataset."""
        out: list[Campaign] = []
        for cid in self.list_campaign_ids():
            campaign = self.load_campaign(cid)
            if campaign is None:
                continue
            if dataset_name and campaign.dataset_name != dataset_name:
                continue
            out.append(campaign)
        return out

    def mark_campaign_finished(self, campaign_id: str, *, status: str, finished_at: str) -> None:
        """Stamp the terminal ``status`` + ``finished_at`` onto ``campaign.json``.

        ``status`` reflects the campaign's most-recent session — a fresh
        ``new`` reactivates the campaign (see ``auto_mint_session``)."""
        if self.load_campaign(campaign_id) is None:
            return
        self.update_campaign(campaign_id, {"status": status, "finished_at": finished_at})

    def list_sessions(self, campaign_id: str) -> list[str]:
        """Every session-root cycle id in the campaign's forest, ordered by
        session index.

        A session root is a cycle that is its own family root — no
        ``_fork_``/``_diag_``/``_sweep_`` separator. The bare ``cycle_{hash}``
        is session 1; ``cycle_{hash}_s{N}`` is the Nth ``new`` re-run of the
        same origin declaration.
        """
        cycles_dir = self.campaign_root_dir(campaign_id) / "cycles"
        if not cycles_dir.exists():
            return []
        roots = [
            p.name
            for p in cycles_dir.iterdir()
            if p.is_dir() and (p / "index.json").is_file() and root_cycle_id(p.name) == p.name
        ]
        return sorted(roots, key=session_index)

    def next_session_index(self, campaign_id: str) -> int:
        """Index for the next session minted into this campaign (1 if empty)."""
        sessions = self.list_sessions(campaign_id)
        if not sessions:
            return 1
        return max(session_index(s) for s in sessions) + 1

    # -- Cycle index CRUD -----------------------------------------------------

    def load(self, campaign_id: str, cycle_id: str) -> dict[str, Any] | None:
        """Load a cycle's ``index.json``; ``cycle_id`` is injected from the dir name."""
        data = read_json_optional(self._index_path(campaign_id, cycle_id))
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
        """Create/augment a cycle's ``index.json``.

        Fresh write fills defaults; replay merges new keys without
        clobbering ``rounds``/``best_*`` accumulators.
        """
        path = self._index_path(campaign_id, cycle_id)
        existing = read_json_optional(path) or {}
        existing.pop("cycle_id", None)
        now = datetime.now(UTC).isoformat()
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
        """Merge *updates* into the cycle ``index.json`` and write back (+ timestamp)."""
        path = self._index_path(campaign_id, cycle_id)
        data = read_json(path)
        for key in remove:
            data.pop(key, None)
        data.update(updates)
        data["updated_at"] = datetime.now(UTC).isoformat()
        write_json(path, data)

    def rewind_to_round(
        self,
        campaign_id: str,
        cycle_id: str,
        after_round: int,
    ) -> None:
        """Archive round_data/candidate files for rounds > ``after_round``.

        Moves ``rounds/round_{M:04d}.json`` and the matching candidate files
        for M > after_round into ``.runtime/archived/resumed_at_<ts>/``, then
        rebuilds the cycle's round index. Admissibility consults the ledger.
        """
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
        """Recompute ``rounds`` / ``n_rounds`` / ``best_*`` from surviving detail files."""
        index_path = self._index_path(campaign_id, cycle_id)
        data = read_json(index_path)

        rebuilt = [round_summary(read_json(p)) for p in sorted(survivors)]
        best = max(rebuilt, key=lambda s: s["accuracy"], default=None)

        data["rounds"] = rebuilt
        data["n_rounds"] = len(rebuilt)
        data["best_accuracy"] = best["accuracy"] if best else 0.0
        data["best_round_id"] = best["round_id"] if best else None
        data["updated_at"] = datetime.now(UTC).isoformat()
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
        """Write the terminal status/stop_reason + outcome summary to the cycle index.

        ``final`` is the terminal-summary namespace (``index.json::final``):
        the frozen verdict the ``potter-l1-meta-campaign`` skill, ``review.md``
        and the leaderboard read after a cycle ends. The caller assembles it
        (it needs the optimizer prompt hashes, an application-layer fact);
        this method only persists it.
        """
        from promptpotter.shared.errors import graceful

        # Round count is monotonic. A degenerate finalize — a resume that
        # diverged or crashed during init, before a Cycle was built — carries
        # n_rounds=0 / best_accuracy=0.0; it must not regress a cycle that
        # already completed real rounds. On no-advance, only status /
        # stop_reason / finished_at reflect this run; the outcome summary and
        # the frozen `final` block are kept from disk.
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
        remove_keys: list[str] = []
        if status in {"interrupted", "crashed"}:
            if interrupted_round is not None:
                updates["interrupted_round"] = interrupted_round
            if crash_traceback:
                updates["crash_traceback"] = crash_traceback
        else:
            remove_keys = ["interrupted_round", "crash_traceback"]
        with graceful("Cycle completion update failed"):
            self.update(campaign_id, cycle_id, updates, remove=remove_keys)

    def _index_files(self) -> list[Path]:
        """Every cycle ``index.json`` under this tenant — ``campaigns/*/cycles/*/index.json``."""
        campaigns_dir = self._base_dir / "campaigns"
        if not campaigns_dir.exists():
            return []
        return sorted(campaigns_dir.glob("*/cycles/*/index.json"))

    @staticmethod
    def _ids_from_index_path(index_path: Path) -> tuple[str, str]:
        """``(campaign_id, cycle_id)`` for a ``campaigns/{c}/cycles/{cy}/index.json`` path."""
        cycle_id = index_path.parent.name
        campaign_id = index_path.parent.parent.parent.name
        return campaign_id, cycle_id

    def list_all(self, backend_id: str) -> list[dict[str, Any]]:
        """Summary row for every cycle on disk, optionally filtered by backend."""
        results = []
        for index_path in self._index_files():
            data = read_json(index_path)
            header = data.get("header") if isinstance(data.get("header"), dict) else {}
            row_backend = data.get("backend_id") or header.get("backend_id", "")
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
        """Every cycle on disk, with the richer shape the webapp picker needs.

        Tolerant of corrupt ``index.json``: a cycle with an unreadable index
        contributes a stub entry with ``status="unreadable"``.
        """
        results: list[dict[str, Any]] = []
        for index_path in self._index_files():
            campaign_id, cycle_id = self._ids_from_index_path(index_path)
            try:
                data = read_json_optional(index_path)
            except Exception:
                data = None
            kind = sibling_kind(cycle_id)
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
                    "backend_id": data.get("backend_id") or header.get("backend_id", ""),
                    "sibling_kind": sk,
                    "unit_kind": _unit_kind(sk, fork_trigger),
                    "is_root": kind == "root",
                    "status": data.get("status", ""),
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
        """Delete a stub cycle dir; return ``(deleted, reason)``.

        Guards: dir exists, ``n_rounds == 0``, cycle is not a family root,
        and no other cycle in the campaign names it as ``parent_cycle_id``.
        """
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

    # -- Fork helpers ---------------------------------------------------------

    def save_divergence_fork(
        self,
        campaign_id: str,
        parent_cycle_id: str,
        new_cycle_id: str,
        *,
        surviving_rounds: list[dict[str, Any]],
        forked_at: str,
        forked_from_round: int,
    ) -> Path:
        """Divergence-fork ``index.json`` inheriting parent state (same campaign)."""
        parent_index = read_json_optional(self._index_path(campaign_id, parent_cycle_id)) or {}
        best_acc = max(
            (float(t.get("accuracy", 0.0)) for t in surviving_rounds),
            default=0.0,
        )
        best_round_id = next(
            (
                t.get("round_id")
                for t in surviving_rounds
                if float(t.get("accuracy", 0.0)) == best_acc
            ),
            None,
        )
        index = {
            **parent_index,
            "parent_cycle_id": parent_cycle_id,
            "sibling_kind": "fork",
            "forked_from_round": forked_from_round,
            "forked_at": forked_at,
            "rounds": list(surviving_rounds),
            "n_rounds": len(surviving_rounds),
            "best_accuracy": best_acc,
            "best_round_id": best_round_id,
            "status": "resumed",
            "updated_at": forked_at,
        }
        index.pop("cycle_id", None)
        path = self._index_path(campaign_id, new_cycle_id)
        write_json(path, index)
        return path

    def save_diag_fork(
        self,
        campaign_id: str,
        parent_cycle_id: str,
        new_cycle_id: str,
        *,
        forked_at: str,
    ) -> Path:
        """Clean-slate diag-sibling ``index.json``."""
        parent_index = read_json_optional(self._index_path(campaign_id, parent_cycle_id)) or {}
        blob = fresh_sibling_index_blob(parent_index, parent_cycle_id, "diag", forked_at)
        path = self._index_path(campaign_id, new_cycle_id)
        write_json(path, blob)
        return path

    def save_sweep_fork(
        self,
        campaign_id: str,
        parent_cycle_id: str,
        new_cycle_id: str,
        *,
        sweep_batch_id: str,
        forked_at: str,
    ) -> Path:
        """Clean-slate sweep-fork ``index.json`` carrying ``sweep_batch_id``."""
        parent_index = read_json_optional(self._index_path(campaign_id, parent_cycle_id)) or {}
        blob = fresh_sibling_index_blob(
            parent_index,
            parent_cycle_id,
            "sweep",
            forked_at,
            sweep_batch_id=sweep_batch_id,
        )
        path = self._index_path(campaign_id, new_cycle_id)
        write_json(path, blob)
        return path

    def copy_parent_rounds_and_candidates(
        self,
        campaign_id: str,
        parent_cycle_id: str,
        new_cycle_id: str,
        *,
        before_round: int,
    ) -> int:
        """Copy parent's ``rounds/`` + ``candidates/`` files for rounds < ``before_round``."""
        copy_specs: tuple[tuple[Path, Path, str], ...] = (
            (
                self._rounds_dir(campaign_id, parent_cycle_id),
                self._rounds_dir(campaign_id, new_cycle_id),
                "round_",
            ),
            (
                self._candidates_dir(campaign_id, parent_cycle_id),
                self._candidates_dir(campaign_id, new_cycle_id),
                "round_",
            ),
        )
        n_copied = 0
        for src, dst, prefix in copy_specs:
            if not src.exists():
                continue
            dst.mkdir(parents=True, exist_ok=True)
            for p in sorted(src.glob(f"{prefix}*.json")):
                try:
                    n = int(p.stem.removeprefix(prefix))
                except ValueError:
                    continue
                if n < before_round:
                    shutil.copyfile(p, dst / p.name)
                    n_copied += 1
        return n_copied

    # -- Round CRUD -----------------------------------------------------------

    def save_round_file(
        self,
        campaign_id: str,
        cycle_id: str,
        round_data: dict[str, Any],
    ) -> Path:
        """Persist a round_data detail file and update the cycle index."""
        round_id = round_data["round_id"]
        validate_path_component(round_id)
        round_num = round_data.get("round", 0)

        detail_path = self._rounds_dir(campaign_id, cycle_id) / f"round_{round_num:04d}.json"
        write_json(detail_path, round_data)

        index_path = self._index_path(campaign_id, cycle_id)
        data = read_json(index_path)

        data["rounds"] = [t for t in data["rounds"] if t.get("round") != round_num]
        data["rounds"].append(round_summary(round_data))
        data["n_rounds"] = len(data["rounds"])

        if round_data["accuracy"] > data.get("best_accuracy", 0.0):
            data["best_accuracy"] = round_data["accuracy"]
            data["best_round_id"] = round_id

        data["updated_at"] = datetime.now(UTC).isoformat()
        write_json(index_path, data)

        return detail_path

    def load_round_file(
        self,
        campaign_id: str,
        cycle_id: str,
        round_num: int,
    ) -> dict[str, Any] | None:
        """Load a round_data detail by round number. ``None`` if not found."""
        return read_json_optional(
            self._rounds_dir(campaign_id, cycle_id) / f"round_{round_num:04d}.json",
        )

    def load_rounds_range(
        self,
        campaign_id: str,
        cycle_id: str,
        start: int,
        end: int,
    ) -> list[dict[str, Any]]:
        """Load rounds ``start..end`` inclusive, in round order. Missing rounds skipped."""
        out: list[dict[str, Any]] = []
        for r in range(start, end + 1):
            round_data = self.load_round_file(campaign_id, cycle_id, r)
            if round_data is not None:
                out.append(round_data)
        return out

    def save_round_candidates(
        self,
        campaign_id: str,
        cycle_id: str,
        round_num: int,
        candidates: list[dict[str, Any]],
    ) -> None:
        """Persist generated candidates before scoring (mid-round checkpoint)."""
        path = self._candidates_dir(campaign_id, cycle_id) / f"round_{round_num:04d}.json"
        write_json(path, candidates)
        logger.debug("Saved %d candidates for round %d → %s", len(candidates), round_num, path.name)

    def load_round_candidates(
        self,
        campaign_id: str,
        cycle_id: str,
        round_num: int,
    ) -> list[dict[str, Any]] | None:
        """Load persisted candidates for a round. ``None`` if not on disk."""
        return read_json_optional(
            self._candidates_dir(campaign_id, cycle_id) / f"round_{round_num:04d}.json",
        )

    def delete_round_candidates(
        self,
        campaign_id: str,
        cycle_id: str,
        round_num: int,
    ) -> None:
        """Delete persisted candidates for a round (forces fresh generation)."""
        path = self._candidates_dir(campaign_id, cycle_id) / f"round_{round_num:04d}.json"
        if path.exists():
            path.unlink()
            logger.debug(
                "Deleted cached candidates for round %d (escalation invalidation)", round_num
            )


__all__ = ["CampaignStore"]
