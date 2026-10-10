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
"""Published: renaming it un-registers every third-party plugin at once."""

DEFAULT_CONNECTOR = "termnorm"
"""What a fresh upload drafts against when its ``pipeline.yaml`` names no connector."""


def _validate(c: object, origin: str) -> Connector:
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
    if bool(c.experiment_file) != callable(c.extract_experiment):
        raise RuntimeError(
            f"{where}: experiment_file {c.experiment_file!r} requires extract_experiment "
            f"{'set' if c.experiment_file else 'unset'}."
        )
    valid_execution = set(typing.get_args(ConnectorExecution))
    if c.execution not in valid_execution:
        raise RuntimeError(f"{where}: execution {c.execution!r} not in {valid_execution}.")
    if (c.execution == "in_process") != callable(c.in_process_run):
        raise RuntimeError(
            f"{where}: execution={c.execution!r} requires in_process_run "
            f"{'set' if c.execution == 'in_process' else 'unset'}."
        )
    if c.execution == "in_process" and c.auth_token is not None:
        raise RuntimeError(f"{where}: execution='in_process' has no wire — drop auth_token.")
    if c.cells_hold_the_machine and c.execution != "in_process":
        raise RuntimeError(f"{where}: cells_hold_the_machine needs execution='in_process'.")
    if c.compose_overlay is not None and not c.cells_hold_the_machine:
        raise RuntimeError(f"{where}: compose_overlay needs cells_hold_the_machine.")
    # A remote backend answers `GET /pipeline` itself, so a second declaration would never be read.
    if c.pipeline_declaration is not None and c.execution != "in_process":
        raise RuntimeError(f"{where}: pipeline_declaration needs execution='in_process'.")
    return c


# Loaded by name: this package imports nothing of the application (`scripts/gate.py::_LAYERING`).
_APPLICATION_BUILT_INS = ("promptpotter.application.runner.inner.connector",)


def _builtins() -> Iterator[tuple[str, object]]:
    here = [f"{__name__}.{m.name}" for m in pkgutil.iter_modules(__path__) if m.name != "protocol"]
    for name in (*here, *_APPLICATION_BUILT_INS):
        module = importlib.import_module(name)
        yield module.__name__, getattr(module, "CONNECTOR", None)


@functools.cache
def _load() -> tuple[Mapping[str, Connector], Mapping[str, str]]:
    loaded = load_registry(ENTRY_POINT_GROUP, _builtins(), _validate)
    if loaded[1].get(DEFAULT_CONNECTOR) != BUILT_IN:
        raise RuntimeError(f"DEFAULT_CONNECTOR {DEFAULT_CONNECTOR!r} is not a built-in connector.")
    return loaded


def registered() -> Mapping[str, Connector]:
    return _load()[0]


def connector_origins() -> Mapping[str, str]:
    return _load()[1]


def get(name: str) -> Connector:
    return lookup(ENTRY_POINT_GROUP, _load(), name)
