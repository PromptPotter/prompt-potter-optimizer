"""CampaignConfig validation — rejects unknown keys, accepts legacy nested shape."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from promptpotter.application.config import CampaignConfig, load_campaign_config

DATASETS_DIR = Path(__file__).resolve().parent.parent / "datasets"


def test_all_persisted_campaign_jsons_parse() -> None:
    """Every ``datasets/*/campaign.json`` must load into the new model unchanged."""
    files = sorted(DATASETS_DIR.glob("*/campaign.json"))
    assert files, "no campaign.json fixtures found — test setup broken"
    for path in files:
        raw = json.loads(path.read_text(encoding="utf-8"))
        payload = raw.get("campaign_config", raw)
        cfg = load_campaign_config(payload)
        assert isinstance(cfg, CampaignConfig), f"{path} failed to validate"


def test_unknown_top_level_key_rejected() -> None:
    with pytest.raises(ValidationError):
        CampaignConfig.model_validate({"dataset_name": "x", "nonsense_key": 1})


def test_unknown_optimization_key_rejected() -> None:
    with pytest.raises(ValidationError):
        CampaignConfig.model_validate(
            {"optimization": {"zero_signal_filtre_enabled": True}}  # typo: filtre
        )


def test_unknown_optimizer_llm_key_rejected() -> None:
    with pytest.raises(ValidationError):
        CampaignConfig.model_validate({"optimizer_llm": {"modle": "x"}})  # typo: modle


def test_legacy_pipeline_params_is_rejected() -> None:
    """Runtime ``pipeline_params`` lives on ``Session``; it must not appear
    in user-authored campaign config and Pydantic raises if it does."""
    with pytest.raises(ValidationError):
        CampaignConfig.model_validate({"dataset_name": "x", "pipeline_params": {"steps": ["a"]}})


def test_required_optimization_fields_must_be_explicit() -> None:
    """Shared knobs (``improvement_threshold``, ``seed``, ``max_failures``,
    ``degradation_threshold``, ``enable_l2``, ``enable_l3``) have no default —
    a campaign that omits them is rejected at load time so dataset configs are
    self-describing rather than relying on hidden defaults."""
    with pytest.raises(ValidationError):
        CampaignConfig.model_validate({"optimization": {"l1_patience": 3}})
