"""The machine's power, as the kernel reports it: the one read of `/sys/class/power_supply`."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

_SUPPLIES = Path("/sys/class/power_supply")


class Power(NamedTuple):
    on_battery: bool
    percent: int


def read_power() -> Power | None:
    """``None`` where the machine has no battery: a server, Windows, a container.

    A battery that is there but unreadable raises: absent and broken are different answers.
    """
    batteries = sorted(_SUPPLIES.glob("BAT*"))
    if not batteries:
        return None
    battery = batteries[0]
    status = (battery / "status").read_text(encoding="ascii").strip()
    percent = int((battery / "capacity").read_text(encoding="ascii").strip())
    return Power(on_battery=status == "Discharging", percent=percent)
