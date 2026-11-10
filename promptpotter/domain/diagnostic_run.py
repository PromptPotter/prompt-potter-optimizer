from __future__ import annotations

from typing import NewType

RescoreCount = NewType("RescoreCount", int)


def rescore_count(said: int | str) -> RescoreCount:
    """*said* as typed or as a number: a text no integer reads is refused here, in its own words."""
    try:
        k = int(said)
    except ValueError:
        raise ValueError(f"k={said!r}: a rescore count is a whole number, two or more.") from None
    if k < 2:
        raise ValueError(f"k={k}: one rescore has no spread to report — ask for two or more.")
    return RescoreCount(k)
