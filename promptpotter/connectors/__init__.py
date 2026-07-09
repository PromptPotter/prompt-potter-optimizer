"""Connector registry — single lookup point for backend-specific hooks.

Connectors are declared as data in :data:`CONNECTORS`. To add one, write
``connectors/<name>.py`` exporting a ``Connector(...)`` binding, then add
an import + dict entry below. No import-side-effects.
"""

from __future__ import annotations

import typing

from promptpotter.connectors.llm_only import CONNECTOR as _LLM_ONLY
from promptpotter.connectors.promptpotter import CONNECTOR as _PROMPTPOTTER
from promptpotter.connectors.protocol import (
    BackendUnreachableError,
    Connector,
    ConnectorExecution,
)
from promptpotter.connectors.termnorm import CONNECTOR as _TERMNORM

__all__ = ["CONNECTORS", "DEFAULT_CONNECTOR", "BackendUnreachableError", "Connector", "get"]

CONNECTORS: dict[str, Connector] = {
    "termnorm": _TERMNORM,
    "promptpotter": _PROMPTPOTTER,
    "llm_only": _LLM_ONLY,
}

DEFAULT_CONNECTOR = "termnorm"
"""Connector a fresh upload drafts against when its ``pipeline.json`` names none.
Lives beside :data:`CONNECTORS` because that is what knows a name is registered;
the import-time guard below keeps the two from drifting."""


# Fail import if any row is half-wired: registry key must match ``name``, the
# three hooks must be callable, and ``execution`` must be a declared mode
# ``BackendClient.run_query`` can dispatch on. (The session contract —
# ``session_factory()`` building a SessionProtocol — stays a behavior test;
# constructing a session is a side effect we don't want at import.)
_valid_execution = set(typing.get_args(ConnectorExecution))
for _key, _c in CONNECTORS.items():
    if _c.name != _key:
        raise RuntimeError(f"CONNECTORS[{_key!r}] registry key != connector.name ({_c.name!r}).")
    for _hook in ("wire_adapter", "extract_experiment", "session_factory"):
        if not callable(getattr(_c, _hook, None)):
            raise RuntimeError(f"CONNECTORS[{_key!r}]: {_hook} is not callable.")
    if _c.execution not in _valid_execution:
        raise RuntimeError(
            f"CONNECTORS[{_key!r}]: execution {_c.execution!r} not in {_valid_execution}."
        )
    # An in_process connector MUST supply the dispatch arm run_query calls (and a
    # remote_http one must not — the field only makes sense paired with the mode).
    if (_c.execution == "in_process") != callable(_c.in_process_run):
        raise RuntimeError(
            f"CONNECTORS[{_key!r}]: execution={_c.execution!r} requires "
            f"in_process_run {'set' if _c.execution == 'in_process' else 'unset'}."
        )
del _valid_execution, _key, _c, _hook

if DEFAULT_CONNECTOR not in CONNECTORS:
    raise RuntimeError(f"DEFAULT_CONNECTOR {DEFAULT_CONNECTOR!r} is not a registered connector.")


def get(name: str) -> Connector:
    """Look up a connector by name. Raises ``KeyError`` when unknown."""
    if name not in CONNECTORS:
        known = ", ".join(sorted(CONNECTORS)) or "(none)"
        raise KeyError(f"connector {name!r} not registered. Known: {known}")
    return CONNECTORS[name]
