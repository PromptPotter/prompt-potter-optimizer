from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.connectors.protocol import Connector, InProcessWorkload
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import LLMSpendBound
from promptpotter.domain.spend import StepUsage, TokenAccount
from promptpotter.infrastructure.llm.base import hold_ceiling, send_bound
from promptpotter.infrastructure.llm.pricing import inline_route
from promptpotter.infrastructure.llm.send_failure import failed_send
from promptpotter.infrastructure.llm.send_pacing import drawn_budget, held_wait
from promptpotter.infrastructure.llm.spend_book import (
    Admission,
    Billed,
    CallLabel,
    Reported,
    admitted,
    answered,
)
from promptpotter.shared.errors import (
    CellHaltedError,
    CellUnscoreableError,
    ErrorCategory,
    SendRefusedError,
    cell_failure,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Mapping

    from promptpotter.domain.sample import Sample

logger = logging.getLogger(__name__)


PROGRAM_NODE = "program"

SCORE_KEY = "dspy_score"

# Decides nothing: the metric already graded the sample, and this shows a human the prediction.
RESULT_KEY = "final_ranking"


@dataclass(frozen=True)
class DspyProgram:
    student: Any
    """Never mutated: each candidate scores a ``deepcopy``."""

    metric: Callable[[Any, Any], Any]
    """``(example, prediction) -> float``, and it IS the scorer: its number rides ``SCORE_KEY``."""

    examples: list[Any]
    """In the order its samples were numbered: a sample's ``id`` is its example's position."""


def dspy_wire_adapter(query: str, pipeline_params: dict[str, Any] | None) -> dict[str, Any]:
    payload: dict[str, Any] = {"query": query}
    for node, cfg in node_config_items(pipeline_params):
        if node != PROGRAM_NODE:
            continue
        if prompt := cfg.get("prompt"):
            payload["prompt"] = prompt
        if params := {k: v for k, v in cfg.items() if k != "prompt"}:
            payload["params"] = params
    return payload


def _configured(
    student: Any, prompt: str | None, lm_kwargs: dict[str, Any], sends: _CellSends
) -> Any:
    import dspy

    # Never `save`/`load_state`: it zips fields back positionally, `strict=False`, silently.
    copy = student.deepcopy()
    for predictor in copy.predictors():
        if prompt:
            predictor.signature = predictor.signature.with_instructions(prompt)
        base = predictor.lm or dspy.settings.lm
        if base is None and lm_kwargs:
            base = dspy.LM(**lm_kwargs)
        if base is not None:
            predictor.lm = _metered(base, sends, lm_kwargs)
    return copy


_LM_KEYS = frozenset({"model", "temperature", "max_tokens"})

_NOT_THE_PROMPT = frozenset(
    {
        ErrorCategory.PROVIDER_THROTTLED,
        ErrorCategory.PROVIDER_CREDIT,
        ErrorCategory.CONNECTION,
    }
)

_SEND = CallLabel(PROGRAM_NODE, "backend")


class _CellSends:
    """A forward-only student calls from a worker thread; the spend book is the event loop's."""

    def __init__(
        self, sample_id: int, max_calls: int | None, priced_as: tuple[str | None, str | None]
    ) -> None:
        self.what = f"dspy example {sample_id}"
        self.calls_left = max_calls
        self.model, self.provider = priced_as
        self.loop = asyncio.get_running_loop()
        self.budget = drawn_budget()
        self.used = TokenAccount()
        # Raised again on every later call: the program may catch a failure or answer around it.
        self.ended: Exception | None = None

    def __deepcopy__(self, _memo: object) -> _CellSends:
        # DSPy deep-copies an LM to vary a call; the copy's sends are still this cell's.
        return self

    def end(self, failure: Exception) -> Exception:
        self.ended = self.ended or failure
        return self.ended

    def step_tokens(self) -> dict[str, dict[str, object]]:
        if not self.used.total:
            return {}
        return {
            PROGRAM_NODE: StepUsage(
                input=self.used.input,
                output=self.used.output,
                model=self.model,
                provider=self.provider,
            ).wire()
        }

    async def _admit(
        self, lm: _Metered, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> tuple[contextlib.ExitStack, Admission]:
        model, provider = self.model, self.provider
        if self.ended is not None:
            raise self.ended
        if self.calls_left is not None:
            if self.calls_left <= 0:
                raise self.end(
                    CellHaltedError(
                        f"{self.what} was stopped: its program made more LM calls than the "
                        f"`max_calls` nodes.{PROGRAM_NODE}.config declares, which is what its "
                        "spend bound was sized on.",
                        spent=self.step_tokens(),
                    )
                )
            self.calls_left -= 1
        sent = kwargs.get("messages") or (args[0] if args else kwargs.get("prompt"))
        max_tokens = kwargs.get("max_tokens", lm.kwargs.get("max_tokens"))
        hold = contextlib.ExitStack()
        try:
            bound = send_bound(
                await hold_ceiling(model, provider) if model and provider else None,
                sent=sent,
                max_tokens=max_tokens,
            )
            admission = hold.enter_context(admitted(_SEND, bound, model=model, provider=provider))
        except SendRefusedError as refused:
            raise self.end(refused) from None
        return hold, admission

    async def _settle(
        self,
        hold: contextlib.ExitStack,
        admission: Admission,
        totals: dict[str, dict[str, Any]],
        failure: Exception | None,
    ) -> bool:
        """``True`` where the send goes again."""
        bill = None
        if totals:
            usage = TokenAccount(
                input=sum(int(u.get("prompt_tokens") or 0) for u in totals.values()),
                output=sum(int(u.get("completion_tokens") or 0) for u in totals.values()),
            )
            self.used += usage
            bill = Billed(usage, None)
        with hold:
            if failure is None:
                admission.close(answered(bill))
                return False
            outcome = failed_send(failure)
            if bill is not None:
                outcome = outcome._replace(reported=Reported((bill,)))
            admission.close(outcome)
        wait = self.budget.resend_wait() if outcome.resendable else None
        if wait is not None:
            logger.warning(
                "%s: %s (attempt %d/%d); waiting %.1fs",
                self.what,
                type(failure).__name__,
                self.budget.resent,
                self.budget.attempts,
                wait,
            )
            await held_wait(wait, self.what)
            return True
        if outcome.failure in _NOT_THE_PROMPT:
            raise self.end(
                cell_failure(
                    f"{self.what}: {outcome.detail[:300]}",
                    outcome.failure,
                    spent=self.step_tokens(),
                )
            ) from failure
        return False

    def _on_loop(self, step: Coroutine[Any, Any, Any]) -> Any:
        return asyncio.run_coroutine_threadsafe(step, self.loop).result()

    async def asend(
        self,
        lm: _Metered,
        call: Callable[[], Awaitable[Any]],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        from dspy.utils.usage_tracker import track_usage

        while True:
            hold, admission = await self._admit(lm, args, kwargs)
            try:
                with track_usage() as used:
                    reply = await call()
            except Exception as failure:
                if not await self._settle(hold, admission, used.get_total_tokens(), failure):
                    raise
            except BaseException:
                # Cancelled with the send out: nothing reported what it cost.
                hold.close()
                raise
            else:
                await self._settle(hold, admission, used.get_total_tokens(), None)
                return reply

    def send(
        self, lm: _Metered, call: Callable[[], Any], args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> Any:
        from dspy.utils.usage_tracker import track_usage

        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop()
            raise self.end(
                TypeError(
                    f"{self.what}: the student called its LM synchronously on the event loop, "
                    "which blocks every other task of the run — await `lm.acall(...)` in "
                    "`aforward`."
                )
            )
        while True:
            hold, admission = self._on_loop(self._admit(lm, args, kwargs))
            try:
                with track_usage() as used:
                    reply = call()
            except Exception as failure:
                closing = self._settle(hold, admission, used.get_total_tokens(), failure)
                if not self._on_loop(closing):
                    raise
            except BaseException:
                self.loop.call_soon_threadsafe(hold.close)
                raise
            else:
                self._on_loop(self._settle(hold, admission, used.get_total_tokens(), None))
                return reply


class _Metered:
    cell_sends: _CellSends
    model: str
    kwargs: dict[str, Any]

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        call = super().__call__  # type: ignore[misc]  # the LM class this is mixed ahead of
        return self.cell_sends.send(self, lambda: call(*args, **kwargs), args, kwargs)

    async def acall(self, *args: Any, **kwargs: Any) -> Any:
        acall = super().acall  # type: ignore[misc]
        return await self.cell_sends.asend(self, lambda: acall(*args, **kwargs), args, kwargs)


_METERED: dict[type[Any], type[Any]] = {}


def _metered(lm: Any, sends: _CellSends, changed: dict[str, Any]) -> Any:
    # `copy`, not `dspy.LM(...)`: reconstructing swaps the caller's LM subclass for a generic one.
    own = lm.copy(**changed)
    lm_class = type(own)
    if lm_class not in _METERED:
        _METERED[lm_class] = type(lm_class.__name__, (_Metered, lm_class), {})
    own.__class__ = _METERED[lm_class]
    own.cell_sends = sends
    # Off: a resend is admitted anew from the cell's budget, and a cached reply reports no usage.
    if hasattr(own, "num_retries"):
        own.num_retries = 0
    if hasattr(own, "cache"):
        own.cache = False
    return own


def _sent_spend_bound(node: str, cfg: Mapping[str, Any]) -> LLMSpendBound | None:
    if node != PROGRAM_NODE:
        return None
    calls, context, reply = cfg.get("max_calls"), cfg.get("max_input_tokens"), cfg.get("max_tokens")
    if calls is None or context is None or reply is None:
        return None
    return LLMSpendBound(
        kind="llm",
        attempts=int(calls),
        input_tokens=int(context),
        max_tokens=int(reply),
    )


async def _acall(student: Any, example: Any) -> Any:
    import dspy

    inputs = example.inputs().toDict()
    if hasattr(type(student), "aforward"):
        return await student.acall(**inputs)
    # Never `asyncio.to_thread`: `dspy.settings` is thread-local, so it would run a default LM.
    return await dspy.asyncify(student)(**inputs)


async def _in_process_run(
    workload: InProcessWorkload, sample: Sample, payload: dict[str, Any]
) -> dict[str, Any]:
    program = workload.program
    if not isinstance(program, DspyProgram) or sample.id >= len(program.examples):
        raise CellUnscoreableError(
            f"dspy connector: sample {sample.id} is no row of the trainset this run opened with "
            "— PromptPotterOpt.acompile hands the student and its rows to open_session(program=...).",
            spent={},
        )
    example = program.examples[sample.id]

    import dspy

    params: dict[str, Any] = payload.get("params") or {}
    calls = params.get("max_calls")
    lm_kwargs = {k: v for k, v in params.items() if k in _LM_KEYS}
    model = lm_kwargs.get("model")
    sends = _CellSends(
        sample.id,
        None if calls is None else int(calls),
        inline_route(model) if isinstance(model, str) else (None, None),
    )
    student = _configured(program.student, payload.get("prompt"), lm_kwargs, sends)
    # Metered too, so a call naming no LM runs the model the cell is priced as.
    default = dspy.settings.lm
    try:
        with dspy.context(lm=None if default is None else _metered(default, sends, lm_kwargs)):
            prediction = await _acall(student, example)
    except Exception as exc:
        if sends.ended is not None and sends.ended is not exc:
            raise sends.ended from exc
        raise
    finally:
        # The student's worker thread outlives a cancelled cell: nothing it calls from here is sent.
        ended = sends.ended
        sends.end(CellHaltedError(f"{sends.what} is over; no further call is sent.", spent={}))
    if ended is not None:
        raise ended

    return {
        "data": {
            RESULT_KEY: [str(prediction)],
            SCORE_KEY: float(program.metric(example, prediction)),
            "terminal_node": PROGRAM_NODE,
            "step_tokens": sends.step_tokens(),
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
            # `is_llm` makes a per-node `model` REQUIRED.
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


CONNECTOR = Connector(
    name="dspy",
    execution="in_process",
    wire_adapter=dspy_wire_adapter,
    in_process_run=_in_process_run,
    holds_own_sends=True,
    sent_spend_bound=_sent_spend_bound,
    # A DSPy model string is litellm's: the provider is its prefix.
    model_names_provider=True,
    # Overlapping two buys nothing and doubles peak memory in the host's own process.
    max_cells_in_flight=1,
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
