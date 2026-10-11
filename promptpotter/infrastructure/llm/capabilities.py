from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

from promptpotter.domain.pipeline_schema import CapabilityMenu, ModelCapability, PipelineSchema
from promptpotter.infrastructure.llm.openai_compat import PROVIDER_REQUEST_PARAMS
from promptpotter.infrastructure.llm.pricing import lookup_rate
from promptpotter.infrastructure.llm.registry import model_profile, normalize_model_id
from promptpotter.infrastructure.store.io import (
    read_json_tolerant,
    read_yaml_optional,
    write_json,
)
from promptpotter.infrastructure.store.read_model import derived, file_sig
from promptpotter.infrastructure.tls import tls_context
from promptpotter.shared.clock import utcnow_iso

logger = logging.getLogger(__name__)

MODELS_URL = "https://openrouter.ai/api/v1/models"

CAPABILITY_FILE = "model_capabilities.yaml"

_CACHE_REL = Path(".cache") / "model_capabilities.json"

_EFFORT_PARAM = "reasoning_effort"
_REASONING_PARAM = "reasoning"

STANDARD_EFFORT_LADDER: tuple[str, ...] = ("none", "default", "low", "medium", "high")
"""``assets/optimizers/potter/pipeline.yaml`` lists exactly these five by hand: change both."""


def _override_path(workspace: Path) -> Path:
    return workspace / CAPABILITY_FILE


def _cache_path(workspace: Path) -> Path:
    return workspace / _CACHE_REL


def _per_mtok(per_token: float | None) -> float | None:
    return None if per_token is None else round(per_token * 1_000_000, 6)


def _as_int(raw: object) -> int | None:
    if not isinstance(raw, str | int | float) or isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _card(entry: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    top, arch = (
        part if isinstance(part := entry.get(key), dict) else {}
        for key in ("top_provider", "architecture")
    )
    return {
        "display_name": str(entry.get("name") or ""),
        "context_length": _as_int(entry.get("context_length")),
        "max_output_tokens": _as_int(top.get("max_completion_tokens")),
        "modality": str(arch.get("modality") or ""),
        "moderated": top.get("is_moderated"),
        "fetched_at": fetched_at,
    }


def resolve_model_capabilities(model: str, provider: str, *, workspace: Path) -> ModelCapability:
    key = normalize_model_id(model)
    # The table a bill is computed from, never the snapshot's `pricing` — that is one gateway's.
    rate = lookup_rate(model, provider or None)
    answer = partial(
        ModelCapability,
        model=model,
        provider=provider,
        input_usd_per_mtok=_per_mtok(rate.input if rate else None),
        output_usd_per_mtok=_per_mtok(rate.output if rate else None),
    )

    override_path = _override_path(workspace)
    override = derived(
        ("model_capability_override", override_path),
        sig=file_sig(override_path),
        compute=lambda: read_yaml_optional(override_path),
    )
    entry = override.get(key) if isinstance(override, dict) else None
    if isinstance(entry, dict):
        raw = entry.get("reasoning_efforts")
        note = str(entry.get("note") or "")
        if isinstance(raw, list):
            return answer(
                reasoning_efforts=[str(v) for v in raw],
                reasoning_note=note or f"Declared in {CAPABILITY_FILE} by the operator.",
                source="override",
            )
        if note:
            return answer(reasoning_efforts=None, reasoning_note=note, source="override")

    path = _cache_path(workspace)
    cached = (
        derived(
            ("model_capabilities", path),
            sig=file_sig(path),
            compute=lambda: read_json_tolerant(path, default={}),
        )
        or {}
    )
    models = cached.get("models") if isinstance(cached, dict) else None
    record = models.get(key) if isinstance(models, dict) else None
    if not isinstance(record, dict):
        return answer(
            reasoning_efforts=None,
            reasoning_note=(
                f"Not in this workspace's catalogue snapshot — every value the node declares is "
                f"offered. Correct it in {CAPABILITY_FILE} if that is wrong."
            ),
            source="unknown",
        )

    raw_params = record.get("supported_parameters")
    # An absent list is UNKNOWN (`None`), never "supports none".
    unsupported: list[str] | None
    if isinstance(raw_params, list):
        params = [str(p) for p in raw_params]
        # Listed under `reasoning` alone, a model still honours `reasoning_effort` on the wire.
        accepted = set(params) | ({_EFFORT_PARAM} if _REASONING_PARAM in params else set())
        unsupported = sorted(PROVIDER_REQUEST_PARAMS - accepted)
    else:
        params = []
        unsupported = None
    offered = list(STANDARD_EFFORT_LADDER)
    note = (
        "Catalogue lists reasoning_effort."
        if _EFFORT_PARAM in params
        else "Catalogue lists no reasoning_effort — which does not mean the rungs are inert here."
    )

    profile = model_profile(model)
    indistinct: list[str] | None = None
    if profile is not None:
        if refused := profile.refuses_efforts & set(offered):
            offered = [rung for rung in offered if rung not in refused]
            note += f" Measured to REFUSE {', '.join(sorted(refused))}, so not offered."
        # Not subtracted: an indistinct rung is legal. `None` stays `None` — `[]` claims "distinct".
        if profile.indistinct_efforts is not None:
            indistinct = sorted(profile.indistinct_efforts & set(offered))
            if indistinct:
                note += f" Measured INDISTINCT from each other: {', '.join(indistinct)}."

    return answer(
        reasoning_efforts=offered,
        reasoning_note=note,
        indistinct_efforts=indistinct,
        source="openrouter",
        unsupported_params=unsupported,
        **_card(record, str(cached.get("fetched_at") or "")),
    )


def resolve_menu(routes: Iterable[tuple[str, str]], *, workspace: Path | None) -> CapabilityMenu:
    menu: CapabilityMenu = {}
    if workspace is None:
        return menu
    for provider, model in routes:
        menu.setdefault(provider, {})[model] = resolve_model_capabilities(
            model, provider, workspace=workspace
        )
    return menu


def resolve_schema_menu(schema: PipelineSchema, *, workspace: Path | None) -> CapabilityMenu:
    return resolve_menu(schema.selectable_routes(), workspace=workspace)


async def ensure_model_capabilities(
    workspace: Path, *, max_age_days: float = 7.0, timeout: float = 15.0
) -> int:
    """0 is "nothing written" — an already-fresh snapshot as much as a failed fetch."""
    cached = read_json_tolerant(_cache_path(workspace), default={}) or {}
    fetched = cached.get("fetched_at") if isinstance(cached, dict) else None
    if isinstance(fetched, str) and fetched:
        try:
            age = datetime.now(UTC) - datetime.fromisoformat(fetched)
        except ValueError:
            age = None
        if age is not None and age < timedelta(days=max_age_days):
            return 0
    return await refresh_model_capabilities(workspace, timeout=timeout)


async def refresh_model_capabilities(workspace: Path, *, timeout: float = 15.0) -> int:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout, verify=tls_context()) as client:
            resp = await client.get(MODELS_URL)
            resp.raise_for_status()
            payload: dict[str, Any] = resp.json()
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception as exc:
        logger.info("model capability refresh failed, keeping any cached snapshot: %s", exc)
        return 0

    entries = payload.get("data")
    if not isinstance(entries, list):
        return 0
    models: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        mid = entry.get("id")
        if not isinstance(mid, str):
            continue
        params = entry.get("supported_parameters")
        models[normalize_model_id(mid)] = {
            "supported_parameters": [str(p) for p in params] if isinstance(params, list) else [],
            "name": entry.get("name"),
            "context_length": entry.get("context_length"),
            "pricing": entry.get("pricing"),
            "top_provider": entry.get("top_provider"),
            "architecture": entry.get("architecture"),
        }
    if not models:
        return 0
    write_json(_cache_path(workspace), {"fetched_at": utcnow_iso(), "models": models})
    return len(models)


__all__ = [
    "CAPABILITY_FILE",
    "MODELS_URL",
    "STANDARD_EFFORT_LADDER",
    "ensure_model_capabilities",
    "refresh_model_capabilities",
    "resolve_menu",
    "resolve_model_capabilities",
    "resolve_schema_menu",
]
