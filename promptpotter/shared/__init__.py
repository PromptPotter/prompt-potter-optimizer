from __future__ import annotations

import math
import re

__all__ = [
    "GSM8K_ANSWER_RE",
    "extract_boxed_number",
    "extract_gsm8k_number",
    "extract_last_bold",
    "sigmoid",
    "text_list_items",
    "text_list_rank",
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
# Leading list furniture on one returned item: "1.", "1)", "-", "*", "•".
_LIST_ITEM_RE = re.compile(r"^\s*(?:\d+\s*[.)]|[-*•])\s*")


def text_list_items(text: str) -> list[str]:
    """The ordered items of a newline-listed *text*, normalised for comparison.

    The family above exists so the scored label and the reported one cannot diverge, and this is
    the member a text-list ranker needs: its node emits the whole ordered list as ONE blob, so a
    walk over ranked-item OBJECTS sees a single unsplittable entry — reporting one candidate and
    not-found for a row that answered correctly. Blank lines, bullets and ``1.`` numbering are
    dropped so the prompt's own formatting is not what scores."""
    out: list[str] = []
    for raw in text.splitlines():
        line = _LIST_ITEM_RE.sub("", raw.strip()).strip().strip("*_").strip().lower().strip(".")
        if line:
            out.append(line)
    return out


def text_list_rank(text: str, item: str) -> int | None:
    """1-based position of *item* among :func:`text_list_items`, else ``None``."""
    want = item.strip().lower().strip(".")
    if not want:
        return None
    items = text_list_items(text)
    return items.index(want) + 1 if want in items else None


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


def extract_boxed_number(text: str) -> float | None:
    """The ONE definition of the AIME answer value, read identically by the scorer and the display side, so the shown answer never
    diverges from the scored one. A non-numeric boxed expression falls back to the last bare number."""
    boxed = BOXED_RE.findall(text)
    if boxed:
        try:
            return float(boxed[-1].strip().replace(",", ""))
        except ValueError:
            pass
    matches = NUMBER_RE.findall(text)
    if matches:
        return float(matches[-1].replace(",", ""))
    return None
