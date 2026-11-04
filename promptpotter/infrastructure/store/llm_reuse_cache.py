from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.infrastructure.llm.response import LLMResponse
from promptpotter.infrastructure.llm.telemetry import claim_call_price, emit_token_usage
from promptpotter.infrastructure.store.io import read_bytes_optional, write_text
from promptpotter.shared.hashing import ADDRESS_HEX, stable_hash

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from promptpotter.domain.spend import TokenUsageKind
    from promptpotter.infrastructure.llm.request import ChatMessage

logger = logging.getLogger(__name__)


def hash_call(
    *,
    messages: list[ChatMessage],
    model: str | None,
    provider: str,
    temperature: float,
    json_schema: dict[str, Any] | None,
    response_model: str | None = None,
    seed: int | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
    top_p: float | None = None,
    route_order: list[str] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "messages": messages,
        "model": model,
        "provider": provider,
        "temperature": temperature,
        "json_schema": json_schema,
        "response_model": response_model,
        "seed": seed,
        "max_tokens": max_tokens,
        "reasoning_effort": reasoning_effort,
    }
    # An optional lever joins only when set: an unconditional `null` re-keys every banked reply.
    if route_order:
        payload["route_order"] = route_order
    if top_p is not None:
        payload["top_p"] = top_p
    return stable_hash(payload, length=ADDRESS_HEX)


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
        if (banked := self._replay(key)) is not None:
            duration_s = round(time.monotonic() - started, 2)
            if claim_call_price(key):
                emit_token_usage(
                    node=node,
                    kind=kind,
                    model=banked.model or model,
                    provider=provider,
                    served_by=banked.served_by,
                    usage=banked.usage,
                    cost_usd=banked.cost_usd,
                    duration_s=duration_s,
                    key_source=None,
                    cached=True,
                )
            return Answered(banked, replayed=True, duration_s=duration_s)
        response = await send()
        # The send metered itself: a later replay of its reply in this campaign meters nothing.
        claim_call_price(key)
        if response.content.strip() or response.parsed is not None:
            self._save(key, response)
        return Answered(response, replayed=False, duration_s=round(time.monotonic() - started, 2))

    def _save(self, key: str, response: LLMResponse) -> None:
        write_text(self._path(key), response.model_dump_json(indent=2))
        logger.debug("LLMReuseCache[%s]: saved %s", self._namespace, key)
