"""Dataset-specific scorers: AIME (\\boxed{N}) and GSM8K (#### N)."""

import pytest

from promptpotter.shared.scoring import (
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
        # boxed wins, even with trailing reasoning
        (r"\boxed{42}", "42", 1.0),
        (r"\boxed{42}. This uses 126/3 = 3", "42", 1.0),
        (r"First: \boxed{10}. Rechecking: \boxed{42}", "42", 1.0),  # last boxed wins
        (r"\boxed{99}", "42", 0.0),
        (r"\boxed{42.0}", "42", 1.0),  # float truncates
        # fallback paths
        ("42", "42", 1.0),
        ("Step 1: 10 + 32 = 42", "42", 1.0),
        ("The answer is 42", "42", 1.0),
        (r"\boxed{undefined} The answer is 42", "42", 1.0),  # bad boxed → fallback
        # failure modes
        ("no numbers", "42", 0.0),
        ("", "42", 0.0),
        (None, "42", 0.0),
        ("42", "not a number", 0.0),
    ],
)
def test_aime_match(predicted, gt, expected):
    assert _aime_match(predicted, gt) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("#### 42", 42.0),
        ("#### -3", -3.0),
        ("#### 3.5", 3.5),
        ("#### 1,234", 1234.0),
        ("####42", 42.0),
        ("42", 42.0),
        ("Step 1: 10 + 32 = 42", 42.0),
        ("The answer is 42", 42.0),
        ("Result: 3.14", 3.14),
        ("I calculated 99 but #### 42", 42.0),  # #### preferred over fallback
        ("", None),
        ("no numbers", None),
    ],
)
def test_extract_gsm8k_number(text, expected):
    assert _extract_gsm8k_number(text) == expected


@pytest.mark.parametrize(
    "predicted,gt,expected",
    [
        ("#### 42", "#### 42", 1.0),
        ("The answer is 42", "#### 42", 1.0),  # cross-format
        ("42.0", "#### 42", 1.0),  # numeric equivalence
        ("#### 99", "#### 42", 0.0),
        ("", "#### 42", 0.0),
        ("#### 42", "", 0.0),
        (None, None, 0.0),
    ],
)
def test_gsm8k_match(predicted, gt, expected):
    assert _gsm8k_match(predicted, gt) == expected  # type: ignore[arg-type]


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


@pytest.mark.parametrize(
    "formula,predicted,gt,expected",
    [
        ("aime_match(predicted, ground_truth)", r"the answer is $\boxed{42}$.", "42", 1.0),
        ("aime_match(predicted, ground_truth)", r"$\boxed{99}$", "42", 0.0),
        ("gsm8k_match(predicted, ground_truth)", "6 * 7 = 42. The answer is 42.", "#### 42", 1.0),
        ("gsm8k_match(predicted, ground_truth)", "I think it's 43.", "#### 42", 0.0),
    ],
)
def test_compile_scorer(formula, predicted, gt, expected):
    assert compile_scorer(formula)(_result(predicted, gt)) == expected


@pytest.mark.parametrize(
    "predicted,gt,expected",
    [
        # BBEH-style: bold-wrapped answer in a reasoning chain
        ("The answer is **Disproved** - it can be proved that.", "Disproved", 1.0),
        ("**Disproved**", "disproved", 1.0),  # case-insensitive
        ("**Wrong**", "Right", 0.0),
        # No markers: plain exact-match still works
        ("plain text answer", "plain text answer", 1.0),
        ("plain text answer", "Plain Text Answer", 1.0),  # case-insensitive
        ("foo", "bar", 0.0),
        # Multiple bold runs: last one wins
        ("First try **No**. Corrected: **Yes**", "yes", 1.0),
    ],
)
def test_exact_match_bold_aware(predicted, gt, expected):
    assert _exact_match(predicted, gt) == expected


@pytest.mark.parametrize(
    "predicted,formula,expected",
    [
        (
            "The answer is **Disproved** - it can be proved that.",
            "exact_match(predicted, ground_truth)",
            "Disproved",
        ),
        ("Reasoning… #### 42", "gsm8k_match(predicted, ground_truth)", "42"),
        (r"Working… \boxed{17}", "aime_match(predicted, ground_truth)", "17"),
        ("raw text", None, "raw text"),
        ("  padded  ", None, "padded"),
    ],
)
def test_extract_display_answer(predicted, formula, expected):
    assert extract_display_answer(predicted, formula) == expected
