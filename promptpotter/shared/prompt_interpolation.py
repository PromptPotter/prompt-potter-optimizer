"""Template variable interpolation for job prompts.

Prompt templates can contain ``{{variable}}`` placeholders that are
filled at eval time from ``query_data`` fields.  This keeps prompts
dataset-agnostic while supporting per-query injection of context,
few-shot examples, format hints, etc.

Syntax
------
Uses double-brace ``{{name}}`` — same as the optimizer meta-prompt
convention in ``PromptTemplate.compile_prompt()``.

Available variables come from the dataset item dict (``query_data``).
Every dataset provides at least ``query`` and ``ground_truth``; loaders
can add arbitrary extra fields (``context``, ``options``, ``passage``,
etc.).  ``ground_truth`` is always excluded to prevent data leakage.

If a prompt contains no ``{{...}}`` tokens, nothing happens — fully
backward-compatible.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_TEMPLATE_VAR_RE = re.compile(r"\{\{(\w+)\}\}")

# Fields that must never be interpolated into prompts (answer leakage).
_EXCLUDED_FIELDS: frozenset[str] = frozenset(
    {
        "ground_truth",
        "hit",
        "score",
        "error",
        "source_sheet",
    }
)


def extract_template_variables(text: str) -> list[str]:
    """Return sorted list of ``{{variable}}`` names found in *text*."""
    return sorted(set(_TEMPLATE_VAR_RE.findall(text)))


def interpolate_prompt(text: str, variables: dict[str, Any]) -> str:
    """Replace ``{{key}}`` placeholders in *text* from *variables*.

    - Keys in ``_EXCLUDED_FIELDS`` are silently skipped (data-leakage guard).
    - Missing keys are left as-is (no crash, logged at debug level).
    - Values are stringified via ``str()``.
    """
    expected = set(_TEMPLATE_VAR_RE.findall(text))
    if not expected:
        return text

    safe_vars = {k: v for k, v in variables.items() if k not in _EXCLUDED_FIELDS}
    for key in expected:
        if key in safe_vars:
            text = text.replace("{{" + key + "}}", str(safe_vars[key]))
        else:
            logger.debug("Template variable {{%s}} not in query_data — left as-is", key)
    return text


def interpolate_pipeline_params(
    pipeline_params: dict[str, Any],
    query_data: dict[str, Any],
) -> dict[str, Any]:
    """Return a shallow copy of *pipeline_params* with prompts interpolated.

    Walks every node config dict.  If a node has a ``"prompt"`` key whose
    value contains ``{{...}}`` tokens, those tokens are replaced from
    *query_data*.  Other keys are untouched.

    The original dict is never mutated.
    """
    has_templates = False
    for v in pipeline_params.values():
        if isinstance(v, dict) and "prompt" in v:
            prompt = v["prompt"]
            if isinstance(prompt, str) and _TEMPLATE_VAR_RE.search(prompt):
                has_templates = True
                break

    if not has_templates:
        return pipeline_params  # fast path — no copy needed

    out = dict(pipeline_params)
    for node_name, node_cfg in out.items():
        if not isinstance(node_cfg, dict) or "prompt" not in node_cfg:
            continue
        prompt = node_cfg["prompt"]
        if not isinstance(prompt, str):
            continue
        interpolated = interpolate_prompt(prompt, query_data)
        if interpolated is not prompt:
            out[node_name] = {**node_cfg, "prompt": interpolated}
    return out
