"""Per-cycle override store — the cycle-seed home under ``.overrides/``.

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

from pathlib import Path

from promptpotter.domain.run_records import CycleSeed
from promptpotter.infrastructure.store.base import read_json_optional, write_json
from promptpotter.infrastructure.store.campaign_store._kernel import CampaignStoreKernel


class CycleOverrideMixin(CampaignStoreKernel):
    """``.overrides/seed.json`` writer/reader — the cycle-seed projection.

    Peer of :class:`ForkMixin` / :class:`RoundMixin`; composed into
    ``CampaignStore``."""

    def _overrides_dir(self, campaign_id: str, cycle_id: str) -> Path:
        return self.cycle_dir(campaign_id, cycle_id) / ".overrides"

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


__all__ = ["CycleOverrideMixin"]
