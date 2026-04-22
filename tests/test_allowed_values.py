"""Tests for the enum-aware L1 candidate flow.

Covers two seams added to stop the optimizer from proposing invalid
provider-side param values (the original `reasoning_effort="minimal"`
case that produced backend 502s):

1. `parse_pipeline_response` threads ``param_allowed_values`` onto
   ``PipelineNode``.
2. ``validate_overrides`` attaches a ``ValidationFailure`` when a
   candidate's node override has an out-of-range value (Rail 1).

Historical note: a third test asserted the L1 response JSON schema
re-emitted the enum list inline. That duplication was removed as part of
the L1 meta-prompt TPM-budget shrinkage — ``validate_overrides`` is now
the single producer of truth. Enforcement still happens at parse time
via the same ValidationFailure rail; the schema just no longer carries
the same list twice.
"""

from __future__ import annotations

from promptpotter.application.optimization.nodes.generate import (
    validate_overrides,
)
from promptpotter.application.pipeline_discovery import parse_pipeline_response
from promptpotter.domain.pipeline_schema import PipelineNode, PipelineSchema


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
                    "config": {"reasoning_effort": "medium"},
                    "optimizer": {
                        "param_keys": ["reasoning_effort", "response_format"],
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


def test_validate_overrides_accepts_valid_enum_and_free_params():
    schema = _bbeh_schema()
    failures = validate_overrides(
        {"llm_only": {"reasoning_effort": "medium", "temperature": 0.7}},
        schema,
    )
    assert failures == []


def test_validate_overrides_still_rejects_bad_model():
    schema = _bbeh_schema()
    failures = validate_overrides(
        {"llm_only": {"model": "gpt-5-turbo-xxl"}},
        schema,
    )
    assert len(failures) == 1
    assert failures[0].axis == "llm_only.model"
    assert failures[0].reason == "not_in_available_models"
