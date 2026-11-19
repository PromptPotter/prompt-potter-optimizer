"""No LiteLLM dependency, by choice: the rate table is vendored from its price backup (MIT, BerriAI)."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import os
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from pydantic import Field, TypeAdapter, ValidationError

from promptpotter.config.paths import user_data_root
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.io import (
    read_bytes_optional,
    unlink_robust,
    write_bytes,
    write_text,
)
from promptpotter.infrastructure.store.read_model import derived, file_sig
from promptpotter.shared.errors import ErrorCategory, SendRefusedError
from promptpotter.shared.hashing import stable_hash

logger = logging.getLogger(__name__)


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
BUNDLED_PATH = Path(__file__).parent / "data" / "rates.json"
_FETCH_TIMEOUT_S = 30.0
_MAX_BODY_BYTES = 8 * 1024 * 1024
_TTL_SECONDS = 24 * 60 * 60


class RateBody(StrictModel):
    """One row of `rates.json`, USD per token; ``lister``, the provider listing a bare name."""

    input_cost_per_token: float
    output_cost_per_token: float
    cache_creation_input_token_cost: float | None = None
    cache_read_input_token_cost: float | None = None
    max_output_tokens: int | None = None
    lister: str | None = None


class RatesFile(StrictModel):
    """The bundled floor and the fetched cache alike: a name to its one rate body."""

    models: dict[str, RateBody]


def _strip_upstream(raw: dict[str, Any]) -> RatesFile:
    """The one door upstream's vocabulary reaches: its ``litellm_provider`` is our ``lister``, and a
    row no rate body types (a side unpriced) is no rate."""
    models: dict[str, RateBody] = {}
    for name, body in raw.items():
        if not isinstance(body, dict):
            continue
        kept = {k: body[k] for k in RateBody.model_fields if k in body}
        with contextlib.suppress(ValidationError):
            models[name] = RateBody.model_validate(kept | {"lister": body.get("litellm_provider")})
    return RatesFile(models=models)


def cache_path() -> Path:
    """Resolved per call, as every data-root reader is: a home set after import reads its own rates."""
    return user_data_root() / "rates.json"


def refresh_rates(*, force: bool = False) -> bool:
    """Every process that prices calls it, a launch and the API's sweep: the home's rates file is the
    once-a-day key, and a failed fetch stamps it too, so a dead upstream costs one timeout a day."""
    path = cache_path()
    with contextlib.suppress(OSError):
        if not force and time.time() - path.stat().st_mtime < _TTL_SECONDS:
            return True
    try:
        with urllib.request.urlopen(UPSTREAM_URL, timeout=_FETCH_TIMEOUT_S) as resp:
            payload = resp.read(_MAX_BODY_BYTES + 1)
        if len(payload) > _MAX_BODY_BYTES:
            raise ValueError(f"payload exceeded {_MAX_BODY_BYTES} bytes")
        raw = json.loads(payload.decode("utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("spend: rate refresh from %s failed (%s); prior or floor", UPSTREAM_URL, exc)
        try:
            os.utime(path)
        except FileNotFoundError:  # The floor prices alone, as it did.
            write_text(path, RatesFile(models={}).model_dump_json())
        return False

    stripped = _strip_upstream(raw)
    write_text(path, stripped.model_dump_json(exclude_none=True))
    logger.info("spend: refreshed %d model rates → %s", len(stripped.models), path)
    return True


def _read_rates(path: Path | None) -> dict[str, RateBody]:
    raw = None if path is None else read_bytes_optional(path)
    return {} if raw is None else RatesFile.model_validate_json(raw).models


def _models_to_rates(models: Mapping[str, RateBody]) -> RateTable:
    prefixed: dict[tuple[str, str], Rate] = {}
    bare: dict[tuple[str, str], Rate] = {}
    listed_by: dict[str, str] = {}
    for name, body in models.items():
        rate = Rate(
            input=body.input_cost_per_token,
            output=body.output_cost_per_token,
            cache_write=body.cache_creation_input_token_cost,
            cache_read=body.cache_read_input_token_cost,
            max_output=body.max_output_tokens,
        )
        provider, _, model = name.lower().partition("/")
        if model:
            prefixed[provider, model] = rate
        elif lister := (body.lister or "").lower():
            bare[lister, provider] = rate
            listed_by[provider] = lister
    # `prefixed` last: where upstream lists a pair both ways, the row spelling its provider wins.
    return RateTable(bare | prefixed, listed_by)


def load_rates() -> RateTable:
    """Kept by where it reads and what the files hold: a rates file any process rewrites re-prices
    every reader's next send; a home that fetched none reads the floor's one table."""
    fetched = None if (seen := file_sig(cache_path())) is None else cache_path()
    return derived(
        ("rates", BUNDLED_PATH, fetched),
        sig=(file_sig(BUNDLED_PATH), seen),
        compute=lambda: _rates_at(BUNDLED_PATH, fetched),
    )


def _rates_at(bundled_path: Path, fetched_path: Path | None) -> RateTable:
    """The shipped floor refuses, the bug it is; a fetched cache that does not load is dropped, so the
    next refresh fetches it again, and the floor prices meanwhile."""
    try:
        fetched = _read_rates(fetched_path)
    except ValidationError as exc:
        assert fetched_path is not None, "no fetch reads no row, so none refuses"
        logger.warning("spend: dropped %s, which does not load (%s)", fetched_path, exc)
        unlink_robust(fetched_path)
        fetched = {}
    # The fetch lies OVER the floor: a row upstream never listed keeps the price the bundle gave it.
    return _models_to_rates(_read_rates(bundled_path) | fetched)


def lookup_rate(model: str | None, provider: str | None) -> Rate | None:
    """A price belongs to the (provider, model) PAIR, so a call naming no provider has none."""
    if not model or not provider:
        return None
    return load_rates().rates.get((provider.lower().strip(), model.lower().strip()))


def priced_pair(
    model: str | None, provider: str | None, *, names_provider: bool
) -> tuple[str | None, str | None]:
    """The ``(model, provider)`` a node's sends are priced as; a connector whose model strings
    name their provider has one with no ``provider`` split here, off the rate table alone: no
    operator-typed string reaches a library's provider resolution."""
    if model is None or not names_provider or provider is not None:
        return model, provider
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
# Under the data root: one file per route a fetch answered, so every process reads what any
# process priced, a served menu what a run's child process did.
ROUTE_ANSWERS_DIR = "route_ceilings"


@dataclass(frozen=True)
class RouteAnswer:
    """What a fetch of a route's hosts answered, and when: no ceiling where no host prices it. It
    names its route and hosts, which the one refresher reads back (`refresh_route_answers`), and
    *since*, its route's first answer of that fate, which a re-fetch answering the same keeps."""

    route: str
    hosts: tuple[str, ...] | None
    at: float
    since: float
    ceiling: RateCeiling | None


ROUTE_ANSWER = TypeAdapter(RouteAnswer)
_OUTAGE_MEMO_S = 60.0
_ROUTE_OUTAGE: dict[Path, tuple[float, str]] = {}


class PriceListUnreachableError(SendRefusedError):
    """An outage, never "no rate bounds this call": a send needing the price is refused unsent."""

    def __init__(self, message: str) -> None:
        super().__init__(message, category=ErrorCategory.PRICE_LIST_UNREACHABLE)


async def rate_ceiling(
    model: str, provider: str, *, hosts: tuple[str, ...] | None = None
) -> RateCeiling | None:
    """Off the event loop only where a gateway's price list must be FETCHED."""
    key = _route_model(model) if provider.lower() == "openrouter" else None
    if key is None:
        return route_ceiling(model, provider, hosts=hosts)
    if (hit := _remembered(_memo_key(key, hosts))) is not _MISS:
        return cast("RateCeiling | None", hit)
    return await asyncio.to_thread(route_ceiling, model, provider, hosts=hosts)


def route_ceiling(
    model: str, provider: str, *, hosts: tuple[str, ...] | None = None
) -> RateCeiling | None:
    """A gateway's ceiling is its DEAREST eligible host: whichever answers bills within the hold."""
    if provider.lower() == "openrouter":
        return _openrouter_ceiling(model, hosts)
    rate = lookup_rate(model, provider)
    if rate is None:
        return None
    return RateCeiling(
        tiers=(PriceTier(0, max(rate.input, rate.cache_write or 0.0), rate.output),),
        max_output=rate.max_output,
    )


def route_priced(model: str, provider: str, *, hosts: tuple[str, ...] | None = None) -> bool | None:
    """Whether a dollar ceiling can hold a send on this route: the spend book's own question, asked
    before anything is sent. ``None`` while the price list is unreachable — an outage, not a verdict."""
    try:
        return route_ceiling(model, provider, hosts=hosts) is not None
    except PriceListUnreachableError:
        return None


def remembered_priced(model: str, provider: str) -> bool | None:
    """`route_priced` off what is already known, never a fetch: the last answer any process's
    fetch gave, which the API's sweep keeps fresh (`refresh_route_answers`). ``None`` for a route
    no answer stands for, as through an outage, which the Start admits through."""
    if provider.lower() != "openrouter":
        return lookup_rate(model, provider) is not None
    key = _route_model(model)
    if key is None:
        return False
    answer = _route_answer(key)
    return None if answer is None else answer.ceiling is not None


def refresh_route_answers() -> None:
    """Blocking, the API sweep's alone, one rule for both fates: an answer a day past its *since*
    goes, which reads as no answer, offered, and fetched again by its route's next send; a younger
    unpriced one past `_OUTAGE_MEMO_S` is fetched again, a host may price its route since. No row
    outlives a day of its answer, and one row's failure ends that row alone."""
    answers = user_data_root() / ROUTE_ANSWERS_DIR
    for path in sorted(answers.glob("*.json")) if answers.is_dir() else ():
        try:
            # An outage keeps the answer it had.
            with contextlib.suppress(PriceListUnreachableError):
                _refresh(path)
        except OSError as exc:
            logger.warning("spend: route answer %s left as it stood: %s", path.name, exc)


def _refresh(path: Path) -> None:
    if (answer := _read_answer(path)) is None:
        return
    if time.time() - answer.since >= _TTL_SECONDS:
        unlink_robust(path)
    elif answer.ceiling is None and time.time() - answer.at >= _OUTAGE_MEMO_S:
        _openrouter_ceiling(answer.route, answer.hosts)


def _route_answer_path(memo_key: str) -> Path:
    # Named by its digest: a route or a host pin set may run past what a file name holds.
    return user_data_root() / ROUTE_ANSWERS_DIR / f"{stable_hash(memo_key)}.json"


def _route_answer(memo_key: str) -> RouteAnswer | None:
    return _read_answer(_route_answer_path(memo_key))


def _read_answer(path: Path) -> RouteAnswer | None:
    """A row that does not load is the cache its row declares: dropped, and no answer."""
    raw = read_bytes_optional(path)
    if raw is None:
        return None
    try:
        return ROUTE_ANSWER.validate_json(raw)
    except ValidationError:
        unlink_robust(path)
        return None


def _route_model(model: str) -> str | None:
    """``None`` for a suffix that changes what the call BUYS (``:online`` adds priced search)."""
    base, _, suffix = model.strip().lower().partition(":")
    return base if not suffix or suffix in _ROUTE_SORTS else None


_MISS = object()


def _memo_key(key: str, pinned: tuple[str, ...] | None) -> str:
    return f"{key}@{','.join(pinned)}" if pinned else key


def _remembered(memo_key: str) -> RateCeiling | None | object:
    """``_MISS`` where only a fetch answers; a remembered outage raises."""
    answer = _route_answer(memo_key)
    # An unpriced answer is re-asked sooner: a host listing the model later prices it.
    if answer is not None and time.time() - answer.at < (
        _TTL_SECONDS if answer.ceiling is not None else _OUTAGE_MEMO_S
    ):
        return answer.ceiling
    outage = _ROUTE_OUTAGE.get(_route_answer_path(memo_key))
    if outage is not None and time.time() - outage[0] < _OUTAGE_MEMO_S:
        raise PriceListUnreachableError(outage[1])
    return _MISS


def _openrouter_ceiling(model: str, pinned: tuple[str, ...] | None) -> RateCeiling | None:
    key = _route_model(model)
    if key is None:
        return None
    memo_key = _memo_key(key, pinned)
    if (hit := _remembered(memo_key)) is not _MISS:
        return cast("RateCeiling | None", hit)
    try:
        ceiling = _fetch_route_ceiling(key, pinned)
    except PriceListUnreachableError as exc:
        _ROUTE_OUTAGE[_route_answer_path(memo_key)] = (time.time(), str(exc))
        raise
    now = time.time()
    held = _route_answer(memo_key)
    since = held.since if held is not None and (held.ceiling is None) == (ceiling is None) else now
    answer = RouteAnswer(key, pinned, now, since, ceiling)
    write_bytes(_route_answer_path(memo_key), ROUTE_ANSWER.dump_json(answer))
    return ceiling


def _either(own: float | None, base: float | None) -> float | None:
    return base if own is None else own


def _rate(price: float | None, surcharge: float | None) -> float:
    # An unlisted surcharge is none; an unlisted price leaves the host unpriced.
    if price is None:
        raise ValueError("no price is listed")
    return price if surcharge is None else max(price, surcharge)


class _Price(StrictModel):
    """A price row as OpenRouter lists it, USD per token; ``None`` is unlisted."""

    prompt: float | None = None
    completion: float | None = None
    input_cache_write: float | None = None
    internal_reasoning: float | None = None

    def tier(self, start: int, base: _Price) -> PriceTier:
        """Its prices from *start* input tokens on; a price it leaves unlisted is *base*'s."""
        return PriceTier(
            start,
            _rate(
                _either(self.prompt, base.prompt),
                _either(self.input_cache_write, base.input_cache_write),
            ),
            _rate(
                _either(self.completion, base.completion),
                _either(self.internal_reasoning, base.internal_reasoning),
            ),
        )


class _PriceOverride(_Price):
    # Listed: it prices prompts from this length on. Unlisted: a time-of-day row, from token 0.
    min_prompt_tokens: int | None = None


class _Pricing(_Price):
    request: float = 0.0
    overrides: list[_PriceOverride] = Field(default_factory=list)


class _Endpoint(StrictModel):
    tag: str = ""
    pricing: _Pricing = _Pricing()
    max_completion_tokens: int | None = None
    context_length: int | None = None

    def serves(self, pinned: tuple[str, ...]) -> bool:
        """OpenRouter's own matching: a bare slug admits each of its endpoints, a tagged one only
        itself."""
        return any(self.tag == host or self.tag.startswith(f"{host}/") for host in pinned)


class _Hosts(StrictModel):
    endpoints: list[_Endpoint] = Field(default_factory=list)


class _EndpointsPayload(StrictModel):
    """OpenRouter's `/models/{model}/endpoints`, the fields a ceiling reads."""

    data: _Hosts


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
    try:
        listed = _EndpointsPayload.model_validate(payload, extra="ignore").data.endpoints
    except ValidationError:
        logger.warning("spend: %s lists hosts no ceiling can read", model)
        return None
    endpoints = [e for e in listed if not pinned or e.serves(pinned)]
    if not endpoints:
        logger.warning("spend: OpenRouter lists no host for %s among %s", model, pinned or "any")
        return None
    try:
        hosts = [_host_tiers(e.pricing) for e in endpoints]
    except ValueError:
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
    replies = [e.max_completion_tokens or e.context_length for e in endpoints]
    return RateCeiling(
        tiers=tiers,
        per_request=max(request for _, request in hosts),
        max_output=None if None in replies else max(r for r in replies if r is not None),
    )


def _host_tiers(pricing: _Pricing) -> tuple[list[PriceTier], float]:
    by_time = [o for o in pricing.overrides if o.min_prompt_tokens is None]
    # A time-of-day override (`utc_start`/`utc_end`) prices from token 0, so the dearest is the base.
    base = [p.tier(0, pricing) for p in (pricing, *by_time)]
    tiers = [PriceTier(0, max(t.input for t in base), max(t.output for t in base))]
    tiers += [
        o.tier(o.min_prompt_tokens, pricing)
        for o in pricing.overrides
        if o.min_prompt_tokens is not None
    ]
    return tiers, pricing.request


def _host_at(tiers: list[PriceTier], input_tokens: int) -> PriceTier:
    return max(
        (t for t in tiers if t.from_input_tokens <= input_tokens),
        key=lambda t: t.from_input_tokens,
    )
