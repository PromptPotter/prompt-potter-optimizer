from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import suppress

import httpx

from promptpotter.domain.judges import JudgeStage, JudgeVerdict
from promptpotter.domain.scoring import NO_RESULT, MeasuredCell
from promptpotter.infrastructure.llm.heartbeat import heartbeat, silence_detail
from promptpotter.infrastructure.llm.request import ChatRequest
from promptpotter.infrastructure.llm.response import LLMResponse
from promptpotter.infrastructure.llm.send_pacing import SEND_ATTEMPTS, SendBudget
from promptpotter.infrastructure.llm.spend_book import CallLabel
from promptpotter.infrastructure.llm.telemetry import (
    active_cycle_ledger,
    current_round,
)
from promptpotter.infrastructure.llm.wallet import keyed_client
from promptpotter.infrastructure.run_scope import run_scope, scoped
from promptpotter.infrastructure.store.llm_reuse_cache import hash_call
from promptpotter.shared.errors import (
    CellSendRefusedError,
    SendRefusedError,
    UngeneratedRequestError,
)

logger = logging.getLogger(__name__)


async def ask(stage: JudgeStage, prompt: str, *, judge: str) -> tuple[str, str]:
    """``(reply, error)``, exactly one non-empty: a provider's failure is the error half, any other raises.

    The REPLY is cached, never the verdict — keyed by ``hash_call`` over the rendered prompt and
    model / provider / temperature / max_tokens, so a changed ``_parse`` or ``to_score`` re-derives.
    The cache replays, meters and keeps it (`LLMReuseCache.answer`): the run scope's, its
    store's `judge_reuse`, or none outside a scoring scope.
    """
    started = time.monotonic()
    cache = run_scope().judge_cache

    async def send() -> LLMResponse:
        # The catch covers the provider's reply alone: the cache's own replay, meter and keep raise.
        try:
            return await _sample(stage, prompt, judge=judge, started=started)
        except SendRefusedError as exc:
            # `spent` is empty: `measure_sample` bills the backend spend before any judge runs.
            raise CellSendRefusedError(str(exc), category=exc.category, spent={}) from exc
        except (httpx.HTTPError, UngeneratedRequestError, ValueError) as exc:
            raise _ProviderFailedError(exc) from exc

    try:
        if cache is None:
            response = await send()
        else:
            response = (
                await cache.answer(
                    hash_call(_request(stage, prompt), provider=stage.provider),
                    send,
                    node=f"{judge}:{stage.role}",
                    kind="judge",
                    provider=stage.provider,
                    model=stage.model,
                )
            ).response
    except _ProviderFailedError as failed:
        exc = failed.cause
        logger.warning("judge %s stage %s failed: %s", judge, stage.role, exc)
        return "", f"{type(exc).__name__}: {exc}"

    return response.content or "", ""


class _ProviderFailedError(Exception):
    """The provider's reply failed: the one failure `ask` turns into an absent grade."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(str(cause))
        self.cause = cause


def absent(judge: str, reason: str) -> JudgeVerdict:
    return JudgeVerdict(name=judge, score=None, error=reason)


def judge_answer(result: MeasuredCell) -> str | None:
    predicted = result.predicted.strip()
    return None if not predicted or predicted == NO_RESULT else predicted


def judge_question(result: MeasuredCell) -> str:
    return result.pipeline.question or result.query


async def graded(
    stage: JudgeStage,
    prompt: str,
    *,
    judge: str,
    parse: Callable[[str], str | None],
    to_score: Mapping[str, float],
) -> JudgeVerdict:
    reply, error = await ask(stage, prompt, judge=judge)
    if error:
        return absent(judge, error)
    label = parse(reply)
    if label is None:
        return absent(judge, f"grader returned no verdict in {sorted(to_score)}: {reply[:120]!r}")
    return JudgeVerdict(
        name=judge,
        score=to_score[label],
        label=label,
        explanation=f"graded {label} by {stage.model}",
    )


def _request(stage: JudgeStage, prompt: str) -> ChatRequest:
    return ChatRequest(
        messages=[{"role": "user", "content": prompt}],
        model=stage.model,
        temperature=stage.temperature,
        max_tokens=stage.max_tokens,
    )


async def _sample(stage: JudgeStage, prompt: str, *, judge: str, started: float) -> LLMResponse:
    client = keyed_client(stage.provider)
    label = f"{judge}:{stage.role}"
    # Unconditional: `heartbeat` takes `ledger=None`, so a missing sink cannot disarm liveness.
    beat = asyncio.create_task(
        heartbeat(
            active_cycle_ledger(),
            call_id=uuid.uuid4().hex,
            node=label,
            round_num=current_round(),
            start_monotonic=started,
            detail_fn=lambda: silence_detail(client, stage.model, role="grader"),
        )
    )
    try:
        with scoped(send_budget=SendBudget(None, attempts=SEND_ATTEMPTS)):
            return await client.chat(_request(stage, prompt), label=CallLabel(label, "judge"))
    finally:
        # An in-flight task survives the function exit and appends progress against a closed call.
        beat.cancel()
        with suppress(asyncio.CancelledError):
            await beat
