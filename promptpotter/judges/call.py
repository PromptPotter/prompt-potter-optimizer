from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

from promptpotter.domain.scoring import NO_RESULT, MeasuredCell
from promptpotter.infrastructure.llm.heartbeat import heartbeat, waiting_on
from promptpotter.infrastructure.llm.registry import get_llm_client
from promptpotter.infrastructure.llm.request import ChatRequest
from promptpotter.infrastructure.llm.response import LLMResponse
from promptpotter.infrastructure.llm.send_pacing import SEND_ATTEMPTS, SendBudget, under_budget
from promptpotter.infrastructure.llm.spend_book import CallLabel
from promptpotter.infrastructure.llm.telemetry import (
    _CURRENT_ROUND,
    _CYCLE_LEDGER,
    call_priced,
    emit_token_usage,
)
from promptpotter.infrastructure.store.llm_reuse_cache import LLMReuseCache, hash_call
from promptpotter.judges.protocol import JudgeStage, JudgeVerdict
from promptpotter.shared.errors import CellSendRefusedError, SendRefusedError

logger = logging.getLogger(__name__)

__all__ = ["absent", "ask", "bind_cache", "graded", "judge_answer", "judge_question"]


_CACHE: ContextVar[LLMReuseCache | None] = ContextVar("judge_reuse_cache", default=None)


@contextmanager
def bind_cache(cache: LLMReuseCache | None) -> Iterator[None]:
    token = _CACHE.set(cache)
    try:
        yield
    finally:
        _CACHE.reset(token)


async def ask(stage: JudgeStage, prompt: str, *, judge: str) -> tuple[str, str]:
    """``(reply, error)``, exactly one non-empty; raises nothing but ``CellSendRefusedError``."""
    started = time.monotonic()
    cache = _CACHE.get()
    key: str | None = None
    cached: LLMResponse | None = None
    if cache is not None:
        key = hash_call(
            messages=[{"role": "user", "content": prompt}],
            model=stage.model,
            provider=stage.provider,
            temperature=stage.temperature,
            json_schema=None,
            max_tokens=stage.max_tokens,
        )
        cached = _replay(cache, key, judge=judge, role=stage.role)

    if cached is not None:
        response = cached
    else:
        try:
            response = await _sample(stage, prompt, judge=judge, started=started)
        except SendRefusedError as exc:
            # `spent` is empty: `measure_sample` bills the backend spend before any judge runs.
            raise CellSendRefusedError(str(exc), category=exc.category, spent={}) from exc
        except Exception as exc:
            logger.warning("judge %s stage %s failed: %s", judge, stage.role, exc)
            return "", f"{type(exc).__name__}: {exc}"

    # Once per campaign: a grading its ledger already priced is not metered on a later replay.
    counted = key is not None and call_priced(key)
    if cached is not None and not counted:
        emit_token_usage(
            node=f"{judge}:{stage.role}",
            kind="judge",
            model=response.model or stage.model,
            provider=stage.provider,
            served_by=response.served_by,
            usage=response.usage,
            cost_usd=response.cost_usd,
            duration_s=time.monotonic() - started,
            cached=True,
        )

    if cached is None and cache is not None and key is not None and response.content.strip():
        cache.save(key, response.model_dump())

    return response.content or "", ""


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


def _replay(cache: LLMReuseCache, key: str, *, judge: str, role: str) -> LLMResponse | None:
    try:
        payload = cache.load(key)
        if payload is None:
            return None
        response = LLMResponse.model_validate(payload)
    except Exception as exc:
        logger.warning("judge_reuse entry for %s:%s unusable, re-sampling — %s", judge, role, exc)
        return None
    logger.debug("judge_reuse hit for %s:%s (%s)", judge, role, key)
    return response


async def _sample(stage: JudgeStage, prompt: str, *, judge: str, started: float) -> LLMResponse:
    client = get_llm_client(stage.provider)
    label = f"{judge}:{stage.role}"
    # Unconditional: `heartbeat` takes `ledger=None`, so a missing sink cannot disarm liveness.
    beat = asyncio.create_task(
        heartbeat(
            _CYCLE_LEDGER.get(),
            call_id=uuid.uuid4().hex,
            node=label,
            round_num=_CURRENT_ROUND.get(),
            start_monotonic=started,
            detail_fn=lambda: waiting_on(client, stage.model, role="grader"),
        )
    )
    try:
        with under_budget(SendBudget(None, attempts=SEND_ATTEMPTS)):
            return await client.chat(
                ChatRequest(
                    messages=[{"role": "user", "content": prompt}],
                    model=stage.model,
                    temperature=stage.temperature,
                    max_tokens=stage.max_tokens,
                ),
                label=CallLabel(label, "judge"),
            )
    finally:
        # An in-flight task survives the function exit and appends progress against a closed call.
        beat.cancel()
        try:
            await beat
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("heartbeat task for judge %s raised on teardown", label, exc_info=True)
