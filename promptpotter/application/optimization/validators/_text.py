"""Shared token-set helpers for the output validators.

Several paraphrase-repeat guards (L2 task_context fields, L2 supplemental
rules vs auto-rules) measure the same thing: the Jaccard overlap of the
significant-word sets of two strings. One definition lives here so the
threshold comparison and the word-extraction semantics can't drift apart.
"""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"\w+")


def word_set(text: str, *, min_len: int = 3) -> set[str]:
    """Lower-cased word tokens of ``text`` at least ``min_len`` chars long."""
    return {w for w in _WORD_RE.findall(text.lower()) if len(w) >= min_len}


def word_set_jaccard(a: str, b: str, *, min_len: int = 3) -> float:
    """Jaccard overlap of the two strings' significant-word sets.

    Returns ``0.0`` when either side has no qualifying words (an empty set
    has no meaningful overlap), so callers can compare against a threshold
    without a separate empty-guard.
    """
    wa = word_set(a, min_len=min_len)
    wb = word_set(b, min_len=min_len)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


__all__ = ["word_set", "word_set_jaccard"]
