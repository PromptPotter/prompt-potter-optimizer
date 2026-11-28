"""The device class a visit is logged under, read off its request headers."""

from __future__ import annotations

import enum
import re


class Device(enum.StrEnum):
    PHONE = "phone"
    TABLET = "tablet"
    COMPUTER = "computer"


_TABLET_UA = re.compile(r"iPad|Tablet")


_PHONE_UA = re.compile(r"Mobi|Android|iPhone")


def device_of(mobile_hint: str | None, user_agent: str | None) -> Device | None:
    """The site's visit-log classes; ``None`` where the request sent no user-agent to read."""
    if mobile_hint == "?1":
        return Device.PHONE
    if not user_agent:
        return None
    if _TABLET_UA.search(user_agent):
        return Device.TABLET
    return Device.PHONE if _PHONE_UA.search(user_agent) else Device.COMPUTER
