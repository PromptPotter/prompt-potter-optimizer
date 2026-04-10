"""Tests for AIME integer answer extraction and scoring."""

from promptpotter.shared.scoring import _aime_match, compile_scorer


class TestAimeMatchBoxed:
    def test_boxed_simple(self):
        assert _aime_match(r"\boxed{42}", "42") == 1.0

    def test_boxed_with_trailing_numbers(self):
        # The bug case: model verifies after boxing, last number != answer
        assert _aime_match(r"\boxed{42}. This uses the fact that 126/3 = 3", "42") == 1.0

    def test_boxed_in_reasoning(self):
        text = (
            "We compute 6 * 7 = 42. After checking all cases, "
            r"the answer is $\boxed{42}$."
        )
        assert _aime_match(text, "42") == 1.0

    def test_last_boxed_wins(self):
        # Model boxes intermediate result then re-boxes final answer
        text = r"First attempt: \boxed{10}. After rechecking: \boxed{42}"
        assert _aime_match(text, "42") == 1.0

    def test_boxed_mismatch(self):
        assert _aime_match(r"\boxed{99}", "42") == 0.0

    def test_boxed_zero(self):
        assert _aime_match(r"\boxed{0}", "0") == 1.0

    def test_boxed_large(self):
        assert _aime_match(r"\boxed{999}", "999") == 1.0


class TestAimeMatchFallback:
    def test_plain_number(self):
        assert _aime_match("42", "42") == 1.0

    def test_last_number_in_chain(self):
        assert _aime_match("Step 1: 10 + 32 = 42", "42") == 1.0

    def test_answer_is_pattern(self):
        assert _aime_match("The answer is 42", "42") == 1.0

    def test_no_numbers(self):
        assert _aime_match("no numbers here", "42") == 0.0

    def test_empty_string(self):
        assert _aime_match("", "42") == 0.0

    def test_none_predicted(self):
        assert _aime_match(None, "42") == 0.0  # type: ignore[arg-type]


class TestAimeMatchEdgeCases:
    def test_invalid_ground_truth(self):
        assert _aime_match("42", "not a number") == 0.0

    def test_boxed_non_numeric_falls_through(self):
        # Boxed content can't parse as number -> fall through to last number
        assert _aime_match(r"\boxed{undefined} The answer is 42", "42") == 1.0

    def test_float_in_boxed_truncates(self):
        assert _aime_match(r"\boxed{42.0}", "42") == 1.0

    def test_both_none(self):
        assert _aime_match(None, None) == 0.0  # type: ignore[arg-type]


class TestAimeScorerIntegration:
    def test_compile_scorer_aime(self):
        scorer = compile_scorer("aime_match(predicted, ground_truth)")
        result = {
            "query": "Find the sum of all positive integers n...",
            "predicted": r"After careful analysis, the answer is $\boxed{42}$.",
            "ground_truth": "42",
            "hit": False,
            "score": 0.0,
            "error": None,
            "pipeline_data": None,
        }
        assert scorer(result) == 1.0

    def test_compile_scorer_aime_mismatch(self):
        scorer = compile_scorer("aime_match(predicted, ground_truth)")
        result = {
            "query": "Find the sum of all positive integers n...",
            "predicted": r"I believe the answer is $\boxed{99}$.",
            "ground_truth": "42",
            "hit": False,
            "score": 0.0,
            "error": None,
            "pipeline_data": None,
        }
        assert scorer(result) == 0.0
