"""The one place a judge reaches a model.

Every judge asks through :func:`ask`, so metering, caching and failure handling are decided once
rather than per judge. That is the same argument ``dispatch/llm_call/call.py`` makes for the
optimizer's own chokepoint — and this is deliberately a SECOND one rather than a reuse of it: that
module is the optimizer's, it meters ``kind="optimizer"``, and routing grading spend through it
would put judge cost in the loop's bucket, which is precisely the boundary a judge must not cross.
"""

from __future__ import annotations

import logging
import time

from promptpotter.infrastructure.llm.registry import get_llm_client
from promptpotter.infrastructure.llm.telemetry import emit_token_usage
from promptpotter.judges.protocol import JudgeStage

logger = logging.getLogger(__name__)

__all__ = ["ask"]


async def ask(stage: JudgeStage, prompt: str, *, judge: str) -> tuple[str, str]:
    """Run one judge stage. Returns ``(reply, error)`` — exactly one is non-empty.

    **Never raises.** A judge that blew up must produce an absent verdict, not an exception that
    kills the measurement of a cell the backend already paid for: the answer was measured, only
    the grading failed, and the two are different facts. The caller turns the error into a
    ``JudgeVerdict`` with ``score=None``, which omits the term and makes the formula halt loud.

    Meters through ``emit_token_usage(kind="judge", …)`` — the third spend arm — carrying BOTH
    ``provider`` and ``model``, since a rate belongs to the pair and a model alone cannot be
    priced (``shared/pricing.py::lookup_rate``). Filed under the stage's ``role`` so a multi-stage
    judge's rows stay tellable apart.
    """
    started = time.monotonic()
    try:
        client = get_llm_client(stage.provider)
        response = await client.chat(
            messages=[{"role": "user", "content": prompt}],
            model=stage.model,
            temperature=stage.temperature,
            max_tokens=stage.max_tokens,
        )
    except Exception as exc:
        logger.warning("judge %s stage %s failed: %s", judge, stage.role, exc)
        return "", f"{type(exc).__name__}: {exc}"

    usage = response.usage or {}
    emit_token_usage(
        node=f"{judge}:{stage.role}",
        kind="judge",
        model=response.model or stage.model,
        provider=stage.provider,
        input_tokens=int(usage.get("prompt_tokens", 0)),
        output_tokens=int(usage.get("completion_tokens", 0)),
        duration_s=time.monotonic() - started,
    )
    return response.content or "", ""
