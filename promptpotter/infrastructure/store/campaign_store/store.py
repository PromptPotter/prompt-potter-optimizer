from __future__ import annotations

import contextlib
import json
import logging
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from promptpotter.domain.campaign import Campaign
from promptpotter.domain.cycle_paths import CycleDir, CycleHop, WorkspaceDir
from promptpotter.domain.export import PromptExport, parse_prompt_export
from promptpotter.domain.phases import RunPhase, StopReason
from promptpotter.domain.results import RoundResult, best_round_by_measured_accuracy
from promptpotter.domain.run_records import CycleSeed, CycleSeedRecord
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.runtime_flags import derive_run_phase, is_checkin
from promptpotter.infrastructure.store.account_spend import (
    FORWARDED_SPEND_KEY,
    BilledSpend,
    bank_spend,
    sandbox_cycle_dirs,
)
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_ledger_cycle_seed,
    scan_ledger_max_round_complete,
)
from promptpotter.infrastructure.store.io import (
    read_json,
    read_json_optional,
    read_json_tolerant,
    read_text_optional,
    rmtree_robust,
    unlink_robust,
    validate_path_component,
    write_json,
    write_text,
)
from promptpotter.infrastructure.store.layout import (
    ROUND_GLOB,
    CycleLayout,
    campaign_cycles_dir,
    campaign_root_dir_for,
    campaigns_root_dir_for,
    classify,
    cycle_dir_for,
    inner_sandbox_key,
    root_cycle_id,
    round_number,
    sibling_kind,
)
from promptpotter.infrastructure.store.session_pointer import (
    clear_active_pointer,
    read_active_pointer,
)
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import BadRequestError, ConflictError, NotFoundError

logger = logging.getLogger(__name__)


def _round_summary(rr: RoundResult) -> dict[str, Any]:
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
    """Round 0 IS the origin and there is no stored copy — every path that (re)scores it
    (init, a diag fork, the origin gate) re-emits round 0 through ``save_round_file``."""
    rounds = index.get("rounds") or []
    return next((float(r["accuracy"]) for r in rounds if r.get("round") == 0), None)


def _apply_best(data: dict[str, Any]) -> None:
    """Never argmax ``cumulative_accuracy``: no rescore backs that series, so the headline
    would exceed anything the cycle measured. Two deliberate bases — ``architecture.md`` §0.5."""
    data["best_accuracy"], data["best_round"] = best_round_by_measured_accuracy(data["rounds"])


def _fresh_sibling_index_blob(
    parent_index: dict[str, Any],
    parent_cycle_id: str,
    sibling_kind: str,
    forked_at: str,
    **extras: Any,
) -> dict[str, Any]:
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
    for d in sorted(
        (p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True
    ):
        with contextlib.suppress(OSError):  # non-empty (holds keepsake) or already gone
            d.rmdir()


def _strip_to_keepsake(campaign_dir: Path) -> None:
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
    if sibling_kind == "root":
        return "session"
    if fork_trigger == "scoring_divergence":
        return "divergent_resume"
    if fork_trigger in ("l2_rebase", "l3_rebase"):
        return "auto_rebase"
    return "user_fork"


class CampaignStore:
    def __init__(self, base_dir: WorkspaceDir):
        self._base_dir = base_dir

    @property
    def workspace(self) -> WorkspaceDir:
        """The tenant root this store is rooted at — ask the store rather than passing a second
        ``projects_root``, which lets a mint target a workspace other than its own cycle's."""
        return self._base_dir

    # ------------------------------------------------------------------
    # Path resolution + cross-cutting reads
    # ------------------------------------------------------------------

    def campaign_root_dir(self, campaign_id: str) -> Path:
        return campaign_root_dir_for(self._base_dir, campaign_id)

    def cycle_dir(self, hop: CycleHop) -> Path:
        return cycle_dir_for(self._base_dir, hop)

    def _manifest_path(self, campaign_id: str) -> Path:
        return self.campaign_root_dir(campaign_id) / "campaign.json"

    def _layout(self, hop: CycleHop) -> CycleLayout:
        return CycleLayout(self.cycle_dir(hop))

    def _index_path(self, hop: CycleHop) -> Path:
        return self._layout(hop).manifest

    def _rounds_dir(self, hop: CycleHop) -> Path:
        return self._layout(hop).rounds

    def _candidates_dir(self, hop: CycleHop) -> Path:
        return self._layout(hop).candidates_cache

    def load_campaign(self, campaign_id: str) -> Campaign | None:
        data = read_json_optional(self._manifest_path(campaign_id))
        if data is None:
            return None
        return Campaign.model_validate(data)

    def load_owned(self, campaign_id: str, owner_user_id: str) -> Campaign | None:
        """Missing and cross-owner both collapse to ``None`` so every caller 404s them alike
        (existence-hiding). The single definition — never re-checked at a call site."""
        campaign = self.load_campaign(campaign_id)
        if campaign is None or campaign.owner_user_id != owner_user_id:
            return None
        return campaign

    def _campaigns_root(self) -> Path:
        return campaigns_root_dir_for(self._base_dir)

    def _index_files(self) -> list[Path]:
        root = self._campaigns_root()
        return sorted(root.glob("*/cycles/*/index.json")) if root.exists() else []

    def iter_campaign_dirs(self) -> list[Path]:
        """The one campaign-tree enumeration, archived included — anything reducing over
        all campaigns walks this, so an archived campaign's spend stays visible."""
        root = self._campaigns_root()
        if not root.exists():
            return []
        return sorted(p.parent for p in root.glob("*/campaign.json"))

    def campaign_cycle_dirs(self, campaign_id: str) -> list[Path]:
        """One campaign's cycle directories — the enumeration `bank_spend` reads before
        `delete_campaign` destroys them, and the grain the L4 roll-up's forwarded mark sits at."""
        cycles = campaign_cycles_dir(self.campaign_root_dir(campaign_id))
        return sorted(p for p in cycles.glob("*") if p.is_dir())

    def campaign_cycle_ledgers(self, campaign_id: str) -> list[Path]:
        """One campaign's cycle ledgers. Derived from the dirs so the tree is spelled once."""
        return [
            ledger
            for cycle_dir in self.campaign_cycle_dirs(campaign_id)
            if (ledger := CycleLayout(cycle_dir).ledger).is_file()
        ]

    def iter_cycle_ledgers(self) -> list[Path]:
        """Every cycle ledger, archived included — archiving must not free daily spend-cap budget."""
        return [
            ledger
            for campaign_dir in self.iter_campaign_dirs()
            for ledger in self.campaign_cycle_ledgers(campaign_dir.name)
        ]

    @staticmethod
    def _ids_from_index_path(index_path: Path) -> tuple[str, str]:
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
        """The SINGLE source of truth for the allow-list, read by the fork cap-gate and the
        runner's overlay. Identity-neutral: not in ``root_content_hash``, so no re-measure."""
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
        """Rewrites the manifest pin ``campaign.json::dataset_name`` — the ONE owner, which
        every cycle-level reader derives from — across every lifecycle, archived included."""
        count = 0
        for cid in self.list_campaign_ids():
            campaign = self.load_campaign(cid)
            if campaign is None or campaign.dataset_name != old_name:
                continue
            self.update_campaign(cid, {"dataset_name": new_name})
            count += 1
        return count

    def list_campaign_ids(self) -> list[str]:
        root = self._campaigns_root()
        if not root.is_dir():
            return []
        return sorted(p.name for p in root.iterdir() if (p / "campaign.json").is_file())

    def list_campaigns(
        self,
        dataset_name: str | None = None,
        *,
        lifecycle: str = "active",
        owner_user_id: str | None = None,
    ) -> list[Campaign]:
        """The sole lifecycle/owner filter gateway — API and CLI pass through, never re-filtering."""
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
        """``RUNNING`` is the whole answer — a gated cycle heartbeats, so it derives ``RUNNING``
        too, and none of the rest is a reason to refuse a verb the operator asked for."""
        cycles_dir = campaign_cycles_dir(self.campaign_root_dir(campaign_id))
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
        return self._live_cycle_ids(campaign_id)

    def _guard_and_release(self, campaign_id: str, verb: str) -> None:
        """Only a LIVE producer's open handles are a hazard; a stranded pointer is a UI state
        to fix, not a reason to refuse, so it is simply released."""
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
        """``lifecycle_status`` is the WHOLE mechanism — the tree stays in ``campaigns/``. Every
        reader already asks the flag (the ``list_campaigns`` filter, the sidebar, the storage
        rollup), so moving the tree only bought a second parent each enumerator had to remember."""
        if self.load_campaign(campaign_id) is None:
            return False
        self._guard_and_release(campaign_id, "archive")
        self.update_campaign(campaign_id, self._lifecycle_updates("archived", changed_at, reason))
        return True

    def unarchive_campaign(self, campaign_id: str, *, changed_at: str, reason: str = "") -> bool:
        if self.load_campaign(campaign_id) is None:
            return False
        self.update_campaign(campaign_id, self._lifecycle_updates("active", changed_at, reason))
        return True

    def bank_all_before_removal(self) -> None:
        """Bank EVERY campaign's spend, for a caller about to remove this workspace's campaign tree
        wholesale instead of campaign by campaign — the host-only CLI `reset`, which is the third
        path that can take a ledger. The name states the precondition because banking a subject
        that KEEPS its rows counts the money twice; only a removal may call this.

        It lives here beside the two destroyers' own calls so all three pair ledgers with a
        campaign_id one way. A caller doing that walk itself is a third spelling of the pairing,
        free to drift from how a delete does it."""
        for campaign_dir in self.iter_campaign_dirs():
            bank_spend(
                workspace=self._base_dir,
                cycle_dirs=self.campaign_cycle_dirs(campaign_dir.name),
                campaign_id=campaign_dir.name,
            )

    def delete_campaign(
        self,
        campaign_id: str,
        *,
        keep_results: bool,
        changed_at: str,
        reason: str = "",
        inner_sandbox_root: Path | None = None,
    ) -> bool:
        """Destructive. The cross-campaign ``measurements/`` cache is NEVER touched, and
        ``inner_sandbox_root`` cascades to this campaign's off-tree L4 sandboxes, which bank their
        own residue on the way out.

        Both arms take the cycle ledgers — ``.runtime/ledger.jsonl`` is not a keepsake, so
        ``keep_results`` does not spare it — and those ledgers ARE the account's lifetime spend
        record, so the spend is banked here rather than by the caller. After the guard: a refused
        delete keeps its rows, and a tombstone beside them is the same money counted twice."""
        campaign_dir = self.campaign_root_dir(campaign_id)
        if not (campaign_dir / "campaign.json").is_file():
            return False
        self._guard_and_release(campaign_id, "delete")
        bank_spend(
            workspace=self._base_dir,
            cycle_dirs=self.campaign_cycle_dirs(campaign_id),
            campaign_id=campaign_id,
        )
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
                inner_dir = inner_sandbox_root / inner_sandbox_key(
                    tenant_id, CycleHop(campaign_id=campaign_id, cycle_id=cycle_id)
                )
                self.delete_inner_sandbox(inner_dir, campaign_id=campaign_id)
        return True

    def delete_inner_sandbox(self, sandbox: Path, *, campaign_id: str) -> None:
        """The third destroyer. An inner sandbox is off the account walk — it is a SIBLING of the
        tenant tree — so nothing else banks what its ledgers still hold, and every cycle inside it
        has already forwarded part of that onto its outer cycle. Banking the residue is what makes
        the delete safe in both directions.

        The tombstone is keyed on the sandbox DIRECTORY, whose name hashes the full owner triple;
        keying it on the inner cycle would collide, because inner cycle ids are content-addressed
        and repeat across sandboxes."""
        if not sandbox.exists():
            return
        bank_spend(
            workspace=self._base_dir,
            cycle_dirs=sandbox_cycle_dirs(sandbox),
            campaign_id=campaign_id,
            cycle_id=sandbox.name,
        )
        rmtree_robust(sandbox)

    def mark_spend_forwarded(self, hop: CycleHop, spent: BilledSpend) -> None:
        """Raise this cycle's forwarded high-water mark. Sole writer of the key `bank_spend` reads
        — written AFTER the spend reached the other ledger, so a crash between the two re-forwards
        rather than losing the money."""
        self.update(hop, {FORWARDED_SPEND_KEY: dict(spent._asdict())})

    # ------------------------------------------------------------------
    # Per-cycle ``index.json`` CRUD — create, update, rewind, enumerate
    # ------------------------------------------------------------------

    def load(self, hop: CycleHop) -> dict[str, Any] | None:
        data: dict[str, Any] | None = read_json_optional(self._index_path(hop))
        if data is None:
            return None
        data["cycle_id"] = hop.cycle_id
        return data

    def create(
        self,
        hop: CycleHop,
        metadata: dict[str, Any],
    ) -> Path:
        """Create/augment ``index.json``; a replay merges keys without clobbering rounds/best."""
        path = self._index_path(hop)
        existing = read_json_optional(path) or {}
        now = utcnow_iso()
        defaults: dict[str, Any] = {
            "created_at": existing.get("created_at", now),
            "updated_at": now,
            "status": "active",
            "type": "optimization_loop",
            "parent_session_id": existing.get("parent_session_id", ""),
            "parent_cycle_id": None,
            "sibling_kind": sibling_kind(hop.cycle_id),
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
        hop: CycleHop,
        updates: dict[str, Any],
        *,
        remove: Sequence[str] = (),
    ) -> None:
        path = self._index_path(hop)
        data = read_json(path)
        for key in remove:
            data.pop(key, None)
        data.update(updates)
        data["updated_at"] = utcnow_iso()
        write_json(path, data)

    def mark_human_intervened(
        self,
        hop: CycleHop,
        *,
        kind: str,
        at: str,
    ) -> None:
        """Written the moment the operator intervenes, not at teardown — a still-running
        babysat cycle must already be distinguishable from a pure, reproducible one."""
        path = self._index_path(hop)
        data = read_json(path)
        data["human_intervened"] = True
        # setdefault: the babysit stamp fires at init on a fresh-sibling fork index
        # that carries no `interventions` list yet, unlike the skip-searchpoint caller
        # which runs on an established cycle. The single append site guarantees the key.
        data.setdefault("interventions", []).append({"kind": kind, "at": at})
        data["updated_at"] = utcnow_iso()
        write_json(path, data)

    def rewind_to_round(
        self,
        hop: CycleHop,
        after_round: int,
    ) -> None:
        layout = self._layout(hop)
        rounds_dir = layout.rounds
        candidates_dir = layout.candidates_cache

        ledger_path = layout.ledger
        if not ledger_path.exists():
            raise NotFoundError(f"cycle {hop.cycle_id!r} has no ledger on disk")
        max_complete = scan_ledger_max_round_complete(ledger_path)
        if after_round > max_complete:
            raise BadRequestError(
                f"--from {after_round}: ledger only has completed rounds 0..{max_complete}"
            )

        if after_round >= 1:
            if not rounds_dir.exists():
                raise NotFoundError(
                    f"cycle {hop.cycle_id!r}: ledger has rounds 0..{max_complete} but "
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
        for p in sorted(rounds_dir.glob(ROUND_GLOB)):
            if (n := round_number(p)) is None:
                continue
            (displaced if n > after_round else survivors).append(p)
        if candidates_dir.exists():
            for p in sorted(candidates_dir.glob(ROUND_GLOB)):
                if (n := round_number(p)) is not None and n > after_round:
                    displaced.append(p)

        if displaced:
            for p in displaced:
                unlink_robust(p)
            logger.info(
                "Rewind cycle %s to round %d: deleted %d displaced file(s)",
                hop.cycle_id,
                after_round,
                len(displaced),
            )

        # No dashboard.json repair here. `resolve_resume_state` cuts the surviving
        # trajectory when the resumed run's view is constructed — the one cut, off the
        # schema. A second writer here re-spelled that rule against the raw dict, with
        # its own `max(...)` fold in place of the domain helper.
        self._rebuild_round_index(hop, survivors)

    def _rebuild_round_index(
        self,
        hop: CycleHop,
        survivors: list[Path],
    ) -> None:
        index_path = self._index_path(hop)
        data = read_json(index_path)

        data["rounds"] = [
            _round_summary(RoundResult.model_validate(read_json(p))) for p in sorted(survivors)
        ]
        data["n_rounds"] = len(data["rounds"])
        _apply_best(data)
        data["updated_at"] = utcnow_iso()
        write_json(index_path, data)

    def mark_finished(
        self,
        hop: CycleHop,
        *,
        status: str,
        stop_reason: str,
        finished_at: str,
        interrupted_round: int | None = None,
        crash_traceback: str | None = None,
        final: dict[str, Any] | None = None,
        export: PromptExport | None = None,
    ) -> None:
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
            self.update(hop, updates, remove=remove_keys)
        # The export is written HERE, from the same call that stamps `final`, because both are
        # projections of the one `CycleResult` the runner just built. A separate observer would
        # be a second walk over the same facts, free to disagree with this one. It is its own
        # FILE and not a key under `final` for the opposite reason: its readers are outside this
        # package, and handing them the campaign index to dig through is not an artifact.
        if export is not None:
            with graceful("Export artifact write failed"):
                write_text(
                    self._layout(hop).export,
                    export.model_dump_json(indent=2) + "\n",
                )

    def read_export(self, hop: CycleHop) -> PromptExport | None:
        """The finished cycle's export artifact, or ``None`` when it wrote none.

        The reader half of "we write a file and provide a reader" (`roadmap.md` § Application
        radius). It lives here so a consumer never has to know where the file sits — every caller
        that re-derived the winner from `CycleResult` instead built it out of the wire-side
        `winner_prompt_fields`, which cannot be rebuilt into a `PromptTemplate`.
        """
        text = read_text_optional(self._layout(hop).export)
        return parse_prompt_export(text) if text else None

    def reopen_for_continuation(self, hop: CycleHop) -> None:
        """The ONLY writer that removes ``finished_at``, which is a latch: ``derive_run_phase``
        returns ``TERMINAL`` on it, and TERMINAL is the one phase the reaper will re-stamp."""
        self.update(
            hop,
            {"status": "active"},
            remove=[
                "finished_at",
                "stop_reason",
                "final",
                "interrupted_round",
                "crash_traceback",
            ],
        )

    def mark_superseded(self, hop: CycleHop) -> bool:
        """The line moved to a branch. Unstamped, a parent that stops BY DESIGN presents
        exactly as a crashed one; ``reopen_for_continuation`` still clears the latch."""
        return self._stamp_terminal(hop, StopReason.REBASED)

    def _stamp_terminal(self, hop: CycleHop, reason: StopReason) -> bool:
        data = read_json_optional(self._index_path(hop))
        if not isinstance(data, dict) or data.get("finished_at"):
            return False
        self.mark_finished(
            hop,
            status=reason.value,
            stop_reason=reason.value,
            finished_at=utcnow_iso(),
        )
        return True

    def mark_producer_vanished(self, hop: CycleHop) -> bool:
        """Never reaps a paused, check-in or origin-gated cycle — none is a dead producer, and
        pause is checked through BOTH its writers (the flag, and the runner's declaration)."""
        cycle_dir = self.cycle_dir(hop)
        layout = CycleLayout(cycle_dir)
        if layout.pause_flag.is_file() or is_checkin(cycle_dir):
            return False
        dash = read_json_optional(layout.dashboard)
        if isinstance(dash, dict) and dash.get("declared_phase") in (
            RunPhase.GATE,
            RunPhase.PAUSED,
        ):
            return False
        return self._stamp_terminal(hop, StopReason.PRODUCER_VANISHED)

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
        return [self._entry_from_index(p) for p in self._index_files()]

    def _stub_deletion_blocked(self, hop: CycleHop) -> str | None:
        """Why this cycle may NOT be deleted as a stub, or ``None`` when it may. Asked before the
        spend is banked, because banking a cycle the delete then refuses counts that money twice.

        Banked-nothing is measured against what the cut INHERITED, never against zero: a rebase
        fork mints carrying ``n_rounds == from_round`` without having run a round."""
        index_path = self._index_path(hop)
        # NOT read_json_tolerant: this has to tell "absent" from "corrupt" — one is a
        # stub to delete, the other is a cycle whose state we cannot vouch for.
        try:
            index = read_json_optional(index_path)
        except (OSError, json.JSONDecodeError) as exc:
            return f"index.json unreadable: {exc}"
        if index is None:
            return "not on disk"
        if root_cycle_id(hop.cycle_id) == hop.cycle_id:
            return "family root — deletion is for sibling stubs only"
        n_rounds = index.get("n_rounds", 0)
        fork = index.get("fork")
        from_round = (fork or {}).get("from_round") if isinstance(fork, dict) else None
        inherited = from_round if isinstance(from_round, int) and from_round > 0 else 0
        if not isinstance(n_rounds, int) or n_rounds > inherited:
            return f"n_rounds={n_rounds} against {inherited} inherited — cycle ran real work"
        for other in self._index_files():
            other_campaign, other_cycle = self._ids_from_index_path(other)
            if other_campaign != hop.campaign_id or other_cycle == hop.cycle_id:
                continue
            other_data = read_json_optional(other)
            if isinstance(other_data, dict) and other_data.get("parent_cycle_id") == hop.cycle_id:
                return f"has descendant {other_cycle}"
        return None

    def try_delete_stub_cycle(self, hop: CycleHop) -> tuple[bool, str]:
        """Bank, then destroy — a stub is deletable at ``n_rounds == inherited``, which an
        origin-scored fork reaches having already paid for round 0, so its ledger holds real money
        the ``rmtree`` would otherwise un-spend."""
        blocked = self._stub_deletion_blocked(hop)
        if blocked is not None:
            return False, blocked
        cycle_dir = self.cycle_dir(hop)
        bank_spend(
            workspace=self._base_dir,
            cycle_dirs=[cycle_dir],
            campaign_id=hop.campaign_id,
            cycle_id=hop.cycle_id,
        )
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
        """The single writer for the diag / steered / sweep triggers — numbering restarts at
        round 1; parent-round inheritance is ``save_rebase_fork``'s job."""
        parent = CycleHop(campaign_id=campaign_id, cycle_id=parent_cycle_id)
        child = CycleHop(campaign_id=campaign_id, cycle_id=new_cycle_id)
        parent_index = read_json_optional(self._index_path(parent)) or {}
        blob = _fresh_sibling_index_blob(
            parent_index, parent_cycle_id, kind, forked_at, **blob_kwargs
        )
        path = self._index_path(child)
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
        """Single writer for all four rebase triggers; the issuer rides ``ForkSpec.trigger`` on
        the FORK_CUT record. ``rounds[]`` is the index SUMMARY shape, never whole documents."""
        parent = CycleHop(campaign_id=campaign_id, cycle_id=parent_cycle_id)
        parent_index = read_json_optional(self._index_path(parent)) or {}
        index = {
            **parent_index,
            "parent_cycle_id": parent_cycle_id,
            "sibling_kind": "fork",
            "forked_from_round": forked_from_round,
            "forked_at": forked_at,
            "rounds": [_round_summary(rr) for rr in surviving_rounds],
            "n_rounds": len(surviving_rounds),
            "status": "resumed",
            "updated_at": forked_at,
        }
        _apply_best(index)
        path = self._index_path(CycleHop(campaign_id=campaign_id, cycle_id=new_cycle_id))
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
        parent = CycleHop(campaign_id=campaign_id, cycle_id=parent_cycle_id)
        child = CycleHop(campaign_id=campaign_id, cycle_id=new_cycle_id)
        copy_specs: tuple[tuple[Path, Path, str], ...] = (
            (self._rounds_dir(parent), self._rounds_dir(child), "round_"),
            (self._candidates_dir(parent), self._candidates_dir(child), "round_"),
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
        self._copy_parent_decisions(parent, child, before_round=before_round)
        return n_copied

    @staticmethod
    def _decision_line(line: str) -> dict[str, Any] | None:
        """One ledger line as a dict, or ``None`` if it is torn — a partial trailing write is
        normal on an append-only file and must not fail the mint."""
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            return None
        return rec if isinstance(rec, dict) else None

    def _copy_parent_decisions(
        self, parent: CycleHop, child: CycleHop, *, before_round: int
    ) -> int:
        """Append the parent's ``decision`` records for the LIFTED rounds onto the fork's ledger.

        A fork answers for itself: every ``scan_ledger_*`` reads the physical file, deliberately,
        so a record only the parent holds is invisible to the branch — the same rule that makes a
        repair re-bank its corrected rounds. Resume replays the lifted rounds to find where the
        branch departs, and it reads their decisions from here; without the copy that check sees
        an empty list and passes every inherited round silently.

        Only decisions: the rest of the prefix is the parent's own history and copying it would
        duplicate a ledger that already measured 56% duplication once."""
        src = CycleLayout(self.cycle_dir(parent)).ledger
        if not src.is_file():
            return 0
        lifted = [
            line
            for line in src.read_text(encoding="utf-8").splitlines()
            if line.strip()
            and (rec := self._decision_line(line)) is not None
            and rec.get("record_type") == "decision"
            and isinstance(rec.get("round"), int)
            and rec["round"] < before_round
        ]
        if not lifted:
            return 0
        dst = CycleLayout(self.cycle_dir(child)).ledger
        dst.parent.mkdir(parents=True, exist_ok=True)
        with dst.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lifted) + "\n")
        return len(lifted)

    # ------------------------------------------------------------------
    # Round + candidate detail-file CRUD under a cycle dir
    # ------------------------------------------------------------------

    def save_round_file(
        self,
        hop: CycleHop,
        rr: RoundResult,
    ) -> Path:
        validate_path_component(rr.round_id)

        detail_path = self._layout(hop).round_file(rr.round)
        write_json(detail_path, rr.model_dump(mode="json"))

        index_path = self._index_path(hop)
        data = read_json(index_path)

        data["rounds"] = [t for t in data["rounds"] if t.get("round") != rr.round]
        data["rounds"].append(_round_summary(rr))
        data["n_rounds"] = len(data["rounds"])
        _apply_best(data)
        data["updated_at"] = utcnow_iso()
        write_json(index_path, data)

        return detail_path

    def load_round_file(
        self,
        hop: CycleHop,
        round_num: int,
    ) -> RoundResult | None:
        raw = read_json_optional(self._layout(hop).round_file(round_num))
        return None if raw is None else RoundResult.model_validate(raw)

    def load_rounds_range(
        self,
        hop: CycleHop,
        start: int,
        end: int,
    ) -> list[RoundResult]:
        out: list[RoundResult] = []
        for r in range(start, end + 1):
            rr = self.load_round_file(hop, r)
            if rr is not None:
                out.append(rr)
        return out

    def save_round_candidates(
        self,
        hop: CycleHop,
        round_num: int,
        candidates: list[dict[str, Any]],
        *,
        consumed: str,
    ) -> None:
        """``consumed`` is the ``round_document_digest`` of the round this generation read;
        without it a replayed cache is unfalsifiable, so both halves ride ONE file."""
        path = self._layout(hop).candidate_file(round_num)
        write_json(path, {"consumed": consumed, "candidates": candidates})
        logger.debug("Saved %d candidates for round %d → %s", len(candidates), round_num, path.name)

    def load_round_candidates(
        self,
        hop: CycleHop,
        round_num: int,
    ) -> tuple[list[dict[str, Any]], str | None] | None:
        """``consumed`` is ``None`` for a cache written before the digest was recorded —
        unvouched, which is a state to act on rather than a detail to shrug at."""
        raw = read_json_optional(self._layout(hop).candidate_file(round_num))
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
        hop: CycleHop,
        round_num: int,
    ) -> None:
        path = self._layout(hop).candidate_file(round_num)
        if path.exists():
            unlink_robust(path)
            logger.debug(
                "Deleted cached candidates for round %d (escalation invalidation)", round_num
            )

    # ------------------------------------------------------------------
    # Cycle-seed I/O — the read-once ``CycleSeedRecord`` on the ledger
    # ------------------------------------------------------------------

    def write_cycle_seed(self, hop: CycleHop, seed: CycleSeed) -> None:
        cycle_dir = self.cycle_dir(hop)
        CycleEventLog.open(CycleDir(cycle_dir)).append(CycleSeedRecord(seed=seed))

    def read_cycle_seed(self, hop: CycleHop) -> CycleSeed | None:
        return scan_ledger_cycle_seed(self._layout(hop).ledger)


__all__ = ["CampaignStore", "origin_accuracy_of"]
