"""The LABEL arm of a two-arm extraction seam — it parses answer prose and decides HIT/MISS. The SHAPE arm is NOT
in this repo: the backend destructures ``answer_field`` first, so check there when scoring looks wrong."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from promptpotter.shared import (
    extract_boxed_number,
    extract_gsm8k_number,
    extract_last_bold,
    sigmoid,
    text_list_rank,
)


def _gsm8k_match(predicted: str, ground_truth: str) -> float:
    gt = extract_gsm8k_number(ground_truth)
    pred = extract_gsm8k_number(predicted)
    if gt is None or pred is None:
        return 0.0
    return 1.0 if gt == pred else 0.0


def _rr(k: int | None) -> float:
    return 1.0 / k if k else 0.0


def _aime_match(predicted: str, ground_truth: str) -> float:
    """Match AIME integers in [0, 999]. Extraction is the SHARED ``extract_boxed_number`` — the same value the display side
    renders — so this keeps only the int coercion, the overflow guard and the range semantics."""
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
    """Reciprocal rank of ``ground_truth`` within a LIST the model returned, else 0.0.

    The recommendation shape: the answer is not one label but an ordered set, and the held-out
    item is either somewhere in it or not. Graded rather than binary on purpose — naming the
    right film first and naming it tenth are different answers, and a hit/miss matcher would
    hand the optimizer the same number for both."""
    rank = text_list_rank(predicted, ground_truth)
    return 1.0 / rank if rank else 0.0


def _exact_match(predicted: str, ground_truth: str) -> float:
    """Exact match after bold-strip + lowercase. Markdown bold markers stripped both sides."""
    p = extract_last_bold(predicted).strip().lower()
    g = extract_last_bold(ground_truth).strip().lower()
    return 1.0 if p == g else 0.0


def _relu(x: float) -> float:
    return max(0.0, float(x))


def _hockeystick(x: float, threshold: float, slope: float = 1.0) -> float:
    return max(0.0, (float(x) - float(threshold)) * float(slope))


def _smoothstep(x: float, edge0: float, edge1: float) -> float:
    e0 = float(edge0)
    e1 = float(edge1)
    if e1 == e0:
        return 0.0 if float(x) < e0 else 1.0
    t = max(0.0, min(1.0, (float(x) - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


SCORING_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "rr": _rr,
    "gsm8k_match": _gsm8k_match,
    "aime_match": _aime_match,
    "exact_match": _exact_match,
    "list_rr": _list_rr,
    "relu": _relu,
    "hockeystick": _hockeystick,
    "sigmoid": sigmoid,
    "smoothstep": _smoothstep,
}


# The answer-format contract each extract-then-compare matcher imposes on the
# committed prompt. This is where extractability is DECIDED — the matcher reads a
# label out of the raw model output — so the contract lives with the matcher, not
# the backend (TermNorm's ``llm_only`` passes the raw answer straight through; it's
# the ``shared`` extractors (``extract_last_bold`` / ``extract_gsm8k_number``) that
# isolate the label). Fed to the
# origin check-in resolver (``origin_resolve.build_origin_consultation``) so it
# authors an ``answer_format`` the chosen scorer can actually read — told, not
# gated: an empty format is a legal origin the optimizer evolves, and round-0
# health is what catches an unscoreable one. Matchers that compare the raw text
# (no extraction step) carry no entry — the output IS the label.
EXTRACTION_NOTES: dict[str, str] = {
    "exact_match": (
        "Scoring exact-matches the answer after taking the LAST bolded span (the "
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


def extraction_note_for_scoring(scoring: str) -> str:
    """The answer-format contract the committed prompt must satisfy — the union of notes for every matcher the formula
    names. Empty when no extract-then-compare matcher is used, since the raw output is then compared as-is."""
    return " ".join(note for name, note in EXTRACTION_NOTES.items() if name in scoring)


__all__ = [
    "EXTRACTION_NOTES",
    "SCORING_FUNCTIONS",
    "extraction_note_for_scoring",
]
