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


class Registry[M: _Named]:
    """Members by their own ``name``, loaded on first read; rules: ``connectors/CLAUDE.md``."""

    def __init__(
        self,
        group: str,
        builtins: Callable[[], Iterable[tuple[str, object]]],
        validate: Callable[[object, str], M],
        *,
        shipped: frozenset[str] = frozenset(),
    ) -> None:
        self.group = group
        self._builtins = builtins
        self._validate = validate
        self._shipped = shipped

    @functools.cached_property
    def _loaded(self) -> tuple[Mapping[str, M], Mapping[str, str]]:
        table: dict[str, M] = {}
        origins: dict[str, str] = {}

        def add(member: M, origin: str, label: str) -> None:
            if member.name in table:
                raise RuntimeError(
                    f"{self.group}: {member.name!r} declared twice: "
                    f"[{origins[member.name]}] and [{origin}]."
                )
            table[member.name] = member
            origins[member.name] = label

        for where, obj in self._builtins():
            origin = f"{BUILT_IN}: {where}"
            add(self._validate(obj, origin), origin, BUILT_IN)
        if absent := sorted(self._shipped - table.keys()):
            raise RuntimeError(f"{self.group}: {absent} must be built-in and are not.")
        built_in = frozenset(table)

        for label, origin, obj in load_plugins(self.group):
            member = self._validate(obj, origin)
            if member.name in built_in:
                raise RuntimeError(
                    f"{self.group} entry point {label!r} [{origin}] declares {member.name!r}, "
                    "which ships with PromptPotter. A plugin may not replace a built-in: which "
                    "object answers a built-in key is read by name inside the loop. Rename it."
                )
            add(member, origin, origin)
        return MappingProxyType(table), MappingProxyType(origins)

    def complete(self) -> None:
        """Loads now, so a member that cannot load stops the boot rather than the first read."""
        _ = self._loaded

    @property
    def table(self) -> Mapping[str, M]:
        return self._loaded[0]

    @property
    def origins(self) -> Mapping[str, str]:
        return self._loaded[1]

    def get(self, name: str) -> M:
        table, origins = self._loaded
        if name not in table:
            known = ", ".join(f"{k} [{origins[k]}]" for k in sorted(table)) or "(none)"
            raise KeyError(f"{name!r} is not registered in {self.group}. Known: {known}")
        return table[name]
