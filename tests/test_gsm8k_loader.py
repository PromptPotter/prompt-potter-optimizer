"""Tests for GSM8K answer extraction in dataset_builder."""

from promptpotter.application.datasets.builder import _GSM8K_ANSWER_RE


def _extract(text: str) -> str:
    """Replicate the extraction logic from load_gsm8k."""
    m = _GSM8K_ANSWER_RE.search(text)
    return f"#### {m.group(1)}" if m else text.strip()


class TestGsm8kAnswerExtraction:
    def test_standard_format(self):
        assert _extract("Janet sells 9 eggs.\n#### 9") == "#### 9"

    def test_comma_separated(self):
        assert _extract("The total is 1,234 dollars.\n#### 1,234") == "#### 1,234"

    def test_negative(self):
        assert _extract("The temperature dropped.\n#### -5") == "#### -5"

    def test_decimal(self):
        assert _extract("She has 3.5 cups.\n#### 3.5") == "#### 3.5"

    def test_no_marker_fallback(self):
        assert _extract("The answer is 42") == "The answer is 42"

    def test_whitespace_around_marker(self):
        assert _extract("Steps...\n####   42") == "#### 42"
