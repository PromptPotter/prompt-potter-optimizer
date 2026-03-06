"""Tests for terminating-step inference, annotation, and display."""

import sys
from pathlib import Path

from api.models.pipeline_schema import PipelineSchema, PipelineStep

# Import notebook helpers (not a package, so use importlib)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "notebooks"))
from _campaign_lib import _step_tag, _fmt_query_result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _termnorm_schema() -> PipelineSchema:
    """Minimal TermNorm-like schema with 6 ordered steps."""
    return PipelineSchema(
        name="termnorm",
        steps=[
            PipelineStep(name="cache_lookup"),
            PipelineStep(name="fuzzy_matching", short_circuit=True),
            PipelineStep(name="web_search"),
            PipelineStep(name="entity_profiling"),
            PipelineStep(name="token_matching"),
            PipelineStep(name="llm_ranking"),
        ],
    )


# ---------------------------------------------------------------------------
# PipelineSchema.infer_terminating_step
# ---------------------------------------------------------------------------

class TestInferTerminatingStep:
    def test_full_pipeline(self):
        schema = _termnorm_schema()
        timings = {
            "cache_lookup": 0.01,
            "fuzzy_matching": 0.05,
            "web_search": 1.2,
            "entity_profiling": 0.8,
            "token_matching": 0.3,
            "llm_ranking": 5.1,
        }
        assert schema.infer_terminating_step(timings) == "llm_ranking"

    def test_no_llm_ranking(self):
        schema = _termnorm_schema()
        timings = {
            "cache_lookup": 0.01,
            "fuzzy_matching": 0.05,
            "web_search": 1.2,
            "entity_profiling": 0.8,
            "token_matching": 0.3,
            "llm_ranking": None,
        }
        assert schema.infer_terminating_step(timings) == "token_matching"

    def test_fuzzy_only(self):
        schema = _termnorm_schema()
        timings = {"fuzzy_matching": 0.02}
        assert schema.infer_terminating_step(timings) == "fuzzy_matching"

    def test_empty_timings(self):
        schema = _termnorm_schema()
        assert schema.infer_terminating_step({}) is None

    def test_all_none(self):
        schema = _termnorm_schema()
        timings = {"fuzzy_matching": None, "llm_ranking": None}
        assert schema.infer_terminating_step(timings) is None

    def test_custom_schema(self):
        schema = PipelineSchema(
            name="custom",
            steps=[
                PipelineStep(name="step_a"),
                PipelineStep(name="step_b"),
                PipelineStep(name="step_c"),
            ],
        )
        timings = {"step_a": 0.1, "step_b": 0.2, "step_c": None}
        assert schema.infer_terminating_step(timings) == "step_b"


# ---------------------------------------------------------------------------
# _extract_pipeline_data annotation
# ---------------------------------------------------------------------------

class TestExtractPipelineDataAnnotation:
    def test_explicit_terminated_at_priority(self):
        """Explicit terminated_at from backend wins over inference."""
        from api.services.prompt_eval import _extract_pipeline_data

        schema = _termnorm_schema()
        backend_data = {
            "terminated_at": "fuzzy_matching",
            "step_timings": {
                "fuzzy_matching": 0.02,
                "web_search": 1.0,
                "entity_profiling": 0.5,
                "token_matching": 0.3,
                "llm_ranking": 4.0,
            },
            "total_time": 6.0,
        }
        pd = _extract_pipeline_data(backend_data, [], schema)
        # Explicit value wins — inference would say llm_ranking
        assert pd["terminated_at"] == "fuzzy_matching"

    def test_inferred_when_no_explicit(self):
        """Falls back to inference when no explicit terminated_at."""
        from api.services.prompt_eval import _extract_pipeline_data

        schema = _termnorm_schema()
        backend_data = {
            "step_timings": {
                "fuzzy_matching": 0.02,
                "token_matching": 0.3,
                "llm_ranking": None,
            },
            "total_time": 0.5,
        }
        pd = _extract_pipeline_data(backend_data, [], schema)
        assert pd["terminated_at"] == "token_matching"


# ---------------------------------------------------------------------------
# _step_tag display helper
# ---------------------------------------------------------------------------

class TestStepTag:
    def test_step_tag_known(self):
        assert _step_tag("llm_ranking") == "[llm]"
        assert _step_tag("fuzzy_matching") == "[fuzzy]"
        assert _step_tag("cache_lookup") == "[cache]"
        assert _step_tag("token_matching") == "[token]"

    def test_step_tag_unknown(self):
        assert _step_tag("my_custom_step") == "[my_cu]"

    def test_step_tag_none(self):
        assert _step_tag(None) == ""


# ---------------------------------------------------------------------------
# _fmt_query_result with terminated_at
# ---------------------------------------------------------------------------

class TestFmtQueryResult:
    def test_hit_with_step(self):
        r = {
            "query": "Stainless Steel",
            "hit": True,
            "predicted": "Stainless Steel Sheet",
            "pipeline_data": {"terminated_at": "fuzzy_matching", "total_time": 0.1},
        }
        line = _fmt_query_result(r)
        assert "[fuzzy]" in line
        assert "HIT" in line

    def test_miss_with_step(self):
        r = {
            "query": "Kupferblech CW004A",
            "hit": False,
            "predicted": "Copper Sheet",
            "ground_truth": "Copper Plate",
            "pipeline_data": {
                "terminated_at": "llm_ranking",
                "total_time": 12.1,
                "ranked_candidates": [
                    {"candidate": "Copper Sheet"},
                    {"candidate": "Copper Plate"},
                ],
            },
        }
        line = _fmt_query_result(r)
        assert "[llm]" in line
        assert "MISS" in line

    def test_no_step(self):
        r = {
            "query": "test",
            "hit": True,
            "predicted": "Test",
            "pipeline_data": {"total_time": 1.0},
        }
        line = _fmt_query_result(r)
        assert "HIT" in line
        # No step tag — should still format cleanly
        assert "[" not in line
