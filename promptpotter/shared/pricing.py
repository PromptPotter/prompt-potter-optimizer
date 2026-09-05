"""Token → USD — wire cost (``override_usd``) → runtime cache → the checked-in floor. stdlib only,
by choice: the table is vendored from LiteLLM's public price backup (MIT, BerriAI), never eval-ed."""

from __future__ import annotations

import contextlib
import functools
import json
import logging
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from promptpotter.config.paths import user_data_root

logger = logging.getLogger(__name__)

__all__ = [
    "BUNDLED_PATH",
    "CACHE_PATH",
    "UPSTREAM_URL",
    "Rate",
    "compute_usd",
    "load_rates",
    "lookup_rate",
    "refresh_rates",
    "refresh_rates_in_background",
]


@dataclass(frozen=True)
class Rate:
    """Per-token prices for one ``(provider, model)`` pair. An input token bills at one of THREE
    rates — written to the provider's prompt cache, read back from it, or neither. A cache rate is
    ``None`` where upstream lists no tier, which :func:`compute_usd` bills at ``input``."""

    input: float
    output: float
    cache_write: float | None = None
    cache_read: float | None = None


UPSTREAM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "litellm/model_prices_and_context_window_backup.json"
)
# Beside the rest of the user data, through the one resolver — it hand-rolled
# ``Path.home()/".promptpotter"``, which ignores ``$PROMPTPOTTER_HOME`` and, on Windows,
# misses ``%LOCALAPPDATA%`` where every other user file lands.
CACHE_PATH = user_data_root() / "rates.json"
BUNDLED_PATH = Path(__file__).parent / "data" / "rates.json"
_KEEP_FIELDS = (
    "input_cost_per_token",
    "output_cost_per_token",
    "cache_creation_input_token_cost",
    "cache_read_input_token_cost",
    "litellm_provider",
    "mode",
)
_FETCH_TIMEOUT_S = 30.0
_MAX_BODY_BYTES = 8 * 1024 * 1024  # upstream is ~1 MB; cap defends against runaway payload
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
    """Age off the file's own mtime, which the atomic replace below stamps at write time. The table
    is ~1 MB and this fires on every launch, so parsing it to read a timestamp inside it cost a
    second full parse for a number the filesystem already had — and left the age readable by nothing
    but this function, where ``ls -l`` now answers it."""
    try:
        age_s = time.time() - CACHE_PATH.stat().st_mtime
    except OSError:
        return False
    return age_s < _TTL_SECONDS


def refresh_rates(*, force: bool = False, timeout: float = _FETCH_TIMEOUT_S) -> bool:
    """Fetch upstream → strip → write ``CACHE_PATH``; ``True`` on success. A network failure logs and
    leaves the prior cache — with none, :func:`load_rates` falls through to the bundled floor."""
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
    # Hand-rolled rather than through ``store/io.py::write_json`` because this module is
    # stdlib-only and one layer below it. The tmp name must be UNIQUE: this runs on a daemon
    # thread per launch and an L4 run starts one per inner campaign, so a fixed name lets two
    # writers interleave into one file — and a torn table then ages as FRESH off its own mtime.
    fd, tmp = tempfile.mkstemp(dir=CACHE_PATH.parent, prefix=f"{CACHE_PATH.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"models": stripped}, indent=0, separators=(",", ":")))
        os.replace(tmp, CACHE_PATH)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    load_rates.cache_clear()
    logger.info("spend: refreshed %d model rates → %s", len(stripped), CACHE_PATH)
    return True


def refresh_rates_in_background() -> None:
    """Start :func:`refresh_rates` on a daemon thread and return at once — nothing on the run path
    consumes it. The write is atomic, so a reader either sees the prior table or the new one."""
    threading.Thread(target=refresh_rates, name="rates-refresh", daemon=True).start()


def _read_models(path: Path) -> dict[str, Any] | None:
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


def _optional_cost(body: dict[str, Any], key: str) -> float | None:
    value = body.get(key)
    return float(value) if value is not None else None


def _models_to_rates(models: dict[str, Any]) -> dict[str, Rate]:
    rates: dict[str, Rate] = {}
    for name, body in models.items():
        if not isinstance(body, dict):
            continue
        in_c = body.get("input_cost_per_token")
        out_c = body.get("output_cost_per_token")
        if in_c is None and out_c is None:
            continue
        rates[name.lower()] = Rate(
            input=float(in_c or 0.0),
            output=float(out_c or 0.0),
            cache_write=_optional_cost(body, "cache_creation_input_token_cost"),
            cache_read=_optional_cost(body, "cache_read_input_token_cost"),
        )
    return rates


@functools.lru_cache(maxsize=1)
def load_rates() -> dict[str, Rate]:
    """Rates from the cache, else the bundled floor, else ``{}`` — on which callers leave
    ``rate_known=False`` and the spend chip shows a token count instead of a price."""
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


def lookup_rate(model: str | None, provider: str | None = None) -> Rate | None:
    """The per-token :class:`Rate` as billed by *provider*, or ``None``. **A price belongs to the
    (provider, model) PAIR** — a bare key is another vendor's list; ``:nitro`` is unpriceable by design."""
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
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """USD for one call; ``override_usd`` short-circuits the lookup. ``None`` when no rate is on file,
    and *provider* is who billed it — without one only an exact key resolves. The two cache counts
    are SUBSETS of *input_tokens* (every client normalizes to that), so they are re-priced OUT of
    it rather than added on top, and an absent tier reproduces the pre-cache number exactly."""
    if override_usd is not None:
        return float(override_usd)
    rate = lookup_rate(model, provider)
    if rate is None:
        return None
    # `cache_read` / `cache_write`, never `cached_*`: across this package `cached` is the boolean
    # "we replayed our own archive and no provider was reached", and these are the opposite fact —
    # a provider DID serve the call and discounted part of its input. Same collision that kept the
    # harbor count out of the ledger; this was the last spelling of it left.
    cache_read = max(0, int(cache_read_tokens))
    cache_write = max(0, int(cache_write_tokens))
    full_rate_input = max(0, input_tokens - cache_read - cache_write)
    return (
        full_rate_input * rate.input
        + cache_read * (rate.cache_read if rate.cache_read is not None else rate.input)
        + cache_write * (rate.cache_write if rate.cache_write is not None else rate.input)
        + output_tokens * rate.output
    )
