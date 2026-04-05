"""Loader for optimizer meta-prompt templates.

Optimizer prompts are ``PromptTemplate`` instances with ``{{variable}}``
runtime placeholders in their prompt fields. The loader tries Langfuse
first (by ``production`` label with SDK-side caching), then falls back to
local JSON defaults in ``optimizer_prompts/``.

Usage::

    from promptpotter.config.optimizer_prompt_loader import load_optimizer_prompt

    tpl = load_optimizer_prompt("critique_negative")
    prompt = tpl.compile_prompt(accuracy_pct="85.0%", n_failures="5", ...)
"""

import functools
import json
import logging
from pathlib import Path

from promptpotter.models.opt_search_point import PromptTemplate

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).parent / "optimizer_prompts"


# ---------------------------------------------------------------------------
# Local JSON loading (LRU-cached — files don't change at runtime)
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=32)
def _load_local(name: str) -> PromptTemplate:
    path = _PROMPT_DIR / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return PromptTemplate(**data)


# ---------------------------------------------------------------------------
# Langfuse integration (optional, graceful degradation)
# ---------------------------------------------------------------------------

_LANGFUSE_PREFIX = "optimizer_"
_LANGFUSE_CACHE_TTL = 300  # seconds


def _try_langfuse(name: str) -> PromptTemplate | None:
    """Fetch from Langfuse prompt registry by *production* label.

    Returns ``None`` on any failure (credentials missing, network error,
    prompt not found).  Langfuse SDK caches internally for
    ``_LANGFUSE_CACHE_TTL`` seconds.
    """
    try:
        from promptpotter.config.settings import settings

        if not settings.LANGFUSE_PROMPTS_ENABLED:
            return None

        from promptpotter.services.tracing.langfuse_client import LangfuseLogger

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

        return PromptTemplate(**config)
    except Exception:
        logger.debug("Langfuse prompt fetch failed for %s", name, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_optimizer_prompt(name: str) -> PromptTemplate:
    """Load an optimizer prompt template as a PromptTemplate.

    Resolution order:
    1. Langfuse prompt registry (by ``production`` label, SDK-cached)
    2. Local JSON default in ``promptpotter/config/optimizer_prompts/{name}.json``
    """
    lf_prompt = _try_langfuse(name)
    return lf_prompt or _load_local(name)


def push_all_to_langfuse(*, label: str = "production") -> dict[str, bool]:
    """Push all local JSON defaults to the Langfuse prompt registry.

    For each JSON file, creates a new prompt version with:
    - ``prompt`` = assembled template text (``render()``) for Langfuse UI display
    - ``config`` = full PromptTemplate dict (for reconstruction on fetch)
    - ``labels`` = ``[label]``
    - ``tags`` = ``["optimizer", "meta-prompt"]``

    Returns ``{name: success_bool}`` mapping.
    """
    from promptpotter.services.tracing.langfuse_client import LangfuseLogger

    lf = LangfuseLogger.get_instance()
    if not lf.enabled or not lf.client:
        logger.warning("push_all_to_langfuse: Langfuse not available")
        return {}

    results: dict[str, bool] = {}
    for name in list_optimizer_prompts():
        try:
            tpl = _load_local(name)
            lf.client.create_prompt(
                name=f"{_LANGFUSE_PREFIX}{name}",
                prompt=tpl.render(),
                config=tpl.model_dump(),
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
