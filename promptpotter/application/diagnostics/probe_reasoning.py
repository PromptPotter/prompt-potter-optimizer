"""Through ``get_llm_client().chat``, never a raw request: that would measure a wire nothing uses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from promptpotter.application.initialization.loop_start import diagnostic_trace
from promptpotter.application.jobs.quota import paid_verb
from promptpotter.infrastructure.llm.capabilities import STANDARD_EFFORT_LADDER
from promptpotter.infrastructure.llm.openai_compat import PROVIDER_DEFAULT_EFFORT
from promptpotter.infrastructure.llm.registry import get_llm_client, normalize_model_id
from promptpotter.infrastructure.llm.request import ChatRequest
from promptpotter.infrastructure.llm.send_failure import failed_send
from promptpotter.infrastructure.llm.send_pacing import SEND_ATTEMPTS, SendBudget, under_budget
from promptpotter.infrastructure.llm.spend_book import CallLabel
from promptpotter.shared.errors import ERROR_IS_CHARGED, ErrorCategory

if TYPE_CHECKING:
    from promptpotter.infrastructure.llm.base import LLMClientBase
    from promptpotter.infrastructure.store.stores import Stores

# The measurand is the reasoning token COUNT, never the answer.
_PROMPT = (
    "A company buys oak boards and metal angles to resell unmodified. Which ledger account: "
    "4000, 4200, 6000, or 1500? Answer with the 4-digit code only, nothing else."
)

_MAX_TOKENS = 3000

# Spread (max/min), never ORDER: no measured model orders its ladder monotonically.
_INDISTINCT_SPREAD = 1.5


@dataclass(frozen=True)
class RungReading:
    """``reasoning`` is ``None`` where the call failed; 0 is a model that ran and thought nothing."""

    rung: str
    reasoning: int | None
    output: int | None
    refused: str = ""

    @property
    def ok(self) -> bool:
        return not self.refused


async def _one(client: LLMClientBase, model: str, rung: str | None) -> RungReading:
    try:
        with under_budget(SendBudget(None, attempts=SEND_ATTEMPTS)):
            resp = await client.chat(
                ChatRequest(
                    messages=[{"role": "user", "content": _PROMPT}],
                    model=model,
                    max_tokens=_MAX_TOKENS,
                    reasoning_effort=rung,
                ),
                label=CallLabel("probe_reasoning", "diagnostic"),
            )
    except Exception as exc:
        # A throttle, an outage or a spent ceiling is no refusal: read as one, it writes a wrong profile.
        if not ERROR_IS_CHARGED[failed_send(exc).failure or ErrorCategory.UNKNOWN]:
            raise
        return RungReading(rung or "(unset)", None, None, refused=str(exc)[:160])
    return RungReading(rung or "(unset)", resp.usage.reasoning, resp.usage.output)


async def probe_reasoning(
    model: str,
    *,
    stores: Stores,
    provider: str = "openrouter",
    rungs: tuple[str, ...] = STANDARD_EFFORT_LADDER,
) -> list[RungReading]:
    """Serial on purpose: concurrent probes share one rate limit, and a 429 reads as a refusal."""
    client = get_llm_client(provider)
    async with paid_verb(stores=stores, bucket="probe-reasoning", hop=None):
        with diagnostic_trace(stores, None):
            readings = [await _one(client, model, None)]
            for rung in rungs:
                readings.append(await _one(client, model, rung))
    return readings


def profile_suggestion(model: str, readings: list[RungReading]) -> str:
    refused = sorted(r.rung for r in readings if not r.ok and r.rung != "(unset)")

    # `default` IS the unset baseline and `none` the floor: ranking either manufactures a verdict.
    ranked = [
        r
        for r in readings
        if r.ok
        and r.reasoning is not None
        and r.rung not in {"(unset)", PROVIDER_DEFAULT_EFFORT, "none"}
    ]
    counts = [r.reasoning or 0 for r in ranked]
    measured = len(counts) > 1 and min(counts) > 0
    tight = measured and max(counts) / min(counts) <= _INDISTINCT_SPREAD

    if not refused and not measured:
        return f"# {normalize_model_id(model)}: nothing measured to record — add no row."

    lines = [f'    "{normalize_model_id(model)}": ModelProfile(']
    if refused:
        inner = ", ".join(f'"{r}"' for r in refused)
        lines.append(f"        refuses_efforts=frozenset({{{inner}}}),")
    # Absent (UNMEASURED) where no ranked rung answered: the empty set claims all-distinct.
    if measured:
        rungs = sorted(r.rung for r in ranked) if tight else []
        inner = "{" + ", ".join(f'"{r}"' for r in rungs) + "}" if rungs else ""
        lines.append(f"        indistinct_efforts=frozenset({inner}),")
    lines.append("    ),")
    return "\n".join(lines)


__all__ = ["RungReading", "probe_reasoning", "profile_suggestion"]
