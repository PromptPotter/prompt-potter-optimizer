"""Token → USD resolution — bundled floor + runtime refresh, no third-party dep.

Three layers, in priority order:

1. **Wire passthrough.** When the LLM call reports a USD cost on the
   wire — currently only OpenRouter does this via
   ``response.usage.cost``/``total_cost`` — the value is forwarded as
   ``override_usd`` and the rate lookup is bypassed. This is the path
   that matches the operator's actual provider invoice.

2. **Runtime cache** at ``~/.promptpotter/rates.json``. ``refresh_rates()``
   fetches LiteLLM's public
   ``model_prices_and_context_window_backup.json``, strips it, and writes
   it wrapped as ``{"fetched_at": <iso>, "models": {...}}``. Subsequent
   calls within ``_TTL_SECONDS`` (24 h) are no-ops; the CLI calls this
   once per ``optimize`` start, so a fresh cache survives across runs.
   ``--refresh-rates`` forces a fetch.

3. **Bundled floor** at ``promptpotter/shared/data/rates.json`` — checked
   into the repo, same wrapped format. ``load_rates()`` falls back to
   this when no cache is present, so a fresh install with no internet
   still resolves rates. Bumped via PR alongside ordinary releases.

Threat surface is intentionally narrow: only ``urllib`` + ``json`` from
the standard library; no PyPI cost-tracking lib (LiteLLM's March 2026
incident motivated this stance, and ``tokencost`` pulls in unnecessary
``aiohttp`` + ``anthropic`` deps). The fetched JSON is treated as pure
data — keys become dict lookups, values become floats; nothing is ever
``eval`` / ``exec``-ed.

Source: ``https://raw.githubusercontent.com/BerriAI/litellm/main/litellm/model_prices_and_context_window_backup.json``
(MIT, BerriAI).
"""

from __future__ import annotations

import functools
import json
import logging
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

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
CACHE_PATH = Path.home() / ".promptpotter" / "rates.json"
BUNDLED_PATH = Path(__file__).parent / "data" / "rates.json"
_KEEP_FIELDS = ("input_cost_per_token", "output_cost_per_token", "litellm_provider", "mode")
_FETCH_TIMEOUT_S = 30.0
_MAX_BODY_BYTES = 8 * 1024 * 1024  # upstream is ~1 MB; cap defends against runaway payload
_TTL_SECONDS = 24 * 60 * 60


def _strip_upstream(raw: dict) -> dict:
    """Keep only cost-bearing entries with the four fields we use."""
    out: dict = {}
    for name, body in raw.items():
        if not isinstance(body, dict):
            continue
        if "input_cost_per_token" not in body and "output_cost_per_token" not in body:
            continue
        out[name] = {k: body[k] for k in _KEEP_FIELDS if k in body}
    return out


def _wrap(models: dict) -> dict:
    """Wrap the stripped models dict with a fetched-at timestamp for TTL."""
    return {"fetched_at": datetime.now(UTC).isoformat(timespec="seconds"), "models": models}


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


def _read_models(path: Path) -> dict | None:
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


def _models_to_rates(models: dict) -> dict[str, tuple[float, float]]:
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


def lookup_rate(model: str | None) -> tuple[float, float] | None:
    """Return ``(input_per_token, output_per_token)`` for *model*, or None.

    Match is suffix-fuzzy: an exact key wins, otherwise the first key
    that is contained in the needle (or vice versa) wins. Lowercased
    so wire variants like ``"groq:openai/gpt-oss-120b"`` resolve to
    LiteLLM's ``"groq/openai/gpt-oss-120b"`` without per-provider
    normalization.
    """
    if not model:
        return None
    needle = model.lower().strip()
    rates = load_rates()
    if needle in rates:
        return rates[needle]
    # Wire form "provider:model" → LiteLLM's "provider/model". Try the
    # slash-normalised whole and the post-colon tail, so e.g.
    # "groq:openai/gpt-oss-120b" resolves to "groq/openai/gpt-oss-120b" and
    # "openai:gpt-4o" resolves to "gpt-4o".
    if ":" in needle:
        normalised = needle.replace(":", "/")
        if normalised in rates:
            return rates[normalised]
        tail = needle.split(":", 1)[1]
        if tail in rates:
            return rates[tail]
    else:
        normalised = needle
    # Provider-less wire form: Groq returns ``response.model =
    # "openai/gpt-oss-120b"`` while LiteLLM keys it ``groq/openai/gpt-oss-120b``.
    # Match any registered key whose suffix is the needle (or its
    # colon-normalised form); pick the shortest such key so a bare
    # "openai/gpt-oss-120b" prefers a 2-segment match over a deeper one.
    suffix_matches: list[tuple[int, str]] = [
        (len(key), key)
        for key in rates
        if key.endswith("/" + needle) or key.endswith("/" + normalised)
    ]
    if suffix_matches:
        suffix_matches.sort()
        return rates[suffix_matches[0][1]]
    for key, rate in rates.items():
        if key in needle or needle.endswith(key) or key in normalised or normalised.endswith(key):
            return rate
    return None


def compute_usd(
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    *,
    override_usd: float | None = None,
) -> float | None:
    """USD cost for one call. ``override_usd`` short-circuits the rate lookup.

    Returns None when no override is given and no rate is on file —
    callers leave ``rate_known=False`` and fall back to a token-count
    display for the spend chip.
    """
    if override_usd is not None:
        return float(override_usd)
    rate = lookup_rate(model)
    if rate is None:
        return None
    in_per_tok, out_per_tok = rate
    return input_tokens * in_per_tok + output_tokens * out_per_tok
