from __future__ import annotations

import importlib
import pkgutil
import typing
from collections.abc import Iterator

from promptpotter.connectors.protocol import Connector
from promptpotter.domain.cell_terms import misdeclared_connector_terms
from promptpotter.domain.connector import ConnectorExecution
from promptpotter.shared.plugin_registry import BUILT_IN, Registry, built_in_origin

ENTRY_POINT_GROUP = "promptpotter.connectors"
"""Published: renaming it un-registers every third-party plugin at once."""


def _validate(c: object, origin: str) -> Connector:
    if not isinstance(c, Connector):
        raise RuntimeError(f"[{origin}] resolved to {type(c).__name__}, not a Connector.")
    where = f"connector {c.name!r} [{origin}]"
    if c.name != c.name.lower():
        raise RuntimeError(f"{where}: a connector's name is lowercase.")
    hooks = {
        "wire_adapter": c.wire_adapter,
        "session_factory": c.session_factory,
    }
    for hook, fn in hooks.items():
        if not callable(fn):
            raise RuntimeError(f"{where}: {hook} is not callable.")
    if c.resolve_experiment is not None and not c.owns_panel:
        raise RuntimeError(f"{where}: resolve_experiment needs extract_experiment.")
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
    if (c.execution == "remote_http") != (c.endpoint is not None):
        raise RuntimeError(
            f"{where}: execution={c.execution!r} requires endpoint "
            f"{'set' if c.execution == 'remote_http' else 'unset'}."
        )
    if c.cells_hold_the_machine and c.execution != "in_process":
        raise RuntimeError(f"{where}: cells_hold_the_machine needs execution='in_process'.")
    if c.compose_overlay is not None and not c.cells_hold_the_machine:
        raise RuntimeError(f"{where}: compose_overlay needs cells_hold_the_machine.")
    if c.round_trip_is_latency and c.execution != "remote_http":
        raise RuntimeError(f"{where}: core clocks an in-process cell — drop round_trip_is_latency.")
    if c.self_optimization and not origin.startswith(built_in_origin("promptpotter.application.")):
        raise RuntimeError(f"{where}: only an application built-in is the recursion.")
    if refused := misdeclared_connector_terms(c.cell_terms):
        raise RuntimeError(f"{where}: {'; '.join(refused)}.")
    return c


def _builtins() -> Iterator[tuple[str, object]]:
    here = [f"{__name__}.{m.name}" for m in pkgutil.iter_modules(__path__) if m.name != "protocol"]
    for name in here:
        module = importlib.import_module(name)
        yield module.__name__, getattr(module, "CONNECTOR", None)


# The recursion's connector is the application's (`application/runner/inner/spawn.py`), registered
# where it completes its registries: this package imports nothing of it (`scripts/gate.py::_LAYERING`).
CONNECTORS = Registry(ENTRY_POINT_GROUP, _builtins, _validate)


def upload_connector() -> Connector:
    """What a fresh upload drafts against: the one built-in serving uploads; a check-in edit may
    name a plugin's."""
    found = [
        c
        for n, c in CONNECTORS.table.items()
        if c.serves_uploads and CONNECTORS.origins[n] == BUILT_IN
    ]
    if len(found) != 1:
        raise RuntimeError(
            f"one built-in connector serves uploads, and {[c.name for c in found]} do."
        )
    return found[0]
