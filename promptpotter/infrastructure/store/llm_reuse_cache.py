from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from promptpotter.domain.spend import TokenUsageKind
from promptpotter.infrastructure.llm.base import ChatRequest, LLMResponse
from promptpotter.infrastructure.llm.telemetry import (
    call_price,
    claim_call_price,
    emit_replay_usage,
)
from promptpotter.infrastructure.store.io import read_bytes_optional, write_text
from promptpotter.shared.hashing import ADDRESS_HEX, stable_hash

logger = logging.getLogger(__name__)


def hash_call(request: ChatRequest, *, provider: str) -> str:
    """The request as sent: its `wire_schema`, never a class name, keys the reply."""
    payload: dict[str, Any] = {
        "messages": request.messages,
        "model": request.model,
        "provider": provider,
        "temperature": request.temperature,
        "json_schema": request.wire_schema,
        "seed": request.seed,
        "max_tokens": request.max_tokens,
        "reasoning_effort": request.reasoning_effort,
    }
    # An optional lever joins only when set: an unconditional `null` re-keys every banked reply.
    if request.route_order:
        payload["route_order"] = request.route_order
    if request.top_p is not None:
        payload["top_p"] = request.top_p
    return stable_hash(payload, length=ADDRESS_HEX)


class _Claims:
    """A miss claims its key; a second asker of it waits on the claim, then reads the bank."""

    def __init__(self) -> None:
        self._held: dict[Path, tuple[asyncio.Lock, int]] = {}

    @contextlib.asynccontextmanager
    async def claim(self, path: Path) -> AsyncIterator[None]:
        lock, askers = self._held.get(path, (asyncio.Lock(), 0))
        self._held[path] = (lock, askers + 1)
        try:
            async with lock:
                yield
        finally:
            lock, askers = self._held[path]
            if askers == 1:
                del self._held[path]
            else:
                self._held[path] = (lock, askers - 1)


_CLAIMS = _Claims()


@dataclass(frozen=True)
class Answered:
    response: LLMResponse
    # A banked reply, which no send made.
    replayed: bool
    duration_s: float


class LLMReuseCache:
    """A reply banked by its call's `hash_call` key: the cache wraps the send, so the reuse
    protocol — replay, meter, keep — is spelled here once."""

    def __init__(self, base_dir: Path, namespace: str):
        self._namespace = namespace
        self._dir = base_dir / namespace

    def _path(self, key: str) -> Path:
        return self._dir / f"{key}.json"

    def _load(self, key: str) -> LLMResponse | None:
        raw = read_bytes_optional(self._path(key))
        return None if raw is None else LLMResponse.model_validate_json(raw)

    def _replay(self, key: str) -> LLMResponse | None:
        """The banked reply, or none: an unreadable entry is a miss, so the call re-samples."""
        try:
            response = self._load(key)
        except (OSError, ValueError) as exc:
            logger.warning(
                "LLMReuseCache[%s]: %s unusable, re-sampling — %s", self._namespace, key, exc
            )
            return None
        if response is not None:
            logger.debug("LLMReuseCache[%s]: hit %s", self._namespace, key)
        return response

    async def answer(
        self,
        key: str,
        send: Callable[[], Awaitable[LLMResponse]],
        *,
        node: str,
        kind: TokenUsageKind,
        provider: str,
        model: str,
    ) -> Answered:
        """The banked reply, else *send*'s. A replay is metered once per campaign, so incurred
        cost stays invariant to cache history; a fresh reply is kept unless empty, or a transient
        failure replays tenant-wide. A send that raises keeps and meters nothing."""
        started = time.monotonic()
        async with _CLAIMS.claim(self._path(key)):
            if (banked := self._replay(key)) is None:
                response = await send()
                # The send metered itself: a later replay of its reply in this campaign meters nothing.
                claim_call_price(key)
                if response.content.strip() or response.parsed is not None:
                    self._save(key, response)
                return Answered(
                    response, replayed=False, duration_s=round(time.monotonic() - started, 2)
                )
        duration_s = round(time.monotonic() - started, 2)
        # A reply that reported no count replays none, as its send filed none.
        if claim_call_price(key) and (usage := banked.usage) is not None:
            emit_replay_usage(
                node=node,
                usage=usage,
                duration_s=duration_s,
                price=call_price(
                    kind,
                    usage,
                    model=banked.model or model,
                    provider=provider,
                    cost_usd=banked.cost_usd,
                    recorded=None,
                ),
                served_by=banked.served_by,
            )
        return Answered(banked, replayed=True, duration_s=duration_s)

    def _save(self, key: str, response: LLMResponse) -> None:
        write_text(self._path(key), response.model_dump_json(indent=2))
        logger.debug("LLMReuseCache[%s]: saved %s", self._namespace, key)
