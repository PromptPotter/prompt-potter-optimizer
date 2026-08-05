"""Campaign + cycle file I/O under ``campaigns/{campaign_id}/`` — one cohesive store.

One store owns the whole tree, so no sibling writer can disagree about its shape. The
cycle seed rides the ledger as a read-once ``CycleSeedRecord``, never a ``.overrides/``
sidecar, which is what makes a resume or fork recover it by construction; the pure,
no-subscriber ledger scans it needs live in ``ledger_scan.py``. Fork projections and
the ``.runtime/`` flags it must not be confused with: ``infrastructure/CLAUDE.md``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from promptpotter.domain.campaign import Campaign
from promptpotter.domain.cycle_paths import CycleDir, WorkspaceDir
from promptpotter.domain.phases import RunPhase, StopReason
from promptpotter.domain.results import RoundResult, best_round_by_measured_accuracy
from promptpotter.domain.run_records import CycleSeed, CycleSeedRecord
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.runtime_flags import derive_run_phase, is_checkin
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_ledger_cycle_seed,
    scan_ledger_max_round_complete,
)
from promptpotter.infrastructure.store.io import (
    read_json,
    read_json_optional,
    read_json_tolerant,
    rmtree_robust,
    unlink_robust,
    validate_path_component,
    write_json,
)
from promptpotter.infrastructure.store.layout import (
    CycleLayout,
    archive_root_dir_for,
    campaign_cycles_dir,
    campaign_root_dir_for,
    classify,
    cycle_dir_under,
    inner_sandbox_key,
    root_cycle_id,
    sibling_kind,
)
from promptpotter.infrastructure.store.session_pointer import (
    clear_active_pointer,
    read_active_pointer,
)
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import BadRequestError, ConflictError, NotFoundError

logger = logging.getLogger(__name__)


def round_summary(rr: RoundResult) -> dict[str, Any]:
    """Projection of a round into the ``index.json::rounds`` shape."""
    return {
        "round_id": rr.round_id,
        "round": rr.round,
        "label": rr.label,
        "accuracy": rr.accuracy,
        "total": rr.total,
        "improved": rr.improved,
        "status": rr.status,
    }


def origin_accuracy_of(index: dict[str, Any]) -> float | None:
    """The origin's round-0 score, derived from ``rounds[]`` — there is no stored copy.

    Round 0 IS the origin, and every path that (re)scores it — bootstrap, a diag
    fork's re-measure, the interactive origin-gate rescore — re-emits round 0
    through ``save_round_file``, so the round row is always fresh. ``None`` until
    round 0 lands (fresh mint / pre-origin fork)."""
    rounds = index.get("rounds") or []
    return next((float(r["accuracy"]) for r in rounds if r.get("round") == 0), None)


def _apply_best(data: dict[str, Any]) -> None:
    """Set the index's ``best_accuracy`` / ``best_round`` from ``data["rounds"]`` via the
    shared ``best_round_by_measured_accuracy`` (which resume/fork rebuild also rides, so
    ``index.json`` and ``dashboard.json::best`` agree by construction).

    Best = the highest ``accuracy`` any round actually MEASURED — the elected winner on the
    samples that round drew, or on a held round the retained incumbent's re-score. It is
    therefore always a number some individual scored, over a stated ``total``.

    It used to argmax ``cumulative_accuracy``, described here as "the incumbent rescored over
    every sample probed so far". No such rescore ever happened: the series was a sample-keyed
    union of rows measured by DIFFERENT configurations, so the headline routinely exceeded
    anything the cycle had measured — ``57%→78%`` on a run whose best candidate reached 0.679.
    That claim is what kept the defect invisible; do not reintroduce a pooled mean here.

    Deliberately a DIFFERENT basis from the winner export: ``cycle.py::absorb_round`` /
    ``replay_priors`` argmax ``best_sp``/``best_round`` on ``composite_fitness`` — the
    optimizer's objective, also the L2/L3 stall comparator (``escalation/firing.py``). This
    headline argmaxes plain ``accuracy`` — the formula-independent number operators
    recognize. Under a non-accuracy formula the two ``best_round``s can legitimately
    disagree; flipping either to match would break the objective (winner side) or the
    display contract (this side). See ``architecture.md`` §0.5."""
    data["best_accuracy"], data["best_round"] = best_round_by_measured_accuracy(data["rounds"])


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
    cycles_dir = campaign_cycles_dir(campaign_dir)
    if cycles_dir.is_dir():
        for cdir in cycles_dir.iterdir():
            if not cdir.is_dir():
                continue
            for p in [
                f
                for f in cdir.rglob("*")
                if f.is_file() and not classify(f.relative_to(campaign_dir)).keepsake
            ]:
                unlink_robust(p)
            _prune_empty_dirs(cdir)
    sweeps = campaign_dir / "sweeps"
    if sweeps.exists():
        rmtree_robust(sweeps)


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
    files, and the cycle-seed ledger I/O (``CycleSeedRecord``).
    """

    def __init__(self, base_dir: WorkspaceDir):
        self._base_dir = base_dir

    @property
    def workspace(self) -> WorkspaceDir:
        """The tenant root this store is rooted at.

        Exposed because a caller holding a ``CampaignStore`` already holds a resolved
        tenant root, and used to be handed the same fact twice more — the fork mint
        took ``(campaign_store, tenant_id, projects_root=None)`` and defaulted the
        third, so the pointer it retargeted could belong to a different workspace than
        the cycle it just minted."""
        return self._base_dir

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

    def load_campaign(self, campaign_id: str) -> Campaign | None:
        data = read_json_optional(self._manifest_path(campaign_id))
        if data is None:
            return None
        return Campaign.model_validate(data)

    def load_owned(self, campaign_id: str, owner_user_id: str) -> Campaign | None:
        """Load *campaign_id* iff it exists AND is owned by *owner_user_id*, else ``None``.

        The single definition of owner-scoped campaign access: a missing campaign and a
        cross-owner one collapse to ``None`` so every caller 404s both the same way
        (existence-hiding). Callers keep their own ``NotFoundError`` message/code — only
        the ownership rule lives here, not copied at each read/command site.
        """
        campaign = self.load_campaign(campaign_id)
        if campaign is None or campaign.owner_user_id != owner_user_id:
            return None
        return campaign

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

    def iter_campaign_dirs(self) -> list[Path]:
        """Every campaign dir (has a ``campaign.json``) under ``campaigns/`` AND
        ``archive/``. The one campaign-tree enumeration — anything summing or
        reducing over "all campaigns" walks this, so an archived campaign's
        spend and measurements stay visible."""
        out: list[Path] = []
        for parent in self._campaign_parents():
            if parent.exists():
                out.extend(p.parent for p in parent.glob("*/campaign.json"))
        return sorted(out)

    def iter_cycle_ledgers(self) -> list[Path]:
        """Every per-cycle ledger (``cycles/*/.runtime/ledger.jsonl``) across the
        campaign tree, archived included — archiving a campaign mid-day must not
        free daily spend-cap budget."""
        return [
            ledger
            for campaign_dir in self.iter_campaign_dirs()
            for ledger in sorted(campaign_cycles_dir(campaign_dir).glob("*/.runtime/ledger.jsonl"))
        ]

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

    def set_allowed_models(self, campaign_id: str, allowed_models: list[str]) -> None:
        """Rewrite the frozen config snapshot's ``allowed_models`` — the SINGLE source of
        truth for a campaign's model allow-list (read by both the fork-cycle cap-gate off
        ``campaign.config`` and, via ``apply_inherited_overlay``, the runner's grade-C
        stamp). Empty clears the key (delta-snapshot convention: absent = restrictive
        default). Identity-neutral — ``allowed_models`` is not in ``root_content_hash``,
        so no re-stamp and no re-measure. The ``set-allowed-models`` command's writer."""
        path = self._manifest_path(campaign_id)
        data = read_json(path)
        config = data.get("config")
        if not isinstance(config, dict):
            config = {}
        if allowed_models:
            config["allowed_models"] = list(allowed_models)
        else:
            config.pop("allowed_models", None)
        data["config"] = config
        write_json(path, data)

    def repoint_dataset(self, old_name: str, new_name: str) -> int:
        """Move every campaign pinned to *old_name* onto *new_name*. Returns the count.

        The campaign half of the dataset version-and-repoint migration
        (``application/datasets/dataset_replace.py``): rewrites the manifest pin
        (``campaign.json::dataset_name``) — the ONE owner; every cycle-level
        reader derives from it. After this, a prior campaign resolves the exact
        bytes it always ran on, now living under *new_name*. Any lifecycle
        (active / archived / deleted) — an archived campaign's data must stay
        truthful too. Idempotent: a campaign already on *new_name* doesn't match
        and is skipped.
        """
        count = 0
        for cid in self.list_campaign_ids():
            campaign = self.load_campaign(cid)
            if campaign is None or campaign.dataset_name != old_name:
                continue
            self.update_campaign(cid, {"dataset_name": new_name})
            count += 1
        return count

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

    def _live_cycle_ids(self, campaign_id: str) -> list[str]:
        """Cycles of *campaign_id* with a producer attached RIGHT NOW.

        ``RUNNING`` is the whole answer, and it is the only phase that means a process
        is writing here: a cycle held at the origin ``GATE`` is still heartbeating, so
        it derives ``RUNNING`` too. Each of the others is explicitly NOT a reason to
        refuse a verb the operator asked for — ``PAUSED`` and ``CHECKIN`` are resumable
        but unattended, ``DETACHED`` is a dead producer (the reaper's business), and
        ``TERMINAL`` is finished. Derived through the one liveness function every other
        reader uses, never a second "is it running?" of our own.
        """
        cycles_dir = campaign_cycles_dir(self._campaign_dir(campaign_id))
        if not cycles_dir.is_dir():
            return []
        live: list[str] = []
        for cdir in sorted(p for p in cycles_dir.iterdir() if p.is_dir()):
            data = read_json_optional(CycleLayout(cdir).manifest)
            if not isinstance(data, dict):
                continue
            if (
                derive_run_phase(cdir, is_terminal=bool(data.get("finished_at")))
                is RunPhase.RUNNING
            ):
                live.append(cdir.name)
        return live

    def live_cycle_ids(self, campaign_id: str) -> list[str]:
        """Cycles of *campaign_id* whose producer is alive right now.

        Public because two verbs need it and there must not be two answers: campaign
        archive/delete asks below, and the control plane asks before deleting a stub
        cycle. Both route to the one ``derive_run_phase``.
        """
        return self._live_cycle_ids(campaign_id)

    def _guard_and_release(self, campaign_id: str, verb: str) -> None:
        """Refuse *verb* while a producer is attached; otherwise release the pointer.

        The only hazard is a LIVE cycle's open ``.runtime/`` handles; a stranded
        pointer is a UI state to fix, not a reason to refuse. So the live check
        refuses, and the pointer is simply released — deleting what you are looking
        at is the ordinary case, not the dangerous one.
        """
        if live := self._live_cycle_ids(campaign_id):
            raise ConflictError(
                f"refusing to {verb} {campaign_id}: cycle {live[0]} has a live producer "
                "— pause or stop it first"
            )
        _, active_campaign, _ = read_active_pointer(self._base_dir)
        if active_campaign == campaign_id:
            clear_active_pointer(self._base_dir)

    def _lifecycle_updates(self, status: str, changed_at: str, reason: str) -> dict[str, str]:
        return {
            "lifecycle_status": status,
            "lifecycle_changed_at": changed_at,
            "lifecycle_reason": reason,
        }

    def archive_campaign(self, campaign_id: str, *, changed_at: str, reason: str = "") -> bool:
        """Flag the manifest ``archived`` then MOVE the tree into the ``archive/``
        recycle bin. Returns ``False`` if the campaign isn't found; raises
        ``ConflictError`` if any of its cycles has a LIVE producer (see
        ``_guard_and_release``, which also releases the active pointer when it names
        this campaign). ``unarchive_campaign`` reverses it. Measurements
        (``measurements/``) are untouched."""
        if self.load_campaign(campaign_id) is None:
            return False
        self._guard_and_release(campaign_id, "archive")
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
        ``inner_sandboxes_dir(Stores.shared_root)``) — when given, the off-registry L4
        inner-proxy sandboxes this campaign's cycles spawned (siblings of ``projects/``
        and so missed by the campaign-tree rmtree above) are removed for every cycle_id.
        Without this a deleted L4 campaign orphans dozens of inner campaign trees on disk
        forever. Each is resolved through ``inner_sandbox_key`` on THIS campaign's identity,
        so the cascade cannot reach a sibling campaign minted from the same origin — keyed
        on the cycle alone, as it was, deleting one such campaign took the other's inner
        history with it.
        Passed only on delete — NOT on archive, which is recoverable and must keep the
        sandboxes for a later unarchive + the self-potter-hop.

        Returns ``False`` if the campaign isn't found; raises ``ConflictError`` if any
        of its cycles has a LIVE producer (``_guard_and_release``, which also releases
        the active pointer when it names this campaign — deleting the campaign you are
        looking at is the ordinary case)."""
        campaign_dir = self._campaign_dir(campaign_id)
        if not (campaign_dir / "campaign.json").is_file():
            return False
        self._guard_and_release(campaign_id, "delete")
        # Enumerate cycle_ids BEFORE the tree is stripped/removed — the inner
        # sandboxes are keyed by cycle_id and live off-tree, so we need the ids first.
        inner_cycle_ids: list[str] = []
        if inner_sandbox_root is not None:
            cycles_dir = campaign_cycles_dir(campaign_dir)
            if cycles_dir.is_dir():
                inner_cycle_ids = [p.name for p in cycles_dir.iterdir() if p.is_dir()]
        if keep_results:
            self.update_campaign(
                campaign_id, self._lifecycle_updates("deleted", changed_at, reason)
            )
            _strip_to_keepsake(campaign_dir)
        else:
            rmtree_robust(campaign_dir)
        if inner_sandbox_root is not None:
            tenant_id = self._base_dir.name
            for cycle_id in inner_cycle_ids:
                inner_dir = inner_sandbox_root / inner_sandbox_key(tenant_id, campaign_id, cycle_id)
                if inner_dir.exists():
                    rmtree_robust(inner_dir)
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
        now = utcnow_iso()
        defaults: dict[str, Any] = {
            "created_at": existing.get("created_at", now),
            "updated_at": now,
            "status": "active",
            "type": "optimization_loop",
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
    ) -> None:
        """Mark the cycle babysat and append one entry to its intervention log.

        Written the moment the operator intervenes (e.g. skip-searchpoint), not at
        teardown — a still-running babysat cycle must already be distinguishable
        from a pure/reproducible one. Idempotent on the boolean; the log grows by
        one per gesture.

        No ``round`` / ``candidate``: both were declared, both defaulted to ``None``, and the
        sole caller could not have filled them if it tried — ``_apply_skip_searchpoint`` writes
        a one-shot ``skip.flag`` that the RUNNER consumes at some later per-sample checkpoint,
        so at write time nobody knows which round or candidate will pick it up. Every entry
        therefore carried ``"round": None, "candidate": None``. A signature asking its caller
        for information the caller structurally cannot have is not an unwired feature."""
        path = self._index_path(campaign_id, cycle_id)
        data = read_json(path)
        data["human_intervened"] = True
        # setdefault: the babysit stamp fires at bootstrap on a fresh-sibling fork index
        # that carries no `interventions` list yet, unlike the skip-searchpoint caller
        # which runs on an established cycle. The single append site guarantees the key.
        data.setdefault("interventions", []).append({"kind": kind, "at": at})
        data["updated_at"] = utcnow_iso()
        write_json(path, data)

    def rewind_to_round(
        self,
        campaign_id: str,
        cycle_id: str,
        after_round: int,
    ) -> None:
        """Delete round + candidate files for rounds > ``after_round``; rebuild the round index. Ledger-admissibility-gated."""
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
        displaced: list[Path] = []
        for p in sorted(rounds_dir.glob("round_*.json")):
            try:
                n = int(p.stem.removeprefix("round_"))
            except ValueError:
                continue
            (displaced if n > after_round else survivors).append(p)
        if candidates_dir.exists():
            for p in sorted(candidates_dir.glob("round_*.json")):
                try:
                    n = int(p.stem.removeprefix("round_"))
                except ValueError:
                    continue
                if n > after_round:
                    displaced.append(p)

        if displaced:
            for p in displaced:
                unlink_robust(p)
            logger.info(
                "Rewind cycle %s to round %d: deleted %d displaced file(s)",
                cycle_id,
                after_round,
                len(displaced),
            )

        # No dashboard.json repair here. `resolve_resume_state` cuts the surviving
        # trajectory when the resumed run's view is constructed — the one cut, off the
        # schema. A second writer here re-spelled that rule against the raw dict, with
        # its own `max(...)` fold in place of the domain helper.
        self._rebuild_round_index(campaign_id, cycle_id, survivors)

    def _rebuild_round_index(
        self,
        campaign_id: str,
        cycle_id: str,
        survivors: list[Path],
    ) -> None:
        index_path = self._index_path(campaign_id, cycle_id)
        data = read_json(index_path)

        data["rounds"] = [
            round_summary(RoundResult.model_validate(read_json(p))) for p in sorted(survivors)
        ]
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
        the origin-inclusive measured-``accuracy`` basis. ``final`` is written
        unconditionally (a crash finalize records a valid crash verdict)."""
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

    def reopen_for_continuation(self, campaign_id: str, cycle_id: str) -> None:
        """Clear the terminal latch so a continued cycle stops claiming it finished.

        The exact inverse of :meth:`mark_finished`, and the ONLY writer that removes
        ``finished_at``. That field is a latch, not a note: :func:`derive_run_phase`
        returns ``TERMINAL`` on it before it consults anything else, so the cycle list,
        the picker and the reaper's staleness sweep all read it. Continuing a cycle
        without clearing it leaves a live producer on a cycle every reader calls
        finished — and ``TERMINAL`` is precisely the phase the reaper's guards do not
        refuse, so the next sweep would re-stamp it underneath the run.

        ``final`` is removed with it because it names a winner for a trajectory that is
        about to grow; a verdict that outlives the round justifying it is worse than
        none. ``interrupted_round`` / ``crash_traceback`` describe the stop being undone.
        """
        self.update(
            campaign_id,
            cycle_id,
            {"status": "active"},
            remove=[
                "finished_at",
                "stop_reason",
                "final",
                "interrupted_round",
                "crash_traceback",
            ],
        )

    def mark_superseded(self, campaign_id: str, cycle_id: str) -> bool:
        """The line moved to a branch: stamp this cycle ``TERMINAL`` (``REBASED``).

        The retirement half of what :meth:`mark_producer_vanished` answers — both record that
        a cycle stopped writing, and the difference is WHY. Unstamped, a parent that stops
        **by design** presents exactly as a crashed one. Idempotent (an L2/L3 rebase already
        finalizes its own parent), and :meth:`reopen_for_continuation` still clears the latch.
        """
        return self._stamp_terminal(campaign_id, cycle_id, StopReason.REBASED)

    def _stamp_terminal(self, campaign_id: str, cycle_id: str, reason: StopReason) -> bool:
        """Stamp a cycle terminal with no verdict, ONCE — ``False`` if it already carries one.
        The shared tail of the two ways a cycle stops without finishing."""
        data = read_json_optional(self._index_path(campaign_id, cycle_id))
        if not isinstance(data, dict) or data.get("finished_at"):
            return False
        self.mark_finished(
            campaign_id,
            cycle_id,
            status=reason.value,
            stop_reason=reason.value,
            finished_at=utcnow_iso(),
        )
        return True

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

        Never terminates a **paused**, **check-in**, or **origin-gated** cycle —
        pause is an intentional, resumable suspend; check-in is pre-loop origin
        authoring (no ``dashboard.json``, no producer to go silent); and a gated
        cycle is a live process holding for an operator decision. None is a dead
        producer, and both reaper paths defer to these invariants here (not just
        the sweep's staleness gate).

        Pause is checked through BOTH its writers — the operator's flag and the
        runner's declaration — because they are not the same event. A Ctrl+C out
        of a long phase declares ``paused`` and exits without ever writing a flag
        (``runner/entry.py``'s prep guard), so a flag-only check reaped exactly the
        runs the operator had stopped by hand: the ledger said ``control/paused``,
        and fifteen minutes later the index said ``producer_vanished``.

        The gate invariant is the newest and was bought with a real bug: the gate's
        unbounded wait wrote nothing, so the cycle went stale in 30 s and was reaped
        TERMINAL 15 minutes later while alive and still polling — and a decision
        arriving after that resumed a run into a cycle marked finished. The wait now
        heartbeats (``runner/origin_gate.py``), which is the fix; this check is the
        second line, because a heartbeat can still lose a race a machine sleep
        creates, and being reaped is not recoverable by asking again."""
        cycle_dir = self.cycle_dir(campaign_id, cycle_id)
        layout = CycleLayout(cycle_dir)
        if layout.pause_flag.is_file() or is_checkin(cycle_dir):
            return False
        dash = read_json_optional(layout.dashboard)
        if isinstance(dash, dict) and dash.get("run_phase") in (RunPhase.GATE, RunPhase.PAUSED):
            return False
        return self._stamp_terminal(campaign_id, cycle_id, StopReason.PRODUCER_VANISHED)

    def _entry_from_index(self, index_path: Path) -> dict[str, Any]:
        """THE decoder of ``index.json`` into the served ``CycleListEntry`` shape."""
        campaign_id, cycle_id = self._ids_from_index_path(index_path)
        data = read_json_tolerant(index_path)
        kind = sibling_kind(cycle_id)
        # The single run-phase derivation — running / paused / stopping /
        # detached / terminal. Lifecycle (terminal) comes from index
        # ``finished_at``; control + freshness from derive_run_phase. This is
        # the one computation the picker, both live dots, and the badge all
        # read — no surface re-derives "running" from its own inputs.
        is_terminal = isinstance(data, dict) and bool(data.get("finished_at"))
        run_phase = str(derive_run_phase(index_path.parent, is_terminal=is_terminal))
        if not isinstance(data, dict):
            data = {}
            status = "unreadable"
        else:
            status = str(data.get("status", ""))
        header_raw = data.get("header")
        header: dict[str, Any] = header_raw if isinstance(header_raw, dict) else {}
        sk = data.get("sibling_kind", kind)
        fork_raw = data.get("fork")
        fork_trigger = fork_raw.get("trigger") if isinstance(fork_raw, dict) else None
        # Derived from the one owner (campaign.json::dataset_name) — no per-cycle copy.
        campaign = self.load_campaign(campaign_id)
        dataset_name = campaign.dataset_name if campaign is not None else ""
        return {
            "campaign_id": campaign_id,
            "cycle_id": cycle_id,
            "parent_session_id": data.get("parent_session_id", ""),
            "parent_cycle_id": data.get("parent_cycle_id")
            or (None if kind == "root" else root_cycle_id(cycle_id)),
            "dataset_name": dataset_name,
            "backend_id": header.get("backend_id", ""),
            "sibling_kind": sk,
            "unit_kind": _unit_kind(sk, fork_trigger),
            "is_root": kind == "root",
            "status": status,
            "run_phase": run_phase,
            "best_accuracy": data.get("best_accuracy"),
            "origin_accuracy": origin_accuracy_of(data),
            "n_rounds": data.get("n_rounds", 0),
            "created_at": data.get("created_at", ""),
            "updated_at": data.get("updated_at", ""),
            "human_intervened": bool(data.get("human_intervened", False)),
            "spawned_by": data.get("spawned_by"),
        }

    def enumerate_cycles(self) -> list[dict[str, Any]]:
        """Every cycle on disk in the webapp-picker shape; unreadable → ``status='unreadable'`` stubs."""
        return [self._entry_from_index(p) for p in self._index_files()]

    def try_delete_stub_cycle(self, campaign_id: str, cycle_id: str) -> tuple[bool, str]:
        """Delete a stub cycle dir → ``(deleted, reason)``. Guards: banked nothing of its
        OWN, not a family root, no children.

        "Nothing of its own" is measured against what the cut INHERITED, not against zero.
        A rebase fork copies rounds ``0..from_round-1`` at mint, so one that never ran a
        round still carries ``n_rounds == from_round`` — and against a flat ``n_rounds == 0``
        every rebase fork read as "ran real work". The docstring named that family and the
        guard could not reach it, so the one cleanup path in the codebase was unusable for
        the one family that mints stubs on failure.

        A ``from_round`` we cannot read counts as 0, which is the strict reading: when it is
        unknown what the fork inherited, refuse rather than delete.
        """
        cycle_dir = self.cycle_dir(campaign_id, cycle_id)
        index_path = self._index_path(campaign_id, cycle_id)
        # NOT read_json_tolerant: this has to tell "absent" from "corrupt" — one is a
        # stub to delete, the other is a cycle whose state we cannot vouch for.
        try:
            index = read_json_optional(index_path)
        except (OSError, json.JSONDecodeError) as exc:
            return False, f"index.json unreadable: {exc}"
        if index is None:
            return False, "not on disk"
        if root_cycle_id(cycle_id) == cycle_id:
            return False, "family root — deletion is for sibling stubs only"
        n_rounds = index.get("n_rounds", 0)
        fork = index.get("fork")
        from_round = (fork or {}).get("from_round") if isinstance(fork, dict) else None
        inherited = from_round if isinstance(from_round, int) and from_round > 0 else 0
        if not isinstance(n_rounds, int) or n_rounds > inherited:
            return False, (
                f"n_rounds={n_rounds} against {inherited} inherited — cycle ran real work"
            )
        for other in self._index_files():
            other_campaign, other_cycle = self._ids_from_index_path(other)
            if other_campaign != campaign_id or other_cycle == cycle_id:
                continue
            other_data = read_json_optional(other)
            if isinstance(other_data, dict) and other_data.get("parent_cycle_id") == cycle_id:
                return False, f"has descendant {other_cycle}"
        rmtree_robust(cycle_dir)
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
        """The single writer for the diag / operator-steered / sweep fork triggers
        (``kind`` ∈ ``{"diag", "fork", "sweep"}``; numbering restarts at round 1, no
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
        surviving_rounds: list[RoundResult],
    ) -> Path:
        """Mid-cycle fork inheriting parent state up to ``forked_from_round``.

        Single writer for all 4 rebase-shaped triggers
        (``SCORING_DIVERGENCE``, ``L2_REBASE``, ``L3_REBASE``,
        ``OPERATOR_REWIND``). The issuer is recorded on the FORK_CUT
        ledger record via ``ForkSpec.trigger``.

        ``rounds[]`` is the index SUMMARY shape, projected here — never whole round
        documents, which would bury every per-sample row in ``index.json``.
        """
        parent_index = read_json_optional(self._index_path(campaign_id, parent_cycle_id)) or {}
        index = {
            **parent_index,
            "parent_cycle_id": parent_cycle_id,
            "sibling_kind": "fork",
            "forked_from_round": forked_from_round,
            "forked_at": forked_at,
            "rounds": [round_summary(rr) for rr in surviving_rounds],
            "n_rounds": len(surviving_rounds),
            "status": "resumed",
            "updated_at": forked_at,
        }
        _apply_best(index)
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
        rr: RoundResult,
    ) -> Path:
        """Persist a round detail file and update the cycle index.

        ``rr.model_dump()`` IS the file — there is no payload dict between the round
        and the disk, so a field cannot be computed and then dropped on the way out.
        """
        validate_path_component(rr.round_id)

        detail_path = self._layout(campaign_id, cycle_id).round_file(rr.round)
        write_json(detail_path, rr.model_dump(mode="json"))

        index_path = self._index_path(campaign_id, cycle_id)
        data = read_json(index_path)

        data["rounds"] = [t for t in data["rounds"] if t.get("round") != rr.round]
        data["rounds"].append(round_summary(rr))
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
    ) -> RoundResult | None:
        """The sole typed read of a round document. A round file that fails validation is
        corrupt — it raises rather than degrading into a dict of defaults."""
        raw = read_json_optional(self._layout(campaign_id, cycle_id).round_file(round_num))
        return None if raw is None else RoundResult.model_validate(raw)

    def load_rounds_range(
        self,
        campaign_id: str,
        cycle_id: str,
        start: int,
        end: int,
    ) -> list[RoundResult]:
        """Load rounds ``start..end`` inclusive. Missing rounds skipped."""
        out: list[RoundResult] = []
        for r in range(start, end + 1):
            rr = self.load_round_file(campaign_id, cycle_id, r)
            if rr is not None:
                out.append(rr)
        return out

    def save_round_candidates(
        self,
        campaign_id: str,
        cycle_id: str,
        round_num: int,
        candidates: list[dict[str, Any]],
        *,
        consumed: str,
    ) -> None:
        """Persist generated candidates before scoring, WITH what they were generated from.

        ``consumed`` is ``round_document_digest`` of the round this generation read
        (``round_num - 1``). Without it a replayed cache is unfalsifiable: the candidates
        say what they are and nothing says whether the round they came out of still reads
        the same way, so a repaired or re-distilled predecessor is carried straight past
        the replay branch. Both halves ride ONE file so they cannot drift apart.
        """
        path = self._layout(campaign_id, cycle_id).candidate_file(round_num)
        write_json(path, {"consumed": consumed, "candidates": candidates})
        logger.debug("Saved %d candidates for round %d → %s", len(candidates), round_num, path.name)

    def load_round_candidates(
        self,
        campaign_id: str,
        cycle_id: str,
        round_num: int,
    ) -> tuple[list[dict[str, Any]], str | None] | None:
        """``(candidates, consumed)`` or ``None`` when no cache exists.

        ONE read for both facts, because every caller that trusts the candidates needs to
        know what they were generated from. ``consumed`` is ``None`` for a cache written
        before it was recorded — unvouched, which is a state to act on rather than a
        detail to shrug at.
        """
        raw = read_json_optional(self._layout(campaign_id, cycle_id).candidate_file(round_num))
        if raw is None:
            return None
        if isinstance(raw, dict):
            consumed = raw.get("consumed")
            return list(raw.get("candidates") or []), consumed if isinstance(
                consumed, str
            ) else None
        return list(raw), None

    def delete_round_candidates(
        self,
        campaign_id: str,
        cycle_id: str,
        round_num: int,
    ) -> None:
        """Delete cached candidates (forces fresh generation)."""
        path = self._layout(campaign_id, cycle_id).candidate_file(round_num)
        if path.exists():
            unlink_robust(path)
            logger.debug(
                "Deleted cached candidates for round %d (escalation invalidation)", round_num
            )

    # ------------------------------------------------------------------
    # Cycle-seed I/O — the read-once ``CycleSeedRecord`` on the ledger
    # ------------------------------------------------------------------

    def write_cycle_seed(self, campaign_id: str, cycle_id: str, seed: CycleSeed) -> None:
        """Append the cycle seed as the read-once ``CycleSeedRecord`` on the ledger
        (was ``.overrides/seed.json``). A steered fork appends its own after inheriting
        the parent's virtually, so on disk it is this cycle's own first seed record."""
        cycle_dir = self.cycle_dir(campaign_id, cycle_id)
        CycleEventLog.open(CycleDir(cycle_dir)).append(CycleSeedRecord(seed=seed))

    def read_cycle_seed(self, campaign_id: str, cycle_id: str) -> CycleSeed | None:
        """The cycle's own seed from its ledger, or ``None`` when it wasn't seeded. Pure
        scan — no ``CycleEventLog``, no subscribers fire (mirrors the admissibility scan)."""
        return scan_ledger_cycle_seed(self._layout(campaign_id, cycle_id).ledger)


__all__ = ["CampaignStore"]
