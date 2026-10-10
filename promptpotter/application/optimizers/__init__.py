from __future__ import annotations

import functools
import importlib
import pkgutil
import typing

from promptpotter.application.optimizers.nodes import LlmNode, NodeMember, OptimizerRuntime
from promptpotter.domain.pipeline_schema import MEMBER_KINDS, NodeKind
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.hashing import shapes_optimizer_prompt
from promptpotter.shared.plugin_registry import load_plugins, load_registry, lookup

if typing.TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

shapes_optimizer_prompt(__name__)

__all__ = [
    "ENTRY_POINT_GROUP",
    "RUNTIME_ENTRY_POINT_GROUP",
    "llm_nodes",
    "member",
    "other_optimizer_packages",
    "register_round_payloads",
    "registered",
    "runtime",
    "runtime_origins",
    "runtimes",
]

ENTRY_POINT_GROUP = "promptpotter.optimizer_nodes"
"""Published, keyed by manifest node name: renaming it un-registers every plugin at once."""

RUNTIME_ENTRY_POINT_GROUP = "promptpotter.optimizer_runtimes"
"""Published, keyed by manifest name: renaming it un-registers every plugin at once."""

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
    return typing.cast("NodeMember", obj)


def _validate_runtime(obj: object, origin: str) -> OptimizerRuntime:
    name = getattr(obj, "name", None)
    if not isinstance(obj, OptimizerRuntime) or not isinstance(name, str) or not name:
        raise RuntimeError(
            f"[{origin}] resolved to {obj!r}: an optimizer runtime is an `OptimizerRuntime` "
            "instance naming the manifest it implements in `name`."
        )
    return obj


_BUILTIN_PACKAGES = frozenset(pkg.name for pkg in pkgutil.iter_modules(__path__) if pkg.ispkg)

# A payload missing here reads back as an unparsable round.
_PAYLOAD_MODULES = {"capo": "state", "gepa": "state", "levi": "state", "potter": "records"}
assert _PAYLOAD_MODULES.keys() == _BUILTIN_PACKAGES, (
    "built-in optimizers and their payload modules disagree: "
    f"{sorted(_PAYLOAD_MODULES.keys() ^ _BUILTIN_PACKAGES)}"
)


def _builtin_modules(submodules: Mapping[str, str]) -> Iterator[typing.Any]:
    for package, submodule in sorted(submodules.items()):
        yield importlib.import_module(f"{__name__}.{package}.{submodule}")


def register_round_payloads() -> None:
    """No member is imported: the payloads are all a reader of banked rounds needs."""
    for _ in (*_builtin_modules(_PAYLOAD_MODULES), *load_plugins(RUNTIME_ENTRY_POINT_GROUP)):
        pass


def _members() -> Iterator[typing.Any]:
    return _builtin_modules(dict.fromkeys(_BUILTIN_PACKAGES, "members"))


def _builtins() -> Iterator[tuple[str, object]]:
    for module in _members():
        for obj in module.MEMBERS:
            yield module.__name__, obj


@functools.cache
def _load() -> tuple[Mapping[str, NodeMember], Mapping[str, str]]:
    return load_registry(ENTRY_POINT_GROUP, _builtins(), _validate)


@functools.cache
def _load_runtimes() -> tuple[Mapping[str, OptimizerRuntime], Mapping[str, str]]:
    # A preset shipping members alone declares no runtime; a manifest without one fails at lookup.
    runtimes = (
        (module.__name__, module.RUNTIME) for module in _members() if hasattr(module, "RUNTIME")
    )
    return load_registry(RUNTIME_ENTRY_POINT_GROUP, runtimes, _validate_runtime)


def registered() -> Mapping[str, NodeMember]:
    return _load()[0]


def member(name: str) -> NodeMember:
    return lookup(ENTRY_POINT_GROUP, _load(), name)


def llm_nodes() -> Mapping[str, LlmNode]:
    return {name: found for name, found in registered().items() if isinstance(found, LlmNode)}


def runtimes() -> Mapping[str, OptimizerRuntime]:
    return _load_runtimes()[0]


def runtime(name: str) -> OptimizerRuntime:
    return lookup(RUNTIME_ENTRY_POINT_GROUP, _load_runtimes(), name)


def runtime_origins() -> Mapping[str, str]:
    return _load_runtimes()[1]


def other_optimizer_packages(module: str) -> frozenset[str]:
    own = module.removeprefix(f"{__name__}.").split(".")[0]
    return frozenset(f"{__name__}.{package}" for package in _BUILTIN_PACKAGES - {own})
