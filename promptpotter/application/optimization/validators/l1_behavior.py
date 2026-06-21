"""L1 behaviour checks — programmatic conformance for one round of L1 output.

Each check is a pure ``(round_dict, ctx) -> CheckResult`` function with no
I/O. ``round_dict`` is one ``.runtime/cache/rounds/round_NNNN.json`` payload as
written by ``AuditTrailView.flush``; ``ctx`` carries the per-round
context the check needs (prior rounds, OSP at round-start, the three
``context_object`` items).

Adding a new check is one function plus one entry in ``CHECK_REGISTRY``.
The registry is the single source of truth so ``review.md`` and the
``potter-l1-meta-campaign`` skill enumerate the same check set.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.opt_search_point import EVIDENCE_GROUNDING_FIELDS
from promptpotter.domain.search_point import PARAM_SCOPE_KEYS

# Rounds before this one keep param-scope locked so prompt-field exploration
# settles first (see ``_check_param_scope_discipline``). Fixed policy, not a
# per-round knob — derived signals (stall depth, mutation history) drive the
# rest of the loop, but this floor stays constant.
PARAM_UNLOCK_ROUND = 3

__all__ = [
    "CHECK_REGISTRY",
    "CheckResult",
    "ValidatorContext",
    "extract_l1_variants",
    "run_all_checks",
]


def extract_l1_variants(container: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Walk ``nodes.l1_generate.output.response.variants`` on a round/audit dict.

    The same shape rides on both the round-summary dict (as written into
    ``index.json::rounds[N]``) and the per-round audit dict
    (``round_NNNN.json``); both are read by this codepath. Empty list when L1
    didn't fire or the response is malformed.
    """
    if not container:
        return []
    nodes = container.get("nodes") or {}
    node = nodes.get("l1_generate") or {}
    response = ((node.get("output") or {}).get("response")) or {}
    if isinstance(response, dict):
        return [v for v in (response.get("variants") or []) if isinstance(v, dict)]
    return []


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    passed: bool
    evidence: str


@dataclass(frozen=True)
class ValidatorContext:
    """Context shared by all behaviour checks for one round.

    ``prior_rounds`` are the round dicts strictly before ``round_num``,
    in order; ``opt_search_point`` is the OSP snapshot at round-start
    (carries ``task_context`` + the live prompt fields); ``context_object``
    is the three task-decomposition strings L1's prompt was shown.
    ``exploration_budget`` is the round's escalation-panel exploration
    budget (``tight`` / ``normal`` / ``wide``) — read by
    ``evidence_grounding_present`` to gate the ``stall_exploration``
    escape hatch. ``None`` means the wiring layer could not determine it,
    in which case ``stall_exploration`` citations fail-open (no flag).
    """

    round_num: int
    prior_rounds: list[dict[str, Any]] = field(default_factory=list)
    opt_search_point: dict[str, Any] = field(default_factory=dict)
    context_object: list[str] = field(default_factory=list)
    exploration_budget: str | None = None
    # Axes the round-start AxisIndex flagged as ``peaked``. Used by
    # ``evidence_grounding_present`` to reject variants that cite
    # ``axis_memory`` to justify mutating a peaked axis without naming a
    # rebut (sibling_yield>0 this round, or exploration_budget=wide).
    # Populated by ``review.py`` from each round dict's
    # ``axis_memory_peaked`` field, stashed by ``persist_round``.
    peaked_axes: frozenset[str] = field(default_factory=frozenset)


CheckFn = Callable[[dict[str, Any], ValidatorContext], CheckResult]


# --- helpers ---------------------------------------------------------------

# Phrase-seed tokens: alphabetic-start, 3+ chars (distinct from the word-set
# tokenizer in ``l2_output.py`` — this one excludes digits and underscore-first
# tokens because it seeds noun-phrase substring matches, not set overlap).
_PHRASE_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z_\-]{2,}")


def _variant_text_blob(variant: dict[str, Any]) -> str:
    """All free-form variant text checks may scan against.

    Includes changes_description plus the textual content of every mutation
    slot the schema split exposes: ``prompt_fields_override`` (the six
    PROMPT_STRING_FIELDS as a flat dict) and ``task_context_override``
    (the two splice strings as a flat dict).
    """
    parts = [str(variant.get("changes_description") or "")]
    for value in (variant.get("prompt_fields_override") or {}).values():
        parts.append(str(value or ""))
    for value in (variant.get("task_context_override") or {}).values():
        parts.append(str(value or ""))
    return "\n".join(parts).lower()


def _key_phrases(text: str, *, min_len: int = 4, max_phrases: int = 6) -> list[str]:
    """Extract candidate noun phrases from a brief — substring-match seeds."""
    seen: list[str] = []
    for token in _PHRASE_TOKEN_RE.findall(text or ""):
        lowered = token.lower()
        if len(lowered) < min_len or lowered in seen:
            continue
        seen.append(lowered)
        if len(seen) >= max_phrases:
            break
    return seen


# --- checks ----------------------------------------------------------------


def _check_context_object_honored(round_dict: dict[str, Any], ctx: ValidatorContext) -> CheckResult:
    """Each variant must reference at least one ``context_object`` item."""
    items = [c for c in ctx.context_object if isinstance(c, str) and c.strip()]
    variants = extract_l1_variants(round_dict)
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


def _check_param_scope_discipline(round_dict: dict[str, Any], ctx: ValidatorContext) -> CheckResult:
    """No param-scope mutations while prompt-field exploration hasn't settled."""
    variants = extract_l1_variants(round_dict)
    if not variants:
        return CheckResult("param_scope_discipline", True, "no variants emitted")

    early = ctx.round_num < PARAM_UNLOCK_ROUND
    stale_field = _stale_prompt_field(ctx)
    if not early and stale_field is None:
        return CheckResult(
            "param_scope_discipline",
            True,
            f"unlocked: round ≥ {PARAM_UNLOCK_ROUND} and no stale prompt field",
        )

    offenders: list[str] = []
    for v in variants:
        pp = v.get("pipeline_params_override") or {}
        if _touches_param_scope(pp):
            offenders.append(str(v.get("variant_name") or "?"))
    if offenders:
        reason = (
            f"round < {PARAM_UNLOCK_ROUND}"
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


def _check_evidence_grounding_present(
    round_dict: dict[str, Any], ctx: ValidatorContext
) -> CheckResult:
    """Each L1 variant must cite a panel field that justifies its mutation.

    Per ``promptpotter/CLAUDE.md`` L1 contract: *no data justifying a choice
    ⇒ do not gamble*. Fails when:

    - ``evidence_grounding`` is missing / not a dict, or
    - ``field`` is empty / not in :data:`EVIDENCE_GROUNDING_FIELDS`, or
    - ``citation`` is empty / whitespace, or
    - ``field == "stall_exploration"`` while
      ``ctx.exploration_budget == "tight"`` (escape hatch disallowed).
    """
    variants = extract_l1_variants(round_dict)
    if not variants:
        return CheckResult("evidence_grounding_present", True, "no variants emitted")

    offenders: list[tuple[str, str]] = []
    for v in variants:
        label = str(v.get("variant_name") or "?")
        eg = v.get("evidence_grounding")
        if not isinstance(eg, dict):
            offenders.append((label, "missing"))
            continue
        field_name = str(eg.get("field") or "").strip()
        citation = str(eg.get("citation") or "").strip()
        if field_name not in EVIDENCE_GROUNDING_FIELDS:
            offenders.append((label, f"bad_field={field_name!r}" if field_name else "no_field"))
            continue
        if not citation:
            offenders.append((label, "empty_citation"))
            continue
        if field_name == "stall_exploration" and ctx.exploration_budget == "tight":
            offenders.append((label, "stall_exploration_when_tight"))
            continue
        peaked_hit = _cited_peaked_axis(v, citation, field_name, ctx)
        if peaked_hit and not _has_peaked_rebut(v, citation, ctx):
            offenders.append((label, f"axis_memory_peaked={peaked_hit}"))
            continue

    if offenders:
        sample = ", ".join(f"{label}({reason})" for label, reason in offenders[:3])
        return CheckResult(
            "evidence_grounding_present",
            False,
            f"{len(offenders)}/{len(variants)} variants lack valid evidence: {sample}",
        )
    return CheckResult(
        "evidence_grounding_present",
        True,
        f"{len(variants)}/{len(variants)} variants cite a panel field",
    )


def _check_not_only_param_variants(
    round_dict: dict[str, Any], ctx: ValidatorContext
) -> CheckResult:
    """≥1 variant per round must mutate a prompt-field axis (``prompt_fields_override``)."""
    variants = extract_l1_variants(round_dict)
    if not variants:
        return CheckResult("not_only_param_variants", True, "no variants emitted")

    for v in variants:
        if v.get("prompt_fields_override"):
            return CheckResult(
                "not_only_param_variants",
                True,
                "≥1 variant mutates a prompt-field axis",
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
    "not_only_param_variants": _check_not_only_param_variants,
    "evidence_grounding_present": _check_evidence_grounding_present,
}


def run_all_checks(round_dict: dict[str, Any], ctx: ValidatorContext) -> list[CheckResult]:
    """Run every registered check against one round, in registry order."""
    return [fn(round_dict, ctx) for fn in CHECK_REGISTRY.values()]


# --- support helpers used by checks ---------------------------------------


_REBUT_SIGNALS: tuple[str, ...] = (
    "sibling_yield",
    "exploration_budget=wide",
    "exploration_budget = wide",
    "wide exploration",
)


def _cited_peaked_axis(
    variant: dict[str, Any], citation: str, field_name: str, ctx: ValidatorContext
) -> str | None:
    """Return the first peaked axis that this variant's evidence_grounding
    + override fields appear to mutate, or None.

    The match is intentionally permissive: we look for the peaked-axis
    name as a substring in the citation OR as a key inside
    ``pipeline_params_override`` (e.g. ``llm_only.temperature`` matches
    ``temperature`` keyed under ``llm_only``). The validator only fires
    when ``field_name == "axis_memory"`` — citations grounded in other
    panels (sibling_yield, parent_panel, etc.) are independent evidence
    and not rejected by this rule.
    """
    if not ctx.peaked_axes or field_name != "axis_memory":
        return None
    pp = variant.get("pipeline_params_override") or {}
    flat_pp_axes: set[str] = set()
    if isinstance(pp, dict):
        for node, params in pp.items():
            if isinstance(params, dict):
                for p in params:
                    flat_pp_axes.add(f"{node}.{p}")
                    flat_pp_axes.add(str(p))
    for axis in ctx.peaked_axes:
        if axis in citation or axis in flat_pp_axes:
            return axis
        _, _, leaf = axis.partition(".")
        if leaf and (leaf in citation or leaf in flat_pp_axes):
            return axis
    return None


def _has_peaked_rebut(variant: dict[str, Any], citation: str, ctx: ValidatorContext) -> bool:
    """True iff the variant's citation/changes_description cites a real
    rebut for the peaked-axis guard: a sibling_yield row this round, or
    an explicit wide-exploration budget. Permissive substring match —
    the prompt instructs L1 to name the rebut verbatim.
    """
    if ctx.exploration_budget == "wide":
        return True
    blob = " ".join(
        [
            citation,
            str(variant.get("changes_description") or ""),
        ]
    ).lower()
    return any(sig in blob for sig in _REBUT_SIGNALS)


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


def _stale_prompt_field(ctx: ValidatorContext) -> str | None:
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
        for v in extract_l1_variants(r):
            mutated_fields.update((v.get("prompt_fields_override") or {}).keys())
    for field_name in PROMPT_STRING_FIELDS:
        if field_name not in mutated_fields:
            return field_name
    return None
