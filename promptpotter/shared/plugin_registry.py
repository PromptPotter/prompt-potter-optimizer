from __future__ import annotations

import functools
from importlib.metadata import EntryPoints, entry_points
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping

# Which member answers a node name decides the text an optimizer prompt carries.
shapes_optimizer_prompt(__name__)

__all__ = ["BUILT_IN", "load_plugins", "load_registry", "lookup"]

BUILT_IN = "built-in"


class _Named(Protocol):
    @property
    def name(self) -> str: ...


@functools.cache
def _installed_entry_points() -> EntryPoints:
    return entry_points()


def load_plugins(group: str) -> Iterator[tuple[str, str, object]]:
    for ep in _installed_entry_points().select(group=group):
        dist = getattr(getattr(ep, "dist", None), "name", None) or "unknown distribution"
        origin = f"{dist}: {ep.value}"
        try:
            obj = ep.load()
        except Exception as exc:
            raise RuntimeError(
                f"{group} entry point {ep.name!r} [{origin}] failed to import: {exc!r}. "
                "Uninstall or fix that package. PromptPotter does not start with a plugin it "
                "cannot load, because a skipped one comes back later as an unexplained "
                "'not registered'."
            ) from exc
        yield ep.name, origin, obj


def load_registry[M: _Named](
    group: str,
    builtins: Iterable[tuple[str, object]],
    validate: Callable[[object, str], M],
) -> tuple[Mapping[str, M], Mapping[str, str]]:
    """``(members, origins)`` by each member's own ``name``; rules: ``connectors/CLAUDE.md``."""
    table: dict[str, M] = {}
    origins: dict[str, str] = {}

    def add(member: M, origin: str, label: str) -> None:
        if member.name in table:
            raise RuntimeError(
                f"{group}: {member.name!r} declared twice: [{origins[member.name]}] and [{origin}]."
            )
        table[member.name] = member
        origins[member.name] = label

    for where, obj in builtins:
        origin = f"{BUILT_IN}: {where}"
        add(validate(obj, origin), origin, BUILT_IN)
    shipped = frozenset(table)

    for label, origin, obj in load_plugins(group):
        member = validate(obj, origin)
        if member.name in shipped:
            raise RuntimeError(
                f"{group} entry point {label!r} [{origin}] declares {member.name!r}, which "
                "ships with PromptPotter. A plugin may not replace a built-in: which object "
                "answers a built-in key is read by name inside the loop. Rename it."
            )
        add(member, origin, origin)
    return MappingProxyType(table), MappingProxyType(origins)


def lookup[M](group: str, registry: tuple[Mapping[str, M], Mapping[str, str]], name: str) -> M:
    table, origins = registry
    if name not in table:
        known = ", ".join(f"{k} [{origins[k]}]" for k in sorted(table)) or "(none)"
        raise KeyError(f"{name!r} is not registered in {group}. Known: {known}")
    return table[name]
