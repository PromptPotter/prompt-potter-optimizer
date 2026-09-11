from __future__ import annotations

import functools
import importlib
import pkgutil
import typing
from importlib.metadata import entry_points
from types import MappingProxyType

from promptpotter.connectors.protocol import Connector
from promptpotter.domain.connector import ConnectorExecution

if typing.TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "DEFAULT_CONNECTOR",
    "ENTRY_POINT_GROUP",
    "Connector",
    "connector_origins",
    "get",
    "registered",
]

ENTRY_POINT_GROUP = "promptpotter.connectors"
"""The published extension point. A group name, once shipped, is a permanent public
string — third-party packages spell it in their own ``pyproject.toml``, so renaming it
un-registers every plugin at once."""

DEFAULT_CONNECTOR = "termnorm"
"""Connector a fresh upload drafts against when its ``pipeline.yaml`` names none. Building the
table raises unless a built-in answers it."""


def _validate(c: object, origin: str) -> Connector:
    """Every invariant a registered connector satisfies, built-in or plugin — that equivalence IS
    the contract."""
    if not isinstance(c, Connector):
        raise RuntimeError(f"[{origin}] resolved to {type(c).__name__}, not a Connector.")
    where = f"connector {c.name!r} [{origin}]"
    for hook in ("wire_adapter", "extract_experiment", "session_factory"):
        if not callable(getattr(c, hook, None)):
            raise RuntimeError(f"{where}: {hook} is not callable.")
    valid_execution = set(typing.get_args(ConnectorExecution))
    if c.execution not in valid_execution:
        raise RuntimeError(f"{where}: execution {c.execution!r} not in {valid_execution}.")
    # An in_process connector MUST supply the dispatch arm run_query calls (and a
    # remote_http one must not — the field only makes sense paired with the mode).
    if (c.execution == "in_process") != callable(c.in_process_run):
        raise RuntimeError(
            f"{where}: execution={c.execution!r} requires in_process_run "
            f"{'set' if c.execution == 'in_process' else 'unset'}."
        )
    # A credential only means something over a wire. An ``in_process`` connector has
    # none, so a token declared on it is dead config that reads as protection.
    if c.execution == "in_process" and c.auth_token is not None:
        raise RuntimeError(f"{where}: execution='in_process' has no wire — drop auth_token.")
    return c


def _add(
    registry: dict[str, Connector], origins: dict[str, str], c: Connector, origin: str, label: str
) -> None:
    if c.name in registry:
        raise RuntimeError(
            f"connector {c.name!r} declared twice: [{origins[c.name]}] and [{origin}]."
        )
    registry[c.name] = c
    origins[c.name] = label


@functools.cache
def _load() -> tuple[Mapping[str, Connector], Mapping[str, str]]:
    """Every module in this package but ``protocol`` is a built-in, then the plugins — each
    through :func:`_validate`, once per process."""
    registry: dict[str, Connector] = {}
    origins: dict[str, str] = {}
    for m in pkgutil.iter_modules(__path__):
        if m.name != "protocol":
            module = importlib.import_module(f"{__name__}.{m.name}")
            origin = f"built-in: {module.__name__}"
            builtin = _validate(getattr(module, "CONNECTOR", None), origin)
            _add(registry, origins, builtin, origin, "built-in")
    if DEFAULT_CONNECTOR not in registry:
        raise RuntimeError(f"DEFAULT_CONNECTOR {DEFAULT_CONNECTOR!r} is not a built-in connector.")
    builtins = frozenset(registry)

    for ep in entry_points(group=ENTRY_POINT_GROUP):
        dist = getattr(getattr(ep, "dist", None), "name", None) or "unknown distribution"
        origin = f"{dist}: {ep.value}"
        try:
            obj = ep.load()
        except Exception as exc:
            raise RuntimeError(
                f"connector entry point {ep.name!r} [{origin}] failed to import: {exc!r}. "
                f"Uninstall or fix that package. PromptPotter does not start with a "
                f"connector it cannot load, because a skipped one comes back later as an "
                f"unexplained 'not registered'."
            ) from exc
        plugin = _validate(obj, origin)
        if plugin.name in builtins:
            raise RuntimeError(
                f"connector entry point {ep.name!r} [{origin}] declares {plugin.name!r}, which "
                f"ships with PromptPotter. A plugin may not replace a built-in: "
                f"get({plugin.name!r}) is read by name inside the loop. Rename it."
            )
        _add(registry, origins, plugin, origin, origin)
    return MappingProxyType(registry), MappingProxyType(origins)


def registered() -> Mapping[str, Connector]:
    return _load()[0]


def connector_origins() -> Mapping[str, str]:
    """The same keys → ``"built-in"`` or ``"<distribution>: <module>:<attr>"``, the entry point's
    VALUE: its label is free, and a plugin's name greps to nothing in this tree."""
    return _load()[1]


def get(name: str) -> Connector:
    table = registered()
    if name not in table:
        known = ", ".join(f"{k} [{connector_origins()[k]}]" for k in sorted(table)) or "(none)"
        raise KeyError(f"connector {name!r} not registered. Known: {known}")
    return table[name]
