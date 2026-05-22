"""Task-specific matchers + display extractors + the public registries.

``SCORING_FUNCTIONS`` is the name → callable map injected into every
compiled formula's namespace. ``DISPLAY_EXTRACTORS`` mirrors that for
``extract_display_answer``, which the views use to render the parsed
answer next to the raw model output.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import Any

GSM8K_ANSWER_RE = re.compile(r"####\s*(-?[\d,]+\.?\d*)")
"""Matches the GSM8K answer-field format ``#### N``. Shared with the
dataset loader, which normalises raw ground truth to the same shape."""
_NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")
_BOXED_RE = re.compile(r"\\boxed\{([^{}]+)\}")
_BOLD_RE = re.compile(r"\*\*([^*]+?)\*\*")


def _extract_bold(text: str) -> str:
    """Return the last ``**…**`` run, else *text* unchanged."""
    if not text:
        return ""
    matches = _BOLD_RE.findall(text)
    if matches:
        last: str = matches[-1]
        return last.strip()
    return text


def _extract_gsm8k_number(text: str) -> float | None:
    """Extract GSM8K answer: ``#### N`` first, else the last number."""
    m = GSM8K_ANSWER_RE.search(text)
    if m:
        return float(m.group(1).replace(",", ""))
    matches = _NUMBER_RE.findall(text)
    if matches:
        return float(matches[-1].replace(",", ""))
    return None


def _gsm8k_match(predicted: str, ground_truth: str) -> float:
    gt = _extract_gsm8k_number(ground_truth or "")
    pred = _extract_gsm8k_number(predicted or "")
    if gt is None or pred is None:
        return 0.0
    return 1.0 if gt == pred else 0.0


def _rr(k: int | None) -> float:
    return 1.0 / k if k else 0.0


def _aime_match(predicted: str, ground_truth: str) -> float:
    """Match AIME integers in [0, 999]; prefers ``\\boxed{N}``, falls back to last number."""
    try:
        gt = int(ground_truth.strip())
    except (ValueError, AttributeError):
        return 0.0

    text = predicted or ""

    boxed = _BOXED_RE.findall(text)
    if boxed:
        raw = boxed[-1].strip()
        try:
            pred = int(float(raw.replace(",", "")))
            return 1.0 if pred == gt else 0.0
        except (ValueError, OverflowError):
            pass

    matches = _NUMBER_RE.findall(text)
    if not matches:
        return 0.0
    try:
        pred = int(float(matches[-1].replace(",", "")))
    except (ValueError, OverflowError):
        return 0.0
    return 1.0 if pred == gt else 0.0


def _exact_match(predicted: str, ground_truth: str) -> float:
    """Exact match after bold-strip + lowercase. BBEH bold markers stripped both sides."""
    p = _extract_bold(predicted or "").strip().lower()
    g = _extract_bold(ground_truth or "").strip().lower()
    return 1.0 if p == g else 0.0


def _relu(x: float) -> float:
    return max(0.0, float(x))


def _hockeystick(x: float, threshold: float, slope: float = 1.0) -> float:
    return max(0.0, (float(x) - float(threshold)) * float(slope))


def _sigmoid(x: float) -> float:
    x = float(x)
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _smoothstep(x: float, edge0: float, edge1: float) -> float:
    e0 = float(edge0)
    e1 = float(edge1)
    if e1 == e0:
        return 0.0 if float(x) < e0 else 1.0
    t = max(0.0, min(1.0, (float(x) - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


def _extract_gsm8k_display(text: str) -> str:
    n = _extract_gsm8k_number(text or "")
    if n is None:
        return (text or "").strip()
    return str(int(n)) if n.is_integer() else str(n)


def _extract_boxed_display(text: str) -> str:
    if not text:
        return ""
    boxed = _BOXED_RE.findall(text)
    if boxed:
        last_boxed: str = boxed[-1]
        return last_boxed.strip()
    nums = _NUMBER_RE.findall(text)
    if nums:
        last_num: str = nums[-1]
        return last_num
    return text.strip()


SCORING_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "rr": _rr,
    "gsm8k_match": _gsm8k_match,
    "aime_match": _aime_match,
    "exact_match": _exact_match,
    "relu": _relu,
    "hockeystick": _hockeystick,
    "sigmoid": _sigmoid,
    "smoothstep": _smoothstep,
}


DISPLAY_EXTRACTORS: dict[str, Callable[[str], str]] = {
    "exact_match": _extract_bold,
    "gsm8k_match": _extract_gsm8k_display,
    "aime_match": _extract_boxed_display,
}


def extract_display_answer(predicted: str, formula: str | None) -> str:
    """Return the parsed answer for *predicted* under *formula*; falls back to stripped text."""
    text = (predicted or "").strip()
    if not formula:
        return text
    for name, extractor in DISPLAY_EXTRACTORS.items():
        if name in formula:
            return extractor(predicted or "").strip()
    return text


__all__ = [
    "DISPLAY_EXTRACTORS",
    "GSM8K_ANSWER_RE",
    "SCORING_FUNCTIONS",
    "extract_display_answer",
]
