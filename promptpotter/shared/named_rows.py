"""A table row names its code as ``module:attr`` so the table imports none of it; this imports it."""

from __future__ import annotations

import importlib
from functools import reduce


def imported(package: str, row: str) -> object:
    """``attr`` may be dotted (``Class.method``)."""
    module, _, attr = row.partition(":")
    return reduce(getattr, attr.split("."), importlib.import_module(f"{package}.{module}"))
