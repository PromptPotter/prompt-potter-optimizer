"""The one place a judge reaches a model.

Every judge asks through :func:`ask`, so caching, rate-limit retry, liveness and metering are
decided ONCE rather than per judge. That is the same argument ``dispatch/llm_call/call.py`` makes
for the optimizer's own chokepoint — and this is deliberately a SECOND one rather than a reuse of
it: that module is the optimizer's, it meters ``kind="optimizer"``, and routing grading spend
through it would put judge cost in the loop's bucket, which is precisely the boundary a judge must
not cross.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

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

__all__ = ["ask", "bind_cache", "graded"]


_CACHE: ContextVar[LLMReuseCache | None] = ContextVar("judge_reuse_cache", default=None)


@contextmanager
def bind_cache(cache: LLMReuseCache | None) -> Iterator[None]:
    """Scope the reuse cache :func:`ask` reads, for the duration of one grading.

    **A ContextVar rather than an argument on ``GradeFn``.** The cache is infrastructure; threading
    it through ``grade`` would put a store handle in the judge protocol, so every judge author —
    ours and a third party's — would carry something they have nothing to do with. It is set per
    grading rather than per process, and ContextVars are per-task, so concurrent ``measure_sample``
    tasks cannot read each other's binding. ``None`` disables the cache and re-samples every time.
    """
    token = _CACHE.set(cache)
    try:
        yield
    finally:
        _CACHE.reset(token)


async def ask(stage: JudgeStage, prompt: str, *, judge: str) -> tuple[str, str]:
    """Run one judge stage. Returns ``(reply, error)`` — exactly one is non-empty.

    **Never raises.** A judge that blew up must produce an absent verdict, not an exception that
    kills the measurement of a cell the backend already paid for: the answer was measured, only
    the grading failed, and the two are different facts. The caller turns the error into a
    ``JudgeVerdict`` with ``score=None``, which omits the term and makes the formula halt loud.

    **What is cached is the REPLY, not the verdict**, and that is what makes one cache enough. The
    rendered prompt already carries the rubric, the question, the gold and the prediction, so any
    edit to those moves the key on its own — while a judge whose ``_parse`` or ``to_score`` changed
    re-derives correctly from the stored reply, because it is still what that model said. A second
    stage's prompt is a deterministic function of the first's reply (``JudgeStage.temperature``
    defaults to ``0.0`` for exactly this reason), so a chain hits end to end under one key space.
    The economically large hit is two candidates whose mutation did not change the answer.

    Meters through ``emit_token_usage(kind="judge", …)`` — the third spend arm — carrying BOTH
    ``provider`` and ``model``, since a rate belongs to the pair and a model alone cannot be
    priced (``shared/pricing.py::lookup_rate``). Filed under the stage's ``role`` so a multi-stage
    judge's rows stay tellable apart.
    """
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

    usage = response.usage or {}
    # THE metering point: both branches converge here, so a grading is metered by ARRIVING rather
    # than by each branch remembering to. A cache hit is metered too, flagged — it spends nothing,
    # but the cell still had to be graded, so incurred grading cost stays invariant to our cache
    # history instead of making a re-read of an old comparison read as free.
    emit_token_usage(
        node=f"{judge}:{stage.role}",
        kind="judge",
        model=response.model or stage.model,
        provider=stage.provider,
        served_by=response.served_by,
        input_tokens=int(usage.get("prompt_tokens", 0)),
        output_tokens=int(usage.get("completion_tokens", 0)),
        reasoning_tokens=int(usage.get("reasoning_tokens", 0)),
        cost_usd=response.cost_usd,
        duration_s=time.monotonic() - started,
        cached=cached is not None,
    )

    # Meter FIRST, then store: the provider has already billed this call, so nothing that can fail
    # belongs between the response and its record — `cache.save` writes a file, and a disk error
    # above the emit loses the row silently.
    # Never cache an empty reply. Emptiness is a TRANSIENT provider failure and storing it makes it
    # PERMANENT: the key is the prompt hash, so every later grading of that comparison replays the
    # emptiness and the cell is ungradeable forever. The cache is tenant-global, so it would outlive
    # the run that hit it.
    if cached is None and cache is not None and key is not None and response.content.strip():
        cache.save(key, response.model_dump())

    return response.content or "", ""


async def graded(
    stage: JudgeStage,
    prompt: str,
    *,
    judge: str,
    parse: Callable[[str], str | None],
    to_score: Mapping[str, float],
) -> JudgeVerdict:
    """One asked-and-labelled grading: :func:`ask`, then the verdict shaping every judge repeats.

    Here rather than copied per judge for the same reason :func:`ask` is here — the two absence
    arms are not formatting, they are the rule that **a grader which FAILED must never be bankable
    as a graded answer**. Both return ``score=None``, which omits the term and makes the formula
    halt loud; a judge writing its own copy of this is one edit away from defaulting an unreadable
    reply to a category instead, which is precisely the upstream behaviour ``simpleqa.py``
    documents diverging from.

    ``parse`` maps a raw reply to one of ``to_score``'s labels, or ``None`` when it carries none.
    A judge whose taxonomy needs no model call at all — an absent input, say — should return its
    own ``JudgeVerdict`` and never reach here, so nothing is billed for a grading that cannot run.
    """
    reply, error = await ask(stage, prompt, judge=judge)
    if error:
        return JudgeVerdict(name=judge, score=None, error=error)
    label = parse(reply)
    if label is None:
        return JudgeVerdict(
            name=judge,
            score=None,
            error=f"grader returned no verdict in {sorted(to_score)}: {reply[:120]!r}",
        )
    return JudgeVerdict(
        name=judge,
        score=to_score[label],
        label=label,
        explanation=f"graded {label} by {stage.model}",
    )


def _replay(cache: LLMReuseCache, key: str, *, judge: str, role: str) -> LLMResponse | None:
    """The stored reply for *key*, or ``None`` for anything that is not one.

    A miss, an unreadable file and an entry an older build wrote are ONE answer here, because the
    caller acts identically on all three: sample it again. This is also what keeps :func:`ask`'s
    never-raises promise honest — the read and the validate both raise, and they sit on the path
    that exists to make grading cheaper, so letting either kill a cell would trade the whole
    measurement for the cache it was meant to save.
    """
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
    that never does.

    The retry is not optional politeness. A judge failure omits the term, which makes the formula
    halt on that cell, which discards a backend answer already paid for — so one unretried 429
    throws away the expensive half of the measurement to save the cheap half.
    """
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
