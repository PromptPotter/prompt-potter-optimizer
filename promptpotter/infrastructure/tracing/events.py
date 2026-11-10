from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from promptpotter.domain.arms import Concluded
    from promptpotter.domain.cell_fold import ScoreSummary
    from promptpotter.domain.run_records import (
        RoundClosedRecord,
        RoundEnteredRecord,
        RunPhaseRecord,
    )


@dataclass(frozen=True, slots=True)
class DatasetRegistered:
    dataset_name: str
    items: tuple[tuple[str, str], ...]
    """Queries non-empty and distinct: ``TracingProjection._register_dataset`` settles it once."""


@dataclass(frozen=True, slots=True)
class CampaignStart:
    campaign_id: str
    config: dict[str, Any]
    cycle_id: str
    session_id: str | None


class TraceSink:
    def on_dataset_registered(self, event: DatasetRegistered) -> None: ...
    def on_campaign_start(self, event: CampaignStart) -> None: ...
    def on_round_entered(self, record: RoundEnteredRecord) -> None: ...

    def on_span_open(
        self,
        round_num: int | None,
        span_id: str,
        *,
        name: str,
        node_type: str,
        as_type: str,
        metadata: dict[str, Any],
    ) -> None: ...

    def on_span_close(self, round_num: int | None, span_id: str, *, error: str | None) -> None: ...

    def on_arm_concluded(
        self, round_num: int, arm: Concluded, fold: ScoreSummary | None
    ) -> None: ...
    def on_round_closed(self, record: RoundClosedRecord) -> None: ...

    def on_run_stopped(self, record: RunPhaseRecord, *, rounds_closed: int) -> None: ...

    def flush(self) -> None: ...
