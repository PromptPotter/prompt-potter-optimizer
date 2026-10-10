from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from promptpotter.infrastructure.store.io import read_json_optional, write_json
from promptpotter.shared.hashing import ADDRESS_HEX, stable_hash

logger = logging.getLogger(__name__)


def hash_call(
    *,
    messages: list[dict[str, str]],
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


class LLMReuseCache:
    def __init__(self, base_dir: Path, namespace: str):
        self._namespace = namespace
        self._dir = base_dir / namespace

    def _path(self, key: str) -> Path:
        return self._dir / f"{key}.json"

    def load(self, key: str) -> dict[str, Any] | None:
        return read_json_optional(self._path(key))

    def save(self, key: str, value: dict[str, Any]) -> None:
        write_json(self._path(key), value)
        logger.debug("LLMReuseCache[%s]: saved %s", self._namespace, key)


__all__ = ["LLMReuseCache", "hash_call"]
