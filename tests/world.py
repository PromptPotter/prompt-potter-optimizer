"""The edge tier's one world: a fact's stores at its own data root, every network edge answered
by `scripts/offline_world.py`, and campaigns minted and run through the real entry
(`embedded_run.py::run_campaign`) on the offline run's script (`scripts/offline_run.py`). A fact
reads what a run left through the doors that serve it; it never seats a cycle by hand.

The `world` fixture (`conftest.py`) builds one per fact and releases it at teardown."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from factories import ledger_records
from offline_run import (
    DATASET,
    ByCallIdentity,
    campaign_config,
    cited_panel,
    stand_in_fetch,
    stated,
    synthetic_rows,
    text_templates,
)
from offline_world import FakeBackend

from promptpotter.application.embedded_run import run_campaign
from promptpotter.config.paths import install_datasets_root
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.spend import Budget
from promptpotter.infrastructure.ledger import CycleEventLog

if TYPE_CHECKING:
    from pathlib import Path

    from offline_world import Router

    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.cycle_result import CycleResult
    from promptpotter.domain.records import CycleRecord
    from promptpotter.infrastructure.store.stores import Stores


@dataclass(frozen=True)
class Ran:
    """One campaign the world ran: the session its entry opened and what the cycle returned."""

    session: Session
    result: CycleResult

    @property
    def hop(self) -> CycleHop:
        return self.session.hop


@dataclass
class World:
    stores: Stores
    net: Router
    scratch: Path
    ran: list[Ran] = field(default_factory=list)

    def __post_init__(self) -> None:
        # The dataset's definition at the install tier the stores read, as a wheel ships it.
        family = DATASET.split("/")[0]
        shutil.copytree(
            install_datasets_root() / family,
            self.stores.install_datasets_root / family,
            dirs_exist_ok=True,
        )

    async def campaign(
        self,
        optimizer: str = "potter",
        *,
        rounds: int = 1,
        rows: int = 40,
        step_rounds: int | None = None,
        knobs: dict[str, dict[str, Any]] | None = None,
        hardness: int = 0,
    ) -> Ran:
        """A fresh campaign of *optimizer* over *rows* synthetic rows, run through the real entry;
        *step_rounds* pauses it at that round's boundary, as `--step` does."""
        config = campaign_config(optimizer, rounds, bench_trigger="at_end", knobs=knobs)
        samples = synthetic_rows(rows)
        capture = self.scratch / f"requests-{len(self.ran)}"
        capture.mkdir(parents=True)
        self.net.llm.says = ByCallIdentity(
            capture, text_templates(config), cites=cited_panel(config)
        )
        self.net.backend = FakeBackend(samples, hardness=hardness)
        stand_in_fetch(self.stores, samples)
        session, result = await run_campaign(
            DATASET,
            stated(config),
            stores=self.stores,
            readout_sink=lambda _line: None,
            budget=Budget(),
            step_rounds=step_rounds,
            mode=RunMode(),
        )
        ran = Ran(session, result)
        self.ran.append(ran)
        return ran

    async def resume(self, hop: CycleHop) -> Ran:
        """The paused cycle at *hop*, resumed through the same entry."""
        session, result = await run_campaign(
            hop,
            stores=self.stores,
            readout_sink=lambda _line: None,
            budget=Budget(),
            mode=RunMode(),
        )
        ran = Ran(session, result)
        self.ran.append(ran)
        return ran

    def ledger(self, hop: CycleHop) -> CycleEventLog:
        return CycleEventLog.open(CycleDir(self.stores.campaigns.cycle_dir(hop)))

    def records(self, hop: CycleHop) -> list[CycleRecord]:
        return ledger_records(self.ledger(hop))
