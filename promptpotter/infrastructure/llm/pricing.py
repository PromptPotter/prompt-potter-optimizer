"""No LiteLLM dependency, by choice: the rate table is vendored from its price backup (MIT, BerriAI)."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from promptpotter.config.paths import user_data_root
from promptpotter.infrastructure.store.io import read_json_optional, write_text
from promptpotter.shared.errors import ErrorCategory, SendRefusedError

logger = logging.getLogger(__name__)

__all__ = [
    "BUNDLED_PATH",
    "CACHE_PATH",
    "OPENROUTER_ENDPOINTS_URL",
    "UPSTREAM_URL",
    "PriceListUnreachableError",
    "PriceTier",
    "Rate",
    "RateCeiling",
    "RateTable",
    "compute_usd",
    "inline_route",
    "load_rates",
    "lookup_rate",
    "rate_ceiling",
    "refresh_rates",
    "refresh_rates_in_background",
]


@dataclass(frozen=True)
class Rate:
    input: float
    output: float
    cache_write: float | None = None
    cache_read: float | None = None
    max_output: int | None = None


@dataclass(frozen=True)
class RateTable:
    rates: Mapping[tuple[str, str], Rate]
    listed_by: Mapping[str, str] = field(default_factory=dict)

    @functools.cached_property
    def providers(self) -> frozenset[str]:
        return frozenset(provider for provider, _ in self.rates)


@dataclass(frozen=True)
class PriceTier:
    from_input_tokens: int
    input: float
    output: float


@dataclass(frozen=True)
class RateCeiling:
    tiers: tuple[PriceTier, ...]
    per_request: float = 0.0
    max_output: int | None = None

    def at(self, input_tokens: int) -> PriceTier:
        reached = [t for t in self.tiers if t.from_input_tokens <= input_tokens]
        return PriceTier(
            input_tokens, max(t.input for t in reached), max(t.output for t in reached)
        )

    def dearest(self) -> PriceTier:
        return self.at(max(t.from_input_tokens for t in self.tiers))


UPSTREAM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "litellm/model_prices_and_context_window_backup.json"
)
CACHE_PATH = user_data_root() / "rates.json"
BUNDLED_PATH = Path(__file__).parent / "data" / "rates.json"
_KEEP_FIELDS = (
    "input_cost_per_token",
    "output_cost_per_token",
    "cache_creation_input_token_cost",
    "cache_read_input_token_cost",
    "max_output_tokens",
    "litellm_provider",
    "mode",
)
_FETCH_TIMEOUT_S = 30.0
_MAX_BODY_BYTES = 8 * 1024 * 1024
_TTL_SECONDS = 24 * 60 * 60


def _strip_upstream(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, body in raw.items():
        if not isinstance(body, dict):
            continue
        if "input_cost_per_token" not in body and "output_cost_per_token" not in body:
            continue
        out[name] = {k: body[k] for k in _KEEP_FIELDS if k in body}
    return out


def _cache_fresh() -> bool:
    try:
        age_s = time.time() - CACHE_PATH.stat().st_mtime
    except OSError:
        return False
    return age_s < _TTL_SECONDS


def refresh_rates(*, force: bool = False, timeout: float = _FETCH_TIMEOUT_S) -> bool:
    if not force and _cache_fresh():
        return True
    try:
        with urllib.request.urlopen(UPSTREAM_URL, timeout=timeout) as resp:
            payload = resp.read(_MAX_BODY_BYTES + 1)
        if len(payload) > _MAX_BODY_BYTES:
            logger.warning(
                "spend: upstream payload exceeded %d bytes; skipping refresh",
                _MAX_BODY_BYTES,
            )
            return False
        raw = json.loads(payload.decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        logger.warning(
            "spend: rate refresh from %s failed (%s); using prior cache or bundled floor",
            UPSTREAM_URL,
            exc,
        )
        return False

    stripped = _strip_upstream(raw)
    write_text(CACHE_PATH, json.dumps({"models": stripped}, indent=0, separators=(",", ":")))
    load_rates.cache_clear()
    logger.info("spend: refreshed %d model rates → %s", len(stripped), CACHE_PATH)
    return True


def refresh_rates_in_background() -> None:
    threading.Thread(target=refresh_rates, name="rates-refresh", daemon=True).start()


def _read_models(path: Path) -> dict[str, Any] | None:
    try:
        raw = read_json_optional(path)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("spend: failed to load %s (%s)", path, exc)
        return None
    if not isinstance(raw, dict):
        return None
    models = raw.get("models")
    return models if isinstance(models, dict) else None


def _optional_cost(body: dict[str, Any], key: str) -> float | None:
    value = body.get(key)
    return float(value) if value is not None else None


def _models_to_rates(models: dict[str, Any]) -> RateTable:
    prefixed: dict[tuple[str, str], Rate] = {}
    bare: dict[tuple[str, str], Rate] = {}
    listed_by: dict[str, str] = {}
    for name, body in models.items():
        if not isinstance(body, dict):
            continue
        in_c = body.get("input_cost_per_token")
        out_c = body.get("output_cost_per_token")
        if in_c is None and out_c is None:
            continue
        rate = Rate(
            input=float(in_c or 0.0),
            output=float(out_c or 0.0),
            cache_write=_optional_cost(body, "cache_creation_input_token_cost"),
            cache_read=_optional_cost(body, "cache_read_input_token_cost"),
            max_output=_as_tokens(body.get("max_output_tokens")),
        )
        provider, _, model = name.lower().partition("/")
        if model:
            prefixed[provider, model] = rate
        elif lister := str(body.get("litellm_provider") or "").lower():
            bare[lister, provider] = rate
            listed_by[provider] = lister
    # `prefixed` last: where upstream lists a pair both ways, the row spelling its provider wins.
    return RateTable(bare | prefixed, listed_by)


def _as_tokens(raw: object) -> int | None:
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


@functools.lru_cache(maxsize=1)
def load_rates() -> RateTable:
    models = _read_models(CACHE_PATH)
    if models is not None:
        return _models_to_rates(models)
    models = _read_models(BUNDLED_PATH)
    if models is not None:
        logger.info("spend: using bundled rate floor at %s (no cache yet)", BUNDLED_PATH)
        return _models_to_rates(models)
    logger.warning(
        "spend: no rate table available (cache %s + bundle %s both missing)",
        CACHE_PATH,
        BUNDLED_PATH,
    )
    return RateTable({})


def lookup_rate(model: str | None, provider: str | None) -> Rate | None:
    """A price belongs to the (provider, model) PAIR, so a call naming no provider has none."""
    if not model or not provider:
        return None
    return load_rates().rates.get((provider.lower().strip(), model.lower().strip()))


def inline_route(model: str) -> tuple[str, str | None]:
    """Off the rate table alone: no operator-typed string reaches a library's provider resolution."""
    name = model.strip()
    table = load_rates()
    prefix, _, rest = name.partition("/")
    if rest and prefix.lower() in table.providers:
        return rest, prefix.lower()
    return name, table.listed_by.get(name.lower())


def compute_usd(
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    *,
    provider: str | None,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """An estimate, never a bill; every client hands the cache counts as SUBSETS of *input_tokens*."""
    rate = lookup_rate(model, provider)
    if rate is None:
        return None
    # `cache_*`, never `cached_*`: `cached` names a replay of our own archive, no provider reached.
    cache_read = max(0, int(cache_read_tokens))
    cache_write = max(0, int(cache_write_tokens))
    full_rate_input = max(0, input_tokens - cache_read - cache_write)
    return (
        full_rate_input * rate.input
        + cache_read * (rate.cache_read if rate.cache_read is not None else rate.input)
        + cache_write * (rate.cache_write if rate.cache_write is not None else rate.input)
        + output_tokens * rate.output
    )


OPENROUTER_ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{model}/endpoints"

# OpenRouter's routing shortcuts: a different ORDER over the same hosts, so the same ceiling.
_ROUTE_SORTS = frozenset({"nitro", "floor"})
_ROUTE_MEMO: dict[str, tuple[float, RateCeiling]] = {}
_OUTAGE_MEMO_S = 60.0
_ROUTE_OUTAGE: dict[str, tuple[float, str]] = {}


class PriceListUnreachableError(SendRefusedError):
    """An outage, never "no rate bounds this call": a send needing the price is refused unsent."""

    def __init__(self, message: str) -> None:
        super().__init__(message, category=ErrorCategory.PRICE_LIST_UNREACHABLE)


async def rate_ceiling(
    model: str, provider: str, *, hosts: tuple[str, ...] | None = None
) -> RateCeiling | None:
    """A gateway's ceiling is its DEAREST eligible host: whichever answers bills within the hold."""
    if provider.lower() == "openrouter":
        return await _openrouter_ceiling(model, hosts)
    rate = lookup_rate(model, provider)
    if rate is None:
        return None
    return RateCeiling(
        tiers=(PriceTier(0, max(rate.input, rate.cache_write or 0.0), rate.output),),
        max_output=rate.max_output,
    )


def _route_model(model: str) -> str | None:
    """``None`` for a suffix that changes what the call BUYS (``:online`` adds priced search)."""
    base, _, suffix = model.strip().lower().partition(":")
    return base if not suffix or suffix in _ROUTE_SORTS else None


async def _openrouter_ceiling(model: str, pinned: tuple[str, ...] | None) -> RateCeiling | None:
    key = _route_model(model)
    if key is None:
        return None
    memo_key = f"{key}@{','.join(pinned)}" if pinned else key
    memo = _ROUTE_MEMO.get(memo_key)
    if memo is not None and time.time() - memo[0] < _TTL_SECONDS:
        return memo[1]
    outage = _ROUTE_OUTAGE.get(memo_key)
    if outage is not None and time.time() - outage[0] < _OUTAGE_MEMO_S:
        raise PriceListUnreachableError(outage[1])
    try:
        ceiling = await asyncio.to_thread(_fetch_route_ceiling, key, pinned)
    except PriceListUnreachableError as exc:
        _ROUTE_OUTAGE[memo_key] = (time.time(), str(exc))
        raise
    if ceiling is not None:
        _ROUTE_MEMO[memo_key] = (time.time(), ceiling)
    return ceiling


def _serves(endpoint: dict[str, Any], pinned: tuple[str, ...]) -> bool:
    """OpenRouter's own matching: a bare slug admits each of its endpoints, a tagged one only itself."""
    tag = str(endpoint.get("tag") or "")
    return any(tag == host or tag.startswith(f"{host}/") for host in pinned)


def _fetch_route_ceiling(model: str, pinned: tuple[str, ...] | None) -> RateCeiling | None:
    url = OPENROUTER_ENDPOINTS_URL.format(model=model)
    try:
        with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT_S) as resp:
            payload = json.loads(resp.read(_MAX_BODY_BYTES).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # The gateway ANSWERED, and a 4xx says it lists no such model: unpriceable, not an outage.
        if 400 <= exc.code < 500 and exc.code not in (408, 429):
            logger.warning("spend: OpenRouter lists no model %s (HTTP %d)", model, exc.code)
            return None
        raise PriceListUnreachableError(
            f"the host price list for {model} could not be fetched from {url}: HTTP {exc.code}"
        ) from exc
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise PriceListUnreachableError(
            f"the host price list for {model} could not be fetched from {url}: {exc}"
        ) from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    endpoints = [
        e
        for e in (data or {}).get("endpoints") or []
        if isinstance(e, dict) and (not pinned or _serves(e, pinned))
    ]
    if not endpoints:
        logger.warning("spend: OpenRouter lists no host for %s among %s", model, pinned or "any")
        return None
    try:
        hosts = [_host_tiers(e.get("pricing") or {}) for e in endpoints]
    except (TypeError, ValueError):
        logger.warning("spend: %s lists a price no ceiling can read", model)
        return None
    # A negative price is OpenRouter's "decided per request" — a router, which no list bounds.
    if any(v < 0 for host, _ in hosts for tier in host for v in (tier.input, tier.output)):
        return None
    starts = sorted({tier.from_input_tokens for host, _ in hosts for tier in host})
    tiers = tuple(
        PriceTier(
            start,
            max(_host_at(host, start).input for host, _ in hosts),
            max(_host_at(host, start).output for host, _ in hosts),
        )
        for start in starts
    )
    replies = [
        _as_tokens(e.get("max_completion_tokens") or e.get("context_length")) for e in endpoints
    ]
    return RateCeiling(
        tiers=tiers,
        per_request=max(request for _, request in hosts),
        max_output=None if None in replies else max(r for r in replies if r is not None),
    )


def _host_tiers(pricing: dict[str, Any]) -> tuple[list[PriceTier], float]:
    def tier(start: int, prices: dict[str, Any], base: dict[str, Any]) -> PriceTier:
        def rate(*keys: str) -> float:
            return max(float(prices.get(k, base.get(k)) or 0.0) for k in keys)

        return PriceTier(
            start, rate("prompt", "input_cache_write"), rate("completion", "internal_reasoning")
        )

    overrides = pricing.get("overrides") or ()
    # A time-of-day override (`utc_start`/`utc_end`) prices from token 0, so the dearest is the base.
    base = [tier(0, p, pricing) for p in (pricing, *(o for o in overrides if not _by_length(o)))]
    tiers = [PriceTier(0, max(t.input for t in base), max(t.output for t in base))]
    for override in overrides:
        if _by_length(override):
            tiers.append(tier(int(override["min_prompt_tokens"]), override, pricing))
    return tiers, float(pricing.get("request") or 0.0)


def _by_length(override: dict[str, Any]) -> bool:
    return "min_prompt_tokens" in override


def _host_at(tiers: list[PriceTier], input_tokens: int) -> PriceTier:
    return max(
        (t for t in tiers if t.from_input_tokens <= input_tokens),
        key=lambda t: t.from_input_tokens,
    )
