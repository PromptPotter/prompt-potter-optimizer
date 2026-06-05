"""PipelineSchema model + parse + dataset pipeline.json constraints +
CampaignConfig validation.

Three named invariants:
  1. ``PipelineSchema`` derivation methods (``node_param_keys``,
     ``exclude``, ``node_configs``, ``sp_hash``)
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
     Per-dataset knobs (``improvement_threshold``, ``degradation_threshold``)
     have no defaults and must be explicit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from promptpotter.application.config import (
    _FIELD_SCOPES as _CFG_FIELD_SCOPES,
)
from promptpotter.application.config import (
    CampaignConfig,
    DiffScope,
    load_campaign_config,
)
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import (
    NodePromptInfo,
    ObservationMapping,
    PipelineNode,
    PipelineSchema,
)

DATASETS_DIR = Path(__file__).resolve().parent.parent / "datasets"


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
                prompt_info=NodePromptInfo(family="p"),
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


def test_no_numeric_max_tokens_in_dataset_pipeline_configs() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    pipeline_files = sorted(
        p for p in (repo_root / "datasets").glob("*/pipeline.json") if p.parent.name != "_optimizer"
    )
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
    """Per-dataset knobs (``improvement_threshold``, ``degradation_threshold``)
    have no default — a campaign that omits them is rejected at load time so
    dataset configs are self-describing."""
    with pytest.raises(ValidationError):
        CampaignConfig.model_validate({"optimization": {"l1_patience": 3}})


# ---------------------------------------------------------------------------
# DiffScope classifier — resume-safety contract
# ---------------------------------------------------------------------------


def _cfg(**opt_overrides: object) -> CampaignConfig:
    opt = {"improvement_threshold": 0.01, "degradation_threshold": 0.4, **opt_overrides}
    return CampaignConfig.model_validate({"optimization": opt})


def test_classify_diff_scopes() -> None:
    """Per-scenario classifier contract: policy-only changes don't fork."""
    base = _cfg().model_dump(mode="json")

    # No diff
    assert _cfg().classify_diff_against(base) == (DiffScope.NONE, [])

    # Policy-only: PoBB epsilon change (the operator's exact scenario)
    scope, diffed = _cfg(pobb_epsilon=0.10).classify_diff_against(base)
    assert scope is DiffScope.POLICY_ONLY
    assert diffed == ["optimization.pobb_epsilon"]

    # Data-affecting: scoring formula change rescores past fitness
    scope, _ = CampaignConfig.model_validate(
        {
            "optimization": {"improvement_threshold": 0.01, "degradation_threshold": 0.4},
            "scoring": "score(top1_hit)",
        }
    ).classify_diff_against(base)
    assert scope is DiffScope.DATA_AFFECTING

    # Mixed: data wins (forks even if a policy field also moved)
    mixed_base = CampaignConfig.model_validate(
        {
            "optimization": {"improvement_threshold": 0.01, "degradation_threshold": 0.4},
            "scoring": "old",
        }
    ).model_dump(mode="json")
    mixed = CampaignConfig.model_validate(
        {
            "optimization": {
                "improvement_threshold": 0.01,
                "degradation_threshold": 0.4,
                "pobb_epsilon": 0.10,
            },
            "scoring": "new",
        }
    )
    scope, _ = mixed.classify_diff_against(mixed_base)
    assert scope is DiffScope.DATA_AFFECTING


def test_classify_unknown_path_defaults_to_data_affecting(caplog) -> None:
    """A path missing from _FIELD_SCOPES classifies as DATA_AFFECTING with a
    warning — adding a new knob without an entry can't silently downgrade
    resume safety."""
    base = _cfg().model_dump(mode="json")
    base.setdefault("optimization", {})["unknown_new_knob"] = "old"
    # Active dump doesn't have unknown_new_knob — the diff lands at that path
    # and looks up as unclassified.
    with caplog.at_level("WARNING"):
        scope, _ = _cfg().classify_diff_against(base)
    assert scope is DiffScope.DATA_AFFECTING
    assert "unclassified config path" in caplog.text


def test_every_config_leaf_is_classified() -> None:
    """Every leaf field in CampaignConfig must be classified in _FIELD_SCOPES
    (either directly or as a descendant of a subtree entry). Catches new
    knobs added without a scope at PR time — the alternative is silent
    DATA_AFFECTING fallback that breaks the operator's 'don't fork for
    policy changes' contract."""

    def _leaves(model_cls, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
        from pydantic import BaseModel

        out: list[tuple[str, ...]] = []
        for name, field in model_cls.model_fields.items():
            path = (*prefix, name)
            ann = field.annotation
            # Subtree match short-circuits (the registry classifies the whole subtree)
            if path in _CFG_FIELD_SCOPES:
                out.append(path)
                continue
            if isinstance(ann, type) and issubclass(ann, BaseModel):
                out.extend(_leaves(ann, path))
            else:
                out.append(path)
        return out

    unclassified = [".".join(p) for p in _leaves(CampaignConfig) if p not in _CFG_FIELD_SCOPES]
    assert not unclassified, (
        f"CampaignConfig leaves missing from _FIELD_SCOPES: {unclassified}. "
        "Add an entry classifying each as 'policy' or 'data' in "
        "promptpotter/application/config.py."
    )


# ---------------------------------------------------------------------------
# Allowed-value enums + JSON-schema constraint emission
# (merged from test_allowed_values.py — same pipeline-config-contract bucket)
# ---------------------------------------------------------------------------

from promptpotter.application.optimization.validators.l1_strict import (  # noqa: E402
    build_l1_output_schema,
    validate_overrides,
)


def _bbeh_schema() -> PipelineSchema:
    return PipelineSchema(
        name="bbeh",
        available_models=["openai/gpt-oss-120b"],
        nodes=[
            PipelineNode(
                name="llm_only",
                param_keys={"model", "reasoning_effort", "response_format", "temperature"},
                param_allowed_values={
                    "reasoning_effort": ["none", "default", "low", "medium", "high"],
                    "response_format": ["text", "json"],
                },
                param_types={"temperature": "number", "model": "string"},
            ),
        ],
    )


def test_parse_pipeline_response_threads_param_allowed_values():
    data = {
        "config": {
            "name": "bbeh",
            "available_models": ["openai/gpt-oss-120b"],
            "nodes": {
                "llm_only": {
                    "type": "generation",
                    "config": {"reasoning_effort": "medium", "threshold": 70},
                    "optimizer": {
                        "param_keys": [
                            "reasoning_effort",
                            "response_format",
                            "temperature",
                            "max_tokens",
                            "threshold",
                        ],
                        "param_allowed_values": {
                            "reasoning_effort": ["none", "low", "medium", "high"],
                            "response_format": ["text", "json"],
                        },
                    },
                }
            },
            "pipelines": {"default": ["llm_only"]},
        }
    }
    schema = parse_pipeline_response(data)
    node = schema.get_node("llm_only")
    assert node is not None
    assert node.param_allowed_values["reasoning_effort"] == [
        "none",
        "low",
        "medium",
        "high",
    ]
    assert node.param_allowed_values["response_format"] == ["text", "json"]
    # WELL_KNOWN_PARAM_TYPES registry resolves universal LLM params
    # without per-dataset declaration.
    assert node.param_types["temperature"] == "number"
    assert node.param_types["max_tokens"] == "integer"
    assert node.param_types["reasoning_effort"] == "string"
    # Backend-specific params (no registry entry) fall back to inference
    # from the node.config default's Python type.
    assert node.param_types["threshold"] == "integer"


def test_node_config_schema_full_surface_excludes_prompt_fields():
    """The operator-steer config surface: every ``param_keys`` tunable EXCEPT the
    prompt fields, with widget kinds + options. Distinct from ``optimizer_locks``
    — ``model`` is INCLUDED (the operator may set it on a steered fork) with
    ``available_models`` as options, and flagged ``optimizer_locked`` under a
    strict campaign rather than hidden."""
    data = {
        "config": {
            "name": "gsm8k",
            "available_models": ["openai/gpt-oss-120b", "openai/gpt-oss-20b"],
            "nodes": {
                "llm_only": {
                    "type": "generation",
                    "config": {
                        "model": "openai/gpt-oss-120b",
                        "temperature": 0.0,
                        "reasoning_effort": "medium",
                    },
                    "optimizer": {
                        "param_keys": [
                            "temperature",
                            "max_tokens",
                            "model",
                            "reasoning_effort",
                            "persona",  # prompt fields — owned by the prompt editor,
                            "instruction",  # excluded from the config surface
                            "answer_format",
                        ],
                        "param_allowed_values": {"reasoning_effort": ["low", "medium", "high"]},
                    },
                }
            },
            "pipelines": {"default": ["llm_only"]},
        }
    }
    schema = parse_pipeline_response(data)
    params = {p.key: p for p in schema.node_config_schema(forbidden_strict=True)["llm_only"]}
    assert set(params) == {"temperature", "max_tokens", "model", "reasoning_effort"}
    assert params["model"].kind == "model"
    assert params["model"].options == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    assert params["model"].optimizer_locked is True  # locked for optimizer, shown for operator
    assert params["reasoning_effort"].kind == "enum"
    assert params["reasoning_effort"].options == ["low", "medium", "high"]
    assert params["temperature"].kind == "number"
    assert params["max_tokens"].kind == "number"  # WELL_KNOWN integer → number widget
    assert params["max_tokens"].value is None  # declared but unset in config
    relaxed = schema.node_config_schema(forbidden_strict=False)["llm_only"]
    assert {p.key for p in relaxed if p.optimizer_locked} == set()  # nothing locked when lax


def test_validate_overrides_rejects_out_of_enum_reasoning_effort():
    schema = _bbeh_schema()
    failures = validate_overrides(
        {"llm_only": {"reasoning_effort": "minimal"}},
        schema,
    )
    assert len(failures) == 1
    assert failures[0].axis == "llm_only.reasoning_effort"
    assert failures[0].value == "minimal"
    assert failures[0].reason == "not_in_param_allowed_values"
    assert "medium" in failures[0].allowed


def test_validate_overrides_rejects_stringified_number_for_typed_param():
    """L1 emits temperature as JSON string "0.2" → catch before wire payload.

    The bug this guards against: the L1 LLM emits ``{"temperature": "0.2"}``
    (string instead of number), the value reaches the backend's LLM-call
    layer as a string, the upstream provider rejects it with a 4xx, and the
    candidate burns its scoring budget on a poisonous config. Type check
    fires before the wire payload is built — synthetic-0 + L2 heal handle
    the rest via the existing ValidationFailure path.
    """
    schema = _bbeh_schema()
    failures = validate_overrides(
        {"llm_only": {"temperature": "0.2"}},
        schema,
    )
    assert len(failures) == 1
    assert failures[0].axis == "llm_only.temperature"
    assert failures[0].reason == "type_mismatch"
    assert failures[0].allowed == ["number"]


def test_validate_overrides_rejects_model_as_forbidden_axis_by_default():
    """Strict mode (default): any model touch is forbidden, regardless of value."""
    schema = _bbeh_schema()
    failures = validate_overrides(
        {"llm_only": {"model": "gpt-5-turbo-xxl"}},
        schema,
    )
    assert len(failures) == 1
    assert failures[0].axis == "llm_only.model"
    assert failures[0].reason == "forbidden_axis"


def test_validate_overrides_falls_back_to_available_models_when_strict_off():
    """With strict mode off (ablation runs), the model-allowlist check still fires."""
    schema = _bbeh_schema()
    failures = validate_overrides(
        {"llm_only": {"model": "gpt-5-turbo-xxl"}},
        schema,
        forbidden_axes_strict=False,
    )
    assert len(failures) == 1
    assert failures[0].axis == "llm_only.model"
    assert failures[0].reason == "not_in_available_models"


def test_build_l1_output_schema_emits_enum_for_constrained_params():
    """Three override slots, each constrained server-side:

    - ``pipeline_params_override``: per-node enum/type grafting from the
      active PipelineSchema.
    - ``prompt_fields_override``: keys constrained to the six
      PROMPT_STRING_FIELDS, each a string.
    - ``task_context_override``: keys constrained to the two
      TASK_CONTEXT_OVERRIDES, each a string.

    Splitting the slots is what makes the un-nesting defensive repair
    (deleted in B3) unnecessary — the LLM cannot emit a prompt field
    inside ``pipeline_params_override.llm_only`` because that path is
    rejected by ``additionalProperties: false``.
    """
    schema = _bbeh_schema()
    out = build_l1_output_schema(schema)
    variant_props = out["schema"]["properties"]["variants"]["items"]["properties"]

    # Slot 1: pipeline_params_override — per-node enum/type grafting.
    pp_props = variant_props["pipeline_params_override"]["properties"]
    llm_props = pp_props["llm_only"]["properties"]
    assert llm_props["reasoning_effort"] == {
        "type": "string",
        "enum": ["none", "default", "low", "medium", "high"],
    }
    assert llm_props["response_format"] == {"type": "string", "enum": ["text", "json"]}
    # Unconstrained numeric param still carries a type so the L1 LLM cannot
    # emit a stringified number ("0.2" instead of 0.2).
    assert llm_props["temperature"] == {"type": "number"}
    assert "enum" not in llm_props["temperature"]
    assert variant_props["pipeline_params_override"]["additionalProperties"] is False
    assert pp_props["llm_only"]["additionalProperties"] is False

    # Slot 2: prompt_fields_override — six keys, each string.
    pf_slot = variant_props["prompt_fields_override"]
    assert pf_slot["additionalProperties"] is False
    pf_props = pf_slot["properties"]
    for field in (
        "persona",
        "task_intent",
        "problem_description",
        "instruction",
        "thinking_style",
        "answer_format",
    ):
        assert pf_props[field] == {"type": "string"}
    # No prompt-field key under pipeline_params_override.llm_only — the
    # whole point of B1.
    assert "instruction" not in llm_props
    assert "answer_format" not in llm_props

    # Slot 3: task_context_override — two keys, each string.
    tc_slot = variant_props["task_context_override"]
    assert tc_slot["additionalProperties"] is False
    tc_props = tc_slot["properties"]
    assert tc_props["upstream_context"] == {"type": "string"}
    assert tc_props["downstream_context"] == {"type": "string"}

    assert variant_props["variant_name"] == {"type": "string"}
    assert out["strict"] is False


# ---------------------------------------------------------------------------
# optimizer_pipeline.json shape parity with backend pipeline.json
# (merged from test_optimizer_pipeline_parity.py — §3.5 deliverable)
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parent.parent
OPTIMIZER_PIPELINE = REPO / "datasets/_optimizer/pipeline.json"
BACKEND_PIPELINES = sorted(
    p for p in (REPO / "datasets").glob("*/pipeline.json") if p.parent.name != "_optimizer"
)


def _required_top_level_fields(data: dict) -> set[str]:
    return {"name", "nodes", "pipelines"} - set(data.keys())


def test_optimizer_and_backend_pipelines_share_parser_and_shape() -> None:
    """Same parser, same top-level shape, same per-node optimizer sub-object."""
    assert OPTIMIZER_PIPELINE.exists(), OPTIMIZER_PIPELINE
    assert BACKEND_PIPELINES, "no backend pipeline.json files under datasets/"

    files = [OPTIMIZER_PIPELINE, *BACKEND_PIPELINES]

    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))

        missing = _required_top_level_fields(data)
        assert not missing, f"{path}: missing required top-level fields: {sorted(missing)}"
        assert "default" in data["pipelines"], f"{path}: pipelines.default is required"

        schema = parse_pipeline_response(data)
        assert schema.name, f"{path}: parsed schema has empty name"
        assert schema.nodes, f"{path}: parsed schema has zero nodes"

        active = data["pipelines"]["default"]
        active_in_manifest = [n for n in active if n in data["nodes"]]
        assert [n.name for n in schema.nodes] == active_in_manifest, (
            f"{path}: parser walked a different node order than pipelines.default"
        )

        for node in schema.nodes:
            assert node.name, f"{path}: node has empty name"
            assert node.wire_type, f"{path}::{node.name} has empty wire_type"
            assert node.langfuse_type, f"{path}::{node.name} has empty langfuse_type"
