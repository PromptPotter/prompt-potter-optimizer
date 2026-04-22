"""Token usage record + chokepoint emission for LLM calls.

One record type (:class:`TokenUsage`), one write API (:func:`emit_token_usage`),
shared by every LLM call site in PromptPotter:

- Optimizer meta-prompts emit from
  ``promptpotter.application.optimization.pipeline.llm_call`` today.
- Backend-pipeline LLM nodes will emit from their query-node adapter when the
  backend token-count feature lands — same record, same sink.

Consumers subscribe via :func:`register_token_sink`. Built-in sink warns when
an optimizer prompt exceeds the configured threshold — operators see it and
trim the affected node template before meta-prompt size compounds further.

Planned consumers (not implemented here):
- per-query display line (``[ai]tokens=NN`` after HIT/MISS),
- composite-score signal (exponential penalty for long outputs),
- persistence sink for cost + anomaly tracking.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from promptpotter.config.settings import settings

logger = logging.getLogger(__name__)

__all__ = [
    "TokenSink",
    "TokenUsage",
    "emit_token_usage",
    "register_token_sink",
    "reset_token_sinks_for_tests",
]


@dataclass(frozen=True)
class TokenUsage:
    """Per-call LLM token record.

    Attributes:
        node: Logical node name (optimizer: ``"l1_generate"``, ``"critique"``,
            …; backend: ``"entity_profiling"``, ``"llm_ranking"``, …).
        kind: ``"optimizer"`` for meta-prompt calls, ``"backend"`` for
            in-pipeline LLM calls. Sinks use this to threshold separately.
        input_tokens: Prompt (input) tokens reported by the provider.
        output_tokens: Completion (output) tokens reported by the provider.
        duration_s: Wall-clock call duration in seconds.
    """

    node: str
    kind: Literal["optimizer", "backend"]
    input_tokens: int
    output_tokens: int
    duration_s: float

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


TokenSink = Callable[[TokenUsage], None]

_sinks: list[TokenSink] = []


def register_token_sink(sink: TokenSink) -> None:
    """Subscribe a consumer. Idempotent — repeated registration is a no-op."""
    if sink not in _sinks:
        _sinks.append(sink)


def emit_token_usage(usage: TokenUsage) -> None:
    """Fan out a token record to every registered sink. Sink exceptions are
    logged and swallowed so one bad sink cannot break the caller's call site."""
    for sink in _sinks:
        try:
            sink(usage)
        except Exception:
            logger.exception("token sink %r failed", sink)


def reset_token_sinks_for_tests() -> None:
    """Clear sink registry and re-install built-ins. Tests only."""
    _sinks.clear()
    register_token_sink(_warn_if_optimizer_over_threshold)


# -- built-in sinks -----------------------------------------------------------


def _warn_if_optimizer_over_threshold(usage: TokenUsage) -> None:
    """Warn when an optimizer prompt exceeds ``OPTIMIZER_PROMPT_WARN_TOKENS``."""
    if usage.kind != "optimizer":
        return
    threshold = settings.OPTIMIZER_PROMPT_WARN_TOKENS
    if usage.input_tokens > threshold:
        logger.warning(
            "optimizer node %r prompt at %d tokens (threshold=%d) — tune the "
            "template or drop context; large meta-prompts reduce signal-to-noise "
            "for the meta-LLM and risk provider TPM caps",
            usage.node,
            usage.input_tokens,
            threshold,
        )


register_token_sink(_warn_if_optimizer_over_threshold)
