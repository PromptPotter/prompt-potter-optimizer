"""What the operator hears about the box's power: losing it, running low, getting it back."""

from __future__ import annotations

import enum
from typing import NamedTuple

from promptpotter.infrastructure.power import Power, read_power

_CRITICAL_PERCENT = 15


class PowerLevel(enum.IntEnum):
    """Ordered: a notice is a move to mains, or a move deeper."""

    MAINS = 0
    BATTERY = 1
    CRITICAL = 2


class PowerNotice(NamedTuple):
    text: str
    ring: bool


def _level(power: Power | None) -> PowerLevel:
    if power is None or not power.on_battery:
        return PowerLevel.MAINS
    return PowerLevel.CRITICAL if power.percent <= _CRITICAL_PERCENT else PowerLevel.BATTERY


def power_change(was: PowerLevel) -> tuple[PowerLevel, PowerNotice | None]:
    """The level now, and what to tell about it: a box first read on battery is told at once."""
    power = read_power()
    return _level(power), _notice(was, power)


def _notice(was: PowerLevel, power: Power | None) -> PowerNotice | None:
    now = _level(power)
    if power is None or now == was or PowerLevel.MAINS < now < was:
        return None
    if now == PowerLevel.MAINS:
        return PowerNotice(f"🔌 The box is back on power, battery at {power.percent}%.", ring=False)
    if now == PowerLevel.CRITICAL:
        return PowerNotice(
            f"🪫 The box's battery is at {power.percent}% and it shuts down soon. Plug it in now.",
            ring=True,
        )
    return PowerNotice(
        f"🔋 The box lost power and runs on battery, {power.percent}%. Plug it in.", ring=True
    )
