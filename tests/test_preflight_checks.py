"""Preflight check scaffold — fires yellow warnings before a campaign starts."""

from __future__ import annotations

from promptpotter.application.campaign.config import (
    CampaignConfig,
    PreflightWarning,
    run_preflight_checks,
)


def _cfg(sp_budget: int) -> CampaignConfig:
    return CampaignConfig(sp_budget_ttest=sp_budget)


def test_sp_budget_exceeds_dataset_warns() -> None:
    cfg = _cfg(15)
    dataset = [{"query": f"q{i}"} for i in range(10)]

    warnings = run_preflight_checks(cfg, dataset)

    assert len(warnings) == 1
    w = warnings[0]
    assert isinstance(w, PreflightWarning)
    assert w.code == "sp_budget_exceeds_dataset"
    assert "15" in w.title and "10" in w.title
    assert cfg.sp_budget_ttest == 15  # no mutation


def test_sp_budget_equal_or_smaller_is_silent() -> None:
    assert run_preflight_checks(_cfg(10), [{}] * 10) == []
    assert run_preflight_checks(_cfg(5), [{}] * 10) == []


def test_empty_dataset_is_silent() -> None:
    assert run_preflight_checks(_cfg(15), []) == []
