"""What a MODEL accepts and costs — resolved in three layers, hand-authored first, so a wrong or
missing fetch is always correctable without a code change.

Distinct from ``registry._MODEL_PROFILES``, deliberately, and the split is what keeps either
usable. That table holds facts WE measured (a reasoning model's ``max_tokens`` floor, observed
from real ``reasoning_budget_exhausted`` failures) and it belongs in code because it is evidence.
This holds facts the PROVIDER declares, which go stale on their schedule rather than ours and
cannot be shipped in a release. Merging them would make one file both evidence and cache.

The layers, highest first:

1. ``model_capabilities.yaml`` in the tenant's own workspace — HAND-AUTHORED, and the reason the
   whole thing is safe to build on a third party's metadata. A locally-hosted model appears in no
   catalogue at all; a provider's list can simply be wrong. Either is one file away from fixed.
2. ``.cache/model_capabilities.json`` in the same workspace — the fetched snapshot. Per TENANT
   rather than per install: which models an operator asks about is their business, and a shared
   cache would pool that across accounts.
3. Nothing — ``reasoning_efforts=None``, which every caller must render as UNKNOWN and never as
   unsupported. An absent answer that reads as "no" silently deletes a real search axis.

The snapshot stores the provider's fields RAW and derives at resolve time. A derived cache
answers a question it was not asked, and the question here — which rungs does this model take,
against this node's ladder — has an answer that differs per dataset.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from promptpotter.domain.pipeline_schema import ModelCapability, PipelineSchema
from promptpotter.infrastructure.store.io import (
    read_json_tolerant,
    read_yaml_optional,
    write_json,
)
from promptpotter.shared.clock import utcnow_iso

logger = logging.getLogger(__name__)

MODELS_URL = "https://openrouter.ai/api/v1/models"
"""Public catalogue listing — no key, no account, and no request body, so the fetch carries
nothing about the tenant on whose behalf it runs. That is what makes caching it per tenant a
privacy measure rather than a ritual: the REQUEST leaks nothing, and the stored ANSWER never
pools with another account's."""

CAPABILITY_FILE = "model_capabilities.yaml"
"""Operator-authored override, at the tenant workspace root where it is findable — the twin of
the optimizer manifest's ``$PROMPTPOTTER_HOME`` shadow. Shape, and every key optional::

    qwen/qwen3.7-flash:
      reasoning_efforts: [none, default]
      note: local vLLM build ignores the effort ladder
"""

_CACHE_REL = Path(".cache") / "model_capabilities.json"

# The effort ladder is only OFFERED by a model that takes the parameter. OpenRouter reports the
# parameter's presence, not its value set — `reasoning_effort` in `supported_parameters` means the
# ladder is live, `reasoning` alone means the model reasons but takes no effort knob (measured:
# `qwen/qwen3.7-flash` has `reasoning` + `include_reasoning` and no `reasoning_effort`, while
# `openai/gpt-oss-20b` carries all three). Reading `reasoning` as the ladder is the mistake this
# constant exists to prevent.
_EFFORT_PARAM = "reasoning_effort"

# What a model taking no effort parameter still accepts, because these two are OURS: the node
# config uses them to mean "send nothing" and "send the provider default", so neither reaches the
# wire as an effort value. Every other rung does.
_NON_PROVIDER_EFFORTS: frozenset[str] = frozenset({"none", "default"})

STANDARD_EFFORT_LADDER: tuple[str, ...] = ("none", "default", "low", "medium", "high")
"""The rungs a model taking ``reasoning_effort`` is offered — OURS, not a provider's: the
catalogue reports that the parameter exists and never its value set, so this is the ladder we
send. It REPLACES whatever a node declared rather than intersecting with it, because the node's
list is a default authored before anyone knew which model would run there.
``assets/optimizer/pipeline.yaml`` lists exactly these five; a YAML cannot import a constant, so
that file cites this one by name."""


def _normalize(model: str) -> str:
    """The routing suffix is not part of a model's identity — ``:nitro`` picks a HOST, and every
    host of one model takes the same parameters. Mirrors ``registry.model_profile``."""
    return model.split(":", 1)[0].strip().lower()


def _override_path(workspace: Path) -> Path:
    return workspace / CAPABILITY_FILE


def _cache_path(workspace: Path) -> Path:
    return workspace / _CACHE_REL


def _as_float(raw: object) -> float | None:
    """Prices arrive as decimal STRINGS in USD per token. Per Mtok is the unit an operator reads a
    bill in, so the conversion happens here — once — rather than in every surface."""
    if not isinstance(raw, str | int | float) or isinstance(raw, bool):
        return None
    try:
        return float(raw) * 1_000_000
    except ValueError:
        return None


def _as_int(raw: object) -> int | None:
    if not isinstance(raw, str | int | float) or isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _card(model: str, entry: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    """The provider's own description of a model, projected onto :class:`ModelCapability`'s card
    half. Every field optional: a catalogue that drops one must degrade the card, never raise
    inside a resolve the caller already committed to."""
    pricing = entry.get("pricing") if isinstance(entry.get("pricing"), dict) else {}
    top = entry.get("top_provider") if isinstance(entry.get("top_provider"), dict) else {}
    arch = entry.get("architecture") if isinstance(entry.get("architecture"), dict) else {}
    return {
        "display_name": str(entry.get("name") or ""),
        "context_length": _as_int(entry.get("context_length")),
        "max_output_tokens": _as_int((top or {}).get("max_completion_tokens")),
        "input_usd_per_mtok": _as_float((pricing or {}).get("prompt")),
        "output_usd_per_mtok": _as_float((pricing or {}).get("completion")),
        "modality": str((arch or {}).get("modality") or ""),
        "moderated": (top or {}).get("is_moderated") if isinstance(top, dict) else None,
        "fetched_at": fetched_at,
    }


def resolve_model_capabilities(model: str, *, workspace: Path) -> ModelCapability:
    """The three-layer read for ONE model.

    Returns the ladder this model OFFERS, which is a fact about the model and not about any node.
    A caller holding a node's declared ladder uses it only where the answer is ``None``."""
    key = _normalize(model)

    override = read_yaml_optional(_override_path(workspace)) or {}
    entry = override.get(key) if isinstance(override, dict) else None
    if isinstance(entry, dict):
        raw = entry.get("reasoning_efforts")
        note = str(entry.get("note") or "")
        if isinstance(raw, list):
            return ModelCapability(
                model=model,
                reasoning_efforts=[str(v) for v in raw],
                reasoning_note=note or f"Declared in {CAPABILITY_FILE} by the operator.",
                source="override",
            )
        if note:
            # A note with no list is still an override — it says something about this model
            # without claiming to know its ladder, so the answer stays unknown and carries it.
            return ModelCapability(
                model=model, reasoning_efforts=None, reasoning_note=note, source="override"
            )

    cached = read_json_tolerant(_cache_path(workspace), default={}) or {}
    models = cached.get("models") if isinstance(cached, dict) else None
    record = models.get(key) if isinstance(models, dict) else None
    if not isinstance(record, dict):
        return ModelCapability(
            model=model,
            reasoning_efforts=None,
            reasoning_note=(
                f"Not in this workspace's catalogue snapshot — every value the node declares is "
                f"offered. Correct it in {CAPABILITY_FILE} if that is wrong."
            ),
            source="unknown",
        )

    from promptpotter.infrastructure.llm.openai_compat import PROVIDER_REQUEST_PARAMS

    raw_params = record.get("supported_parameters")
    # The GENERAL answer, computed once here so no surface re-derives it: of the keys we would
    # actually send, which does this model not accept. Only where the catalogue DECLARED a list —
    # a record without one says nothing, and an absent list read as "supports none" would strike
    # every row on a model that takes them all.
    unsupported: list[str] | None
    if isinstance(raw_params, list):
        params = [str(p) for p in raw_params]
        unsupported = sorted(PROVIDER_REQUEST_PARAMS - set(params))
    else:
        params = []
        unsupported = None
    takes_effort = _EFFORT_PARAM in params
    return ModelCapability(
        model=model,
        reasoning_efforts=(
            list(STANDARD_EFFORT_LADDER) if takes_effort else sorted(_NON_PROVIDER_EFFORTS)
        ),
        reasoning_note=(
            "Takes reasoning_effort — the full ladder is live."
            if takes_effort
            else "Takes no reasoning_effort parameter, so only the two rungs that send nothing."
        ),
        source="openrouter",
        unsupported_params=unsupported,
        **_card(model, record, str(cached.get("fetched_at") or "")),
    )


def resolve_menu(models: Sequence[str], *, workspace: Path | None) -> dict[str, ModelCapability]:
    """Every model on a menu, resolved once.

    *workspace* ``None`` yields an empty map, which every reader must render as UNKNOWN rather
    than as a menu of unsupported models."""
    if workspace is None:
        return {}
    return {m: resolve_model_capabilities(m, workspace=workspace) for m in models}


def resolve_schema_menu(
    schema: PipelineSchema, *, workspace: Path | None
) -> dict[str, ModelCapability]:
    """Every model a SCHEMA can put on screen, resolved — the ONE call each wire producer makes.

    The PAIRING is the rule, not the convenience. Ask ``available_models`` here instead — the
    obvious spelling, and the one that stood — and a model the OPERATOR typed resolves nothing at
    all, because a typed value rides ``param_allowed_values.model`` and by construction never
    reaches the admin catalogue. The card carrying that model's context, price and modality then
    rendered blank, silently, on the surface where the spend is committed. Spelled once, so the
    next door to open cannot re-derive it wrongly."""
    return resolve_menu(schema.selectable_models(), workspace=workspace)


async def refresh_model_capabilities(workspace: Path, *, timeout: float = 15.0) -> int:
    """Fetch the catalogue and cache each model's raw record. Returns how many models were stored;
    0 means the fetch failed and the previous cache still stands.

    Stores the provider's fields as they arrive rather than a derived ladder: the derivation
    depends on the node asking, which differs per dataset, so baking one in would cache an answer
    to a question this function was not asked.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
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
        models[_normalize(mid)] = {
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
    "refresh_model_capabilities",
    "resolve_menu",
    "resolve_model_capabilities",
    "resolve_schema_menu",
]
