from __future__ import annotations

import importlib
import typing
from collections.abc import Mapping

from promptpotter.application.optimizers.nodes import (
    BUILTIN_PACKAGES,
    AccessKnob,
    CountKnob,
    CycleRead,
    LlmNode,
    NodeMember,
    OptimizerRuntime,
    PromptArity,
    RewriteKnobs,
)
from promptpotter.domain.optimizer_state import StatePart
from promptpotter.domain.pipeline_schema import MEMBER_KINDS, NodeKind
from promptpotter.domain.pool import Pool
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.hashing import shapes_optimizer_prompt
from promptpotter.shared.plugin_registry import Registry

if typing.TYPE_CHECKING:
    from collections.abc import Iterator

    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.domain.pipeline_schema import PipelineSchema

shapes_optimizer_prompt(__name__)


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
    if getattr(obj, "prompt_arity", None) not in typing.get_args(PromptArity):
        raise RuntimeError(
            f"{where}: `prompt_arity` must be one of {typing.get_args(PromptArity)}."
        )
    for declared in ("reads", "writes"):
        parts = getattr(obj, declared, None)
        if not isinstance(parts, frozenset) or not all(
            isinstance(s, type) and issubclass(s, StatePart) and s.__dict__.get("kind")
            for s in parts
        ):
            raise RuntimeError(
                f"{where}: `{declared}` must be a frozenset of registered StatePart kinds."
            )
    sees = getattr(obj, "sees", None)
    if not isinstance(sees, frozenset) or not all(
        isinstance(s, type) and issubclass(s, CycleRead) for s in sees
    ):
        raise RuntimeError(f"{where}: `sees` must be a frozenset of CycleRead kinds.")
    consults = getattr(obj, "consults", None)
    if not isinstance(consults, frozenset) or not all(isinstance(c, type) for c in consults):
        raise RuntimeError(f"{where}: `consults` must be a frozenset of member classes.")
    reached = {knobs, *(getattr(c, "knobs", None) for c in consults)}
    draws = getattr(obj, "draws", None)
    if not isinstance(draws, Mapping) or not all(
        isinstance(pool, type)
        and issubclass(pool, Pool)
        and pool.__dict__.get("kind")
        and isinstance(draw, AccessKnob | CountKnob)
        and draw.knobs in reached
        and draw.knob in draw.knobs.model_fields
        for pool, draw in draws.items()
    ):
        raise RuntimeError(
            f"{where}: `draws` must map registered Pool kinds to an AccessKnob or CountKnob "
            "naming a knob of its own or a consulted member's knobs."
        )
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


def _members() -> Iterator[typing.Any]:
    for package in sorted(BUILTIN_PACKAGES):
        yield importlib.import_module(f"{__name__}.{package}.members")


def _builtins() -> Iterator[tuple[str, object]]:
    for module in _members():
        for obj in module.MEMBERS:
            yield module.__name__, obj


def _runtimes() -> Iterator[tuple[str, object]]:
    # A preset shipping members alone declares no runtime; a manifest without one fails at lookup.
    for module in _members():
        if hasattr(module, "RUNTIME"):
            yield module.__name__, module.RUNTIME


NODE_MEMBERS = Registry(ENTRY_POINT_GROUP, _builtins, _validate)
RUNTIMES = Registry(RUNTIME_ENTRY_POINT_GROUP, _runtimes, _validate_runtime)


def llm_nodes() -> Mapping[str, LlmNode]:
    return {name: found for name, found in NODE_MEMBERS.table.items() if isinstance(found, LlmNode)}


def unfit_pipeline(selected: SelectedOptimizer, schema: PipelineSchema) -> str | None:
    """Why *selected* cannot write *schema*'s prompt nodes, asked before a mint spends; ``None`` where it can."""
    prompt_nodes = schema.prompt_nodes()
    roster = [node.name for node in prompt_nodes]
    for name in selected.member_nodes:
        arity = NODE_MEMBERS.get(name).prompt_arity
        if arity == "one" and len(prompt_nodes) != 1:
            return (
                f"optimizer {selected.name!r} node {name!r} rewrites the pipeline's one prompt "
                f"node, and this pipeline's chain has {len(roster)}: {roster}."
            )
        knobs = selected.knobs(name)
        if (
            arity != "none"
            and isinstance(knobs, RewriteKnobs)
            and (missing := [n.name for n in prompt_nodes if knobs.field not in n.prompt_fields])
        ):
            return (
                f"optimizer {selected.name!r} node {name!r} rewrites the prompt field "
                f"{knobs.field!r}, which {missing} does not declare — set the node's `field` "
                "knob in `optimization.nodes` to one it does."
            )
    return None
