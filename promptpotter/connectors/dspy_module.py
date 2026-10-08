"""DSPy-as-connector — the loop optimizing a program someone else wrote. A THIN adapter: it
declares ``execution="in_process"`` and calls the student once, because a program is not a wire."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.connectors.protocol import Connector, InProcessWorkload, NoopSession
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.spend import StepTokenUsage
from promptpotter.shared.errors import CellUnscoreableError

if TYPE_CHECKING:
    from collections.abc import Callable

    from promptpotter.domain.sample import Sample

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

    examples: list[Any]
    """The trainset's ``dspy.Example`` rows, in the order its samples were numbered — a sample's
    ``id`` is its example's position."""


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


async def _in_process_run(
    workload: InProcessWorkload, sample: Sample, payload: dict[str, Any]
) -> dict[str, Any]:
    """Score one example under one candidate, projected onto the ``{"data": {…}}`` shape
    ``measure_sample`` parses from an HTTP body — so the scorer reads a DSPy result identically
    to a remote one."""
    program = workload.program
    if not isinstance(program, DspyProgram) or sample.id >= len(program.examples):
        raise CellUnscoreableError(
            f"dspy connector: sample {sample.id} is no row of the trainset this run opened with "
            "— PromptPotterOpt.acompile hands the student and its rows to open_session(program=...).",
            spent={},
            step_timings={},
        )
    example = program.examples[sample.id]

    import dspy

    student = _configured(program.student, payload.get("prompt"), payload.get("params") or {})
    # `track_usage` is what makes the STUDENT's spend visible at all: its calls go through
    # litellm, not our client, so `emit_token_usage` never fires for them and without this the
    # campaign's spend ceiling would bound the optimizer half of a compile and nothing else.
    with dspy.context(track_usage=True):
        prediction = await _acall(student, example)

    return {
        "data": {
            RESULT_KEY: [str(prediction)],
            SCORE_KEY: float(program.metric(example, prediction)),
            "terminal_node": PROGRAM_NODE,
            "step_tokens": _step_tokens(prediction),
        }
    }


def dataset_pipeline(
    *,
    model: str,
    temperature: float,
    extra: dict[str, Any],
    tune: tuple[str, ...],
    allowed: dict[str, list[str]],
) -> dict[str, Any]:
    """The ``pipeline.yaml`` a dspy dataset declares: the caller's program as the ONE node, and the
    two observations :func:`_in_process_run` emits mapped where the scorer reads them."""
    node = {
        "type": "llm",
        "runtime": "in_process",
        "node_role": "ranker",
        "description": "The caller's dspy.Module, scored by the caller's metric.",
        "prompt_info": {"template_variables": []},
        "config": {"model": model, "temperature": temperature, **extra},
        "optimizer": {
            "param_keys": list(tune),
            "param_allowed_values": dict(allowed),
            "observation_name": PROGRAM_NODE,
            # `is_llm` on the prediction mapping makes a per-node `model` REQUIRED, so no
            # measurement is attributed to whichever LM happens to be configured.
            "observation_mappings": [
                {"pipeline_key": RESULT_KEY, "is_llm": True},
                {"pipeline_key": SCORE_KEY},
            ],
        },
    }
    return {
        "name": "DSPy",
        "backend_name": "DSPy",
        "backend_type": "dspy",
        "available_models": sorted({model, *allowed.get("model", [])}),
        "nodes": {PROGRAM_NODE: node},
        "pipelines": {"default": [PROGRAM_NODE]},
    }


def _step_tokens(prediction: Any) -> dict[str, StepTokenUsage]:
    """The student's usage on the SAME channel a remote backend's rides — one ``step_tokens``
    entry, metered where the cell is admitted and, on a replay, by ``emit_replayed_step_tokens``. No
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
    session_factory=NoopSession,
    in_process_run=_in_process_run,
    # One call into the caller's module — no latency to hide behind, so overlapping two
    # buys nothing and only doubles peak memory in the host's own process.
    max_cells_in_flight=1,
    # Both keys `_in_process_run` always emits and `dataset_pipeline` declares: drop a mapping
    # there and init raises, instead of the formula grading a score that was silently dropped.
    required_observation_keys=(RESULT_KEY, SCORE_KEY),
    default_pipeline=(PROGRAM_NODE,),
)


__all__ = [
    "CONNECTOR",
    "PROGRAM_NODE",
    "RESULT_KEY",
    "SCORE_KEY",
    "DspyProgram",
    "dataset_pipeline",
]
