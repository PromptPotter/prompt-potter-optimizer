"""A table row names its code as ``module:attr`` so the table imports none of it; this imports it."""

from __future__ import annotations

import importlib
import types
from functools import reduce


def imported(package: str, row: str) -> object:
    """``attr`` may be dotted (``Class.method``)."""
    module, _, attr = row.partition(":")
    return reduce(getattr, attr.split("."), importlib.import_module(f"{package}.{module}"))


def require_own_docstring(model: type, row: str) -> None:
    """A row's model is described by its own docstring alone, read off the class itself: an
    inherited one describes another row."""
    if not (vars(model).get("__doc__") or "").strip():
        raise TypeError(f"{model.__name__} carries {row} and declares no docstring")


def returned(answer: object) -> object:
    """What a row's callable answers: its return annotation alone, since a parameter's type may be
    imported for type-checking only."""
    if not isinstance(answer, types.FunctionType):
        raise TypeError(f"a row's callable is a function, not {answer!r}")
    returns = answer.__annotations__.get("return")
    return eval(returns, answer.__globals__) if isinstance(returns, str) else returns
