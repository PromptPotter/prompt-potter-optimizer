"""Tests for is_result_step_compatible and exclude_steps in scan advisor."""

import pytest

from api.models.pipeline_schema import is_result_step_compatible


# ---------------------------------------------------------------------------
# is_result_step_compatible
# ---------------------------------------------------------------------------

class TestIsResultStepCompatible:
    """Annotation utility: does terminated_at match target steps?"""

    def test_terminated_at_in_target(self):
        result = {"pipeline_data": {"terminated_at": "llm_ranking"}}
        assert is_result_step_compatible(result, {"llm_ranking", "token_matching"}) is True

    def test_terminated_at_not_in_target(self):
        result = {"pipeline_data": {"terminated_at": "web_search"}}
        assert is_result_step_compatible(result, {"llm_ranking", "token_matching"}) is False

    def test_no_terminated_at(self):
        result = {"pipeline_data": {"step_timings": {"llm_ranking": 0.5}}}
        assert is_result_step_compatible(result, {"llm_ranking"}) is False

    def test_no_pipeline_data(self):
        result = {"query": "test", "hit": True}
        assert is_result_step_compatible(result, {"llm_ranking"}) is False

    def test_accepts_list_target(self):
        result = {"pipeline_data": {"terminated_at": "token_matching"}}
        assert is_result_step_compatible(result, ["token_matching", "llm_ranking"]) is True

    def test_empty_target_steps(self):
        result = {"pipeline_data": {"terminated_at": "llm_ranking"}}
        assert is_result_step_compatible(result, set()) is False


# ---------------------------------------------------------------------------
# advise_scan_config with explicit exclude_steps
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_advise_exclude_steps_explicit(monkeypatch):
    """When exclude_steps is passed explicitly, it overrides schema diff."""
    from api.services.search.scan_advisor import advise_scan_config
    from api.models.pipeline_schema import PipelineSchema, PipelineStep

    schema = PipelineSchema(
        name="test",
        version="1",
        steps=[
            PipelineStep(name="token_matching", param_keys={"max_token_candidates"}),
            PipelineStep(name="llm_ranking", param_keys={"ranking_temperature"}),
            PipelineStep(name="web_search"),
        ],
    )

    # Capture the excluded_steps that _build_advisor_prompt receives
    captured = {}

    import api.services.search.scan_advisor as _advisor_mod

    def _mock_build_prompt(*args, **kwargs):
        captured["excluded_steps"] = kwargs.get("excluded_steps")
        return "Return {}"

    monkeypatch.setattr(_advisor_mod, "_build_advisor_prompt", _mock_build_prompt)

    _mock_parsed = {
        "priority_axes": [], "suggested_n_diagnostic": 6,
        "axes_to_skip": [], "budget_breakdown": {}, "reasoning": "test",
    }

    class _MockResponse:
        content = "{}"
        parsed = _mock_parsed
        finish_reason = "end_turn"

    class _MockClient:
        async def chat(self, **kwargs):
            return _MockResponse()

    result = await advise_scan_config(
        pipeline_schema=schema,
        variant_library={"prompt_fields": {}, "pipeline_params": {}},
        llm_client=_MockClient(),
        exclude_steps=["llm_ranking"],
    )

    assert captured["excluded_steps"] == {"llm_ranking"}
    assert "validation_warnings" in result


@pytest.mark.asyncio
async def test_advise_exclude_steps_none_falls_back_to_schema_diff(monkeypatch):
    """When exclude_steps is None, falls back to _excluded_from_schema."""
    from api.services.search.scan_advisor import advise_scan_config
    from api.models.pipeline_schema import PipelineSchema, PipelineStep

    schema = PipelineSchema(
        name="test",
        version="1",
        steps=[
            PipelineStep(name="token_matching"),
            PipelineStep(name="llm_ranking"),
            PipelineStep(name="web_search"),
        ],
    )

    captured = {}

    import api.services.search.scan_advisor as _advisor_mod

    def _mock_build_prompt(*args, **kwargs):
        captured["excluded_steps"] = kwargs.get("excluded_steps")
        return "Return {}"

    monkeypatch.setattr(_advisor_mod, "_build_advisor_prompt", _mock_build_prompt)

    _mock_parsed = {
        "priority_axes": [], "suggested_n_diagnostic": 6,
        "axes_to_skip": [], "budget_breakdown": {}, "reasoning": "test",
    }

    class _MockResponse:
        content = "{}"
        parsed = _mock_parsed
        finish_reason = "end_turn"

    class _MockClient:
        async def chat(self, **kwargs):
            return _MockResponse()

    # pipeline_params excludes web_search → schema diff should find it
    await advise_scan_config(
        pipeline_schema=schema,
        variant_library={"prompt_fields": {}, "pipeline_params": {}},
        llm_client=_MockClient(),
        pipeline_params={"steps": ["token_matching", "llm_ranking"]},
        # exclude_steps not passed → None → fallback
    )

    assert captured["excluded_steps"] == {"web_search"}
