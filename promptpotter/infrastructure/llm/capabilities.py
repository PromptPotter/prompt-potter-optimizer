from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from promptpotter.domain.pipeline_overlay import normalize_model_id
from promptpotter.domain.pipeline_schema import CapabilityMenu, ModelCapability, PipelineSchema
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.llm.openai_compat import PROVIDER_REQUEST_PARAMS
from promptpotter.infrastructure.llm.pricing import lookup_rate
from promptpotter.infrastructure.llm.registry import model_profile
from promptpotter.infrastructure.store.io import parse_yaml, write_json
from promptpotter.infrastructure.store.read_model import derived, file_sig, read_file
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import FileKind
from promptpotter.shared.tls import tls_context

logger = logging.getLogger(__name__)

MODELS_URL = "https://openrouter.ai/api/v1/models"
_FETCH_TIMEOUT_S = 15.0

CAPABILITY_FILE = "model_capabilities.yaml"

CAPABILITIES_CACHE = ".cache/model_capabilities.json"

_EFFORT_PARAM = "reasoning_effort"
_REASONING_PARAM = "reasoning"

STANDARD_EFFORT_LADDER: tuple[str, ...] = ("none", "default", "low", "medium", "high")
"""``assets/optimizers/potter/pipeline.yaml`` lists exactly these five by hand: change both."""


class CapabilityOverride(StrictModel):
    """The operator's word on one model, over the catalogue: its rungs, or a note alone."""

    reasoning_efforts: list[str] | None = None
    note: str | None = None


# Keyed by the normalized model id; an empty file declares nothing.
CAPABILITY_OVERRIDES: TypeAdapter[dict[str, CapabilityOverride] | None] = TypeAdapter(
    dict[str, CapabilityOverride] | None
)


class CatalogueEntry(StrictModel):
    """One catalogue model as the snapshot keeps it, its card's fields as the gateway sent them."""

    supported_parameters: list[str]
    name: str | None = None
    context_length: int | float | str | None = None
    top_provider: dict[str, Any] | None = None
    architecture: dict[str, Any] | None = None


class CapabilitySnapshot(StrictModel):
    fetched_at: str
    models: dict[str, CatalogueEntry]


CAPABILITY_SNAPSHOT = TypeAdapter(CapabilitySnapshot)


def _overrides(raw: bytes) -> dict[str, CapabilityOverride] | None:
    return CAPABILITY_OVERRIDES.validate_python(parse_yaml(raw))


def _override_path(workspace: Path) -> Path:
    return workspace / CAPABILITY_FILE


def _cache_path(workspace: Path) -> Path:
    return workspace / CAPABILITIES_CACHE


def _snapshot(path: Path) -> CapabilitySnapshot | None:
    return read_file(path, CAPABILITY_SNAPSHOT.validate_json)


def _per_mtok(per_token: float | None) -> float | None:
    return None if per_token is None else round(per_token * 1_000_000, 6)


def _as_int(raw: object) -> int | None:
    if not isinstance(raw, str | int | float) or isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _card(entry: CatalogueEntry, fetched_at: str) -> dict[str, Any]:
    top, arch = entry.top_provider or {}, entry.architecture or {}
    return {
        "display_name": entry.name or "",
        "context_length": _as_int(entry.context_length),
        "max_output_tokens": _as_int(top.get("max_completion_tokens")),
        "modality": str(arch.get("modality") or ""),
        "fetched_at": fetched_at,
    }


def resolve_model_capabilities(model: str, provider: str, *, workspace: Path) -> ModelCapability:
    key = normalize_model_id(model)
    # The table a bill is computed from, never the snapshot's `pricing` — that is one gateway's.
    rate = lookup_rate(model, provider or None)
    profile = model_profile(model)
    answer = partial(
        ModelCapability,
        model=model,
        provider=provider,
        input_usd_per_mtok=_per_mtok(rate.input if rate else None),
        output_usd_per_mtok=_per_mtok(rate.output if rate else None),
        # Measured, so it narrows every arm below: an unknown catalogue entry included.
        refused_efforts=sorted(profile.refuses_efforts) if profile else [],
    )

    override_path = _override_path(workspace)
    override = derived(
        ("model_capability_override", override_path),
        sig=file_sig(override_path),
        compute=lambda: read_file(override_path, _overrides, FileKind.STORED_CONFIG),
    )
    entry = None if override is None else override.get(key)
    if entry is not None:
        note = entry.note or ""
        if entry.reasoning_efforts is not None:
            return answer(
                reasoning_efforts=entry.reasoning_efforts,
                reasoning_note=note or f"Declared in {CAPABILITY_FILE} by the operator.",
                source="override",
            )
        if note:
            return answer(reasoning_efforts=None, reasoning_note=note, source="override")

    path = _cache_path(workspace)
    cached = derived(
        ("model_capabilities", path), sig=file_sig(path), compute=lambda: _snapshot(path)
    )
    if cached is None or (record := cached.models.get(key)) is None:
        return answer(
            reasoning_efforts=None,
            reasoning_note=(
                f"Not in this workspace's catalogue snapshot — every value the node declares is "
                f"offered but those measured to fail. Correct it in {CAPABILITY_FILE} if that is wrong."
            ),
            source="unknown",
        )

    params = record.supported_parameters
    # Listed under `reasoning` alone, a model still honours `reasoning_effort` on the wire.
    accepted = set(params) | ({_EFFORT_PARAM} if _REASONING_PARAM in params else set())
    unsupported = sorted(PROVIDER_REQUEST_PARAMS - accepted)
    offered = list(STANDARD_EFFORT_LADDER)
    note = (
        "Catalogue lists reasoning_effort."
        if _EFFORT_PARAM in params
        else "Catalogue lists no reasoning_effort — which does not mean the rungs are inert here."
    )

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
        **_card(record, cached.fetched_at),
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


async def ensure_model_capabilities(workspace: Path, *, max_age_days: float = 7.0) -> int:
    """0 is "nothing written" — an already-fresh snapshot as much as a failed fetch."""
    cached = _snapshot(_cache_path(workspace))
    if cached is not None and (fetched := cached.fetched_at):
        try:
            age = datetime.now(UTC) - datetime.fromisoformat(fetched)
        except ValueError:
            age = None
        if age is not None and age < timedelta(days=max_age_days):
            return 0
    return await refresh_model_capabilities(workspace)


async def refresh_model_capabilities(workspace: Path) -> int:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_S, verify=tls_context()) as client:
            resp = await client.get(MODELS_URL)
            resp.raise_for_status()
            payload: dict[str, Any] = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("model capability refresh failed, keeping any cached snapshot: %s", exc)
        return 0

    entries = payload.get("data")
    if not isinstance(entries, list):
        return 0
    models: dict[str, CatalogueEntry] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        mid = entry.get("id")
        if not isinstance(mid, str):
            continue
        params = entry.get("supported_parameters")
        try:
            models[normalize_model_id(mid)] = CatalogueEntry(
                supported_parameters=[str(p) for p in params] if isinstance(params, list) else [],
                name=entry.get("name"),
                context_length=entry.get("context_length"),
                top_provider=entry.get("top_provider"),
                architecture=entry.get("architecture"),
            )
        except ValidationError:
            continue
    if not models:
        return 0
    snapshot = CapabilitySnapshot(fetched_at=utcnow_iso(), models=models)
    write_json(_cache_path(workspace), snapshot.model_dump(mode="json"))
    return len(models)
