"""Per-cycle optimization artifacts under ``campaigns/``."""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from promptpotter.config.settings import DEFAULT_CONNECTOR_TYPE
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
from promptpotter.infrastructure.store.paths import campaign_dir_for, sibling_kind

logger = logging.getLogger(__name__)


class CampaignStore(EntityStore):
    """File I/O for the per-cycle campaign tree."""

    def __init__(self, base_dir: Path):
        # base_dir is tenant root; campaigns nest directly under it
        super().__init__(base_dir, "campaigns")

    # -- path helpers ---------------------------------------------------------

    def _entity_dir(self, _backend_id: str) -> Path:
        """Parent dir for root campaign trees. Tenant-global."""
        return self._base_dir / "campaigns"

    def _campaign_dir(self, _backend_id: str, cycle_id: str) -> Path:
        """Per-cycle dir. Backend-agnostic; pass ``""`` to read parent indexes."""
        return campaign_dir_for(self._base_dir, cycle_id)

    def campaign_dir(self, cycle_id: str) -> Path:
        """Public wrapper for ``_campaign_dir`` — used by orchestration to derive
        the per-cycle path without leaking the leading underscore."""
        return self._campaign_dir("", cycle_id)

    def _rounds_dir(self, backend_id: str, cycle_id: str) -> Path:
        return self._campaign_dir(backend_id, cycle_id) / "rounds"

    def _candidates_dir(self, backend_id: str, cycle_id: str) -> Path:
        return self._campaign_dir(backend_id, cycle_id) / ".runtime" / "cache" / "candidates"

    def _entity_path(self, backend_id: str, entity_id: str) -> Path:
        """Campaign metadata (index.json) lives INSIDE the per-cycle dir."""
        return self._campaign_dir(backend_id, entity_id) / "index.json"

    # -- Campaign CRUD --------------------------------------------------------

    def create(
        self,
        backend_id: str,
        cycle_id: str,
        metadata: dict[str, Any],
    ) -> Path:
        """Create/augment a campaign's ``index.json`` with metadata.

        When the file doesn't exist yet, writes a fresh blob with defaults.
        When it does, merges the new keys over existing values without
        clobbering round_data/best/origin accumulators — defaults only fill
        gaps. ``parent_session_id`` flows through ``metadata``.
        """
        path = self._entity_path(backend_id, cycle_id)
        existing = read_json_optional(path) or {}
        now = datetime.now(UTC).isoformat()
        defaults: dict[str, Any] = {
            "campaign_id": cycle_id,
            "created_at": existing.get("created_at", now),
            "updated_at": now,
            "status": "active",
            "connector_type": metadata.get("connector_type", DEFAULT_CONNECTOR_TYPE),
            "backend_id": backend_id,
            "parent_session_id": existing.get("parent_session_id", ""),
            "n_rounds": 0,
            "best_accuracy": 0.0,
            "best_round_id": None,
            "origin_accuracy": 0.0,
            "rounds": [],
        }
        # Merge order: defaults for missing keys → existing accumulators →
        # explicit metadata overrides. Accumulators ("rounds" / "n_rounds"
        # / "best_*") are preserved on replay via ``existing``.
        data = {**defaults, **existing, **metadata}
        data["updated_at"] = now
        data["backend_id"] = backend_id
        write_json(path, data)
        return path

    def update(
        self,
        backend_id: str,
        cycle_id: str,
        updates: dict[str, Any],
    ) -> None:
        """Merge *updates* into the campaign file and write back (+ timestamp)."""
        path = self._entity_path(backend_id, cycle_id)
        data = read_json(path)
        data.update(updates)
        data["updated_at"] = datetime.now(UTC).isoformat()
        write_json(path, data)

    def rewind_to_round(
        self,
        backend_id: str,
        cycle_id: str,
        after_round: int,
    ) -> None:
        """Archive round_data/candidate files for rounds > ``after_round``.

        Moves ``rounds/round_{M:04d}.json`` and
        ``.runtime/cache/candidates/round_{M:04d}.json`` for M > after_round
        into ``.runtime/archived/resumed_at_<ts>/{rounds,candidates}/``,
        then rebuilds the cycle's round_data index to reflect only surviving
        rounds (rounds 0..after_round).

        Admissibility consults the ledger first: ``--from N`` is valid iff
        the ledger has a ``PhaseRecord(round, complete, round=N)``. The
        public ``rounds/`` tree IS expected to be present for every round
        in ``[0..max_complete]`` because ``save_round_file`` fires inside
        the same ``_persist_round`` call that emits ``round:complete``.
        Mismatch (ledger has it, public tree doesn't) is surfaced as a
        separate, sharper error.
        """
        cycle_dir = self._campaign_dir(backend_id, cycle_id)
        rounds_dir = self._rounds_dir(backend_id, cycle_id)
        candidates_dir = self._candidates_dir(backend_id, cycle_id)

        ledger_path = cycle_dir / ".runtime" / "ledger.jsonl"
        if not ledger_path.exists():
            raise LookupError(f"cycle {cycle_id!r} has no ledger on disk")
        max_complete = scan_ledger_max_round_complete(ledger_path)
        if after_round > max_complete:
            raise LookupError(
                f"--from {after_round}: ledger only has completed rounds 0..{max_complete}"
            )

        # Origin (round 0) closes via ``(phase=origin, event=exit)`` and writes
        # only the audit cache (``.runtime/cache/rounds/round_0000.json``);
        # ``save_round_file`` runs only inside ``_persist_round`` for round≥1.
        # So ``--from 0`` on an origin-only cycle is legitimate even when
        # the public ``rounds/`` tree doesn't exist yet — there's nothing
        # to archive in that case.
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

        self._rebuild_round_index(backend_id, cycle_id, survivors)

    def _rebuild_round_index(
        self,
        backend_id: str,
        cycle_id: str,
        survivors: list[Path],
    ) -> None:
        """Recompute ``rounds`` / ``n_rounds`` / ``best_accuracy`` / ``best_round_id``
        from the round_data detail files that remain after a rewind."""
        campaign_path = self._entity_path(backend_id, cycle_id)
        data = read_json(campaign_path)

        rebuilt = [round_summary(read_json(p)) for p in sorted(survivors)]
        best = max(rebuilt, key=lambda s: s["accuracy"], default=None)

        data["rounds"] = rebuilt
        data["n_rounds"] = len(rebuilt)
        data["best_accuracy"] = best["accuracy"] if best else 0.0
        data["best_round_id"] = best["round_id"] if best else None
        data["updated_at"] = datetime.now(UTC).isoformat()
        write_json(campaign_path, data)

    def mark_finished(
        self,
        backend_id: str,
        cycle_id: str,
        *,
        status: str,
        stop_reason: str,
        best_accuracy: float,
        best_round: int,
        n_rounds: int,
        finished_at: str,
        interrupted_round: int | None = None,
    ) -> None:
        """Write the terminal status/stop_reason + outcome summary to disk.

        ``interrupted_round`` is recorded only when ``status == "interrupted"``;
        it names which round was active when the operator hit Ctrl+C so the
        webapp / CLI can show "partial round N" without re-deriving from the
        ledger.
        """
        from promptpotter.shared.errors import graceful

        updates: dict[str, Any] = {
            "status": status,
            "stop_reason": stop_reason,
            "best_accuracy": best_accuracy,
            "best_round": best_round,
            "n_rounds": n_rounds,
            "finished_at": finished_at,
        }
        if status == "interrupted" and interrupted_round is not None:
            updates["interrupted_round"] = interrupted_round
        with graceful("Campaign completion update failed"):
            self.update(backend_id, cycle_id, updates)

    def load_many(
        self,
        backend_id: str,
        cycle_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Load full campaign records for *cycle_ids*, or all campaigns when None.

        Skips campaigns whose detail file is missing.
        """
        if cycle_ids is None:
            cycle_ids = [s["campaign_id"] for s in self.list_all(backend_id)]
        return [c for cid in cycle_ids if (c := self.load(backend_id, cid)) is not None]

    def _index_files(self) -> list[Path]:
        """Every ``index.json`` under this tenant's campaigns tree.

        Walks root cycles (``campaigns/{cycle_id}/``) plus all three sibling
        kinds: ``forks/``, ``diag/``, and ``sweeps/*/forks/``. Shared between
        ``list_all`` (backend-filtered summary) and ``enumerate_cycles``
        (webapp picker)."""
        campaigns_dir = self._entity_dir("")
        if not campaigns_dir.exists():
            return []
        out: list[Path] = []
        for root_dir in sorted(campaigns_dir.iterdir()):
            if not root_dir.is_dir():
                continue
            if (idx := root_dir / "index.json").is_file():
                out.append(idx)
            for kind_dir in ("forks", "diag"):
                parent = root_dir / kind_dir
                if parent.is_dir():
                    for fork_dir in sorted(parent.iterdir()):
                        if (idx := fork_dir / "index.json").is_file():
                            out.append(idx)
            sweeps_dir = root_dir / "sweeps"
            if sweeps_dir.is_dir():
                for batch_dir in sorted(sweeps_dir.iterdir()):
                    batch_forks = batch_dir / "forks"
                    if batch_forks.is_dir():
                        for fork_dir in sorted(batch_forks.iterdir()):
                            if (idx := fork_dir / "index.json").is_file():
                                out.append(idx)
        return out

    def list_all(self, backend_id: str) -> list[dict[str, Any]]:
        """Return summary for every campaign stored under this tenant.

        Walks top-level root cycles (``campaigns/{cycle_id}/``) and all three
        sibling kinds: ``forks/``, ``diag/``, and ``sweeps/*/forks/``.
        Optionally filters by ``backend_id`` (matched against
        ``index.json::backend_id``). Pass ``""`` to list all campaigns
        regardless of backend.
        """
        results = []
        for index_path in self._index_files():
            data = read_json(index_path)
            if "campaign_id" not in data:
                continue
            if backend_id and data.get("backend_id") and data["backend_id"] != backend_id:
                continue
            results.append(
                {
                    "campaign_id": data["campaign_id"],
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
        """Every cycle on disk under this tenant, with the richer shape the
        webapp picker needs.

        Walks the same tree as ``list_all`` (roots + forks + diag + sweeps)
        but returns one entry per cycle carrying ``sibling_kind`` + dataset
        header info, and is backend-agnostic (no filter). Tolerant of corrupt
        or partially-written ``index.json``: a cycle dir with an unreadable
        index contributes a stub entry with ``status="unreadable"`` and
        defaulted numeric fields, so the operator can still see it in the
        picker. The enumeration never raises.
        """
        results: list[dict[str, Any]] = []
        for index_path in self._index_files():
            cycle_id_from_dir = index_path.parent.name
            try:
                data = read_json_optional(index_path)
            except Exception:  # corrupt JSON, encoding error, etc.
                data = None
            if not isinstance(data, dict) or "campaign_id" not in data:
                # stub entry for unreadable indexes — directory name is the cycle_id by construction
                results.append(
                    {
                        "cycle_id": cycle_id_from_dir,
                        "parent_session_id": "",
                        "dataset_name": "",
                        "backend_id": "",
                        "sibling_kind": sibling_kind(cycle_id_from_dir),
                        "is_root": sibling_kind(cycle_id_from_dir) == "root",
                        "status": "unreadable",
                        "best_accuracy": None,
                        "n_rounds": 0,
                        "created_at": "",
                        "updated_at": "",
                    }
                )
                continue
            cycle_id = data["campaign_id"]
            header_raw = data.get("header")
            header: dict[str, Any] = header_raw if isinstance(header_raw, dict) else {}
            kind = sibling_kind(cycle_id)
            results.append(
                {
                    "cycle_id": cycle_id,
                    "parent_session_id": data.get("parent_session_id", ""),
                    "dataset_name": header.get("dataset_name", ""),
                    "backend_id": data.get("backend_id") or header.get("backend_id", ""),
                    "sibling_kind": kind,
                    "is_root": kind == "root",
                    "status": data.get("status", ""),
                    "best_accuracy": data.get("best_accuracy"),
                    "n_rounds": data.get("n_rounds", 0),
                    "created_at": data.get("created_at", ""),
                    "updated_at": data.get("updated_at", ""),
                }
            )
        return results

    # -- Fork helpers ---------------------------------------------------------

    def save_divergence_fork(
        self,
        parent_cycle_id: str,
        new_cycle_id: str,
        *,
        surviving_rounds: list[dict[str, Any]],
        forked_at: str,
        forked_from_round: int,
    ) -> Path:
        """Divergence-fork ``index.json`` inheriting parent state.

        Recomputes ``best_*`` from ``surviving_rounds`` so the fork's index
        reflects only the rounds < ``forked_from_round`` it inherited.
        """
        parent_path = self._campaign_dir("", parent_cycle_id) / "index.json"
        parent_index = read_json_optional(parent_path) or {}
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
            "campaign_id": new_cycle_id,
            "parent_cycle_id": parent_cycle_id,
            "forked_from_round": forked_from_round,
            "forked_at": forked_at,
            "rounds": list(surviving_rounds),
            "n_rounds": len(surviving_rounds),
            "best_accuracy": best_acc,
            "best_round_id": best_round_id,
            "status": "resumed",
            "updated_at": forked_at,
        }
        path = self._campaign_dir("", new_cycle_id) / "index.json"
        write_json(path, index)
        return path

    def save_diag_fork(
        self,
        parent_cycle_id: str,
        new_cycle_id: str,
        *,
        forked_at: str,
    ) -> Path:
        """Clean-slate diag-sibling ``index.json``."""
        parent_path = self._campaign_dir("", parent_cycle_id) / "index.json"
        parent_index = read_json_optional(parent_path) or {}
        blob = fresh_sibling_index_blob(
            parent_index, new_cycle_id, parent_cycle_id, "diag_sibling", forked_at
        )
        path = self._campaign_dir("", new_cycle_id) / "index.json"
        write_json(path, blob)
        return path

    def save_sweep_fork(
        self,
        parent_cycle_id: str,
        new_cycle_id: str,
        *,
        sweep_batch_id: str,
        forked_at: str,
    ) -> Path:
        """Clean-slate sweep-fork ``index.json`` carrying ``sweep_batch_id``."""
        parent_path = self._campaign_dir("", parent_cycle_id) / "index.json"
        parent_index = read_json_optional(parent_path) or {}
        blob = fresh_sibling_index_blob(
            parent_index,
            new_cycle_id,
            parent_cycle_id,
            "sweep_fork",
            forked_at,
            sweep_batch_id=sweep_batch_id,
        )
        path = self._campaign_dir("", new_cycle_id) / "index.json"
        write_json(path, blob)
        return path

    def copy_parent_rounds_and_candidates(
        self,
        parent_cycle_id: str,
        new_cycle_id: str,
        *,
        before_round: int,
    ) -> int:
        """Copy parent's ``rounds/`` + ``candidates/`` files for rounds < ``before_round``.

        Returns total files copied. Caller owns deciding when to invoke;
        used by divergence forks for deterministic-replay inheritance.
        """
        copy_specs: tuple[tuple[Path, Path, str], ...] = (
            (
                self._rounds_dir("", parent_cycle_id),
                self._rounds_dir("", new_cycle_id),
                "round_",
            ),
            (
                self._candidates_dir("", parent_cycle_id),
                self._candidates_dir("", new_cycle_id),
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

    # -- Trial CRUD -----------------------------------------------------------

    def save_round_file(
        self,
        backend_id: str,
        cycle_id: str,
        round_data: dict[str, Any],
    ) -> Path:
        """Persist a round_data detail file and update the campaign index."""
        round_id = round_data["round_id"]
        validate_path_component(round_id)
        round_num = round_data.get("round", 0)

        detail_path = self._rounds_dir(backend_id, cycle_id) / f"round_{round_num:04d}.json"
        write_json(detail_path, round_data)

        campaign_path = self._entity_path(backend_id, cycle_id)
        data = read_json(campaign_path)

        data["rounds"] = [t for t in data["rounds"] if t.get("round") != round_num]
        data["rounds"].append(round_summary(round_data))
        data["n_rounds"] = len(data["rounds"])

        if round_data["accuracy"] > data.get("best_accuracy", 0.0):
            data["best_accuracy"] = round_data["accuracy"]
            data["best_round_id"] = round_id

        data["updated_at"] = datetime.now(UTC).isoformat()
        write_json(campaign_path, data)

        return detail_path

    def load_round_file(
        self,
        backend_id: str,
        cycle_id: str,
        round_num: int,
    ) -> dict[str, Any] | None:
        """Load a round_data detail by round number.  Returns None if not found."""
        return read_json_optional(
            self._rounds_dir(backend_id, cycle_id) / f"round_{round_num:04d}.json",
        )

    def load_rounds_range(
        self,
        backend_id: str,
        cycle_id: str,
        start: int,
        end: int,
    ) -> list[dict[str, Any]]:
        """Load rounds for rounds ``start..end`` inclusive, in round order.

        Missing rounds are skipped silently (``None`` from
        :meth:`load_round_file`). Used by the resume-divergence walker in
        :mod:`promptpotter.application.optimization.cycle` to re-derive
        each recorded decision under the current scorer.
        """
        out: list[dict[str, Any]] = []
        for r in range(start, end + 1):
            round_data = self.load_round_file(backend_id, cycle_id, r)
            if round_data is not None:
                out.append(round_data)
        return out

    def complete(self, backend_id: str, cycle_id: str) -> None:
        """Mark a campaign as completed."""
        self.update(backend_id, cycle_id, {"status": "completed"})
        logger.info("Campaign %s completed", cycle_id)

    def save_round_candidates(
        self,
        backend_id: str,
        cycle_id: str,
        round_num: int,
        candidates: list[dict[str, Any]],
    ) -> None:
        """Persist generated candidates before scoring (mid-round checkpoint)."""
        path = self._candidates_dir(backend_id, cycle_id) / f"round_{round_num:04d}.json"
        write_json(path, candidates)
        logger.debug(
            "Saved %d candidates for round %d → %s",
            len(candidates),
            round_num,
            path.name,
        )

    def load_round_candidates(
        self,
        backend_id: str,
        cycle_id: str,
        round_num: int,
    ) -> list[dict[str, Any]] | None:
        """Load persisted candidates for a round.  Returns None if not on disk."""
        return read_json_optional(
            self._candidates_dir(backend_id, cycle_id) / f"round_{round_num:04d}.json",
        )

    def delete_round_candidates(
        self,
        backend_id: str,
        cycle_id: str,
        round_num: int,
    ) -> None:
        """Delete persisted candidates for a round (forces fresh generation)."""
        path = self._candidates_dir(backend_id, cycle_id) / f"round_{round_num:04d}.json"
        if path.exists():
            path.unlink()
            logger.debug(
                "Deleted cached candidates for round %d (escalation invalidation)",
                round_num,
            )
