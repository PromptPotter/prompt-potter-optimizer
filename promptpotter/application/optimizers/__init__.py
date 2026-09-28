from __future__ import annotations

import functools
import importlib
import pkgutil
import typing

from promptpotter.application.optimizers.nodes import MemberCoupling, NodeMember
from promptpotter.domain.pipeline_schema import MEMBER_KINDS, NodeKind
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.hashing import shapes_optimizer_prompt
from promptpotter.shared.plugin_registry import load_registry, lookup

if typing.TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

shapes_optimizer_prompt(__name__)

__all__ = ["ENTRY_POINT_GROUP", "member", "member_origins", "registered"]

ENTRY_POINT_GROUP = "promptpotter.optimizer_nodes"
"""Published: a third party ships an optimizer's node implementations under this group, keyed by
the node name its manifest uses. Renaming it un-registers every plugin at once."""

_MEMBER_KINDS = MEMBER_KINDS | {NodeKind.LLM}


def _validate(obj: object, origin: str) -> NodeMember:
    name = getattr(obj, "name", None)
    if isinstance(obj, type) or not isinstance(name, str) or not name:
        raise RuntimeError(
            f"[{origin}] resolved to {obj!r}: a member is an instance naming the manifest node it "
            "answers in `name`."
        )
    where = f"optimizer node member {name!r} [{origin}]"
    if getattr(obj, "kind", None) not in _MEMBER_KINDS:
        raise RuntimeError(f"{where}: kind must be one of {sorted(_MEMBER_KINDS)}.")
    knobs = getattr(obj, "knobs", None)
    if not (isinstance(knobs, type) and issubclass(knobs, StrictModel)):
        raise RuntimeError(f"{where}: `knobs` must be a StrictModel subclass.")
    if defaulted := sorted(n for n, f in knobs.model_fields.items() if not f.is_required()):
        raise RuntimeError(
            f"{where}: knobs {defaulted} carry a default. The manifest declares every value, so "
            "a code default would be a second source for one number."
        )
    for coupling in getattr(obj, "couplings", None) or ():
        if not isinstance(coupling, MemberCoupling):
            raise RuntimeError(f"{where}: a coupling is not a MemberCoupling.")
        if ghosts := sorted(set(coupling.knobs) - set(knobs.model_fields)):
            raise RuntimeError(f"{where}: coupling {coupling.name!r} names unknown knobs {ghosts}.")
    return typing.cast("NodeMember", obj)


def _builtins() -> Iterator[tuple[str, object]]:
    for pkg in pkgutil.iter_modules(__path__):
        if pkg.ispkg:
            module = importlib.import_module(f"{__name__}.{pkg.name}.members")
            for obj in module.MEMBERS:
                yield module.__name__, obj


@functools.cache
def _load() -> tuple[Mapping[str, NodeMember], Mapping[str, str]]:
    return load_registry(ENTRY_POINT_GROUP, _builtins(), _validate)


def registered() -> Mapping[str, NodeMember]:
    return _load()[0]


def member_origins() -> Mapping[str, str]:
    return _load()[1]


def member(name: str) -> NodeMember:
    return lookup(ENTRY_POINT_GROUP, _load(), name)
