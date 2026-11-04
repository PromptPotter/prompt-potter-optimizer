"""The surface bound to the catalogue: every route a session reaches is named by a `CommandKind` or a
`ReadKind`, and a route answers what its row's application callable returns: a read's params'
`answer`, a command's applied `Applier[R]`. A read route takes its own row's params as its input
and is described by their docstring."""

from __future__ import annotations

import inspect
from collections.abc import Iterable

from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from promptpotter.application.commands.dispatcher import command_answer
from promptpotter.application.read_params import PARAMS_FOR_READ, ReadParams
from promptpotter.domain.command_kinds import CommandAcceptedBody, CommandKind, ReadKind
from promptpotter.shared.named_rows import returned


def _command_faults(command: CommandKind, served: list[APIRoute]) -> Iterable[str]:
    if not served:
        yield f"command {command} has no route"
    applied = command_answer(command)
    # A task kind answering no model answers its launch, whose handle its route projects.
    if applied is None and command.answering == "task":
        return
    for route in served:
        model = vars(route)["response_model"]
        if model != (applied or CommandAcceptedBody):
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
        params = PARAMS_FOR_READ[read]
        # FastAPI sets these on the instance outside `APIRoute`'s declared attributes.
        declared = vars(served[0])
        # `deps.py::read_input` declares the model its dependency builds as its return.
        inputs = [
            built
            for dependency in declared["dependant"].dependencies
            if isinstance(built := inspect.signature(dependency.call).return_annotation, type)
            and issubclass(built, ReadParams)
        ]
        if inputs != [params]:
            yield f"read {read} answers through {params.__name__}; its route binds {inputs!r}"
        if served[0].endpoint.__doc__:
            yield f"read {read}'s route has a docstring; its description is {params.__name__}'s"
        returns = returned(params.answer)
        # A stream or a file answers bytes; its route's model, if any, documents one frame.
        answered_as = declared["response_class"]
        streamed = isinstance(answered_as, type) and not issubclass(answered_as, JSONResponse)
        model = declared["response_model"]
        if not streamed and returns != model:
            yield f"read {read} answers {returns!r}; its route serves {model!r}"


def bind_surface(routes: list[APIRoute]) -> None:
    """Raises at import naming every route, kind or answer out of step, then describes each read
    route by its row's model's docstring. Called before the routes are mounted, so the mounted copy
    carries the description."""
    if faults := list(_faults(routes)):
        raise AssertionError("the surface is out of the catalogue:\n  " + "\n  ".join(faults))
    for route in routes:
        if route.name in ReadKind:
            params = PARAMS_FOR_READ[ReadKind(route.name)]
            # Outside `APIRoute`'s declared attributes, as `_faults` reads its others.
            vars(route)["description"] = inspect.cleandoc(params.__doc__ or "")
