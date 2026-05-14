"""Cross-package scoring primitives.

``extract_item_label`` is shared with metrics, evaluators, and views —
one definition, all callers reach for the same name.
"""

from __future__ import annotations

from typing import Any


def extract_item_label(c: Any) -> str:
    """Return display label of a ranked item (dict ``{candidate: ...}``, list/tuple, or string).

    The wire-side dict key remains ``candidate`` (TermNorm output shape).
    """
    if isinstance(c, dict):
        return str(c.get("candidate", c))
    return c[0] if isinstance(c, (list, tuple)) else str(c)
