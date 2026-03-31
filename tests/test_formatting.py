"""Tests for Wave 1c failure analysis integration in format_context_sections()."""

from api.models.analysis import FailureAnalysis, FailurePattern
from api.services.campaign.formatting import ContextData, format_context_sections


class TestFailureAnalysisFormatting:
    def test_no_analysis_no_section(self):
        ctx = ContextData()
        result = format_context_sections(ctx)
        assert "FAILURE ANALYSIS" not in result

    def test_empty_patterns_no_section(self):
        ctx = ContextData(failure_analysis=FailureAnalysis(total_failures=0, total_results=10))
        result = format_context_sections(ctx)
        assert "FAILURE ANALYSIS" not in result

    def test_single_pattern(self):
        fa = FailureAnalysis(
            patterns=[
                FailurePattern(
                    name="source_miss",
                    query_count=5,
                    fraction=1.0,
                    diagnostic_key=("gt_in_source=False",),
                    example_queries=["aspirin dosage", "ibuprofen interactions"],
                    signals={"gt_in_source": False, "terminated_at": "token_matching"},
                ),
            ],
            total_failures=5,
            total_results=10,
        )
        ctx = ContextData(failure_analysis=fa)
        result = format_context_sections(ctx)

        assert "FAILURE ANALYSIS (5 failures / 10 total):" in result
        assert "source_miss" in result
        assert "5 queries (100%)" in result
        assert "aspirin dosage" in result
        assert "IMPROVEMENT DIRECTIONS:" in result
        assert "Address source_miss" in result

    def test_multiple_patterns_capped_at_3(self):
        patterns = [
            FailurePattern(
                name=f"pattern_{i}",
                query_count=10 - i,
                fraction=(10 - i) / 30,
                diagnostic_key=(f"key_{i}",),
            )
            for i in range(5)
        ]
        fa = FailureAnalysis(patterns=patterns, total_failures=30, total_results=50)
        ctx = ContextData(failure_analysis=fa)
        result = format_context_sections(ctx)

        # Only top 3 should appear
        assert "pattern_0" in result
        assert "pattern_1" in result
        assert "pattern_2" in result
        assert "pattern_3" not in result

    def test_coexists_with_scan_and_critique(self):
        fa = FailureAnalysis(
            patterns=[
                FailurePattern(
                    name="rank_miss",
                    query_count=3,
                    fraction=1.0,
                    diagnostic_key=("gt_in_ranked=False",),
                ),
            ],
            total_failures=3,
            total_results=10,
        )
        ctx = ContextData(
            failure_analysis=fa,
            critique_text="Focus on ranking quality",
            scan_context={"tested_values": "persona: 3 values", "sensitivity_text": ""},
        )
        result = format_context_sections(ctx)
        assert "SCAN:" in result
        assert "FAILURE ANALYSIS" in result
        assert "CRITIQUE:" in result
