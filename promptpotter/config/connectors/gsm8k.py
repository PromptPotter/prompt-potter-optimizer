"""GSM8K connector config — grade school math word problems.

Query format: math word problem (plain string)
Ground truth: numeric answer (extracted from "#### N" format)
Metrics: Exact Match (numeric)
Source: Cobbe et al., 2021 (https://github.com/openai/grade-school-math)
Split: test, 1319 questions
"""

from __future__ import annotations

import re
from typing import Any

DATASET_SIZE = 1319
METRICS = ["exact_match_numeric"]

_ANSWER_PATTERN = re.compile(r"####\s*(.+)")


# -- Query parsing ----------------------------------------------------------


def split_query(query: str) -> tuple[str, str]:
    """GSM8K queries are plain strings — no splitting needed."""
    return query.strip(), ""


def extract_answer(raw_answer: str) -> str:
    """Extract numeric answer from GSM8K ``#### N`` format.

    GSM8K answers end with ``#### <number>``. This extracts the number
    and strips commas/whitespace for exact-match comparison.
    """
    match = _ANSWER_PATTERN.search(raw_answer)
    if match:
        return match.group(1).strip().replace(",", "")
    return raw_answer.strip().replace(",", "")


def build_query_item(question: str, raw_answer: str) -> dict[str, Any]:
    """Build a query dict from GSM8K format.

    Args:
        question: The math word problem.
        raw_answer: Full answer string including reasoning + ``#### N``.
    """
    return {
        "query": question,
        "ground_truth": extract_answer(raw_answer),
        "query_fields": {"question": question},
        "raw_answer": raw_answer,
    }
