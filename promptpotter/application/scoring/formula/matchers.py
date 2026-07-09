"""Task-specific matchers + the public scoring registries.

``SCORING_FUNCTIONS`` is the name → callable map injected into every compiled
formula's namespace; ``EXTRACTION_NOTES`` is the answer-format contract each
extract-then-compare matcher imposes on the committed prompt. (Display-side
extraction lives in ``domain/rendering.py``.)

**This is the LABEL arm of a two-arm extraction seam** — it parses the answer prose
and decides HIT/MISS. It is NOT where a structured-output response is opened: that
runs earlier, in ``connectors/llm_only.py::_extract_answer``, which destructures the
slot named by ``answer_field`` (the SHAPE arm). If a task returns JSON and scoring
looks wrong, check that arm first — by the time a matcher here runs, the envelope is
already gone.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from promptpotter.shared import (
    extract_boxed_number,
    extract_gsm8k_number,
    extract_last_bold,
    sigmoid,
)


def _gsm8k_match(predicted: str, ground_truth: str) -> float:
    gt = extract_gsm8k_number(ground_truth or "")
    pred = extract_gsm8k_number(predicted or "")
    if gt is None or pred is None:
        return 0.0
    return 1.0 if gt == pred else 0.0


def _rr(k: int | None) -> float:
    return 1.0 / k if k else 0.0


def _aime_match(predicted: str, ground_truth: str) -> float:
    """Match AIME integers in [0, 999]; prefers ``\\boxed{N}``, falls back to last number.

    The extract-the-answer step is the shared :func:`extract_boxed_number` (the
    same value the display side renders); this keeps the int-coercion +
    ``OverflowError`` guard + ``[0, 999]``-int ground-truth semantics."""
    try:
        gt = int(ground_truth.strip())
    except (ValueError, AttributeError):
        return 0.0

    pred_num = extract_boxed_number(predicted or "")
    if pred_num is None:
        return 0.0
    try:
        pred = int(pred_num)
    except (ValueError, OverflowError):
        return 0.0
    return 1.0 if pred == gt else 0.0


def _exact_match(predicted: str, ground_truth: str) -> float:
    """Exact match after bold-strip + lowercase. Markdown bold markers stripped both sides."""
    p = extract_last_bold(predicted or "").strip().lower()
    g = extract_last_bold(ground_truth or "").strip().lower()
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
# authors an ``answer_format`` the chosen scorer can actually read, and gated by
# ``origin_readiness._check_commit_format``. Matchers that compare the raw text
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
}


def extraction_note_for_scoring(scoring: str) -> str:
    """The answer-format contract the committed prompt must satisfy for ``scoring``
    to extract a label — the union of notes for every matcher named in the formula.

    Empty when the formula uses no extract-then-compare matcher (the raw output is
    compared as-is, so no commit format is required)."""
    return " ".join(note for name, note in EXTRACTION_NOTES.items() if name in (scoring or ""))


__all__ = [
    "EXTRACTION_NOTES",
    "SCORING_FUNCTIONS",
    "extraction_note_for_scoring",
]
