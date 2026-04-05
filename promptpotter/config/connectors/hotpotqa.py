"""HotPotQA connector config — multi-hop question answering.

Query format: plain question string
Ground truth: short answer string
Metrics: Token F1, Exact Match
Source: Yang et al., 2018 (https://hotpotqa.github.io/)
Split: validation (distractor setting), 7405 questions
"""

from __future__ import annotations

from typing import Any

# -- Defaults ---------------------------------------------------------------

BACKEND_NAME = "HotPotQA Benchmark"
BACKEND_TYPE = "benchmark"
DATASET_DESCRIPTION = "HotPotQA validation set (distractor setting)"
DATASET_SIZE = 7405
METRICS = ["token_f1", "exact_match"]


# -- Query parsing ----------------------------------------------------------


def split_query(query: str) -> tuple[str, str]:
    """HotPotQA queries are plain strings — no splitting needed."""
    return query.strip(), ""


def build_query_item(
    question: str,
    answer: str,
    context: list[list] | None = None,
) -> dict[str, Any]:
    """Build a query dict from HotPotQA format.

    Args:
        question: The multi-hop question.
        answer: Expected short answer.
        context: Optional list of [title, sentences] supporting passages.
    """
    item: dict[str, Any] = {
        "query": question,
        "ground_truth": answer,
        "query_fields": {"question": question},
    }
    if context is not None:
        item["query_fields"]["context"] = context
    return item
