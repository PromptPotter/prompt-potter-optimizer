"""Tests for GSM8K numeric answer extraction and scoring."""

from promptpotter.shared.scoring import (
    _extract_gsm8k_number,
    _gsm8k_match,
    compile_scorer,
)


class TestExtractGsm8kNumber:
    def test_canonical_format(self):
        assert _extract_gsm8k_number("#### 42") == 42.0

    def test_negative(self):
        assert _extract_gsm8k_number("#### -3") == -3.0

    def test_decimal(self):
        assert _extract_gsm8k_number("#### 3.5") == 3.5

    def test_comma_separated(self):
        assert _extract_gsm8k_number("#### 1,234") == 1234.0

    def test_no_space_after_hashes(self):
        assert _extract_gsm8k_number("####42") == 42.0

    def test_fallback_plain_number(self):
        assert _extract_gsm8k_number("42") == 42.0

    def test_fallback_last_number(self):
        assert _extract_gsm8k_number("Step 1: 10 + 32 = 42") == 42.0

    def test_fallback_the_answer_is(self):
        assert _extract_gsm8k_number("The answer is 42") == 42.0

    def test_fallback_decimal(self):
        assert _extract_gsm8k_number("Result: 3.14") == 3.14

    def test_empty_string(self):
        assert _extract_gsm8k_number("") is None

    def test_no_numbers(self):
        assert _extract_gsm8k_number("no numbers here") is None

    def test_hashes_preferred_over_fallback(self):
        # #### pattern takes priority even when other numbers are present
        assert _extract_gsm8k_number("I calculated 99 but #### 42") == 42.0


class TestGsm8kMatch:
    def test_exact_match(self):
        assert _gsm8k_match("#### 42", "#### 42") == 1.0

    def test_cross_format_match(self):
        assert _gsm8k_match("The answer is 42", "#### 42") == 1.0

    def test_numeric_equivalence(self):
        assert _gsm8k_match("42.0", "#### 42") == 1.0

    def test_mismatch(self):
        assert _gsm8k_match("#### 99", "#### 42") == 0.0

    def test_predicted_empty(self):
        assert _gsm8k_match("", "#### 42") == 0.0

    def test_ground_truth_empty(self):
        assert _gsm8k_match("#### 42", "") == 0.0

    def test_both_none(self):
        assert _gsm8k_match(None, None) == 0.0  # type: ignore[arg-type]


class TestGsm8kScorerIntegration:
    def test_compile_scorer_gsm8k(self):
        scorer = compile_scorer("gsm8k_match(predicted, ground_truth)")
        result = {
            "query": "What is 6 * 7?",
            "predicted": "Let me think... 6 * 7 = 42. The answer is 42.",
            "ground_truth": "#### 42",
            "hit": False,  # string exact-match is wrong, but scorer overrides
            "score": 0.0,
            "error": None,
            "pipeline_data": None,
        }
        assert scorer(result) == 1.0

    def test_compile_scorer_gsm8k_mismatch(self):
        scorer = compile_scorer("gsm8k_match(predicted, ground_truth)")
        result = {
            "query": "What is 6 * 7?",
            "predicted": "I think it's 43.",
            "ground_truth": "#### 42",
            "hit": False,
            "score": 0.0,
            "error": None,
            "pipeline_data": None,
        }
        assert scorer(result) == 0.0
