"""Campaign + cycle file I/O under ``campaigns/{campaign_id}/`` — one cohesive store.

``CampaignStore`` owns the whole ``campaigns/`` tree: the ``campaign.json``
manifest, per-cycle ``index.json`` CRUD, fork-sibling index writers, round +
candidate detail files, and the per-cycle ``.overrides/seed.json`` home. Path
resolution + cross-cutting reads (``load_campaign``, index enumeration) sit at
the top of the class; the no-subscriber pure ledger scan lives in its own
``ledger_scan.py`` module (imported here for ``rewind_to_round``).

``.overrides/`` holds **declared-at-mint, read-once-at-bootstrap** data: the
cycle seed (chosen origin prompt + pipeline overlay + limit overrides), written
for an operator-steered fork OR a campaign-from-origin root mint. Contrast
``.runtime/`` (``stop.flag`` / ``pause.flag`` / ``spend_cap.json``) — those are
**mutated-during-run, polled-every-tick** by the round loop (read via
``infrastructure/runtime_flags.py``). The directory name encodes the read
cadence; conflating the two invites cache-staleness bugs.

For a fork, the seed is one of three projections of a typed :class:`ForkSpec`:
the ledger ``FORK_CUT`` record is the SoT, ``.overrides/seed.json`` is the
bootstrap-read copy the origin resolver consumes, ``index.json::fork`` is the
lineage-read copy (seed-excluded). Writers: ``_mint_fork`` (forks) and the mint
seam (campaign-from-origin).
"""

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
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.run_records import CycleSeed
from promptpotter.infrastructure.runtime_flags import derive_run_phase
from promptpotter.infrastructure.store.base import (
    read_json,
    read_json_optional,
    validate_path_component,
    write_json,
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
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import BadRequestError, NotFoundError

logger = logging.getLogger(__name__)


def round_summary(round_data: dict[str, Any]) -> dict[str, Any]:
    """Projection of round detail into the ``index.json::rounds`` shape.

    Both writers — the scored ``_build_round_payload`` and the sparse
    ``generation_only`` sweep dict — guarantee every key read here, so they're
    read directly (a missing key is a corrupt round file, not a default case)."""
    return {
        "round_id": round_data["round_id"],
        "round": round_data["round"],
        "label": round_data["label"],
        "accuracy": round_data["accuracy"],
        "hits": round_data["hits"],
        "total": round_data["total"],
        "improved": round_data["improved"],
    }


def fresh_sibling_index_blob(
    parent_index: dict[str, Any],
    parent_cycle_id: str,
    sibling_kind: str,
    forked_at: str,
    **extras: Any,
) -> dict[str, Any]:
    """Clean-slate sibling index inheriting type + identity ``header`` from the parent.

    ``sibling_kind ∈ {fork, diag, sweep}``. ``backend_id`` / ``dataset_name`` ride the
    inherited ``header`` block (the single identity home built by
    ``_build_index_header``) — no top-level copy.
    """
    return {
        "type": parent_index.get("type", "optimization_loop"),
        "connector_type": parent_index.get("connector_type", ""),
        "header": parent_index.get("header", {}),
        "parent_cycle_id": parent_cycle_id,
        "parent_session_id": parent_index.get("parent_session_id", ""),
        "sibling_kind": sibling_kind,
        "forked_from_round": 0,
        "forked_at": forked_at,
        "rounds": [],
        "n_rounds": 0,
        "best_accuracy": 0.0,
        "best_round_id": None,
        "origin_accuracy": parent_index.get("origin_accuracy", 0.0),
        "status": "active",
        "created_at": forked_at,
        "updated_at": forked_at,
        **extras,
    }


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
    """Sidebar unit kind ∈ {``session``, ``divergent_resume``, ``user_fork``, ``auto_rebase``}."""
    if sibling_kind == "root":
        return "session"
    if fork_trigger == "scoring_divergence":
        return "divergent_resume"
    if fork_trigger in ("l2_rebase", "l3_rebase"):
        return "auto_rebase"
    return "user_fork"


class CampaignStore:
    """Campaign + cycle artifacts under ``campaigns/{campaign_id}/``.

    One class over the whole tree: manifest CRUD, per-cycle ``index.json``
    CRUD + rewind + enumeration, fork-sibling writers, round/candidate detail
    files, and the ``.overrides/seed.json`` cycle-seed home.
    """

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    # ------------------------------------------------------------------
    # Path resolution + cross-cutting reads
    # ------------------------------------------------------------------

    def campaign_root_dir(self, campaign_id: str) -> Path:
        return campaign_root_dir_for(self._base_dir, campaign_id)

    def cycle_dir(self, campaign_id: str, cycle_id: str) -> Path:
        return cycle_dir_for(self._base_dir, campaign_id, cycle_id)

    def _manifest_path(self, campaign_id: str) -> Path:
        return self.campaign_root_dir(campaign_id) / "campaign.json"

    def _index_path(self, campaign_id: str, cycle_id: str) -> Path:
        return self.cycle_dir(campaign_id, cycle_id) / "index.json"

    def _rounds_dir(self, campaign_id: str, cycle_id: str) -> Path:
        return self.cycle_dir(campaign_id, cycle_id) / "rounds"

    def _candidates_dir(self, campaign_id: str, cycle_id: str) -> Path:
        return self.cycle_dir(campaign_id, cycle_id) / ".runtime" / "cache" / "candidates"

    def _overrides_dir(self, campaign_id: str, cycle_id: str) -> Path:
        return self.cycle_dir(campaign_id, cycle_id) / ".overrides"

    def load_campaign(self, campaign_id: str) -> Campaign | None:
        data = read_json_optional(self._manifest_path(campaign_id))
        if data is None:
            return None
        return Campaign.model_validate(data)

    def _index_files(self) -> list[Path]:
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

    # ------------------------------------------------------------------
    # Campaign manifest CRUD — ``campaign.json`` + the cycle tree
    # ------------------------------------------------------------------

    def create_campaign(self, campaign: Campaign) -> Path:
        """Write ``campaign.json``; the single config-snapshot writer."""
        path = self._manifest_path(campaign.campaign_id)
        write_json(path, campaign.model_dump(mode="json"))
        return path

    def update_campaign(self, campaign_id: str, updates: dict[str, Any]) -> None:
        path = self._manifest_path(campaign_id)
        data = read_json(path)
        data.update(updates)
        write_json(path, data)

    def repoint_dataset(self, old_name: str, new_name: str) -> int:
        """Move every campaign pinned to *old_name* onto *new_name*. Returns the count.

        The campaign half of the dataset version-and-repoint migration
        (``application/datasets/dataset_replace.py``): rewrites both the
        manifest pin (``campaign.json::dataset_name``, which resolution reads
        live) *and* every cycle ``index.json::header.dataset_name`` (which the
        cycle listing surfaces — the backfill only fires when that header is
        empty, so a stale stamp would otherwise outlive the move). After this,
        a prior campaign resolves the exact bytes it always ran on, now living
        under *new_name*. Any lifecycle (active / archived / deleted) — an
        archived campaign's data must stay truthful too. Idempotent: a campaign
        already on *new_name* doesn't match and is skipped.
        """
        count = 0
        for cid in self.list_campaign_ids():
            campaign = self.load_campaign(cid)
            if campaign is None or campaign.dataset_name != old_name:
                continue
            self.update_campaign(cid, {"dataset_name": new_name})
            self._repoint_cycle_headers(cid, old_name, new_name)
            count += 1
        return count

    def _repoint_cycle_headers(self, campaign_id: str, old_name: str, new_name: str) -> None:
        """Rewrite ``header.dataset_name`` on every cycle index that still stamps *old_name*."""
        cycles_dir = self.campaign_root_dir(campaign_id) / "cycles"
        if not cycles_dir.exists():
            return
        for index_path in sorted(cycles_dir.glob("*/index.json")):
            data = read_json_optional(index_path)
            if not isinstance(data, dict):
                continue
            header = data.get("header")
            if isinstance(header, dict) and header.get("dataset_name") == old_name:
                header["dataset_name"] = new_name
                write_json(index_path, data)

    def list_campaign_ids(self) -> list[str]:
        """Every campaign id on disk (dir with ``campaign.json``), sorted."""
        campaigns_dir = self._base_dir / "campaigns"
        if not campaigns_dir.exists():
            return []
        return sorted(
            p.name
            for p in campaigns_dir.iterdir()
            if p.is_dir() and (p / "campaign.json").is_file()
        )

    def list_campaigns(
        self,
        dataset_name: str | None = None,
        *,
        lifecycle: str = "active",
        owner_user_id: str | None = None,
    ) -> list[Campaign]:
        """Campaigns matching the filter.

        *lifecycle* — one of ``"active"`` / ``"archived"`` / ``"deleted"`` /
        ``"all"``; defaults to ``"active"`` so archived + deleted campaigns
        drop out of the default sidebar. The store is the sole filter
        gateway; API + CLI pass through.

        *owner_user_id* — when set, only campaigns owned by this user are
        returned. Unset means no owner filter (the in-process tenant scope
        is still enforced by ``Stores.identity``).
        """
        out: list[Campaign] = []
        for cid in self.list_campaign_ids():
            campaign = self.load_campaign(cid)
            if campaign is None:
                continue
            if dataset_name and campaign.dataset_name != dataset_name:
                continue
            if lifecycle != "all" and campaign.lifecycle_status != lifecycle:
                continue
            if owner_user_id is not None and campaign.owner_user_id != owner_user_id:
                continue
            out.append(campaign)
        return out

    def mark_campaign_finished(self, campaign_id: str, *, status: str, finished_at: str) -> None:
        if self.load_campaign(campaign_id) is None:
            return
        self.update_campaign(campaign_id, {"status": status, "finished_at": finished_at})

    def mark_campaign_lifecycle(
        self,
        campaign_id: str,
        *,
        lifecycle_status: str,
        lifecycle_changed_at: str,
        lifecycle_reason: str = "",
    ) -> None:
        """Soft-mark a campaign as ``archived`` / ``deleted`` / ``active``.

        Never physically removes data — measurements survive so siblings
        still cache-hit per ADR-0002 §0.5. ``unarchive`` flips back to
        ``"active"``.
        """
        if self.load_campaign(campaign_id) is None:
            return
        self.update_campaign(
            campaign_id,
            {
                "lifecycle_status": lifecycle_status,
                "lifecycle_changed_at": lifecycle_changed_at,
                "lifecycle_reason": lifecycle_reason,
            },
        )

    def list_sessions(self, campaign_id: str) -> list[str]:
        """Session-root cycle ids in the campaign tree, ordered by ``session_index``."""
        cycles_dir = self.campaign_root_dir(campaign_id) / "cycles"
        if not cycles_dir.exists():
            return []
        roots = [
            p.name
            for p in cycles_dir.iterdir()
            if p.is_dir() and (p / "index.json").is_file() and root_cycle_id(p.name) == p.name
        ]
        return sorted(roots, key=session_index)

    # ------------------------------------------------------------------
    # Per-cycle ``index.json`` CRUD — create, update, rewind, enumerate
    # ------------------------------------------------------------------

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
            # Babysat marker: flips True the moment an operator manually
            # intervenes (today: skip-searchpoint), so the cycle is permanently
            # distinguishable from a pure/reproducible run. `interventions` is the
            # append-only audit trail of those gestures.
            "human_intervened": False,
            "interventions": [],
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

    def mark_human_intervened(
        self,
        campaign_id: str,
        cycle_id: str,
        *,
        kind: str,
        at: str,
        round: int | None = None,
        candidate: str | None = None,
    ) -> None:
        """Mark the cycle babysat and append one entry to its intervention log.

        Written the moment the operator intervenes (e.g. skip-searchpoint), not at
        teardown — a still-running babysat cycle must already be distinguishable
        from a pure/reproducible one. Idempotent on the boolean; the log grows by
        one per gesture. Tolerates an index minted before this field existed."""
        path = self._index_path(campaign_id, cycle_id)
        data = read_json(path)
        data["human_intervened"] = True
        log = data.get("interventions")
        if not isinstance(log, list):
            log = []
        log.append({"kind": kind, "at": at, "round": round, "candidate": candidate})
        data["interventions"] = log
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
            raise NotFoundError(f"cycle {cycle_id!r} has no ledger on disk")
        max_complete = scan_ledger_max_round_complete(ledger_path)
        if after_round > max_complete:
            raise BadRequestError(
                f"--from {after_round}: ledger only has completed rounds 0..{max_complete}"
            )

        if after_round >= 1:
            if not rounds_dir.exists():
                raise NotFoundError(
                    f"cycle {cycle_id!r}: ledger has rounds 0..{max_complete} but "
                    f"{rounds_dir} is missing — projection cache out of sync with ledger"
                )
            target = rounds_dir / f"round_{after_round:04d}.json"
            if not target.exists():
                raise NotFoundError(
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

    def list_all(self) -> list[dict[str, Any]]:
        results = []
        for index_path in self._index_files():
            data = read_json(index_path)
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
                        "human_intervened": False,
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
                    "human_intervened": bool(data.get("human_intervened", False)),
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

    # ------------------------------------------------------------------
    # Fork-sibling ``index.json`` writers — rebase / diag / sweep
    # ------------------------------------------------------------------

    def _write_fresh_sibling(
        self,
        campaign_id: str,
        parent_cycle_id: str,
        new_cycle_id: str,
        kind: str,
        *,
        forked_at: str,
        **blob_kwargs: Any,
    ) -> Path:
        """Read parent index → fresh sibling blob → write child index. The shared
        body behind the diag / operator / sweep fork writers (numbering restarts at
        round 1; no parent-round inheritance — that's ``save_rebase_fork``'s job)."""
        parent_index = read_json_optional(self._index_path(campaign_id, parent_cycle_id)) or {}
        blob = fresh_sibling_index_blob(
            parent_index, parent_cycle_id, kind, forked_at, **blob_kwargs
        )
        path = self._index_path(campaign_id, new_cycle_id)
        write_json(path, blob)
        return path

    def save_diag_fork(
        self,
        campaign_id: str,
        parent_cycle_id: str,
        new_cycle_id: str,
        *,
        forked_at: str,
    ) -> Path:
        return self._write_fresh_sibling(
            campaign_id, parent_cycle_id, new_cycle_id, "diag", forked_at=forked_at
        )

    def save_rebase_fork(
        self,
        campaign_id: str,
        parent_cycle_id: str,
        new_cycle_id: str,
        *,
        forked_at: str,
        forked_from_round: int,
        surviving_rounds: list[dict[str, Any]],
    ) -> Path:
        """Mid-cycle fork inheriting parent state up to ``forked_from_round``.

        Single writer for all 4 rebase-shaped triggers
        (``SCORING_DIVERGENCE``, ``L2_REBASE``, ``L3_REBASE``,
        ``OPERATOR_REWIND``). The issuer is recorded on the FORK_CUT
        ledger record via ``ForkSpec.trigger``.
        """
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
        # Identity is the directory name — never inherit a stored id from the parent.
        index.pop("cycle_id", None)
        index.pop("campaign_id", None)
        path = self._index_path(campaign_id, new_cycle_id)
        write_json(path, index)
        return path

    def save_operator_fork(
        self,
        campaign_id: str,
        parent_cycle_id: str,
        new_cycle_id: str,
        *,
        forked_at: str,
    ) -> Path:
        """Clean-offshoot fork from the lineage/control panel (endorse or steered).

        Fresh sibling index (no parent-round copy, numbering restarts at round
        1); the origin re-scores from the selected/edited searchpoint at
        bootstrap. The ``ForkSpec`` provenance (trigger + from_round +
        from_candidate_id) lands on ``index.json::fork`` via the single
        fork-block writer in ``_mint_fork``; the steered seed rides
        ``.overrides/seed.json`` (its own read-once home).
        """
        return self._write_fresh_sibling(
            campaign_id, parent_cycle_id, new_cycle_id, "fork", forked_at=forked_at
        )

    def save_sweep_fork(
        self,
        campaign_id: str,
        parent_cycle_id: str,
        new_cycle_id: str,
        *,
        sweep_batch_id: str,
        forked_at: str,
    ) -> Path:
        return self._write_fresh_sibling(
            campaign_id,
            parent_cycle_id,
            new_cycle_id,
            "sweep",
            forked_at=forked_at,
            sweep_batch_id=sweep_batch_id,
        )

    def copy_parent_rounds_and_candidates(
        self,
        campaign_id: str,
        parent_cycle_id: str,
        new_cycle_id: str,
        *,
        before_round: int,
    ) -> int:
        """Copy parent's round + candidate files for rounds < ``before_round``."""
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

    # ------------------------------------------------------------------
    # Round + candidate detail-file CRUD under a cycle dir
    # ------------------------------------------------------------------

    def save_round_file(
        self,
        campaign_id: str,
        cycle_id: str,
        round_data: dict[str, Any],
    ) -> Path:
        """Persist a round detail file and update the cycle index."""
        round_id = round_data["round_id"]
        validate_path_component(round_id)
        round_num = round_data["round"]

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

        data["updated_at"] = utcnow_iso()
        write_json(index_path, data)

        return detail_path

    def load_round_file(
        self,
        campaign_id: str,
        cycle_id: str,
        round_num: int,
    ) -> dict[str, Any] | None:
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
        """Load rounds ``start..end`` inclusive. Missing rounds skipped."""
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
        """Persist generated candidates before scoring."""
        path = self._candidates_dir(campaign_id, cycle_id) / f"round_{round_num:04d}.json"
        write_json(path, candidates)
        logger.debug("Saved %d candidates for round %d → %s", len(candidates), round_num, path.name)

    def load_round_candidates(
        self,
        campaign_id: str,
        cycle_id: str,
        round_num: int,
    ) -> list[dict[str, Any]] | None:
        return read_json_optional(
            self._candidates_dir(campaign_id, cycle_id) / f"round_{round_num:04d}.json",
        )

    def delete_round_candidates(
        self,
        campaign_id: str,
        cycle_id: str,
        round_num: int,
    ) -> None:
        """Delete cached candidates (forces fresh generation)."""
        path = self._candidates_dir(campaign_id, cycle_id) / f"round_{round_num:04d}.json"
        if path.exists():
            path.unlink()
            logger.debug(
                "Deleted cached candidates for round %d (escalation invalidation)", round_num
            )

    # ------------------------------------------------------------------
    # Per-cycle override store — the ``.overrides/seed.json`` cycle-seed home
    # ------------------------------------------------------------------

    def write_cycle_seed(self, campaign_id: str, cycle_id: str, seed: CycleSeed) -> Path:
        """Persist the cycle seed (read once at bootstrap)."""
        path = self._overrides_dir(campaign_id, cycle_id) / "seed.json"
        write_json(path, seed.model_dump(mode="json"))
        return path

    def read_cycle_seed(self, campaign_id: str, cycle_id: str) -> CycleSeed | None:
        """Load the cycle seed, or ``None`` when this cycle wasn't seeded."""
        data = read_json_optional(self._overrides_dir(campaign_id, cycle_id) / "seed.json")
        if data is None:
            return None
        return CycleSeed.model_validate(data)


__all__ = ["CampaignStore"]
