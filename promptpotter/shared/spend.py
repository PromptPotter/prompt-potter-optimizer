"""Token → USD resolution — bundled floor + runtime refresh, no third-party dep.

Resolution order is wire cost (``override_usd``, currently OpenRouter only) → runtime
cache → the checked-in floor; see :func:`compute_usd` and :func:`load_rates`. Only
``urllib`` + ``json``, by choice — a cost-tracking package is a supply-chain surface for
a table we can vendor from LiteLLM's public price backup (MIT, BerriAI). The fetched
JSON stays data: keys become dict lookups, values floats, nothing is ever ``eval``-ed.
"""

from __future__ import annotations

import functools
import json
import logging
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from promptpotter.config.paths import user_data_root
from promptpotter.shared.clock import utcnow_iso

logger = logging.getLogger(__name__)

__all__ = [
    "BUNDLED_PATH",
    "CACHE_PATH",
    "UPSTREAM_URL",
    "compute_usd",
    "load_rates",
    "lookup_rate",
    "refresh_rates",
]


UPSTREAM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "litellm/model_prices_and_context_window_backup.json"
)
# Beside the rest of the user data, through the one resolver — it hand-rolled
# ``Path.home()/".promptpotter"``, which ignores ``$PROMPTPOTTER_HOME`` and, on Windows,
# misses ``%LOCALAPPDATA%`` where every other user file lands.
CACHE_PATH = user_data_root() / "rates.json"
BUNDLED_PATH = Path(__file__).parent / "data" / "rates.json"
_KEEP_FIELDS = ("input_cost_per_token", "output_cost_per_token", "litellm_provider", "mode")
_FETCH_TIMEOUT_S = 30.0
_MAX_BODY_BYTES = 8 * 1024 * 1024  # upstream is ~1 MB; cap defends against runaway payload
_TTL_SECONDS = 24 * 60 * 60


def _strip_upstream(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep only cost-bearing entries with the four fields we use."""
    out: dict[str, Any] = {}
    for name, body in raw.items():
        if not isinstance(body, dict):
            continue
        if "input_cost_per_token" not in body and "output_cost_per_token" not in body:
            continue
        out[name] = {k: body[k] for k in _KEEP_FIELDS if k in body}
    return out


def _wrap(models: dict[str, Any]) -> dict[str, Any]:
    return {"fetched_at": utcnow_iso(), "models": models}


def _cache_fresh() -> bool:
    """True if ``CACHE_PATH`` exists and was fetched within the TTL window."""
    if not CACHE_PATH.exists():
        return False
    try:
        raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(raw, dict):
        return False
    fetched_at = raw.get("fetched_at")
    if not isinstance(fetched_at, str):
        return False
    try:
        dt = datetime.fromisoformat(fetched_at)
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return (datetime.now(UTC) - dt).total_seconds() < _TTL_SECONDS


def refresh_rates(*, force: bool = False, timeout: float = _FETCH_TIMEOUT_S) -> bool:
    """Fetch upstream → strip → write ``CACHE_PATH``. Return True on success.

    No-op (returns True) when the cache is younger than ``_TTL_SECONDS``
    unless ``force=True``. Network failure logs a warning and leaves any
    prior cache in place so a startup with no internet still resolves
    rates from the last successful fetch — and if no cache exists yet,
    ``load_rates`` falls through to the bundled floor.
    """
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
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(_wrap(stripped), indent=0, separators=(",", ":")),
        encoding="utf-8",
    )
    load_rates.cache_clear()
    logger.info("spend: refreshed %d model rates → %s", len(stripped), CACHE_PATH)
    return True


def _read_models(path: Path) -> dict[str, Any] | None:
    """Return the ``models`` dict from a wrapped rates file, or None."""
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("spend: failed to load %s (%s)", path, exc)
        return None
    if not isinstance(raw, dict):
        return None
    models = raw.get("models")
    return models if isinstance(models, dict) else None


def _models_to_rates(models: dict[str, Any]) -> dict[str, tuple[float, float]]:
    rates: dict[str, tuple[float, float]] = {}
    for name, body in models.items():
        if not isinstance(body, dict):
            continue
        in_c = body.get("input_cost_per_token")
        out_c = body.get("output_cost_per_token")
        if in_c is None and out_c is None:
            continue
        rates[name.lower()] = (float(in_c or 0.0), float(out_c or 0.0))
    return rates


@functools.lru_cache(maxsize=1)
def load_rates() -> dict[str, tuple[float, float]]:
    """Load rates with cache → bundled-floor → empty fallback chain.

    The cache (``CACHE_PATH``) wins when present so operators get fresh
    pricing. ``BUNDLED_PATH`` is the floor — checked into the repo so a
    fresh install with no internet still resolves rates. Returns ``{}``
    only when both are missing/unreadable; callers leave
    ``rate_known=False`` and the spend chip falls back to a token-count
    display. ``refresh_rates`` clears this cache after a successful write.
    """
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
    return {}


def lookup_rate(model: str | None, provider: str | None = None) -> tuple[float, float] | None:
    """``(input_per_token, output_per_token)`` for *model* as billed by *provider*, or None.

    **A price is a property of the (provider, model) PAIR, and the table is keyed that
    way** — LiteLLM registers eight distinct ``*/deepseek-v4-flash`` entries spanning
    $0.10/$0.20 to $0.25/$1.75. So the provider is the first half of the question, not a
    hint: given one, only a key that agrees with it can answer, and a bare key is not a
    fallback but a different vendor's price list.

    This replaced a chain that matched across providers by suffix and then by bare
    substring. It resolved an OpenRouter ``deepseek/deepseek-v4-flash`` call onto
    DeepSeek's own first-party key at $0.14/$0.28 against OpenRouter's listed
    $0.088/$0.176 — ~1.6x, on the route where the wire returns no cost at all, so the
    estimate was the only number and nothing could contradict it. Which of the eight it
    picked was decided by key length. A wrong number that cannot be told from a right one
    is worse than ``None``, which at least arms the "USD cap inactive" warning.

    Threading the provider also RETIRES the suffix hack rather than replacing it: Groq
    answers ``response.model = "openai/gpt-oss-120b"`` while the table keys it
    ``groq/openai/gpt-oss-120b``, and ``provider="groq"`` composes that exactly.

    A routing suffix (``:nitro``, ``:floor``) stays deliberately unpriceable. It selects a
    different upstream provider with its own rate — measured, ``xiaomi/mimo-v2.5:nitro``
    billed ~6x its base listing — so the base-model price is not an approximation of it.
    """
    if not model:
        return None
    needle = model.lower().strip()
    rates = load_rates()
    if provider:
        prefix = provider.lower().strip()
        # Some wires echo the provider back inside the model id, in either separator
        # ("groq:openai/gpt-oss-120b"; "deepseek/deepseek-v4-flash" called AT DeepSeek).
        # Stripping it is normalization, not a fallback — it makes the composition below
        # idempotent instead of producing "deepseek/deepseek/deepseek-v4-flash".
        needle = needle.removeprefix(f"{prefix}:").removeprefix(f"{prefix}/")
        # Any colon SURVIVING that is a route selector, not a provider — unpriceable above.
        if ":" in needle:
            return None
        qualified = rates.get(f"{prefix}/{needle}")
        if qualified is not None or "/" in needle:
            # A model id carrying a "/" already names a vendor's namespace, so reading it as
            # a bare key means pricing our call off THAT vendor's list — the whole defect
            # ("deepseek/deepseek-v4-flash" is simultaneously DeepSeek's own key and
            # OpenRouter's model id, at different prices). Refuse; unpriced is the answer.
            return qualified
        # No "/" left ⇒ nothing to collide with, and the bare namespace is where the table
        # keeps first-party OpenAI and Anthropic (523 unprefixed keys, "gpt-4o" not
        # "openai/gpt-4o"). That is a naming convention, not a fallback.
        return rates.get(needle)
    # Provider unknown (a historical ledger row predating the field): an exact key is the
    # only honest answer — it is the one match that cannot belong to somebody else.
    return rates.get(needle)


def compute_usd(
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    *,
    override_usd: float | None = None,
    provider: str | None = None,
) -> float | None:
    """USD cost for one call. ``override_usd`` short-circuits the rate lookup.

    Returns None when no override is given and no rate is on file —
    callers leave ``rate_known=False`` and fall back to a token-count
    display for the spend chip. ``provider`` is who billed it; without it only an
    exact key resolves, because a price belongs to the pair (see :func:`lookup_rate`).
    """
    if override_usd is not None:
        return float(override_usd)
    rate = lookup_rate(model, provider)
    if rate is None:
        return None
    in_per_tok, out_per_tok = rate
    return input_tokens * in_per_tok + output_tokens * out_per_tok
