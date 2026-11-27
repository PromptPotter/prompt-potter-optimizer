"""The dotted-path grammar a door spells a patch in: each ``PATH`` walks a model's own fields, so
every bound is the model's. The CLI's ``--set`` and ``--arm`` lay their pairs through it."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

from promptpotter.domain.layer_fold import wire_value
from promptpotter.shared.errors import PayloadInvalidError


def path_patch(model: type[BaseModel], sets: Iterable[tuple[str, str]]) -> dict[str, Any]:
    """Each ``(PATH, VALUE)`` laid by *model*'s own dotted paths."""
    body: dict[str, Any] = {}
    for path, raw in sets:
        _lay(body, model, path.split("."), raw, path)
    return body


def _words(member: object) -> tuple[object, ...]:
    if isinstance(member, type) and issubclass(member, StrEnum):
        return tuple(member)
    return get_args(member) if get_origin(member) is Literal else ()


def _laid(into: object, annotation: object, rest: list[str], raw: str, path: str) -> Any:
    """*raw* laid at *rest* under a field typed *annotation*: a model by its fields, a mapping by
    any key, ``Any`` as it comes; at the leaf text, or a closed set naming the word, keeps it."""
    split = get_origin(annotation) in (Union, UnionType)
    members = [m for m in get_args(annotation) if m is not type(None)] if split else [annotation]
    if not rest:
        words = [w for m in members for w in _words(m)]
        try:
            return raw if raw in words else wire_value(raw, text=str in members)
        except ValueError as exc:
            said = f"{raw!r} is none of {', '.join(map(str, words))}" if words else exc
            raise PayloadInvalidError(f"{path}: {said}") from None
    for member in members:
        if isinstance(member, type) and issubclass(member, BaseModel):
            return _lay(into if isinstance(into, dict) else {}, member, rest, raw, path)
        if get_origin(member) is dict or member is Any:
            value_type = get_args(member)[1] if member is not Any else Any
            key, *more = rest
            out = into if isinstance(into, dict) else {}
            out[key] = _laid(out.get(key), value_type, more, raw, path)
            return out
    raise PayloadInvalidError(f"{path!r} reaches past a field that takes a value.")


def _lay(
    body: dict[str, Any], model: type[BaseModel], path: list[str], raw: str, whole: str
) -> Any:
    head, *rest = path
    info = model.model_fields.get(head)
    if info is None and model.model_config.get("extra") != "allow":
        raise PayloadInvalidError(
            f"{whole!r} names no field of {model.__name__}: {head!r} is not one of "
            f"{', '.join(model.model_fields)}."
        )
    body[head] = _laid(body.get(head), Any if info is None else info.annotation, rest, raw, whole)
    return body
