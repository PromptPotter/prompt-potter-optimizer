"""DSPy-as-connector — the loop optimizing a program someone else wrote. A THIN adapter: it
declares ``execution="in_process"`` and calls the student once, because a program is not a wire."""

from __future__ import annotations

import logging
import time
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.connectors.protocol import Connector
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.spend import StepTokenUsage

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx

logger = logging.getLogger(__name__)


# The one node a dspy dataset declares. ONE, because a DSPy program is a single call from here —
# its predictors are its own composition, not a chain we route a query through node by node.
PROGRAM_NODE = "program"

# What the campaign formula scores. It reaches `pipeline_data` only because the node names it in
# `observation_mappings`; an undeclared key never arrives and the formula then grades a
# measurement it never got — the same contract the L4 connector's proxies ride.
SCORE_KEY = "dspy_score"

# The terminal ranking `measure_sample` reads. The metric already graded the sample, so this
# carries the prediction for a HUMAN reading the round file and decides nothing.
RESULT_KEY = "final_ranking"


@dataclass(frozen=True)
class DspyProgram:
    """The student and its metric, bound for the length of one compile."""

    student: Any
    """The caller's ``dspy.Module``. Never mutated — each candidate scores a ``deepcopy``."""

    metric: Callable[[Any, Any], Any]
    """The caller's ``(example, prediction) -> float``. It IS the scorer: its number rides
    :data:`SCORE_KEY` and the campaign formula reads that key, so PromptPotter never has to
    reproduce a grading rule the DSPy program already owns."""

    examples: dict[str, Any]
    """``dspy.Example`` per query text — the join a scored row uses to find its own row back.
    Keyed by query because that is the only field a :class:`Sample` and an example share."""


# A ContextVar rather than an argument: ``in_process_run`` is a module-level hook the loop calls
# with no call-site state, which is the same reason the `promptpotter` connector publishes its
# inner-spawn context. Set by `PromptPotterOpt.acompile`, reset by it on the way out.
_PROGRAM: ContextVar[DspyProgram | None] = ContextVar("dspy_program", default=None)


def publish_dspy_program(program: DspyProgram) -> Token[DspyProgram | None]:
    """Bind *program* for this compile; the caller resets the returned token when the run ends."""
    return _PROGRAM.set(program)


def reset_dspy_program(token: Token[DspyProgram | None]) -> None:
    _PROGRAM.reset(token)


def dspy_wire_adapter(query: str, pipeline_params: dict[str, Any] | None) -> dict[str, Any]:
    """Outbound payload for one example. The candidate's rendered prompt rides ``prompt`` (the node
    declares ``prompt_info``, so the searchpoint's render lands there); the rest are its tunables."""
    payload: dict[str, Any] = {"query": query}
    for node, cfg in node_config_items(pipeline_params):
        if node != PROGRAM_NODE:
            continue
        if prompt := cfg.get("prompt"):
            payload["prompt"] = prompt
        if params := {k: v for k, v in cfg.items() if k != "prompt"}:
            payload["params"] = params
    return payload


class DspySession:
    """In-process noop session — no remote service, so no handshake to make or recover."""

    __slots__ = ()

    async def set_terms(
        self, http: httpx.AsyncClient, base_url: str, terms: list[str]
    ) -> dict[str, Any]:
        return {"status": "noop", "terms_count": len(terms)}

    async def recover(self, http: httpx.AsyncClient, base_url: str) -> bool:
        return True


def _extract_experiment(
    experiment_data: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Always empty: a DSPy caller hands its rows to ``mint_and_score_origin`` directly, so there
    is no experiment doc for this connector to read one out of."""
    return [], []


def _configured(student: Any, prompt: str | None, params: dict[str, Any]) -> Any:
    """The candidate applied to a COPY of the student — prompt onto every predictor's signature,
    model settings onto its ``lm``. Jointly, which is the half ``with_instructions()`` alone cannot
    reach and the reason this optimizer is worth swapping in.

    Never ``save``/``load_state``: :meth:`Signature.dump_state` writes fields positionally with no
    names, and ``load_state`` zips them back with ``strict=False``, so a signature that gained a
    field reloads a scrambled prompt and raises nothing."""
    import dspy

    copy = student.deepcopy()
    lm_kwargs = {k: v for k, v in params.items() if k in _LM_KEYS}
    for predictor in copy.predictors():
        if prompt:
            predictor.signature = predictor.signature.with_instructions(prompt)
        if lm_kwargs:
            # `base.copy(**kwargs)` and NOT `dspy.LM(**settings_read_off_base)`: reconstructing
            # swaps the caller's LM CLASS for a generic one, so an Azure wrapper, a local-model
            # handle or a cached client silently becomes a plain client — the measurement then
            # belongs to a model nobody chose. `copy` keeps the subclass and its provider
            # session, and sets `model` as an attribute the same way it sets a request param.
            base = predictor.lm or dspy.settings.lm
            predictor.lm = base.copy(**lm_kwargs) if base is not None else dspy.LM(**lm_kwargs)
    return copy


# What a `Node.tune` entry may move on the predictor's LM. Anything else in `params` is the
# caller's own field and reaches the program untouched through the prompt.
_LM_KEYS = frozenset({"model", "temperature", "max_tokens"})


async def _acall(student: Any, example: Any) -> Any:
    """Await the student. A module declaring ``aforward`` goes straight through ``acall``; one
    declaring only ``forward`` goes through **``dspy.asyncify``**, never ``asyncio.to_thread`` —
    ``dspy.settings`` is thread-local, so a plain worker thread would run under a default
    configuration and attribute the measurement to a model the caller never chose."""
    import dspy

    inputs = example.inputs().toDict()
    if hasattr(type(student), "aforward"):
        return await student.acall(**inputs)
    return await dspy.asyncify(student)(**inputs)


async def _in_process_run(query: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Score one example under one candidate, projected onto the ``{"data": {…}}`` shape
    ``measure_sample`` parses from an HTTP body — so the scorer reads a DSPy result identically
    to a remote one."""
    program = _PROGRAM.get()
    if program is None:
        raise RuntimeError(
            "dspy connector: no program published — PromptPotterOpt.acompile publishes the "
            "student before it scores anything, so this ran outside a compile."
        )
    example = program.examples.get(query)
    if example is None:
        raise RuntimeError(
            f"dspy connector: no example for query {query[:80]!r}. The trainset that keyed this "
            "campaign is not the one being scored — reuse the name only with the same rows."
        )

    import dspy

    student = _configured(program.student, payload.get("prompt"), payload.get("params") or {})
    start = time.monotonic()
    # `track_usage` is what makes the STUDENT's spend visible at all: its calls go through
    # litellm, not our client, so `emit_token_usage` never fires for them and without this the
    # campaign's spend ceiling would bound the optimizer half of a compile and nothing else.
    with dspy.context(track_usage=True):
        prediction = await _acall(student, example)
    elapsed = time.monotonic() - start

    return {
        "data": {
            RESULT_KEY: [str(prediction)],
            SCORE_KEY: float(program.metric(example, prediction)),
            "terminal_node": PROGRAM_NODE,
            "total_time": elapsed,
            "step_timings": {PROGRAM_NODE: elapsed},
            "step_tokens": _step_tokens(prediction),
        }
    }


def _step_tokens(prediction: Any) -> dict[str, StepTokenUsage]:
    """The student's usage on the SAME channel a remote backend's rides — one ``step_tokens``
    entry, metered by ``emit_step_token_usage`` on both the fresh and the cache-replay path. No
    ``cost_usd``: pricing is our rate table's job, and an unpriced model is already a named
    signal rather than a silent zero.

    **DSPy SKIPS recording on its own cache hit** (`base_lm.py` guards `add_usage` on
    ``cache_hit``), so such a call is absent from the sum rather than zeroed. Our measurement
    cache sits above DSPy's and replays archived tokens correctly, so the only under-count is a
    sample OUR cache missed and DSPy's served —
    `dspy.configure_cache(enable_disk_cache=False, enable_memory_cache=False)` makes it exact."""
    usage = (prediction.get_lm_usage() or {}) if hasattr(prediction, "get_lm_usage") else {}
    if not usage:
        return {}
    return {
        PROGRAM_NODE: {
            "input": sum(int(u.get("prompt_tokens") or 0) for u in usage.values()),
            "output": sum(int(u.get("completion_tokens") or 0) for u in usage.values()),
            "estimated": False,
            "model": ", ".join(sorted(usage)),
        }
    }


CONNECTOR = Connector(
    name="dspy",
    execution="in_process",
    wire_adapter=dspy_wire_adapter,
    session_factory=DspySession,
    extract_experiment=_extract_experiment,
    in_process_run=_in_process_run,
    # One call into the caller's module — no latency to hide behind, so overlapping two
    # buys nothing and only doubles peak memory in the host's own process.
    max_cells_in_flight=1,
    # Both keys `_in_process_run` always emits. `PromptPotterOpt` writes the pipeline.yaml that
    # declares them, so this holds today by construction — which is the point of stating it: the
    # guarantee stops depending on that writer staying correct. Drop a mapping there and init
    # raises, instead of the formula grading a score that was silently dropped.
    required_observation_keys=(RESULT_KEY, SCORE_KEY),
    default_pipeline=(PROGRAM_NODE,),
)


__all__ = [
    "CONNECTOR",
    "PROGRAM_NODE",
    "RESULT_KEY",
    "SCORE_KEY",
    "DspyProgram",
    "DspySession",
    "publish_dspy_program",
    "reset_dspy_program",
]
