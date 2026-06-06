"""Per-cycle override store — the fork-seed home under ``.overrides/``.

``.overrides/`` holds **declared-at-fork, read-once-at-bootstrap** data: the
operator-steered fork seed (edited starting prompt + pipeline overlay + limit
overrides). Contrast ``.runtime/`` (``stop.flag`` / ``pause.flag`` /
``spend_cap.json``) — those are **mutated-during-run, polled-every-tick** by the
round loop (read via ``infrastructure/runtime_flags.py``). The directory name
encodes the read cadence; conflating the two invites cache-staleness bugs.

The seed is one of three projections of a typed :class:`ForkSpec`: the ledger
``FORK_CUT`` record is the SoT, ``.overrides/seed.json`` is the bootstrap-read
copy the origin resolver consumes, ``index.json::fork`` is the lineage-read copy
(seed-excluded). Single writer: ``_mint_fork``.
"""

from __future__ import annotations

from pathlib import Path

from promptpotter.domain.run_records import OperatorForkOverride
from promptpotter.infrastructure.store.base import read_json_optional, write_json
from promptpotter.infrastructure.store.campaign_store._kernel import CampaignStoreKernel


class CycleOverrideMixin(CampaignStoreKernel):
    """``.overrides/seed.json`` writer/reader — the fork-seed projection.

    Peer of :class:`ForkMixin` / :class:`RoundMixin`; composed into
    ``CampaignStore``."""

    def _overrides_dir(self, campaign_id: str, cycle_id: str) -> Path:
        return self.cycle_dir(campaign_id, cycle_id) / ".overrides"

    def write_fork_seed(self, campaign_id: str, cycle_id: str, seed: OperatorForkOverride) -> Path:
        """Persist the operator-steered fork seed (read once at bootstrap)."""
        path = self._overrides_dir(campaign_id, cycle_id) / "seed.json"
        write_json(path, seed.model_dump(mode="json"))
        return path

    def read_fork_seed(self, campaign_id: str, cycle_id: str) -> OperatorForkOverride | None:
        """Load the fork seed, or ``None`` when this cycle wasn't operator-steered."""
        data = read_json_optional(self._overrides_dir(campaign_id, cycle_id) / "seed.json")
        if data is None:
            return None
        return OperatorForkOverride.model_validate(data)


__all__ = ["CycleOverrideMixin"]
