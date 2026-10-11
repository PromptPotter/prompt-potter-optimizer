"""A slots dataclass as its OWN wire record; a field is SPARSE unless its ``metadata`` says not."""

from __future__ import annotations

import linecache
from collections.abc import Callable, Mapping
from dataclasses import MISSING, fields
from functools import cached_property
from math import isfinite
from types import GenericAlias, NoneType, UnionType
from typing import (
    Any,
    NamedTuple,
    Required,
    TypedDict,
    Union,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
)

from pydantic import ConfigDict

__all__ = [
    "ALWAYS",
    "BY_HAND",
    "REST",
    "WireRecord",
    "always_as",
    "record_of",
    "typed_record",
    "whole",
]

ALWAYS: Mapping[str, object] = {"wire": "always"}
REST: Mapping[str, object] = {"wire": "rest"}
# Sparse, written and read by the owner; its key holds whatever the field may.
BY_HAND: Mapping[str, object] = {"wire": "by_hand"}


def always_as(key: str) -> Mapping[str, object]:
    return {"wire": "always", "key": key}


def _mistyped(value: object, where: str, declared: str) -> TypeError:
    return TypeError(f"{where} holds {value!r} ({type(value).__name__}), declared {declared}")


def _real(value: object, where: str) -> float:
    # `bool` is an `int` subclass, so a backend answering `true` would read as a number.
    if type(value) is float:
        if isfinite(value):
            return value
    elif isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value):
        return float(value)
    raise _mistyped(value, where, "a finite number")


def whole(value: object, where: str) -> int:
    if type(value) is int or (isinstance(value, int) and not isinstance(value, bool)):
        return value
    raise _mistyped(value, where, "an integer")


def _text(value: object, where: str) -> str:
    if type(value) is str:
        return value
    if isinstance(value, str):
        return str.__str__(value)
    raise _mistyped(value, where, "text")


def _flag(value: object, where: str) -> bool:
    if value is True or value is False:
        return value
    raise _mistyped(value, where, "a boolean")


def _mapping(value: object, where: str) -> Mapping[str, Any]:
    if type(value) is dict or isinstance(value, Mapping):
        return value
    raise _mistyped(value, where, "an object")


def _sequence(value: object, where: str) -> list[Any] | tuple[Any, ...]:
    if type(value) is list or isinstance(value, (list, tuple)):
        return value
    raise _mistyped(value, where, "a list")


def _read_only(self: object, *_: object, **__: object) -> None:
    raise TypeError(f"{type(self).__name__} is read-only")


class _Frozen(dict[str, Any]):
    """A ``dict`` closed to writes, because ``MappingProxyType`` neither copies nor pickles."""

    __slots__ = ()
    __setitem__ = __delitem__ = __ior__ = _read_only  # type: ignore[assignment]
    clear = pop = popitem = setdefault = update = _read_only  # type: ignore[assignment]

    def __reduce__(self) -> tuple[type[_Frozen], tuple[dict[str, Any]]]:
        return _Frozen, (dict(self),)


_SCALARS: dict[object, Callable[..., object]] = {float: _real, int: whole, str: _text, bool: _flag}
_RECORDS: dict[object, WireRecord[Any]] = {}
_HELPERS: dict[str, object] = {
    **{reader.__name__: reader for reader in _SCALARS.values()},
    "_mapping": _mapping,
    "_sequence": _sequence,
    "_frozen": _Frozen,
}


class _Crossing(NamedTuple):
    """How one hint crosses the wire, each direction as source over the name holding the value."""

    read: Callable[[str], str]
    # ``None`` where the field's value is its own wire value.
    write: Callable[[str], str] | None
    declared: object


def _crossing(hint: Any, where: str, scope: dict[str, object]) -> _Crossing:
    if hint is Any:
        return _Crossing(lambda held: held, None, hint)
    if hint in _SCALARS:
        reader = _SCALARS[hint].__name__
        return _Crossing(lambda held: f"{reader}({held}, {where!r})", None, hint)
    if hint in _RECORDS:
        name = hint.__name__
        scope[f"read_{name}"], scope[f"write_{name}"] = hint.from_wire, hint.wire
        return _Crossing(
            lambda held: f"read_{name}(_mapping({held}, {where!r}))",
            lambda held: f"write_{name}({held})",
            _RECORDS[hint].typed,
        )
    if is_typeddict(hint):
        return _Crossing(lambda held: f"_mapping({held}, {where!r})", None, hint)
    origin = get_origin(hint)
    if origin is not Mapping and origin is not tuple:
        raise TypeError(f"no wire reading is declared for {hint!r}")
    one = _crossing(get_args(hint)[1 if origin is Mapping else 0], where, scope)
    # A member that is its own wire value is copied whole; a ``null`` member is an absent one.
    each, put = one.read("m"), one.write
    if origin is Mapping:
        return _Crossing(
            (lambda held: f"_frozen(dict(_mapping({held}, {where!r})))")
            if each == "m"
            else (
                lambda held: (
                    f"_frozen({{k: {each} for k, m in _mapping({held}, {where!r}).items() "
                    "if m is not None})"
                )
            ),
            (lambda held: f"dict({held})")
            if put is None
            else (lambda held: f"{{k: {put('m')} for k, m in {held}.items()}}"),
            GenericAlias(dict, (str, one.declared)),
        )
    return _Crossing(
        (lambda held: f"tuple(_sequence({held}, {where!r}))")
        if each == "m"
        else (
            lambda held: f"tuple([{each} for m in _sequence({held}, {where!r}) if m is not None])"
        ),
        (lambda held: f"list({held})")
        if put is None
        else (lambda held: f"[{put('m')} for m in {held}]"),
        GenericAlias(list, (one.declared,)),
    )


def _without_none(hint: Any) -> Any:
    if get_origin(hint) not in (Union, UnionType):
        return hint
    (kept,) = (arm for arm in get_args(hint) if arm is not NoneType)
    return kept


def typed_record(
    name: str, doc: str, hints: Mapping[str, object], *, module: str, open: bool
) -> type:
    """A key is optional unless its hint is ``Required``."""
    record: type = TypedDict(name, dict(hints), total=False)  # type: ignore[misc]
    record.__doc__ = doc
    record.__module__ = module
    record.__pydantic_config__ = ConfigDict(extra="allow" if open else "ignore")  # type: ignore[attr-defined]
    return record


class WireRecord[T]:
    def __init__(
        self,
        owner: type[T],
        typed: type,
        keys: frozenset[str],
        sources: Mapping[str, tuple[str, list[str]]],
        scope: dict[str, object],
    ) -> None:
        self.typed = typed
        self.keys = keys
        self._owner, self._sources, self._scope = owner, sources, scope

    @classmethod
    def of(cls, owner: type[T], name: str, doc: str) -> WireRecord[T]:
        resolved = get_type_hints(owner)
        scope: dict[str, object] = {**_HELPERS, "__name__": owner.__module__, "make": owner}
        hints: dict[str, object] = {}
        by_hand: list[str] = []
        reads: list[str] = []
        always: list[str] = []
        written: list[str] = []
        said: list[str] = []
        rest: str | None = None
        for f in fields(owner):  # type: ignore[arg-type]
            how = f.metadata.get("wire")
            if how == "rest":
                rest = f.name
                continue
            key = f.metadata.get("key", f.name)
            if how == "by_hand":
                by_hand.append(f.name)
                hints[key] = resolved[f.name]
                continue
            where = f"{owner.__name__}.{f.name}"
            base = _without_none(resolved[f.name])
            crossing = _crossing(base, where, scope)
            put = crossing.write
            held = f"owner.{f.name}"
            if f.default is MISSING and f.default_factory is MISSING:
                default = None
                by_hand.append(f.name)
            else:
                default = f.default_factory() if f.default_factory is not MISSING else f.default
                absent = _absent(base, default, where, scope)
                # Empty text is absent in both directions, so a sparse field never holds it.
                unsaid = " or v == ''" if base is str and default is None else ""
                reads.append(
                    f"{f.name}={absent} if (v := get({key!r})) is None{unsaid} "
                    f"else {crossing.read('v')},"
                )
            if how == "always":
                nullable = put is not None or resolved[f.name] != base
                declared = crossing.declared
                hints[key] = Required[declared | None if nullable else declared]  # type: ignore[operator]
                always.append(f"{key!r}: {held if put is None else f'{put(held)} or None'},")
                said.append(f"out[{key!r}] = {held}")
                continue
            hints[key] = crossing.declared
            if put is not None and base in _RECORDS and default is not None:
                written += [f"value = {put(held)}", "if value:", f"    out[{key!r}] = value"]
                said += [f"value = {held}", f"if value != {absent}:", f"    out[{key!r}] = value"]
                continue
            # Where the default is ``None``, a zero and an empty container are statements.
            says = "value is not None" if default is None and base not in (str, bool) else "value"
            wire = "value" if put is None else put("value")
            written += [f"value = {held}", f"if {says}:", f"    out[{key!r}] = {wire}"]
            said += [f"value = {held}", f"if {says}:", f"    out[{key!r}] = value"]
        scope["keys"] = keys = frozenset(hints)
        opened = "{}"
        if rest is not None:
            reads.append(f"{rest}=_frozen({{k: m for k, m in data.items() if k not in keys}}),")
            scope["rest_claims"] = _rest_claims
            written += [
                f"rest = owner.{rest}",
                "if rest:",
                "    if not keys.isdisjoint(rest):",
                f"        raise rest_claims({f'{owner.__name__}.{rest}'!r}, keys & rest.keys())",
                "    out.update(rest)",
            ]
            # A declared key outranks an undeclared one of its name: the field is the fact.
            opened = f"dict(owner.{rest})"
        reads += [f"{field_name}={field_name}," for field_name in by_hand]
        record = cls(
            owner,
            typed_record(name, doc, hints, module=owner.__module__, open=rest is not None),
            keys,
            {
                "read": (
                    ", ".join(["data", *(["*", *by_hand] if by_hand else [])]),
                    ["get = data.get", "return make(", *(f"    {r}" for r in reads), ")"],
                ),
                "write": (
                    "owner",
                    ["out = {", *(f"    {line}" for line in always), "}", *written, "return out"],
                ),
                "said": ("owner", [f"out = {opened}", *said, "return out"]),
            },
            scope,
        )
        _RECORDS[owner] = record
        return record

    def _derived(self, name: str) -> Callable[..., Any]:
        """Unrolled to source and compiled on first use, as ``dataclasses`` writes ``__init__``: no per-cell field loop."""
        signature, body = self._sources[name]
        filename = f"<wire {self._owner.__qualname__}.{name}>"
        source = "\n".join([f"def {name}({signature}):", *(f"    {line}" for line in body), ""])
        exec(compile(source, filename, "exec"), self._scope)
        linecache.cache[filename] = (len(source), None, source.splitlines(keepends=True), filename)
        derived: Callable[..., Any] = self._scope[name]  # type: ignore[assignment]
        derived.__qualname__ = f"{self._owner.__qualname__}.<wire>.{name}"
        return derived

    @cached_property
    def read(self) -> Callable[..., T]:
        """A flat record as the owner; a field the owner reads by hand is a keyword it passes."""
        return self._derived("read")

    @cached_property
    def write(self) -> Callable[[T], dict[str, object]]:
        """The owner's flat record — :attr:`read`'s inverse, less the keys it writes by hand."""
        return self._derived("write")

    @cached_property
    def said(self) -> Callable[[T], dict[str, object]]:
        """Each key :attr:`write` writes, holding the FIELD in place of its wire value."""
        return self._derived("said")


def _absent(base: Any, default: object, where: str, scope: dict[str, object]) -> str:
    """Only an empty or falsy default is allowed: one the writer cannot tell from a statement is never written."""
    if base in _SCALARS or default is None:
        if default:
            raise TypeError(f"{where} defaults to {default!r}, which an absent key cannot say")
        return repr(default)
    empty: object = (
        () if get_origin(base) is tuple else base.from_wire({}) if base in _RECORDS else _Frozen()
    )
    if empty != default:
        raise TypeError(f"{where} defaults to {default!r}, which an absent key cannot say")
    name = f"no_{where.rpartition('.')[2]}"
    scope[name] = empty
    return name


def _rest_claims(where: str, claimed: set[str]) -> ValueError:
    return ValueError(
        f"{where} holds {sorted(claimed)}, which its owner declares as fields: written there, "
        "each would be read back as the field"
    )


def record_of(owner: type) -> type:
    return _RECORDS[owner].typed
