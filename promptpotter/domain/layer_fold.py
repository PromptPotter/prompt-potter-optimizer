"""The one fold of a document's layers: each key laid by the rule the document's schema declares,
each path stamped with the layer that last laid it."""

from __future__ import annotations

import copy
import functools
import json
import types
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Any, Union, get_args, get_origin

from pydantic import BaseModel


class Lay(Enum):
    """How a layer's value at a key lays over the fold's, declared as the key's field metadata."""

    # Key by key, each by its own rule: a model's or a mapping's default.
    KEYS = "keys"
    # Replaced whole: every other value's default.
    WHOLE = "whole"
    # A node's call: key by key, and a new model laid without a route drops the old route.
    CALL = "call"
    # A list keeps, in the layer's order, what both name; a ``None`` layer keeps the fold's.
    SUBSET = "subset"
    # A list gains, in order, what it lacks.
    UNION = "union"


@dataclass(frozen=True)
class ScopedBy:
    """Declared on a field whose keys address *sibling*: a layer naming another value clears it."""

    sibling: str


@dataclass(frozen=True)
class Laid:
    lay: Lay
    # The sibling a change of which clears this key, and that sibling's value where the fold holds none.
    scope: tuple[str, object] | None = None


type Rule = Callable[[tuple[str, ...]], Laid]


class _Clear:
    def __repr__(self) -> str:
        return "CLEAR"


# A patch value that removes its key, so the layer below shows through.
CLEAR: Any = _Clear()


@dataclass(frozen=True)
class Layer[N: str]:
    name: N
    # A patch, or a function of the fold so far that returns one.
    patch: Mapping[str, Any] | Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class Folded[N: str]:
    document: dict[str, Any] = field(default_factory=dict)
    # Each path a layer laid, to the last layer that laid it.
    stamps: dict[tuple[str, ...], N] = field(default_factory=dict)

    def stamped(self, *at: str) -> dict[str, dict[str, N]]:
        """Per key under *at*, the stamp of each of its own keys."""
        out: dict[str, dict[str, N]] = {}
        for path, name in self.stamps.items():
            if len(path) == len(at) + 2 and path[: len(at)] == at:
                out.setdefault(path[-2], {})[path[-1]] = name
        return out


def fold_layers[N: str](
    rule: Rule, layers: Iterable[Layer[N]], *, under: Folded[N] | None = None
) -> Folded[N]:
    document = copy.deepcopy(under.document) if under is not None else {}
    stamps = dict(under.stamps) if under is not None else {}
    for layer in layers:
        patch = layer.patch(document) if callable(layer.patch) else layer.patch
        document = _laid(rule, (), document, patch, layer.name, stamps)
    return Folded(document, stamps)


def _laid[N: str](
    rule: Rule,
    at: tuple[str, ...],
    held: Mapping[str, object],
    patch: Mapping[str, object],
    name: N,
    stamps: dict[tuple[str, ...], N],
) -> dict[str, object]:
    out = dict(held)
    for key in list(out):
        scope = rule((*at, key)).scope
        if scope is not None and scope[0] in patch and patch[scope[0]] != held.get(*scope):
            del out[key]
            _unstamp(stamps, (*at, key), itself=True)
    for key, value in patch.items():
        path = (*at, key)
        if value is CLEAR:
            out.pop(key, None)
            _unstamp(stamps, path, itself=True)
            continue
        current = out.get(key)
        lay = rule(path).lay
        if lay is Lay.SUBSET and isinstance(current, list):
            out[key] = [v for v in value if v in current] if isinstance(value, list) else current
        elif lay is Lay.UNION and isinstance(current, list) and isinstance(value, list):
            out[key] = [*current, *(v for v in value if v not in current)]
        elif lay in (Lay.KEYS, Lay.CALL) and isinstance(value, Mapping):
            base: Mapping[str, object] = current if isinstance(current, Mapping) else {}
            if base is not current:
                _unstamp(stamps, path, itself=False)
            laid = _laid(rule, path, base, value, name, stamps)
            if not laid and value and _only_clears(value):
                out.pop(key, None)
                _unstamp(stamps, path, itself=True)
                continue
            if (
                lay is Lay.CALL
                and "route_order" not in value
                and value.get("model", base.get("model")) != base.get("model")
            ):
                laid.pop("route_order", None)
                _unstamp(stamps, (*path, "route_order"), itself=True)
            out[key] = laid
        else:
            _unstamp(stamps, path, itself=False)
            out[key] = copy.deepcopy(value)
        stamps[path] = name
    return out


def _only_clears(patch: Mapping[str, object]) -> bool:
    """A container a patch only clears, left empty, goes with it: it reads as never laid."""
    return all(v is CLEAR or (isinstance(v, Mapping) and _only_clears(v)) for v in patch.values())


def wire_patch(rule: Rule, sent: Mapping[str, object], *at: str) -> dict[str, object]:
    """A patch as every door sends it, laid as the fold reads it: an absent key leaves its value,
    ``null`` is `CLEAR` (the layer below shows through), and ``""`` is the empty string. A value
    *rule* lays whole is the value, nulls inside it included."""
    out: dict[str, object] = {}
    for key, value in sent.items():
        if value is None:
            out[key] = CLEAR
        elif isinstance(value, Mapping) and rule((*at, key)).lay in (Lay.KEYS, Lay.CALL):
            out[key] = wire_patch(rule, value, *at, key)
        else:
            out[key] = value
    return out


def wire_value(raw: str, *, text: bool) -> Any:
    """A value typed in as text, read by its declared type: a field taking text keeps the word,
    any other reads *raw* as JSON (``null`` the `CLEAR` `wire_patch` lays); every door's typing."""
    if text:
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        raise ValueError(f"{raw!r} is no JSON value (a string field keeps the word)") from None


def _unstamp(stamps: dict[tuple[str, ...], Any], path: tuple[str, ...], *, itself: bool) -> None:
    for stamped in [p for p in stamps if p[: len(path)] == path and (itself or p != path)]:
        del stamps[stamped]


def rules_of(model: type[BaseModel]) -> Rule:
    """*model*'s declared rule at each path: a `Lay` in a field's metadata, else its type's default."""
    return functools.cache(functools.partial(_rule, model))


def _rule(model: type[BaseModel], at: tuple[str, ...]) -> Laid:
    annotation: Any = model
    metadata: list[object] = []
    owner: type[BaseModel] | None = None
    for key in at:
        annotation, _ = _unwrapped(annotation)
        if _is_model(annotation):
            info = annotation.model_fields.get(key)
            if info is None:
                return Laid(Lay.WHOLE)
            owner, annotation, metadata = annotation, info.annotation, list(info.metadata)
        elif get_origin(annotation) is dict:
            owner, annotation, metadata = None, get_args(annotation)[1], []
        else:
            return Laid(Lay.WHOLE)
    base, more = _unwrapped(annotation)
    metadata += more
    lay = next((m for m in metadata if isinstance(m, Lay)), None)
    if lay is None:
        lay = Lay.KEYS if _is_model(base) or get_origin(base) is dict else Lay.WHOLE
    scoped = next((m for m in metadata if isinstance(m, ScopedBy)), None)
    if scoped is None or owner is None:
        return Laid(lay)
    return Laid(lay, (scoped.sibling, owner.model_fields[scoped.sibling].default))


def _unwrapped(annotation: object) -> tuple[Any, list[object]]:
    """*annotation* less its ``Annotated`` and its ``None`` arm, and the metadata it carried."""
    metadata: list[object] = []
    while True:
        if get_origin(annotation) is Annotated:
            annotation, *carried = get_args(annotation)
            metadata += carried
        elif get_origin(annotation) in (Union, types.UnionType):
            arms = [a for a in get_args(annotation) if a is not type(None)]
            if len(arms) != 1:
                return annotation, metadata
            annotation = arms[0]
        else:
            return annotation, metadata


def _is_model(annotation: object) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)
