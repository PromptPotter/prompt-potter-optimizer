"""PipelineSchema model + parse + dataset pipeline.json constraints +
CampaignConfig validation.

Three named invariants:
  1. ``PipelineSchema`` derivation methods (``node_param_keys``,
     ``obs_extraction_map``, ``exclude``, ``node_configs``, ``sp_hash``)
     produce stable shapes; ``parse_pipeline_response`` builds a
     PipelineSchema with ordered, typed nodes from a self-describing
     backend response.
  2. Dataset ``pipeline.json`` files MUST NOT ship numeric ``max_tokens``
     defaults. Numeric defaults re-introduce the BBEH-style
     reasoning_budget_exhausted trap; operators raise caps via
     campaign.json overrides.
  3. ``CampaignConfig`` parses every persisted ``datasets/*/campaign.json``
     verbatim and rejects unknown keys at every nesting level (top-level,
     ``optimization``, ``optimizer_llm``, runtime ``pipeline_params``).
     Per-dataset knobs (``improvement_threshold``, ``max_failures``,
     ``degradation_threshold``) have no defaults and must be explicit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from promptpotter.application.config import CampaignConfig, load_campaign_config
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import (
    NodePromptMeta,
    ObservationMapping,
    PipelineNode,
    PipelineSchema,
)

DATASETS_DIR = Path(__file__).resolve().parent.parent / "datasets"


# ===========================================================================
# PipelineSchema model + parse_pipeline_response
# ===========================================================================


def test_derivation_methods():
    schema = PipelineSchema(
        name="test",
        nodes=[
            PipelineNode(
                name="search",
                param_keys={"max_results"},
                observation_name="search",
                observation_mappings=[
                    ObservationMapping(pipeline_key="results", output_field="items"),
                ],
                langfuse_type="tool",
            ),
            PipelineNode(
                name="rank",
                param_keys={"temperature"},
                observation_name="rank",
                observation_mappings=[
                    ObservationMapping(pipeline_key="ranked", is_llm=True),
                ],
                langfuse_type="generation",
            ),
            PipelineNode(
                name="cache",
                langfuse_type="span",
            ),
        ],
    )

    # node_param_keys
    assert schema.node_param_keys() == {
        "search": {"max_results"},
        "rank": {"temperature"},
    }
    assert "cache" not in schema.node_param_keys()

    # obs_extraction_map
    obs_map = schema.obs_extraction_map()
    assert set(obs_map.keys()) == {"search", "rank"}
    assert obs_map["search"][0].pipeline_key == "results"
    assert obs_map["rank"][0].is_llm is True


def test_parse_pipeline_response():
    """Self-describing TermNorm pipeline → PipelineSchema with ordered, typed nodes."""
    data = {
        "config": {
            "name": "TermNorm",
            "version": "2.0",
            "nodes": {
                "cache_lookup": {
                    "type": "cache",
                    "short_circuit": True,
                    "node_role": "cache",
                    "config": {},
                    "optimizer": {"langfuse_type": "span"},
                },
                "web_search": {
                    "type": "tool",
                    "node_role": "enricher",
                    "config": {"max_sites": 7},
                    "optimizer": {
                        "param_keys": ["max_sites"],
                        "observation_name": "web_search",
                        "observation_mappings": [{"pipeline_key": "web_sources"}],
                        "langfuse_type": "tool",
                    },
                },
            },
            "pipelines": {"default": ["cache_lookup", "web_search"]},
        },
    }
    schema = parse_pipeline_response(data)

    assert schema.name == "termnorm"
    assert schema.version == "2.0"
    assert [s.name for s in schema.nodes] == ["cache_lookup", "web_search"]
    assert schema.nodes[0].short_circuit is True
    assert schema.nodes[0].node_type == "cache"
    assert schema.nodes[1].param_keys == {"max_sites"}
    assert schema.nodes[1].observation_mappings[0].pipeline_key == "web_sources"


def _three_node_schema() -> PipelineSchema:
    """Helper: a → b → c pipeline with param_keys on a and b."""
    return PipelineSchema(
        name="test",
        nodes=[
            PipelineNode(name="a", param_keys={"max_results"}),
            PipelineNode(
                name="b",
                param_keys={"temperature"},
                prompt_meta=NodePromptMeta(family="p"),
            ),
            PipelineNode(name="c"),
        ],
    )


class TestCoordinateLookups:
    def test_exclude_drops_named_nodes_and_returns_self_for_empty(self):
        schema = _three_node_schema()
        assert [n.name for n in schema.exclude({"b"}).nodes] == ["a", "c"]
        assert schema.exclude(None) is schema
        assert schema.exclude(set()) is schema

    def test_node_configs_preserves_order_and_fills_empty(self):
        schema = _three_node_schema()
        configs = schema.node_configs({"a": {"max_results": 5}, "b": {"temperature": 0.7}})
        assert [name for name, _ in configs] == ["a", "b", "c"]
        assert configs[0][1] == {"max_results": 5}
        assert configs[2][1] == {}  # missing node → empty dict

    def test_sp_hash_distinguishes_configs_and_handles_empty_schema(self):
        from promptpotter.domain.pipeline_schema import stable_hash

        schema = _three_node_schema()
        pp = {"a": {"max_results": 5}}
        assert schema.sp_hash(pp) == stable_hash(schema.node_configs(pp))
        assert schema.sp_hash(pp) != schema.sp_hash({"a": {"max_results": 10}})
        assert PipelineSchema(nodes=[]).sp_hash({}) == ""


# ===========================================================================
# Dataset pipeline.json defaults
# ===========================================================================


def test_no_numeric_max_tokens_in_dataset_pipeline_configs() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    pipeline_files = sorted((repo_root / "datasets").glob("*/pipeline.json"))
    assert pipeline_files, "no datasets/*/pipeline.json found — wrong cwd?"

    offenders: list[str] = []
    for path in pipeline_files:
        spec = json.loads(path.read_text(encoding="utf-8"))
        for node_name, node in (spec.get("nodes") or {}).items():
            mt = (node.get("config") or {}).get("max_tokens", "absent")
            if isinstance(mt, int):
                offenders.append(f"{path.relative_to(repo_root)} :: {node_name} = {mt}")

    assert not offenders, (
        "Numeric max_tokens default(s) snuck into dataset pipeline.json node configs:\n  "
        + "\n  ".join(offenders)
        + "\nUse `null` or omit the field; operators override per-cycle via campaign.json."
    )


# ===========================================================================
# CampaignConfig validation
# ===========================================================================


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


def test_runtime_pipeline_params_is_rejected() -> None:
    """Runtime ``pipeline_params`` lives on ``Session``; it must not appear
    in user-authored campaign config and Pydantic raises if it does."""
    with pytest.raises(ValidationError):
        CampaignConfig.model_validate({"dataset_name": "x", "pipeline_params": {"steps": ["a"]}})


def test_required_optimization_fields_must_be_explicit() -> None:
    """Per-dataset knobs (``improvement_threshold``, ``max_failures``,
    ``degradation_threshold``) have no default — a campaign that omits them
    is rejected at load time so dataset configs are self-describing.
    System invariants (``enable_l2``, ``enable_l3``) DO have
    defaults — they are not per-dataset knobs."""
    with pytest.raises(ValidationError):
        CampaignConfig.model_validate({"optimization": {"l1_patience": 3}})
