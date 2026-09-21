"""The one place a judge reaches a model.

Deliberately a SECOND chokepoint rather than a reuse of ``dispatch/llm_call/call.py``: that one is
the optimizer's and meters ``kind="optimizer"``, so routing grading spend through it would put
judge cost in the loop's bucket — the one boundary a judge may not cross.

WHAT IS CACHED IS THE REPLY, NOT THE VERDICT. ``ask`` reads and writes ``Stores.judge_reuse``,
keyed by ``hash_call`` over the rendered prompt plus the stage's model / provider / temperature /
max_tokens. Storing the model's reply is what makes ONE cache enough:

* A rubric or model edit moves the key BY ITSELF, because the rendered prompt carries the rubric,
  the question, the gold and the prediction. So no judge-version component is needed here, and a
  judge whose ``_parse`` or ``to_score`` changed re-derives correctly from the stored reply — it
  is still what that model said. Identity is the separate question ``fingerprint`` answers.
* A composition caches WHOLE. A second stage's prompt is a deterministic function of the first's
  reply — which is what ``JudgeStage.temperature``'s ``0.0`` default buys — so a chain hits end to
  end under one key space with one invalidation.
* The economically large hit is two candidates whose mutation did not change the answer, which is
  the common case and is composition-independent.

Three rules ride with it, two inherited from the optimizer's call path and each a scar. A fresh
grading is metered by the client at its send, before anything here can fail. **Meter cache hits
too**, flagged, so grading cost stays invariant to our cache history. **Never store an empty
reply** — emptiness is transient, the key is the prompt hash, and the tree is tenant-global, so
caching one makes that comparison ungradeable forever with nothing on any surface pointing at the
cause. And this seam's own: **absent, unreadable and stale are ONE answer — sample it again.**
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

from promptpotter.application.optimization.dispatch.llm_call.heartbeat import (
    heartbeat,
    waiting_on,
)
from promptpotter.config.settings import NO_RESULT
from promptpotter.infrastructure.llm.registry import get_llm_client
from promptpotter.infrastructure.llm.response import LLMResponse
from promptpotter.infrastructure.llm.spend_book import CallLabel
from promptpotter.infrastructure.llm.telemetry import (
    _CURRENT_ROUND,
    _CYCLE_LEDGER,
    emit_token_usage,
)
from promptpotter.infrastructure.store.stores import LLMReuseCache, hash_call
from promptpotter.judges.protocol import JudgeStage, JudgeVerdict
from promptpotter.shared.errors import CellSendRefusedError, SendRefusedError

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
    not kill the measurement of a cell the backend already paid for. The one exception is a refused
    send — a spent provider account, a provider throttling past every wait, or the run's ceiling —
    which is no grading at all: it raises ``CellSendRefusedError``."""
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
            # `spent` is empty because `measure_sample` bills the cell's backend spend before any
            # judge runs; its catch banks the hole and the walk halts on it.
            raise CellSendRefusedError(
                str(exc), category=exc.category, spent={}, step_timings={}
            ) from exc
        except Exception as exc:
            logger.warning("judge %s stage %s failed: %s", judge, stage.role, exc)
            return "", f"{type(exc).__name__}: {exc}"

    # A hit is metered too, flagged, so grading cost stays invariant to our cache history rather
    # than making a re-read of an old comparison read as free. A fresh grading was metered at its
    # send. A grading is the one call shape with a naturally cacheable prefix — the rubric is a
    # module constant, so most of the prompt is byte-identical on every cell of every campaign.
    if cached is not None:
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

    # Never store an EMPTY reply: emptiness is transient, the key is the prompt hash, and this tree
    # is tenant-global, so caching one makes that comparison ungradeable forever.
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
    """One provider round-trip, heartbeated; the client admits, retries and meters it. RAISES;
    :func:`ask` is the half that never does."""
    # Local: `judges/` is a leaf package and this reaches back into `application/`.

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
            detail_fn=lambda: waiting_on(client, stage.model, role="grader"),
        )
    )
    try:
        return await client.chat(
            messages=[{"role": "user", "content": prompt}],
            model=stage.model,
            label=CallLabel(label, "judge"),
            temperature=stage.temperature,
            max_tokens=stage.max_tokens,
        )
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
