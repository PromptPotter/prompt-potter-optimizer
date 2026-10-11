from __future__ import annotations

from promptpotter.domain.dashboard_rows import DashboardCandidate, RoundSummary
from promptpotter.domain.l4.proxies import PanelPrecision
from promptpotter.domain.results_health import critical_health_title
from promptpotter.domain.run_records import RoundClosedRecord
from promptpotter.domain.scoring import WalkedCell


def _measurement_order(arms: dict[str, list[WalkedCell]]) -> list[int]:
    if not arms:
        return []
    longest_cid = max(arms, key=lambda k: (len(arms[k]), k))
    return list(dict.fromkeys(sample_id for _, sample_id, _, _ in arms[longest_cid]))


def build_round_summary(
    closed: RoundClosedRecord, panel_precision: PanelPrecision | None
) -> RoundSummary:
    candidates = [DashboardCandidate(reading=reading) for reading in closed.arm_readings()]
    return RoundSummary(
        round=closed.round,
        leading=closed.leading_arm,
        selected=closed.selected_arms,
        accuracy=closed.accuracy,
        composite_fitness=None if closed.accuracy is None else closed.composite_fitness,
        total=closed.total,
        ability=closed.ability,
        improved=None if closed.round == 0 else closed.improved,
        verdict_reason=None if closed.round == 0 else closed.verdict_reason,
        candidates=candidates,
        selection=_measurement_order(closed.cells.arms),
        health=closed.health,
        health_alert=critical_health_title(closed.round, closed.health),
        overlap=closed.overlap,
        panel_precision=panel_precision,
        optimizer_facts=closed.optimizer_facts,
    )


__all__ = ["build_round_summary"]
