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
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

from promptpotter.shared.errors import PayloadInvalidError


class Lay(Enum):
    """How a layer's value at a key lays over the fold's, declared as the key's field metadata."""

    # Key by key, each by its own rule: a model's or a mapping's default.
    KEYS = "keys"
    # Replaced whole: every other value's default.
    WHOLE = "whole"
    # A node's call: key by key, and a new model laid without a route drops the old route.
    CALL = "call"
    # A list keeps, in the layer's order, what both name.
    SUBSET = "subset"
    # A list gains, in order, what it lacks.
    UNION = "union"


@dataclass(frozen=True)
class ScopedBy:
    """Declared on a field whose keys address *sibling*: a layer naming another value clears it."""

    sibling: str


@dataclass(frozen=True)
class ScalarAs:
    """Declared on a field holding a scalar or a mapping: a held scalar lays as the mapping's *key*,
    and a mapping left holding that key alone reads back as its scalar."""

    key: str


@dataclass(frozen=True)
class Laid:
    lay: Lay
    # The sibling a change of which clears this key, and that sibling's value where the fold holds none.
    scope: tuple[str, object] | None = None
    scalar_as: str | None = None

    def as_mapping(self, value: object) -> object:
        return {self.scalar_as: value} if self.scalar_as and isinstance(value, str) else value


@dataclass(frozen=True)
class LaidAs:
    """Declared on a mapping field holding a partial of *model*: its keys lay by *model*'s rule."""

    model: type[BaseModel]


type Rule = Callable[[tuple[str, ...]], Laid]


def untyped_rule(_at: tuple[str, ...]) -> Laid:
    """A document no schema declares: every mapping laid key by key, every other value whole."""
    return Laid(Lay.KEYS)


# A knob's "no bound", on every layer and every wire: ``null`` is no value but a clear.
Unbounded = Literal["unbounded"]
UNBOUNDED: Unbounded = "unbounded"


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
        # A null removes its key on every layer, so the layer below shows through.
        if value is None:
            out.pop(key, None)
            _unstamp(stamps, path, itself=True)
            continue
        laid_at = rule(path)
        current, lay = laid_at.as_mapping(out.get(key)), laid_at.lay
        if lay is Lay.SUBSET and isinstance(current, list):
            out[key] = _list_laid(lay, current, value) if isinstance(value, list) else current
        elif lay is Lay.UNION and isinstance(current, list) and isinstance(value, list):
            out[key] = _list_laid(lay, current, value)
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
            if laid_at.scalar_as is not None and laid.keys() == {laid_at.scalar_as}:
                _unstamp(stamps, path, itself=False)
                out[key] = laid[laid_at.scalar_as]
            else:
                out[key] = laid
        else:
            _unstamp(stamps, path, itself=False)
            out[key] = copy.deepcopy(value)
        stamps[path] = name
    return out


def _only_clears(patch: Mapping[str, object]) -> bool:
    """A container a patch only clears, left empty, goes with it: it reads as never laid."""
    return all(v is None or (isinstance(v, Mapping) and _only_clears(v)) for v in patch.values())


def patch_between(
    rule: Rule, under: Mapping[str, object], over: Mapping[str, object], *at: str
) -> dict[str, object]:
    """The patch (``null`` clears) *rule* lays over *under* to make
    *over*: what *over* states beyond *under*, and nothing *under* already holds. A list no
    patch lays (a subset that widens, a union that is not *under*'s plus extras) is refused."""
    out: dict[str, object] = {}
    rescoped = {
        k
        for k in under
        if (scope := rule((*at, k)).scope) is not None and over.get(scope[0]) != under.get(scope[0])
    }
    for key in under.keys() - over.keys() - rescoped:
        out[key] = None
    for key, value in over.items():
        path = (*at, key)
        laid_at = rule(path)
        held = {} if key in rescoped else laid_at.as_mapping(under.get(key))
        lay, value = laid_at.lay, laid_at.as_mapping(value)
        if lay in (Lay.KEYS, Lay.CALL) and isinstance(value, Mapping) and isinstance(held, Mapping):
            sub = patch_between(rule, held, value, *path)
            if sub or key not in under or key in rescoped:
                out[key] = sub
        elif key not in under or held != value:
            if (
                lay in (Lay.SUBSET, Lay.UNION)
                and isinstance(held, list)
                and isinstance(value, list)
                and _list_laid(lay, held, value) != value
            ):
                raise PayloadInvalidError(
                    f"`{'.'.join(path)}` lays as a {lay.value} over {held!r}, so no patch "
                    f"makes it {value!r}",
                    code="fold_unexpressible",
                )
            out[key] = value
    return out


def _list_laid(lay: Lay, held: list[object], value: list[object]) -> list[object]:
    if lay is Lay.SUBSET:
        return [v for v in value if v in held]
    return [*held, *(v for v in value if v not in held)]


def wire_value(raw: str, *, text: bool) -> Any:
    """A value typed in as text, read by its declared type: a field taking text keeps the word,
    any other reads *raw* as JSON (``null`` the clear), the bare ``unbounded`` its word; every
    door's typing."""
    if text:
        return raw
    if raw == UNBOUNDED:
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
            if (laid_as := next((m for m in metadata if isinstance(m, LaidAs)), None)) is not None:
                annotation = laid_as.model
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
    scalar = next((m.key for m in metadata if isinstance(m, ScalarAs)), None)
    if scoped is None or owner is None:
        return Laid(lay, scalar_as=scalar)
    return Laid(lay, (scoped.sibling, owner.model_fields[scoped.sibling].default), scalar)


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
