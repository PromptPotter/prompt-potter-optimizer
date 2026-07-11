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
``.runtime/`` (``pause.flag`` / ``skip.flag`` / ``spend_cap.json``) — those are
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

import contextlib
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
from promptpotter.domain.phases import StopReason
from promptpotter.domain.results import best_round_by_cumulative_accuracy
from promptpotter.domain.run_records import CycleSeed
from promptpotter.infrastructure.runtime_flags import derive_run_phase, is_checkin
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_ledger_max_round_complete,
)
from promptpotter.infrastructure.store.io import (
    read_json,
    read_json_optional,
    read_json_tolerant,
    validate_path_component,
    write_json,
)
from promptpotter.infrastructure.store.layout import (
    CycleLayout,
    archive_root_dir_for,
    campaign_root_dir_for,
    classify,
    cycle_dir_under,
    root_cycle_id,
    sibling_kind,
)
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import BadRequestError, ConflictError, NotFoundError

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
        "cumulative_accuracy": round_data["cumulative_accuracy"],
        "hits": round_data["hits"],
        "total": round_data["total"],
        "improved": round_data["improved"],
    }


def origin_accuracy_of(index: dict[str, Any]) -> float | None:
    """The origin's round-0 score, derived from ``rounds[]`` — there is no stored copy.

    Round 0 IS the origin, and every path that (re)scores it — bootstrap, a diag
    fork's re-measure, the interactive origin-gate rescore — re-emits round 0
    through ``save_round_file``, so the round row is always fresh. The old
    top-level ``origin_accuracy`` was stamped once at bootstrap and went stale on
    a gate rescore. ``None`` until round 0 lands (fresh mint / pre-origin fork)."""
    rounds = index.get("rounds") or []
    return next((float(r["accuracy"]) for r in rounds if r.get("round") == 0), None)


def _apply_best(data: dict[str, Any]) -> None:
    """Set the index's ``best_accuracy`` / ``best_round`` from ``data["rounds"]`` via the
    shared ``best_round_by_cumulative_accuracy`` (which resume/fork rebuild also rides, so
    ``index.json`` and ``dashboard.json::best`` agree by construction).

    Best = highest **full-population** ``cumulative_accuracy`` (the incumbent rescored over
    every sample probed so far), NOT the round winner's hard-first/PoBB subset ``accuracy``
    (a lucky 6/8 subset is 0.75 but not comparable to a full-set round). Deliberately a
    DIFFERENT basis from the winner export: ``cycle.py::absorb_round`` / ``replay_priors``
    argmax ``best_sp``/``best_round`` on cumulative ``composite_fitness`` — the optimizer's
    objective, also the L2/L3 stall comparator (``escalation/firing.py``). This headline
    argmaxes plain cumulative ``accuracy`` — the formula-independent number operators
    recognize. Under a non-accuracy formula the two ``best_round``s can legitimately
    disagree; flipping either to match would break the objective (winner side) or the
    display contract (this side). See ``architecture.md`` §0.5."""
    data["best_accuracy"], data["best_round"] = best_round_by_cumulative_accuracy(data["rounds"])


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


def _unlink_robust(path: Path) -> None:
    """``Path.unlink`` with the Windows read-only chmod dance ``_rmtree_robust`` uses."""
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        path.unlink()


def _prune_empty_dirs(root: Path) -> None:
    """``rmdir`` every now-empty directory under *root*, deepest first (a dir holding a
    keepsake file keeps its ancestors alive)."""
    for d in sorted(
        (p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True
    ):
        with contextlib.suppress(OSError):  # non-empty (holds keepsake) or already gone
            d.rmdir()


def _strip_to_keepsake(campaign_dir: Path) -> None:
    """Drop every non-keepsake file in each cycle tree in place, sparing what
    ``delete --keep-results`` preserves — the manifest, the per-cycle reports
    (``index.json`` / ``dashboard.json`` / ``log.md`` / ``review.md`` /
    ``hard_samples.json``), and the shallow ``langfuse/{traces,observations,scores}``
    loop trace. Keepsake is the single ``FileKind.keepsake`` predicate
    (``store/layout.py``) — the same taxonomy the storage rollup rolls into leaves —
    so the heavy tiers (resume state, audit cache, ledger, streams, prompts, the
    langfuse dataset mirror) drop without a hand-maintained subdir list; emptied dirs
    are pruned after. ``sweeps/`` is a batch-diagnostic tree with no keepsake and no
    cycle, so it drops wholesale."""
    cycles_dir = campaign_dir / "cycles"
    if cycles_dir.is_dir():
        for cdir in cycles_dir.iterdir():
            if not cdir.is_dir():
                continue
            for p in [
                f
                for f in cdir.rglob("*")
                if f.is_file() and not classify(f.relative_to(campaign_dir)).keepsake
            ]:
                _unlink_robust(p)
            _prune_empty_dirs(cdir)
    sweeps = campaign_dir / "sweeps"
    if sweeps.exists():
        _rmtree_robust(sweeps)


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

    def _campaign_dir(self, campaign_id: str) -> Path:
        """Resolve a campaign tree wherever it actually lives. Active campaigns sit
        under ``campaigns/``; the ``archive`` verb MOVES the tree into the
        ``archive/`` recycle bin. The active location wins if both somehow exist; a
        fresh write defaults to ``campaigns/``. The free path builders
        (``cycle_dir_for`` etc.) used by the live API/projection routes stay
        ``campaigns/``-only — so an archived campaign is listable + manageable
        through this store but inert to browse until ``unarchive``d, which is the
        recycle-bin semantic."""
        active = campaign_root_dir_for(self._base_dir, campaign_id)
        if active.exists():
            return active
        archived = archive_root_dir_for(self._base_dir, campaign_id)
        if archived.exists():
            return archived
        return active

    def campaign_root_dir(self, campaign_id: str) -> Path:
        return self._campaign_dir(campaign_id)

    def cycle_dir(self, campaign_id: str, cycle_id: str) -> Path:
        return cycle_dir_under(self._campaign_dir(campaign_id), cycle_id)

    def _manifest_path(self, campaign_id: str) -> Path:
        return self.campaign_root_dir(campaign_id) / "campaign.json"

    def _layout(self, campaign_id: str, cycle_id: str) -> CycleLayout:
        """The per-cycle path owner (archive-aware root — ``self.cycle_dir``)."""
        return CycleLayout(self.cycle_dir(campaign_id, cycle_id))

    def _index_path(self, campaign_id: str, cycle_id: str) -> Path:
        return self._layout(campaign_id, cycle_id).manifest

    def _rounds_dir(self, campaign_id: str, cycle_id: str) -> Path:
        return self._layout(campaign_id, cycle_id).rounds

    def _candidates_dir(self, campaign_id: str, cycle_id: str) -> Path:
        return self._layout(campaign_id, cycle_id).candidates_cache

    def _overrides_dir(self, campaign_id: str, cycle_id: str) -> Path:
        return self._layout(campaign_id, cycle_id).overrides

    def load_campaign(self, campaign_id: str) -> Campaign | None:
        data = read_json_optional(self._manifest_path(campaign_id))
        if data is None:
            return None
        return Campaign.model_validate(data)

    def _campaign_parents(self) -> list[Path]:
        """The two dirs a campaign tree can live under: ``campaigns/`` (active) and
        the ``archive/`` recycle bin. The ``*/cycles/*`` + ``*/campaign.json`` filters
        below skip ``archive/``'s non-campaign cache neighbours (optimizer_calls/, …)."""
        return [self._base_dir / "campaigns", self._base_dir / "archive"]

    def _index_files(self) -> list[Path]:
        out: list[Path] = []
        for parent in self._campaign_parents():
            if parent.exists():
                out.extend(parent.glob("*/cycles/*/index.json"))
        return sorted(out)

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
        """Every campaign id on disk (dir with ``campaign.json``), sorted — active
        under ``campaigns/`` plus archived under the ``archive/`` recycle bin."""
        ids: set[str] = set()
        for parent in self._campaign_parents():
            if not parent.is_dir():
                continue
            for p in parent.iterdir():
                if p.is_dir() and (p / "campaign.json").is_file():
                    ids.add(p.name)
        return sorted(ids)

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

    def run_status(self, campaign_id: str, root_cycle_id: str) -> str:
        """Status of the campaign's run, read off its root cycle's ``index.json`` —
        run state is owned per-cycle; ``campaign.json`` carries identity/config +
        lifecycle intent only. ``"active"`` when the cycle has no index yet
        (check-in). A campaign mints exactly one root cycle, so this is *the* run."""
        index = self.load(campaign_id, root_cycle_id) or {}
        return str(index.get("status") or "active")

    def _is_active_campaign(self, campaign_id: str) -> bool:
        """Whether *campaign_id* is the tenant's active-session campaign — the live
        lens / running run. Archiving or deleting it would strand the pointer + its
        open ``.runtime/`` handles, so the move + destructive verbs refuse it."""
        # Function-local: the package `__init__` imports this module, so the
        # pointer reader can only be reached at call time.
        from promptpotter.infrastructure.store import read_active_pointer_under

        _, active_campaign, _ = read_active_pointer_under(self._base_dir)
        return bool(active_campaign) and active_campaign == campaign_id

    def _lifecycle_updates(self, status: str, changed_at: str, reason: str) -> dict[str, str]:
        return {
            "lifecycle_status": status,
            "lifecycle_changed_at": changed_at,
            "lifecycle_reason": reason,
        }

    def archive_campaign(self, campaign_id: str, *, changed_at: str, reason: str = "") -> bool:
        """Flag the manifest ``archived`` then MOVE the tree into the ``archive/``
        recycle bin. Returns ``False`` if the campaign isn't found; raises
        ``ConflictError`` if it's the active campaign. ``unarchive_campaign``
        reverses it. Measurements (``measurements/``) are untouched."""
        if self.load_campaign(campaign_id) is None:
            return False
        if self._is_active_campaign(campaign_id):
            raise ConflictError(
                f"refusing to archive {campaign_id}: active campaign — switch first"
            )
        self.update_campaign(campaign_id, self._lifecycle_updates("archived", changed_at, reason))
        src = campaign_root_dir_for(self._base_dir, campaign_id)
        dst = archive_root_dir_for(self._base_dir, campaign_id)
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        return True

    def unarchive_campaign(self, campaign_id: str, *, changed_at: str, reason: str = "") -> bool:
        """MOVE the tree back from the ``archive/`` recycle bin to ``campaigns/`` and
        flag the manifest ``active``. Returns ``False`` if not found."""
        src = archive_root_dir_for(self._base_dir, campaign_id)
        dst = campaign_root_dir_for(self._base_dir, campaign_id)
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        if self.load_campaign(campaign_id) is None:
            return False
        self.update_campaign(campaign_id, self._lifecycle_updates("active", changed_at, reason))
        return True

    def delete_campaign(
        self,
        campaign_id: str,
        *,
        keep_results: bool,
        changed_at: str,
        reason: str = "",
        inner_sandbox_root: Path | None = None,
    ) -> bool:
        """Destructive — no recovery. ``keep_results=False`` removes the whole tree;
        ``True`` strips the heavy tiers (Resume + Audit + Mirror) in place and flags
        the manifest ``deleted``, sparing only the keepsake (manifest + reports + the
        shallow langfuse loop trace). The cross-campaign measurement cache
        (``measurements/``) is NEVER touched — it belongs to no single campaign, so a
        re-run on the same ``(dataset × config)`` reproduces the identical key.

        ``inner_sandbox_root`` (the workspace ``.inner/``, i.e.
        ``Stores.projects_root.parent / ".inner"``) — when given, the off-registry L4
        inner-proxy sandboxes this campaign's cycles spawned
        (``.inner/<cycle_id>``, siblings of ``projects/`` and so missed by the
        campaign-tree rmtree above) are removed for every cycle_id. Without this a
        deleted L4 campaign orphans dozens of inner campaign trees on disk forever.
        Passed only on delete — NOT on archive, which is recoverable and must keep the
        sandboxes for a later unarchive + the self-potter-hop.

        Returns ``False`` if the campaign isn't found; raises ``ConflictError`` if
        it's the active campaign."""
        campaign_dir = self._campaign_dir(campaign_id)
        if not (campaign_dir / "campaign.json").is_file():
            return False
        if self._is_active_campaign(campaign_id):
            raise ConflictError(f"refusing to delete {campaign_id}: active campaign — switch first")
        # Enumerate cycle_ids BEFORE the tree is stripped/removed — the inner
        # sandboxes are keyed by cycle_id and live off-tree, so we need the ids first.
        inner_cycle_ids: list[str] = []
        if inner_sandbox_root is not None:
            cycles_dir = campaign_dir / "cycles"
            if cycles_dir.is_dir():
                inner_cycle_ids = [p.name for p in cycles_dir.iterdir() if p.is_dir()]
        if keep_results:
            self.update_campaign(
                campaign_id, self._lifecycle_updates("deleted", changed_at, reason)
            )
            _strip_to_keepsake(campaign_dir)
        else:
            _rmtree_robust(campaign_dir)
        if inner_sandbox_root is not None:
            for cycle_id in inner_cycle_ids:
                inner_dir = inner_sandbox_root / cycle_id
                if inner_dir.exists():
                    _rmtree_robust(inner_dir)
        return True

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
        layout = self._layout(campaign_id, cycle_id)
        rounds_dir = layout.rounds
        candidates_dir = layout.candidates_cache

        ledger_path = layout.ledger
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
            target = layout.round_file(after_round)
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
            archive_root = layout.rewind_archive(ts)
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
        self._rewind_dashboard(layout.cycle_dir, after_round)

    @staticmethod
    def _rewind_dashboard(cycle_dir: Path, after_round: int) -> None:
        """Truncate ``dashboard.json`` to the same survivors the index rebuild kept.

        ``LiveDashboardView`` is the sole LIVE writer of ``dashboard.json``, and its
        ``L1_GENERATE:enter`` clamp would drop the rewound rounds anyway — but only once
        the resumed run reaches its first generate. Until then the two ``rounds[]``
        surfaces (``index.json`` via ``get_cycle``, ``dashboard.json`` via ``/lineage``)
        would disagree. Rewind runs offline (admissibility-gated, pre-resume, no view
        alive), so this one repair write closes the window using the view's own
        derivations: rounds ≤ ``after_round`` survive, ``round`` points at the last
        survivor, ``best`` = max surviving ``cumulative_accuracy`` (mirrors
        ``_apply_best`` / ``resolve_resume_state``). Missing/corrupt file ⇒ no-op —
        the resume factory tolerates absence."""
        path = CycleLayout(cycle_dir).dashboard
        dash = read_json_tolerant(path)
        if not isinstance(dash, dict):
            return
        survivors = [
            r
            for r in dash.get("rounds") or []
            if isinstance(r, dict) and int(r.get("round") or 0) <= after_round
        ]
        dash["rounds"] = survivors
        dash["round"] = after_round
        dash["best"] = max(
            (float(r.get("cumulative_accuracy") or 0.0) for r in survivors), default=0.0
        )
        write_json(path, dash)

    def _rebuild_round_index(
        self,
        campaign_id: str,
        cycle_id: str,
        survivors: list[Path],
    ) -> None:
        index_path = self._index_path(campaign_id, cycle_id)
        data = read_json(index_path)

        data["rounds"] = [round_summary(read_json(p)) for p in sorted(survivors)]
        data["n_rounds"] = len(data["rounds"])
        _apply_best(data)
        data["updated_at"] = utcnow_iso()
        write_json(index_path, data)

    def mark_finished(
        self,
        campaign_id: str,
        cycle_id: str,
        *,
        status: str,
        stop_reason: str,
        finished_at: str,
        interrupted_round: int | None = None,
        crash_traceback: str | None = None,
        final: dict[str, Any] | None = None,
    ) -> None:
        """Write the terminal facts this seam uniquely owns: lifecycle status +
        the ``final`` winner block.

        ``best_accuracy`` / ``best_round`` / ``n_rounds`` are NOT written here — they
        are owned by the ``rounds[]``-writer (``save_round_file`` → ``_apply_best``) on
        the origin-inclusive / full-population ``cumulative_accuracy`` basis. The old
        copy-from-``CycleResult`` here maxed on a different (origin-EXCLUSIVE) round
        count, so a now-deleted ``no_advance`` guard fired on every origin-bearing
        cycle and silently dropped the whole block — leaving ``final`` permanently
        unwritten. ``final`` is written unconditionally now (a crash finalize records a
        valid crash verdict)."""
        from promptpotter.shared.errors import graceful

        updates: dict[str, Any] = {
            "status": status,
            "stop_reason": stop_reason,
            "finished_at": finished_at,
        }
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

    def mark_producer_vanished(self, campaign_id: str, cycle_id: str) -> bool:
        """Reap a dead cycle: stamp it ``TERMINAL`` (``producer_vanished``) so the
        one liveness owner and the on-disk truth agree.

        Idempotent — a no-op (returns ``False``) if the cycle already carries a
        ``finished_at`` (never clobber a real ``final`` / crash verdict). Unlike
        a normal :meth:`mark_finished` call there is no verdict — the producer
        simply vanished (crash / kill / sleep left no terminal record, or the
        cycle predates ``finished_at``) — but the write itself IS
        :meth:`mark_finished` (``final=None``): one terminal-stamp seam for every
        stop reason, reaped or not. The caller decides the cycle is dead; this
        method only records it, and inherits ``mark_finished``'s ``graceful()``
        swallow — a truthy return means the write was *attempted* after the
        guards below passed, not that it durably landed. Resumability is
        unaffected (admissibility is ledger-gated, `scan_ledger_max_round_complete`).

        Never terminates a **paused** or **check-in** cycle — pause is an
        intentional, resumable suspend and check-in is pre-loop origin authoring
        (no ``dashboard.json``, no producer to go silent); both are distinct from
        a dead producer, and both reaper paths defer to these two invariants here
        (not just the sweep's staleness gate). A paused cycle stays a suspended
        unit until the operator resumes or archives it."""
        cycle_dir = self.cycle_dir(campaign_id, cycle_id)
        if CycleLayout(cycle_dir).pause_flag.is_file() or is_checkin(cycle_dir):
            return False
        index_path = self._index_path(campaign_id, cycle_id)
        data = read_json_optional(index_path)
        if not isinstance(data, dict) or data.get("finished_at"):
            return False
        reason = StopReason.PRODUCER_VANISHED.value
        self.mark_finished(
            campaign_id,
            cycle_id,
            status=reason,
            stop_reason=reason,
            finished_at=utcnow_iso(),
        )
        return True

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
                    "origin_accuracy": origin_accuracy_of(data),
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
        try:
            index = read_json_optional(index_path)
        except (OSError, json.JSONDecodeError) as exc:
            return False, f"index.json unreadable: {exc}"
        if index is None:
            return False, "not on disk"
        if root_cycle_id(cycle_id) == cycle_id:
            return False, "family root — deletion is for sibling stubs only"
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

    def write_fresh_sibling(
        self,
        campaign_id: str,
        parent_cycle_id: str,
        new_cycle_id: str,
        kind: str,
        *,
        forked_at: str,
        **blob_kwargs: Any,
    ) -> Path:
        """Read parent index → fresh sibling blob → write child index. The single
        writer for the diag / operator-steered / sweep fork triggers (``kind`` ∈
        ``{"diag", "fork", "sweep"}``; numbering restarts at round 1, no
        parent-round inheritance — that's ``save_rebase_fork``'s job)."""
        parent_index = read_json_optional(self._index_path(campaign_id, parent_cycle_id)) or {}
        blob = fresh_sibling_index_blob(
            parent_index, parent_cycle_id, kind, forked_at, **blob_kwargs
        )
        path = self._index_path(campaign_id, new_cycle_id)
        write_json(path, blob)
        return path

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
        index = {
            **parent_index,
            "parent_cycle_id": parent_cycle_id,
            "sibling_kind": "fork",
            "forked_from_round": forked_from_round,
            "forked_at": forked_at,
            "rounds": list(surviving_rounds),
            "n_rounds": len(surviving_rounds),
            "status": "resumed",
            "updated_at": forked_at,
        }
        _apply_best(index)
        # Identity is the directory name — never inherit a stored id from the parent.
        index.pop("cycle_id", None)
        index.pop("campaign_id", None)
        path = self._index_path(campaign_id, new_cycle_id)
        write_json(path, index)
        return path

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

        detail_path = self._layout(campaign_id, cycle_id).round_file(round_num)
        write_json(detail_path, round_data)

        index_path = self._index_path(campaign_id, cycle_id)
        data = read_json(index_path)

        data["rounds"] = [t for t in data["rounds"] if t.get("round") != round_num]
        data["rounds"].append(round_summary(round_data))
        data["n_rounds"] = len(data["rounds"])
        _apply_best(data)
        data["updated_at"] = utcnow_iso()
        write_json(index_path, data)

        return detail_path

    def load_round_file(
        self,
        campaign_id: str,
        cycle_id: str,
        round_num: int,
    ) -> dict[str, Any] | None:
        return read_json_optional(self._layout(campaign_id, cycle_id).round_file(round_num))

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
        path = self._layout(campaign_id, cycle_id).candidate_file(round_num)
        write_json(path, candidates)
        logger.debug("Saved %d candidates for round %d → %s", len(candidates), round_num, path.name)

    def load_round_candidates(
        self,
        campaign_id: str,
        cycle_id: str,
        round_num: int,
    ) -> list[dict[str, Any]] | None:
        return read_json_optional(self._layout(campaign_id, cycle_id).candidate_file(round_num))

    def delete_round_candidates(
        self,
        campaign_id: str,
        cycle_id: str,
        round_num: int,
    ) -> None:
        """Delete cached candidates (forces fresh generation)."""
        path = self._layout(campaign_id, cycle_id).candidate_file(round_num)
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
        path = self._layout(campaign_id, cycle_id).seed
        write_json(path, seed.model_dump(mode="json"))
        return path

    def read_cycle_seed(self, campaign_id: str, cycle_id: str) -> CycleSeed | None:
        """Load the cycle seed, or ``None`` when this cycle wasn't seeded."""
        data = read_json_optional(self._layout(campaign_id, cycle_id).seed)
        if data is None:
            return None
        return CycleSeed.model_validate(data)


__all__ = ["CampaignStore"]
