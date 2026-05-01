"""L1 behaviour checks — programmatic conformance for one round of L1 output.

Each check is a pure ``(round_dict, ctx) -> CheckResult`` function with no
I/O. ``round_dict`` is one ``.runtime/cache/rounds/round_NNNN.json`` payload as
written by ``AuditTrailProjection.flush``; ``ctx`` carries the per-round
context the check needs (prior rounds, OSP at round-start, the three
``context_object`` items, the ``param_unlock_round`` knob).

Adding a new check is one function plus one entry in ``CHECK_REGISTRY``.
The registry is the single source of truth so ``review.md`` and the
``potter-review`` skill enumerate the same check set.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from promptpotter.config.settings import PROMPT_STRING_FIELDS, TASK_CONTEXT_OVERRIDES

__all__ = [
    "CHECK_REGISTRY",
    "PARAM_SCOPE_KEYS",
    "CheckContext",
    "CheckResult",
    "run_all_checks",
]


# Per-node LLM-call params that are NOT prompt fields. A round that touches
# these too early or while prompt fields are still being explored is
# violating the param-scope-discipline rule.
PARAM_SCOPE_KEYS: frozenset[str] = frozenset(
    {"temperature", "max_tokens", "reasoning_effort", "top_p"}
)


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    passed: bool
    evidence: str


@dataclass(frozen=True)
class CheckContext:
    """Context shared by all behaviour checks for one round.

    ``prior_rounds`` are the round dicts strictly before ``round_num``,
    in order; ``opt_search_point`` is the OSP snapshot at round-start
    (carries ``l2_directive`` + the live prompt fields); ``context_object``
    is the three task-decomposition strings L1's prompt was shown.
    """

    round_num: int
    prior_rounds: list[dict[str, Any]] = field(default_factory=list)
    opt_search_point: dict[str, Any] = field(default_factory=dict)
    context_object: list[str] = field(default_factory=list)
    param_unlock_round: int = 3


CheckFn = Callable[[dict[str, Any], CheckContext], CheckResult]


# --- helpers ---------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z_\-]{2,}")


def _l1_generate_variants(round_dict: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the variants list from a round dict; empty when L1 didn't fire."""
    nodes = round_dict.get("nodes") or {}
    node = nodes.get("l1_generate") or {}
    response = ((node.get("output") or {}).get("response")) or {}
    if isinstance(response, dict):
        variants = response.get("variants") or []
        return [v for v in variants if isinstance(v, dict)]
    return []


def _variant_text_blob(variant: dict[str, Any]) -> str:
    """All free-form variant text checks may scan against."""
    parts = [str(variant.get("changes_description") or "")]
    pp = variant.get("pipeline_params_override") or {}
    for key, value in pp.items():
        if key in PROMPT_STRING_FIELDS or key in TASK_CONTEXT_OVERRIDES:
            parts.append(str(value or ""))
    return "\n".join(parts).lower()


def _key_phrases(text: str, *, min_len: int = 4, max_phrases: int = 6) -> list[str]:
    """Extract candidate noun phrases from a directive — substring-match seeds."""
    seen: list[str] = []
    for token in _WORD_RE.findall(text or ""):
        lowered = token.lower()
        if len(lowered) < min_len or lowered in seen:
            continue
        seen.append(lowered)
        if len(seen) >= max_phrases:
            break
    return seen


# --- checks ----------------------------------------------------------------


def _check_context_object_honored(round_dict: dict[str, Any], ctx: CheckContext) -> CheckResult:
    """Each variant must reference at least one ``context_object`` item."""
    items = [c for c in ctx.context_object if isinstance(c, str) and c.strip()]
    variants = _l1_generate_variants(round_dict)
    if not items:
        return CheckResult("context_object_honored", True, "no context_object items to honour")
    if not variants:
        return CheckResult("context_object_honored", True, "no variants emitted")

    item_seeds = [_key_phrases(item) for item in items]
    misses: list[str] = []
    for v in variants:
        blob = _variant_text_blob(v)
        if not any(any(seed in blob for seed in seeds) for seeds in item_seeds if seeds):
            misses.append(str(v.get("variant_name") or "?"))
    if misses:
        return CheckResult(
            "context_object_honored",
            False,
            f"{len(misses)}/{len(variants)} variants ignored context_object: {misses[:3]}",
        )
    return CheckResult(
        "context_object_honored",
        True,
        f"{len(variants)}/{len(variants)} variants reference ≥1 context_object item",
    )


def _check_param_scope_discipline(round_dict: dict[str, Any], ctx: CheckContext) -> CheckResult:
    """No param-scope mutations while prompt-field exploration hasn't settled."""
    variants = _l1_generate_variants(round_dict)
    if not variants:
        return CheckResult("param_scope_discipline", True, "no variants emitted")

    early = ctx.round_num < ctx.param_unlock_round
    stale_field = _stale_prompt_field(ctx)
    if not early and stale_field is None:
        return CheckResult(
            "param_scope_discipline",
            True,
            f"unlocked: round ≥ {ctx.param_unlock_round} and no stale prompt field",
        )

    offenders: list[str] = []
    for v in variants:
        pp = v.get("pipeline_params_override") or {}
        if _touches_param_scope(pp):
            offenders.append(str(v.get("variant_name") or "?"))
    if offenders:
        reason = (
            f"round < {ctx.param_unlock_round}"
            if early
            else f"prompt field {stale_field!r} unchanged for ≥2 rounds"
        )
        return CheckResult(
            "param_scope_discipline",
            False,
            f"{len(offenders)} variant(s) touched param scope ({reason}): {offenders[:3]}",
        )
    return CheckResult(
        "param_scope_discipline",
        True,
        "no variant touched temperature/max_tokens/reasoning_effort",
    )


def _check_l2_directive_followed(round_dict: dict[str, Any], ctx: CheckContext) -> CheckResult:
    """When L2 directive present, ≥1 variant must reference one of its phrases."""
    directive = str(ctx.opt_search_point.get("l2_directive") or "").strip()
    if not directive:
        return CheckResult("l2_directive_followed", True, "no L2 directive active")
    variants = _l1_generate_variants(round_dict)
    if not variants:
        return CheckResult("l2_directive_followed", False, "L2 directive set but no variants")

    phrases = _key_phrases(directive, min_len=5)
    if not phrases:
        return CheckResult(
            "l2_directive_followed", True, "L2 directive yielded no extractable phrases"
        )

    for v in variants:
        blob = _variant_text_blob(v)
        if any(p in blob for p in phrases):
            return CheckResult(
                "l2_directive_followed",
                True,
                "≥1 variant references directive phrase",
            )
    return CheckResult(
        "l2_directive_followed",
        False,
        f"no variant references any of {phrases[:3]}",
    )


def _check_not_only_param_variants(round_dict: dict[str, Any], ctx: CheckContext) -> CheckResult:
    """≥1 variant per round must mutate a ``PROMPT_STRING_FIELDS`` field."""
    variants = _l1_generate_variants(round_dict)
    if not variants:
        return CheckResult("not_only_param_variants", True, "no variants emitted")

    prompt_field_set = set(PROMPT_STRING_FIELDS)
    for v in variants:
        pp = v.get("pipeline_params_override") or {}
        if any(k in prompt_field_set for k in pp):
            return CheckResult(
                "not_only_param_variants",
                True,
                "≥1 variant mutates a prompt field",
            )
    return CheckResult(
        "not_only_param_variants",
        False,
        f"all {len(variants)} variants mutate only non-prompt-field params",
    )


# --- registry --------------------------------------------------------------

CHECK_REGISTRY: dict[str, CheckFn] = {
    "context_object_honored": _check_context_object_honored,
    "param_scope_discipline": _check_param_scope_discipline,
    "l2_directive_followed": _check_l2_directive_followed,
    "not_only_param_variants": _check_not_only_param_variants,
}


def run_all_checks(round_dict: dict[str, Any], ctx: CheckContext) -> list[CheckResult]:
    """Run every registered check against one round, in registry order."""
    return [fn(round_dict, ctx) for fn in CHECK_REGISTRY.values()]


# --- support helpers used by checks ---------------------------------------


def _touches_param_scope(pp_override: dict[str, Any]) -> bool:
    """Recursively scan a pipeline_params_override for PARAM_SCOPE_KEYS."""
    if not isinstance(pp_override, dict):
        return False
    for key, value in pp_override.items():
        if key in PARAM_SCOPE_KEYS:
            return True
        if isinstance(value, dict) and _touches_param_scope(value):
            return True
    return False


def _stale_prompt_field(ctx: CheckContext) -> str | None:
    """Return a prompt-string field name unchanged for the past 2 rounds, or None.

    Inspects ``prior_rounds`` (most-recent last) and looks at each round's
    ``l1_generate`` variants for which prompt fields they mutated. A field
    that appears in zero variants for the last two rounds is "stale" and
    triggers the param-scope lock.
    """
    if len(ctx.prior_rounds) < 2:
        return None
    recent_two = ctx.prior_rounds[-2:]
    mutated_fields: set[str] = set()
    for r in recent_two:
        for v in _l1_generate_variants(r):
            pp = v.get("pipeline_params_override") or {}
            mutated_fields.update(k for k in pp if k in PROMPT_STRING_FIELDS)
    for field_name in PROMPT_STRING_FIELDS:
        if field_name not in mutated_fields:
            return field_name
    return None
