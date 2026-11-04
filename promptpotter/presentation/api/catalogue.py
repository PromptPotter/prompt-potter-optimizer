"""The surface bound to the catalogue: every route a session reaches is named by a `CommandKind` or a
`ReadKind`, and a route answers what its row's application callable returns: a read's answer, a
command's applied `Applier[R]`."""

from __future__ import annotations

import types
from collections.abc import Iterable
from typing import Union, get_args, get_origin

from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel

from promptpotter.application.commands.dispatcher import handler_of
from promptpotter.domain.command_kinds import CommandAcceptedBody, CommandKind, ReadKind
from promptpotter.shared.named_rows import imported


def _returns(answer: object) -> object:
    """The return annotation alone: a parameter's type may be imported for type-checking only."""
    if not isinstance(answer, types.FunctionType):
        raise TypeError(f"a row's callable is a function, not {answer!r}")
    returns = answer.__annotations__.get("return")
    return eval(returns, answer.__globals__) if isinstance(returns, str) else returns


def command_answer(kind: CommandKind) -> object | None:
    """The `R` of the kind's handler's `Applier[R]`; `None` (an `R` of `object`) answers the record."""
    (applied,) = get_args(_returns(handler_of(kind)))
    return None if applied is object else applied


def _without_none(annotation: object) -> frozenset[object]:
    if get_origin(annotation) in (Union, types.UnionType):
        return frozenset(arg for arg in get_args(annotation) if arg is not type(None))
    return frozenset({annotation})


def _command_faults(command: CommandKind, served: list[APIRoute]) -> Iterable[str]:
    if not served:
        yield f"command {command} has no route"
    applied = command_answer(command)
    # A result no wire model carries (a launch's handle) is its route's to project.
    if applied is not None and not all(
        isinstance(arm, type) and issubclass(arm, BaseModel) for arm in _without_none(applied)
    ):
        return
    for route in served:
        model = vars(route)["response_model"]
        if _without_none(model) != _without_none(applied or CommandAcceptedBody):
            yield f"command {command} applies {applied!r}; {route.path} serves {model!r}"


def _faults(routes: list[APIRoute]) -> Iterable[str]:
    named: dict[str, list[APIRoute]] = {}
    for route in routes:
        if route.name not in CommandKind and route.name not in ReadKind:
            yield f"{sorted(route.methods or ())} {route.path} is named {route.name!r}, no kind"
        named.setdefault(route.name, []).append(route)
    for command in CommandKind:
        yield from _command_faults(command, named.get(command, []))
    for read in ReadKind:
        served = named.get(read, [])
        if len(served) != 1:
            yield f"read {read} has {len(served)} routes"
            continue
        returns = _returns(imported("promptpotter.application", read.answer))
        # FastAPI sets these on the instance outside `APIRoute`'s declared attributes.
        declared = vars(served[0])
        # A stream or a file answers bytes; its route's model, if any, documents one frame.
        answered_as = declared["response_class"]
        streamed = isinstance(answered_as, type) and not issubclass(answered_as, JSONResponse)
        model = declared["response_model"]
        if not streamed and _without_none(returns) != _without_none(model):
            yield f"read {read} answers {returns!r}; its route serves {model!r}"


def bind_surface(routes: list[APIRoute]) -> None:
    """Raises at import naming every route, kind or answer out of step."""
    if faults := list(_faults(routes)):
        raise AssertionError("the surface is out of the catalogue:\n  " + "\n  ".join(faults))
