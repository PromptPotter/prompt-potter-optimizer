from __future__ import annotations

import functools
import importlib
import pkgutil
import typing

from promptpotter.connectors.protocol import Connector
from promptpotter.domain.connector import ConnectorExecution
from promptpotter.shared.plugin_registry import BUILT_IN, load_registry, lookup

if typing.TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

__all__ = [
    "DEFAULT_CONNECTOR",
    "ENTRY_POINT_GROUP",
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
    hooks = {
        "wire_adapter": c.wire_adapter,
        "session_factory": c.session_factory,
    }
    for hook, fn in hooks.items():
        if not callable(fn):
            raise RuntimeError(f"{where}: {hook} is not callable.")
    # The panel file and its reader are one declaration: either alone is a file nobody reads or
    # a reader nothing calls.
    if bool(c.experiment_file) != callable(c.extract_experiment):
        raise RuntimeError(
            f"{where}: experiment_file {c.experiment_file!r} requires extract_experiment "
            f"{'set' if c.experiment_file else 'unset'}."
        )
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
    # A cell that holds THIS machine runs on it, and a remote one runs on its backend's.
    if c.cells_hold_the_machine and c.execution != "in_process":
        raise RuntimeError(f"{where}: cells_hold_the_machine needs execution='in_process'.")
    # The overlay is swept for where a machine slot is taken, and only such a cell takes one.
    if c.compose_overlay is not None and not c.cells_hold_the_machine:
        raise RuntimeError(f"{where}: compose_overlay needs cells_hold_the_machine.")
    # A remote backend answers `GET /pipeline` itself, so a second declaration would never be read.
    if c.pipeline_declaration is not None and c.execution != "in_process":
        raise RuntimeError(f"{where}: pipeline_declaration needs execution='in_process'.")
    return c


def _builtins() -> Iterator[tuple[str, object]]:
    for m in pkgutil.iter_modules(__path__):
        if m.name != "protocol":
            module = importlib.import_module(f"{__name__}.{m.name}")
            yield module.__name__, getattr(module, "CONNECTOR", None)


@functools.cache
def _load() -> tuple[Mapping[str, Connector], Mapping[str, str]]:
    """Every module in this package but ``protocol`` is a built-in, then the plugins — once per
    process."""
    loaded = load_registry(ENTRY_POINT_GROUP, _builtins(), _validate)
    if loaded[1].get(DEFAULT_CONNECTOR) != BUILT_IN:
        raise RuntimeError(f"DEFAULT_CONNECTOR {DEFAULT_CONNECTOR!r} is not a built-in connector.")
    return loaded


def registered() -> Mapping[str, Connector]:
    return _load()[0]


def connector_origins() -> Mapping[str, str]:
    """The same keys → ``"built-in"`` or ``"<distribution>: <module>:<attr>"``, the entry point's
    VALUE: its label is free, and a plugin's name greps to nothing in this tree."""
    return _load()[1]


def get(name: str) -> Connector:
    return lookup(ENTRY_POINT_GROUP, _load(), name)
