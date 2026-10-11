"""The LABEL arm only: the backend destructures ``answer_field`` first, outside this repo."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from promptpotter.shared.answer_text import (
    extract_boxed_number,
    extract_gsm8k_number,
    extract_last_bold,
    text_list_rank,
)
from promptpotter.shared.hashing import shapes_optimizer_prompt


def _gsm8k_match(predicted: str, ground_truth: str) -> float:
    gt = extract_gsm8k_number(ground_truth)
    pred = extract_gsm8k_number(predicted)
    if gt is None or pred is None:
        return 0.0
    return 1.0 if gt == pred else 0.0


def _rr(k: int | None) -> float:
    return 1.0 / k if k else 0.0


def _aime_match(predicted: str, ground_truth: str) -> float:
    try:
        gt = int(ground_truth.strip())
    except (ValueError, AttributeError):
        return 0.0

    pred_num = extract_boxed_number(predicted)
    if pred_num is None:
        return 0.0
    try:
        pred = int(pred_num)
    except (ValueError, OverflowError):
        return 0.0
    return 1.0 if pred == gt else 0.0


def _list_rr(predicted: str, ground_truth: str) -> float:
    rank = text_list_rank(predicted, ground_truth)
    return 1.0 / rank if rank else 0.0


def _label_match(predicted: str, ground_truth: str) -> float:
    """The tolerances are google-deepmind/bbeh ``evaluate.py``'s (``preprocess_sample`` + ``fuzzy_match``)."""
    p = extract_last_bold(predicted).strip().strip("_").strip().lower()
    p = p.replace(", ", ",").replace("**", "").split("\n")[0].removesuffix(".")
    if p.startswith("$") and p.endswith("$"):
        p = p[1:-1]
    for wrapper in ("boxed{", "text{", "texttt{"):
        if wrapper in p and p.endswith("}"):
            p = p[:-1].split(wrapper)[1]
    g = extract_last_bold(ground_truth).strip().lower().replace(", ", ",")
    if p == g:
        return 1.0
    if len(p) == 3 and p[0] == "(" and p[-1] == ")":
        return 1.0 if p[1] == g else 0.0
    if len(g) == 3 and g[0] == "(" and g[-1] == ")":
        return 1.0 if g[1] == p else 0.0
    try:
        if float(p) == float(g):
            return 1.0
    except ValueError:
        pass
    return (
        1.0
        if p.replace("'", "") == g.replace("'", "")
        or f"[{g}]" == p
        or f"[{p}]" == g
        or (p.endswith("?") and p[:-1] == g)
        else 0.0
    )


SCORING_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "rr": _rr,
    "gsm8k_match": _gsm8k_match,
    "aime_match": _aime_match,
    "label_match": _label_match,
    "list_rr": _list_rr,
}


# Told to the origin check-in, not gated; a matcher comparing the raw text carries no entry.
EXTRACTION_NOTES: Annotated[dict[str, str], shapes_optimizer_prompt] = {
    "label_match": (
        "Scoring matches the answer after taking the LAST bolded span (the "
        "last **…** run) of the output, lowercased. Commit the final answer on its "
        "own last line wrapped in double asterisks — e.g. **TRUE**. With "
        "chain-of-thought, an unbolded answer leaves the label buried in the "
        "reasoning and scores as a miss; the bold lets scoring isolate it."
    ),
    "aime_match": (
        "Scoring reads the final integer from the last \\boxed{N} (else the last "
        "number in the text). Put the answer in \\boxed{} on the last line — e.g. "
        "\\boxed{42}."
    ),
    "gsm8k_match": (
        "Scoring reads the answer from the '#### N' field (else the last number in "
        "the text). End with the final number on its own line as '#### 42'."
    ),
    "list_rr": (
        "Scoring reads an ORDERED LIST, one item per line, and looks for the held-out "
        "item in it — earlier scores higher. Emit only the list: one item per line, "
        "nothing before or after it, no commentary on the same line. Bullets and '1.' "
        "numbering are stripped, so they neither help nor hurt; prose wrapped around "
        "the list makes its own line an item and pushes the real ones down."
    ),
}


@shapes_optimizer_prompt
def extraction_note_for_scoring(scoring: str) -> str:
    return " ".join(note for name, note in EXTRACTION_NOTES.items() if name in scoring)


__all__ = [
    "EXTRACTION_NOTES",
    "SCORING_FUNCTIONS",
    "extraction_note_for_scoring",
]
