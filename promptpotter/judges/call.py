"""The one place a judge reaches a model.

Deliberately a SECOND chokepoint rather than a reuse of ``dispatch/llm_call/call.py``: that one is
the optimizer's and meters ``kind="optimizer"``, so routing grading spend through it would put
judge cost in the loop's bucket — the one boundary a judge may not cross.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from promptpotter.config.settings import NO_RESULT
from promptpotter.infrastructure.llm.rate_limit import (
    MAX_429_ATTEMPTS,
    decide_429_wait,
    wait_with_countdown,
)
from promptpotter.infrastructure.llm.registry import get_llm_client
from promptpotter.infrastructure.llm.response import LLMResponse
from promptpotter.infrastructure.llm.telemetry import (
    _CURRENT_ROUND,
    _CYCLE_LEDGER,
    emit_token_usage,
)
from promptpotter.infrastructure.store.stores import LLMReuseCache, hash_call
from promptpotter.judges.protocol import JudgeStage, JudgeVerdict

logger = logging.getLogger(__name__)

__all__ = ["absent", "ask", "bind_cache", "graded", "judge_answer", "judge_question"]


_CACHE: ContextVar[LLMReuseCache | None] = ContextVar("judge_reuse_cache", default=None)


@contextmanager
def bind_cache(cache: LLMReuseCache | None) -> Iterator[None]:
    """Scope the reuse cache :func:`ask` reads, for the duration of one grading. ``None`` disables
    it and re-samples every time."""
    token = _CACHE.set(cache)
    try:
        yield
    finally:
        _CACHE.reset(token)


async def ask(stage: JudgeStage, prompt: str, *, judge: str) -> tuple[str, str]:
    """Run one judge stage. Returns ``(reply, error)`` — exactly one is non-empty.

    **Never raises**, which every caller relies on: a grading failure must stay a failed grading,
    not kill the measurement of a cell the backend already paid for."""
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
        except Exception as exc:
            logger.warning("judge %s stage %s failed: %s", judge, stage.role, exc)
            return "", f"{type(exc).__name__}: {exc}"

    # Both branches converge here, so a grading is metered by ARRIVING rather than by each branch
    # remembering to. A hit is metered too, flagged, so grading cost stays invariant to our cache
    # history rather than making a re-read of an old comparison read as free.
    #
    # `cached` below is the OTHER fact: we replayed, so no provider was reached. A grading is the
    # one call shape with a naturally cacheable prefix — the rubric is a module constant, so most
    # of the prompt is byte-identical on every cell of every campaign.
    emit_token_usage(
        node=f"{judge}:{stage.role}",
        kind="judge",
        model=response.model or stage.model,
        provider=stage.provider,
        served_by=response.served_by,
        usage=response.usage,
        cost_usd=response.cost_usd,
        duration_s=time.monotonic() - started,
        cached=cached is not None,
    )

    # Meter FIRST, then store — `cache.save` writes a file, and a disk error above the emit loses a
    # row the provider already billed. And never store an EMPTY reply: emptiness is transient, the
    # key is the prompt hash, and this tree is tenant-global, so caching one makes that comparison
    # ungradeable forever.
    if cached is None and cache is not None and key is not None and response.content.strip():
        cache.save(key, response.model_dump())

    return response.content or "", ""


def absent(judge: str, reason: str) -> JudgeVerdict:
    """The verdict for a grading that COULD NOT RUN — never a zero, which would say the candidate
    did the thing badly rather than that we did not measure."""
    return JudgeVerdict(name=judge, score=None, error=reason)


def judge_answer(result: Mapping[str, Any]) -> str | None:
    """The cell's ANSWER as text, ``None`` where it has none. Callers turn that into :func:`absent`
    BEFORE rendering a prompt, so nothing is billed for a grading that cannot run."""
    predicted = str(result.get("predicted") or "").strip()
    return None if not predicted or predicted == NO_RESULT else predicted


def judge_question(result: Mapping[str, Any]) -> str:
    """What a judge reads as "the question" — the bare one where the dataset declared one, else
    ``query``, which on a long-context bank is the question PLUS its whole haystack."""
    pd = result.get("pipeline_data")
    if isinstance(pd, dict) and (q := pd.get("question")):
        return str(q)
    return str(result.get("query", ""))


async def graded(
    stage: JudgeStage,
    prompt: str,
    *,
    judge: str,
    parse: Callable[[str], str | None],
    to_score: Mapping[str, float],
) -> JudgeVerdict:
    """One asked-and-labelled grading: :func:`ask`, then the verdict shaping every judge repeats.

    ``parse`` maps a raw reply to one of ``to_score``'s labels, or ``None`` when it carries none."""
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
    """The stored reply for *key*, or ``None`` for anything that is not one — a miss, an unreadable
    file and an entry an older build wrote are ONE answer, because the caller re-samples on all
    three. A cache exists to make grading cheaper; nothing in it may ever cost a measurement."""
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
    """One provider round-trip — heartbeated, and retried on a 429. RAISES; :func:`ask` is the half
    that never does."""
    # Local: `judges/` is a leaf package and this reaches back into `application/`.
    from promptpotter.application.optimization.dispatch.llm_call.heartbeat import heartbeat

    client = get_llm_client(stage.provider)
    label = f"{judge}:{stage.role}"
    # Created UNCONDITIONALLY — `heartbeat` takes `ledger=None` precisely so a missing telemetry
    # sink cannot disarm a liveness guard. Without it a slow grader is a silent await, and silence
    # is how this package says the producer died.
    beat = asyncio.create_task(
        heartbeat(
            _CYCLE_LEDGER.get(),
            call_id=uuid.uuid4().hex,
            node=label,
            round_num=_CURRENT_ROUND.get(),
            start_monotonic=started,
            detail_fn=lambda: f"grader {stage.model} has not answered",
        )
    )
    try:
        for attempt in range(MAX_429_ATTEMPTS):
            try:
                return await client.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model=stage.model,
                    temperature=stage.temperature,
                    max_tokens=stage.max_tokens,
                )
            except Exception as exc:
                if getattr(exc, "status_code", None) != 429:
                    raise
                resp = getattr(exc, "response", None)
                headers = getattr(resp, "headers", None) if resp is not None else None
                body = getattr(resp, "text", None) if resp is not None else None
                if body is None:
                    body = str(exc)
                decision = decide_429_wait(headers, body, attempt)
                if decision is None:
                    raise
                logger.warning(
                    "Rate limit on judge %s [%s] (attempt %d/%d); waiting %.1fs",
                    label,
                    decision.scope,
                    attempt + 1,
                    MAX_429_ATTEMPTS,
                    decision.seconds,
                )
                await wait_with_countdown(decision.seconds, f"{label} {decision.scope}")
        raise RuntimeError(f"judge {label}: still rate-limited after {MAX_429_ATTEMPTS} attempts")
    finally:
        # Cancel whether the call returned or raised — an in-flight task survives the function exit
        # and keeps appending progress against a closed call.
        beat.cancel()
        try:
            await beat
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("heartbeat task for judge %s raised on teardown", label, exc_info=True)
