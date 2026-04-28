"""Per-dataset scorer formulas — wrong-reveal cases for AIME / GSM8K / exact_match."""

import pytest

from promptpotter.application.scoring.formula import (
    _aime_match,
    _exact_match,
    _extract_gsm8k_number,
    _gsm8k_match,
    compile_scorer,
    extract_display_answer,
)


@pytest.mark.parametrize(
    "predicted,gt,expected",
    [
        (r"First: \boxed{10}. Rechecking: \boxed{42}", "42", 1.0),  # last boxed wins
        (r"\boxed{undefined} The answer is 42", "42", 1.0),  # bad boxed → fallback
        ("no numbers", "42", 0.0),
    ],
)
def test_aime_match(predicted, gt, expected):
    assert _aime_match(predicted, gt) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("#### 1,234", 1234.0),  # comma-stripped
        ("I calculated 99 but #### 42", 42.0),  # #### preferred over fallback
        ("no numbers", None),
    ],
)
def test_extract_gsm8k_number(text, expected):
    assert _extract_gsm8k_number(text) == expected


@pytest.mark.parametrize(
    "predicted,gt,expected",
    [
        ("42.0", "#### 42", 1.0),  # cross-format numeric equivalence
        ("#### 99", "#### 42", 0.0),
    ],
)
def test_gsm8k_match(predicted, gt, expected):
    assert _gsm8k_match(predicted, gt) == expected


@pytest.mark.parametrize(
    "predicted,gt,expected",
    [
        ("First try **No**. Corrected: **Yes**", "yes", 1.0),  # last bold wins, case-insensitive
        ("plain text answer", "Plain Text Answer", 1.0),  # no markers + case-insensitive
        ("foo", "bar", 0.0),
    ],
)
def test_exact_match_bold_aware(predicted, gt, expected):
    assert _exact_match(predicted, gt) == expected


def _result(predicted: str, ground_truth: str) -> dict:
    return {
        "query": "q",
        "predicted": predicted,
        "ground_truth": ground_truth,
        "hit": False,
        "score": 0.0,
        "error": None,
        "pipeline_data": None,
    }


def test_compile_scorer_routes_to_named_function():
    aime = compile_scorer("aime_match(predicted, ground_truth)")
    assert aime(_result(r"\boxed{42}", "42")) == 1.0
    gsm = compile_scorer("gsm8k_match(predicted, ground_truth)")
    assert gsm(_result("6 * 7 = 42. The answer is 42.", "#### 42")) == 1.0


@pytest.mark.parametrize(
    "predicted,formula,expected",
    [
        ("The answer is **Disproved**.", "exact_match(predicted, ground_truth)", "Disproved"),
        (r"Working… \boxed{17}", "aime_match(predicted, ground_truth)", "17"),
        ("  padded  ", None, "padded"),
    ],
)
def test_extract_display_answer(predicted, formula, expected):
    assert extract_display_answer(predicted, formula) == expected
