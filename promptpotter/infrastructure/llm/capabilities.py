from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

from pydantic import AwareDatetime, TypeAdapter, ValidationError

from promptpotter.domain.pipeline_overlay import normalize_model_id
from promptpotter.domain.pipeline_schema import (
    CapabilityMenu,
    LLMSpendBound,
    ModelCapability,
    PipelineSchema,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.llm.openai_compat import PROVIDER_REQUEST_PARAMS
from promptpotter.infrastructure.llm.pricing import lookup_rate
from promptpotter.infrastructure.llm.registry import model_profile
from promptpotter.infrastructure.store.io import (
    read_bytes_optional,
    read_yaml_optional,
    write_bytes,
)
from promptpotter.infrastructure.store.read_model import derived, file_sig
from promptpotter.shared.errors import FileKind, FileUnreadableError, validation_reason
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
    """One model's row of the operator's `model_capabilities.yaml`."""

    reasoning_efforts: list[str] | None = None
    note: str = ""


OVERRIDE_SHAPE = TypeAdapter(dict[str, CapabilityOverride])


class TopProvider(StrictModel):
    max_completion_tokens: int | None = None


class Architecture(StrictModel):
    modality: str | None = None


class CatalogueEntry(StrictModel):
    """One model's OpenRouter listing, the fields the fetch keeps; an absent parameter list is UNKNOWN."""

    id: str
    supported_parameters: list[str] | None = None
    name: str | None = None
    context_length: int | None = None
    top_provider: TopProvider = TopProvider()
    architecture: Architecture = Architecture()


class _Catalogue(StrictModel):
    data: list[CatalogueEntry]


class CapabilitiesCache(StrictModel):
    fetched_at: AwareDatetime
    models: dict[str, CatalogueEntry]


CACHE_SHAPE = TypeAdapter(CapabilitiesCache)


def _per_mtok(per_token: float | None) -> float | None:
    return None if per_token is None else round(per_token * 1_000_000, 6)


def _overrides(workspace: Path) -> dict[str, CapabilityOverride]:
    """Authored, so a row that does not load refuses, naming the file and its key."""
    path = workspace / CAPABILITY_FILE

    def parse() -> dict[str, CapabilityOverride]:
        try:
            return OVERRIDE_SHAPE.validate_python(read_yaml_optional(path) or {})
        except ValidationError as exc:
            raise FileUnreadableError(
                FileKind.STORED_CONFIG, path=str(path), reason=validation_reason(exc, shown=3)
            ) from exc

    return derived(("model_capability_override", path), sig=file_sig(path), compute=parse)


def _cached(workspace: Path) -> CapabilitiesCache | None:
    """Run data: one that does not load reads as none, and the next ensure fetches it again."""
    path = workspace / CAPABILITIES_CACHE

    def parse() -> CapabilitiesCache | None:
        raw = read_bytes_optional(path)
        try:
            return None if raw is None else CACHE_SHAPE.validate_json(raw)
        except ValidationError:
            return None

    return derived(("model_capabilities", path), sig=file_sig(path), compute=parse)


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

    if (entry := _overrides(workspace).get(key)) is not None:
        if entry.reasoning_efforts is not None:
            return answer(
                reasoning_efforts=entry.reasoning_efforts,
                reasoning_note=entry.note or f"Declared in {CAPABILITY_FILE} by the operator.",
                source="override",
            )
        if entry.note:
            return answer(reasoning_efforts=None, reasoning_note=entry.note, source="override")

    cached = _cached(workspace)
    record = cached.models.get(key) if cached is not None else None
    if cached is None or record is None:
        return answer(
            reasoning_efforts=None,
            reasoning_note=(
                f"Not in this workspace's catalogue snapshot — every value the node declares is "
                f"offered but those measured to fail. Correct it in {CAPABILITY_FILE} if that is wrong."
            ),
            source="unknown",
        )

    # An absent list is UNKNOWN (`None`), never "supports none".
    unsupported: list[str] | None
    if (params := record.supported_parameters) is not None:
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
        display_name=record.name or "",
        context_length=record.context_length,
        max_output_tokens=record.top_provider.max_completion_tokens,
        modality=record.architecture.modality or "",
        fetched_at=cached.fetched_at.isoformat(),
    )


def context_window(
    model: str | None, provider: str | None, *, workspace: Path | None
) -> int | None:
    """The model's input window, off its card; ``None`` where no card holds one."""
    if not (model and provider and workspace):
        return None
    return resolve_model_capabilities(model, provider, workspace=workspace).context_length


def sent_llm_bound(
    where: str,
    route: tuple[str | None, str | None],
    max_tokens: int | None,
    *,
    workspace: Path | None,
    attempts: int,
    hosts: tuple[str, ...] | None = None,
) -> LLMSpendBound:
    """What a node's sends bill at most: *attempts*, each reading *route*'s whole window off its
    card and answering ``max_tokens``. An unknown of either refuses, before a cell is bought."""
    model, provider = route
    window = context_window(model, provider, workspace=workspace)
    if max_tokens is None or window is None:
        raise ValueError(
            f"{where} declares no `max_tokens`"
            if max_tokens is None
            else f"{where}: no catalogue card gives {model!r} on {provider!r} a context window, "
            "so its sends have no bound"
        )
    return LLMSpendBound(
        kind="llm", attempts=attempts, input_tokens=window, max_tokens=max_tokens, hosts=hosts
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
    cached = _cached(workspace)
    if cached is not None and datetime.now(UTC) - cached.fetched_at < timedelta(days=max_age_days):
        return 0
    return await refresh_model_capabilities(workspace)


async def refresh_model_capabilities(workspace: Path) -> int:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_S, verify=tls_context()) as client:
            resp = await client.get(MODELS_URL)
            resp.raise_for_status()
            # Upstream's other fields are dropped; a ValidationError is a ValueError, a failed fetch.
            catalogue = _Catalogue.model_validate_json(resp.content, extra="ignore")
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("model capability refresh failed, keeping any cached snapshot: %s", exc)
        return 0

    if not catalogue.data:
        return 0
    cache = CapabilitiesCache(
        fetched_at=datetime.now(UTC),
        models={normalize_model_id(entry.id): entry for entry in catalogue.data},
    )
    write_bytes(workspace / CAPABILITIES_CACHE, CACHE_SHAPE.dump_json(cache))
    return len(cache.models)
