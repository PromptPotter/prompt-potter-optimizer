from __future__ import annotations

from typing import NewType

from promptpotter.domain.strict_model import FrozenModel


class DiagnosticRunRecord(FrozenModel):
    """One ``noise-floor`` run's sidecar: the run-to-run spread of fresh re-scores of ONE config."""

    ts: str
    dataset: str
    source_campaign: str
    source_cycle: str
    source_candidate_id: str
    config_hash: str
    workspace_n: int
    workspace_accuracy: float
    source_campaign_accuracy: float | None
    source_campaign_composite: float
    source_campaign_n: int
    noise_floor_k: int
    noise_floor_mean: float
    noise_floor_ci_lo: float
    noise_floor_ci_hi: float
    noise_floor_raw: list[float]


RescoreCount = NewType("RescoreCount", int)


def rescore_count(k: int) -> RescoreCount:
    if k < 2:
        raise ValueError(f"k={k}: one rescore has no spread to report — ask for two or more.")
    return RescoreCount(k)
