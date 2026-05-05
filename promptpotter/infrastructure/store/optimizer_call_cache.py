"""Content-addressed cache for optimizer LLM calls.

Mirrors :class:`MeasurementArchive`'s file-per-record pattern. Storage is
tenant-global at ``<base_dir>/archive/optimizer_calls/{hash}.json``.
Lookups are point-keyed; no index file.

The cache is consumed by :func:`promptpotter.application.optimization.llm_call.llm_call`
— if a hash hits, the stored ``LLMResponse.model_dump()`` is replayed and the
real LLM call is skipped. Cross-cycle / cross-fork by construction.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from promptpotter.infrastructure.store.base import read_json_optional, write_json
from promptpotter.shared.hashing import HASH_TRUNCATE

logger = logging.getLogger(__name__)


def hash_call(
    *,
    messages: list[dict[str, str]],
    model: str | None,
    provider: str,
    temperature: float,
    json_schema: dict | None,
) -> str:
    """SHA-256 (truncated to 24 hex) of the byte-identical LLM-call inputs."""
    blob = json.dumps(
        {
            "messages": messages,
            "model": model,
            "provider": provider,
            "temperature": temperature,
            "json_schema": json_schema,
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:HASH_TRUNCATE]


class OptimizerCallCache:
    """File-backed cache for optimizer LLM responses keyed by input hash."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    def _dir(self) -> Path:
        return self._base_dir / "archive" / "optimizer_calls"

    def _path(self, key: str) -> Path:
        return self._dir() / f"{key}.json"

    def load(self, key: str) -> dict[str, Any] | None:
        """Return the cached ``LLMResponse.model_dump()`` dict, or ``None``."""
        return read_json_optional(self._path(key))

    def save(self, key: str, value: dict[str, Any]) -> None:
        """Persist ``value`` under ``key``. ``value`` is ``LLMResponse.model_dump()``."""
        write_json(self._path(key), value)
        logger.debug("OptimizerCallCache: saved %s", key)
