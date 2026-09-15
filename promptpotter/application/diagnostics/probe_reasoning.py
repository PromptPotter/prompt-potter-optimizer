"""Measure which reasoning rungs a model actually honours — the only way to fill
``registry._MODEL_PROFILES``, since no catalogue publishes a value set.

Runs through ``get_llm_client().chat`` rather than a raw request, so what it reports is what THIS
repo sends, ``PROVIDER_DEFAULT_EFFORT``'s omission included; a parallel implementation would
measure a wire nothing uses.

Fenced like ``noise_floor``: no config field, no L1 injection, no ledger event. The loop never
learns this verb exists — it reads the profiles a human committed after reading the output.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from promptpotter.infrastructure.llm.capabilities import STANDARD_EFFORT_LADDER
from promptpotter.infrastructure.llm.openai_compat import PROVIDER_DEFAULT_EFFORT
from promptpotter.infrastructure.llm.registry import get_llm_client, normalize_model_id

# One terse-answer task. The measurand is the reasoning token COUNT, not the answer, so the prompt
# only has to be something a reasoning model will think about and a terse one will not.
_PROMPT = (
    "A company buys oak boards and metal angles to resell unmodified. Which ledger account: "
    "4000, 4200, 6000, or 1500? Answer with the 4-digit code only, nothing else."
)

# Bounds one probe's spend. A refusing rung costs nothing; a runaway one is the case being measured,
# and 3000 output tokens on the cheapest reasoning models is a fraction of a cent.
_MAX_TOKENS = 3000

# Rungs are INDISTINCT when max/min across the ranked ones is this small. Spread, never ORDER: no
# measured model orders its ladder monotonically, so an ordering test flags every one of them — and
# an unordered ladder is still not an identical one.
_INDISTINCT_SPREAD = 1.5


@dataclass(frozen=True)
class RungReading:
    """What one rung did. ``reasoning`` is ``None`` where the call failed — distinct from 0, which
    is a model that ran and thought nothing."""

    rung: str
    reasoning: int | None
    output: int | None
    refused: str = ""

    @property
    def ok(self) -> bool:
        return not self.refused


async def _one(client: object, model: str, rung: str | None) -> RungReading:
    kwargs = {} if rung is None else {"reasoning_effort": rung}
    try:
        resp = await client.chat(  # type: ignore[attr-defined]
            messages=[{"role": "user", "content": _PROMPT}],
            model=model,
            temperature=0.0,
            max_tokens=_MAX_TOKENS,
            **kwargs,
        )
    except Exception as exc:
        return RungReading(rung or "(unset)", None, None, refused=str(exc)[:160])
    return RungReading(rung or "(unset)", resp.usage.reasoning, resp.usage.output)


async def probe_reasoning(
    model: str, *, provider: str = "openrouter", rungs: tuple[str, ...] = STANDARD_EFFORT_LADDER
) -> list[RungReading]:
    """Every rung plus an unset baseline, serially — concurrent probes hit one endpoint's rate
    limit and a 429 would read as a refusal, which is the one answer this must not fabricate."""
    client = get_llm_client(provider)
    readings = [await _one(client, model, None)]
    for rung in rungs:
        readings.append(await _one(client, model, rung))
    return readings


def profile_suggestion(model: str, readings: list[RungReading]) -> str:
    """The `_MODEL_PROFILES` row these readings support — printed for a human to paste, never
    written. The table ships in the wheel as evidence, and evidence with no author is a cache."""
    refused = sorted(r.rung for r in readings if not r.ok and r.rung != "(unset)")

    # RANKED rungs only. `default` reproduces the unset baseline by definition and `none` is the
    # floor by definition; including either manufactures a verdict out of what the rung already means.
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

    # An all-default row narrows nothing and reads identically to absence at every call site, so
    # the honest suggestion is no row at all rather than one that looks like recorded evidence.
    if not refused and not measured:
        return f"# {normalize_model_id(model)}: nothing measured to record — add no row."

    lines = [f'    "{normalize_model_id(model)}": ModelProfile(']
    if refused:
        inner = ", ".join(f'"{r}"' for r in refused)
        lines.append(f"        refuses_efforts=frozenset({{{inner}}}),")
    # Emitted only where the ranked rungs actually answered, so an unreadable probe leaves the
    # field absent (UNMEASURED) rather than writing the empty set, which claims all-distinct.
    if measured:
        rungs = sorted(r.rung for r in ranked) if tight else []
        inner = "{" + ", ".join(f'"{r}"' for r in rungs) + "}" if rungs else ""
        lines.append(f"        indistinct_efforts=frozenset({inner}),")
    lines.append("    ),")
    return "\n".join(lines)


__all__ = ["RungReading", "probe_reasoning", "profile_suggestion"]


if __name__ == "__main__":  # pragma: no cover - operator convenience
    import sys

    for r in asyncio.run(probe_reasoning(sys.argv[1])):
        print(r)
