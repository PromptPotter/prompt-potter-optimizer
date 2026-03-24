"""Loader for optimizer meta-prompt templates.

Optimizer prompts are OptSearchPoint instances with ``{{variable}}`` runtime
placeholders in their prompt fields. The loader tries Langfuse first
(by ``production`` label with SDK-side caching), then falls back to local
JSON defaults in ``optimizer_prompts/``.

Usage::

    from api.config.optimizer_prompt_loader import load_optimizer_prompt

    osp = load_optimizer_prompt("critique_negative")
    prompt = osp.compile_prompt(accuracy_pct="85.0%", n_failures="5", ...)
"""

import functools
import json
import logging
from pathlib import Path

from api.models.opt_search_point import OptSearchPoint

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).parent / "optimizer_prompts"


# ---------------------------------------------------------------------------
# Local JSON loading (LRU-cached — files don't change at runtime)
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=32)
def _load_local(name: str) -> OptSearchPoint:
    path = _PROMPT_DIR / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return OptSearchPoint(**data)


# ---------------------------------------------------------------------------
# Langfuse integration (optional, graceful degradation)
# ---------------------------------------------------------------------------

_LANGFUSE_PREFIX = "optimizer_"
_LANGFUSE_CACHE_TTL = 300  # seconds


def _try_langfuse(name: str) -> OptSearchPoint | None:
    """Fetch from Langfuse prompt registry by *production* label.

    Returns ``None`` on any failure (credentials missing, network error,
    prompt not found).  Langfuse SDK caches internally for
    ``_LANGFUSE_CACHE_TTL`` seconds.
    """
    try:
        from api.services.obs.langfuse_client import LangfuseLogger

        lf = LangfuseLogger.get_instance()
        if not lf.enabled or not lf.client:
            return None

        prompt_client = lf.client.get_prompt(
            name=f"{_LANGFUSE_PREFIX}{name}",
            label="production",
            cache_ttl_seconds=_LANGFUSE_CACHE_TTL,
        )
        config = getattr(prompt_client, "config", None)
        if not config or not isinstance(config, dict):
            return None

        return OptSearchPoint(**config)
    except Exception:
        logger.debug("Langfuse prompt fetch failed for %s", name, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_optimizer_prompt(name: str) -> OptSearchPoint:
    """Load an optimizer prompt template as an OptSearchPoint.

    Resolution order:
    1. Langfuse prompt registry (by ``production`` label, SDK-cached)
    2. Local JSON default in ``api/config/optimizer_prompts/{name}.json``
    """
    lf_prompt = _try_langfuse(name)
    return lf_prompt or _load_local(name)


def push_all_to_langfuse(*, label: str = "production") -> dict[str, bool]:
    """Push all local JSON defaults to the Langfuse prompt registry.

    For each JSON file, creates a new prompt version with:
    - ``prompt`` = assembled template text (``render_prompt()``) for Langfuse UI display
    - ``config`` = full OptSearchPoint dict (for reconstruction on fetch)
    - ``labels`` = ``[label]``
    - ``tags`` = ``["optimizer", "meta-prompt"]``

    Returns ``{name: success_bool}`` mapping.
    """
    from api.services.obs.langfuse_client import LangfuseLogger

    lf = LangfuseLogger.get_instance()
    if not lf.enabled or not lf.client:
        logger.warning("push_all_to_langfuse: Langfuse not available")
        return {}

    results: dict[str, bool] = {}
    for name in list_optimizer_prompts():
        try:
            osp = _load_local(name)
            lf.client.create_prompt(
                name=f"{_LANGFUSE_PREFIX}{name}",
                prompt=osp.render_prompt(),
                config=osp.model_dump(
                    exclude={"critique_text", "critique", "thinking_styles",
                             "escalation_journal", "warning_inventory",
                             "l2_directive", "content_hashes",
                             "degradation_reset_count", "backend_warning_emitted"},
                ),
                labels=[label],
                tags=["optimizer", "meta-prompt"],
                commit_message=f"Push local default for {name}",
            )
            results[name] = True
            logger.info("Pushed optimizer prompt %s to Langfuse", name)
        except Exception:
            logger.warning("Failed to push %s to Langfuse", name, exc_info=True)
            results[name] = False

    # Clear local cache so next load picks up Langfuse versions
    _load_local.cache_clear()
    return results


def list_optimizer_prompts() -> list[str]:
    """List available optimizer prompt names from local JSON files."""
    return sorted(p.stem for p in _PROMPT_DIR.glob("*.json"))
