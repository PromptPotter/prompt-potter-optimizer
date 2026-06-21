"""Shared leaf-level utilities — no service or model dependencies."""

from __future__ import annotations

import math
import re

__all__ = [
    "BOXED_RE",
    "GSM8K_ANSWER_RE",
    "NUMBER_RE",
    "extract_gsm8k_number",
    "extract_last_bold",
    "sigmoid",
    "truncate",
]


def truncate(s: str, max_len: int, ellipsis: str = "…") -> str:
    """Cut *s* to ``max_len`` at the nearest preceding word boundary; returns unchanged if it fits."""
    if len(s) <= max_len:
        return s
    cut = s[: max_len - len(ellipsis)].rsplit(" ", 1)[0]
    return (cut if cut else s[: max_len - len(ellipsis)]) + ellipsis


def sigmoid(x: float) -> float:
    """Numerically-stable logistic σ(x) — no SciPy dependency."""
    x = float(x)
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    ex = math.exp(x)
    return ex / (1.0 + ex)


# --------------------------------------------------------------------------- #
# Answer-label extraction — the regexes + isolators that pull a final answer    #
# out of raw model output. Both the scorer (application/scoring/.../matchers)   #
# and the display side (domain/rendering) read a label the same way through     #
# these, so the displayed answer never diverges from the one that was scored.   #
# They live here in the pure leaf because the two consumers sit in different     #
# layers (application can't be imported by the pure domain).                    #
# --------------------------------------------------------------------------- #

GSM8K_ANSWER_RE = re.compile(r"####\s*(-?[\d,]+\.?\d*)")
"""Matches the GSM8K answer field ``#### N``. Shared with the dataset loader, which
normalises raw ground truth to the same shape."""
NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")
BOXED_RE = re.compile(r"\\boxed\{([^{}]+)\}")
_BOLD_RE = re.compile(r"\*\*([^*]+?)\*\*")


def extract_last_bold(text: str) -> str:
    """Return the last ``**…**`` run, else *text* unchanged."""
    if not text:
        return ""
    matches = _BOLD_RE.findall(text)
    if matches:
        last: str = matches[-1]
        return last.strip()
    return text


def extract_gsm8k_number(text: str) -> float | None:
    """Extract a GSM8K answer: ``#### N`` first, else the last number in the text."""
    m = GSM8K_ANSWER_RE.search(text)
    if m:
        return float(m.group(1).replace(",", ""))
    matches = NUMBER_RE.findall(text)
    if matches:
        return float(matches[-1].replace(",", ""))
    return None
