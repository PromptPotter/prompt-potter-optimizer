from __future__ import annotations

from collections.abc import Iterable
from typing import Literal, get_args

from pydantic import Field

from promptpotter.domain.strict_model import FrozenModel

# In order of loudness; only `warn` and `danger` pin.
type Tone = Literal["quiet", "success", "live", "attention", "warn", "danger"]
TONES: tuple[Tone, ...] = get_args(Tone.__value__)


class Said(FrozenModel):
    """One line composed once for a person: every medium renders it as served, none rewords it."""

    key: str = Field(description="What the line is about, for code to branch on; never shown.")
    tone: Tone
    label: str
    value: str | None = None
    sub: str | None = None
    detail: str | None = Field(default=None, description="The longer reading, a tap away.")


def pins(tone: Tone) -> bool:
    return TONES.index(tone) >= TONES.index("warn")


def pinned(rows: Iterable[Said]) -> Said | None:
    """The first of the loudest rows, where it is loud enough to pin."""
    said = max(rows, key=lambda row: TONES.index(row.tone), default=None)
    return said if said is not None and pins(said.tone) else None
