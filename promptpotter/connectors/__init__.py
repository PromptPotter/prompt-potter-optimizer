"""Connector registry — single lookup point for backend-specific hooks.

**Two ways in, one gate.** Ours are declared as data in :data:`_BUILTIN`; anyone else
ships a package declaring a :data:`ENTRY_POINT_GROUP` entry point
(``docs/developer/stable-api.md`` §1). Both go through :func:`_validate`, so
"registered" means precisely the same thing for a connector we wrote and one we have
never seen. That equivalence is why this module has a body at all.

To add one HERE: write ``connectors/<name>.py`` exporting a ``Connector(...)`` binding,
then add an import + a :data:`_BUILTIN` entry below. No import side effects.

To add one from OUTSIDE, in your own package::

    [project.entry-points."promptpotter.connectors"]
    anything = "my_package.connector:CONNECTOR"

The object named must be a :class:`Connector`, and **its ``name`` field is the registry
key** — the entry-point name is only a label, so a package cannot claim a key its
connector does not declare.

**Discovery is two paths; validation is one. Deliberately.** Declaring our own two as
entry points would be the tidier "single path", and it is wrong here: it makes
``import promptpotter.connectors`` depend on this distribution's installed metadata, so
a plain source-tree run would find zero backends. The property worth protecting is the
one below — a half-wired connector fails at import, never mid-campaign — and that lives
in the validator, not in the channel it arrived through.

**A plugin may not shadow a built-in, and a broken one is fatal.**
``CONNECTORS["promptpotter"]`` is read by name by the L4 inner runner
(``application/runner/inner/tasks.py``), so which object answers that key is not a third
party's call. And a plugin that fails to import raises here rather than being skipped:
skipping would trade a loud error naming the package for ``connector 'x' not
registered`` at mint time, with nothing pointing at the cause.

**A connector is trusted code, not sandboxed — and that is stated, not implied.** Loading
one imports its module into this process, where it sees the provider API keys, the tenant
tree and the identity store, exactly as a module we ship does. Entry points do not weaken
that boundary (anything that can install a distribution into this environment can already
run code here), but they do make the trust *explicit*: installing a connector package is
trusting its publisher completely, and this repo's capability scoping (ADR-0005) governs
API principals, not in-process code. :data:`CONNECTOR_ORIGINS` is the audit surface — it
names the distribution behind every registered key, including the ones that are ours.
"""

from __future__ import annotations

import typing
from importlib.metadata import entry_points

from promptpotter.connectors.promptpotter import CONNECTOR as _PROMPTPOTTER
from promptpotter.connectors.protocol import (
    BackendUnreachableError,
    Connector,
    ConnectorExecution,
)
from promptpotter.connectors.termnorm import CONNECTOR as _TERMNORM

__all__ = [
    "CONNECTORS",
    "CONNECTOR_ORIGINS",
    "DEFAULT_CONNECTOR",
    "ENTRY_POINT_GROUP",
    "BackendUnreachableError",
    "Connector",
    "get",
]

ENTRY_POINT_GROUP = "promptpotter.connectors"
"""The published extension point. A group name, once shipped, is a permanent public
string — third-party packages spell it in their own ``pyproject.toml``, so renaming it
un-registers every plugin at once."""

_BUILTIN: dict[str, Connector] = {
    "termnorm": _TERMNORM,
    "promptpotter": _PROMPTPOTTER,
}

DEFAULT_CONNECTOR = "termnorm"
"""Connector a fresh upload drafts against when its ``pipeline.yaml`` names none.
Lives beside the registry because that is what knows a name is registered; the
import-time guard below keeps the two from drifting."""


def _validate(key: str, c: Connector, origin: str) -> None:
    """Every invariant a registered connector satisfies, whoever wrote it.

    Run over built-ins and plugins alike — that equivalence IS the contract. Registry key
    must match ``name``, the three hooks must be callable, and ``execution`` must be a
    declared mode ``BackendClient.run_query`` can dispatch on. Raises at import, so a
    half-wired connector cannot reach a campaign. (The session contract —
    ``session_factory()`` building a SessionProtocol — stays a behaviour test;
    constructing a session is a side effect we don't want at import.)
    """
    where = f"connector {key!r} [{origin}]"
    if c.name != key:
        raise RuntimeError(f"{where}: registry key != connector.name ({c.name!r}).")
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


def _load() -> tuple[dict[str, Connector], dict[str, str]]:
    """Built-ins then plugins, each through :func:`_validate`. Returns (registry, origins).

    A function rather than inline module code so the resolution rules are testable without
    installing anything — ``tests/test_integrity.py`` monkeypatches :func:`entry_points`
    and calls this directly.
    """
    registry = dict(_BUILTIN)
    origins = dict.fromkeys(_BUILTIN, "built-in")
    for key, builtin in _BUILTIN.items():
        _validate(key, builtin, "built-in")

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
        if not isinstance(obj, Connector):
            raise RuntimeError(
                f"connector entry point {ep.name!r} [{origin}] resolved to "
                f"{type(obj).__name__}, not a promptpotter.connectors.Connector."
            )
        if obj.name in _BUILTIN:
            raise RuntimeError(
                f"connector entry point {ep.name!r} [{origin}] declares {obj.name!r}, which "
                f"ships with PromptPotter. A plugin may not replace a built-in: "
                f"CONNECTORS[{obj.name!r}] is read by name inside the loop. Rename it."
            )
        if obj.name in registry:
            raise RuntimeError(
                f"connector {obj.name!r} declared twice: [{origins[obj.name]}] and [{origin}]."
            )
        _validate(obj.name, obj, origin)
        registry[obj.name] = obj
        origins[obj.name] = origin
    return registry, origins


CONNECTORS, CONNECTOR_ORIGINS = _load()
"""``CONNECTORS`` maps name → connector. ``CONNECTOR_ORIGINS`` maps the same keys to
where each came from (``"built-in"``, or ``"<distribution>: <module>:<attr>"`` — the entry
point's VALUE, since its label is free and only the value says what was imported). The second
exists because a plugin's name is not greppable in this tree: without it, "which code
answers this key" stops being a question this repo can answer."""

if DEFAULT_CONNECTOR not in CONNECTORS:
    raise RuntimeError(f"DEFAULT_CONNECTOR {DEFAULT_CONNECTOR!r} is not a registered connector.")


def get(name: str) -> Connector:
    """Look up a connector by name. Raises ``KeyError`` when unknown."""
    if name not in CONNECTORS:
        known = ", ".join(f"{k} [{CONNECTOR_ORIGINS[k]}]" for k in sorted(CONNECTORS)) or "(none)"
        raise KeyError(f"connector {name!r} not registered. Known: {known}")
    return CONNECTORS[name]
