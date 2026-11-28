"""One LLM call: its start, its heartbeat while blocked, and its answer."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.records import CycleRecord
from promptpotter.domain.spend import TokenAccount
from promptpotter.shared.clock import utcnow_iso


class LLMCallStartRecord(CycleRecord):
    """Pairs with :class:`LLMCallRecord` via ``call_id``, so a long call does not look frozen."""

    record_type: Literal["llm_call_start"] = "llm_call_start"
    call_id: str
    node: str
    round: int | None = None
    model: str | None = None
    prompt_chars: int = 0
    # Injection name → rendered chars. Empty for a node composing no layout and on a cache replay.
    injection_chars: dict[str, int] = Field(default_factory=dict)
    # What the node's ceiling REFUSED, per panel; `injection_chars` reports only what SURVIVED.
    injection_dropped: dict[str, int] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utcnow_iso)

    @property
    def refused_panels(self) -> list[str]:
        """Dropped WHOLE, nothing surviving in ``injection_chars``; a THINNED panel is not one."""
        return sorted(n for n in self.injection_dropped if n not in self.injection_chars)


class LLMCallProgressRecord(CycleRecord):
    """Heartbeat while the provider call is blocked; cache replays skip it."""

    record_type: Literal["llm_call_progress"] = "llm_call_progress"
    call_id: str
    node: str
    round: int | None = None
    elapsed_s: float
    detail: str | None = None
    # The cycle the waited-on cell runs (`Connector.cell_hop`), in THIS cycle's sandbox; ``None`` on an ordinary call.
    inner: CycleHop | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class LLMCallRecord(CycleRecord):
    record_type: Literal["llm_call"] = "llm_call"
    node: str
    round: int | None = None
    candidate_idx: int | None = None
    call_id: str = ""
    usage: TokenAccount | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utcnow_iso)
