from __future__ import annotations

from typing import Literal, NamedTuple

PrefixState = Literal["discounted", "cold", "unreported", "replayed"]


class PrefixReading(NamedTuple):
    state: PrefixState
    #: ``None`` on ``unreported`` and ``replayed``.
    share: float | None
    #: ``c39%`` / ``c0%`` / ``c?``, and ``""`` on a replay, whose line already carries 📖.
    #: Byte-identical to the browser's (`derivations/token-account.ts::prefixReading`).
    badge: str


def prefix_reading(share: float | None, *, replayed: bool) -> PrefixReading:
    """THE reading of a provider's prefix-cache discount, one implementation — so *unreported*
    ("never asked") and a cold *0%* cannot collapse into one blank.

    *replayed* is passed rather than inferred: ``cache_share`` folds it into ``None``, and every
    call site already holds it beside the share."""
    if replayed:
        return PrefixReading("replayed", None, "")
    if share is None:
        return PrefixReading("unreported", None, "c?")
    return PrefixReading("discounted" if share > 0 else "cold", share, f"c{share:.0%}")


__all__ = ["PrefixReading", "PrefixState", "prefix_reading"]
