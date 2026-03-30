"""Dict-style access mixins for dataclasses.

Provides backward compatibility with code that treats dataclasses as dicts
(``obj["key"]``, ``obj.get("key")``).  Leaf-level — no domain dependencies.
"""

from typing import Any


class DictAccessMixin:
    """Read-only dict-style access: ``obj["key"]`` and ``obj.get("key")``."""

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


class MutableDictAccessMixin(DictAccessMixin):
    """Adds ``obj["key"] = value`` on top of read-only access."""

    def __setitem__(self, key: str, value: Any) -> None:
        setattr(self, key, value)
