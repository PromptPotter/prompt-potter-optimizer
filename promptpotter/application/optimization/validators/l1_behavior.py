"""Each check is a pure ``(round_dict, ctx) -> CheckResult`` over one round's L1 output.
``CHECK_REGISTRY`` is the single source, so every surface that enumerates them agrees."""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from promptpotter.application.optimization.dispatch.injections.registry import (
    INJECTIONS,
    STALL_EXPLORATION,
    citable_fields,
)
from promptpotter.application.optimization.validators.behavior_base import (
    CheckFn,
    CheckResult,
    ValidatorContext,
)
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.candidate_diff import variant_prose_written
from promptpotter.domain.l1_layout import L1Layout
from promptpotter.domain.search_point import PARAM_SCOPE_KEYS

# Rounds before this one keep param-scope locked so prompt-field exploration
# settles first (see ``_check_param_scope_discipline``). Fixed policy, not a
# per-round knob — derived signals (stall depth, mutation history) drive the
# rest of the loop, but this floor stays constant.
PARAM_UNLOCK_ROUND = 3

__all__ = [
    "CHECK_REGISTRY",
    "extract_l1_variants",
    "run_all_checks",
]


def extract_l1_variants(container: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The same shape rides both the round-summary dict and the per-round audit dict, and both
    reach this codepath. Empty list when L1 did not fire or the response is malformed."""
    if not container:
        return []
    nodes = container.get("nodes") or {}
    node = nodes.get("l1_generate") or {}
    response = ((node.get("output") or {}).get("response")) or {}
    if isinstance(response, dict):
        return [v for v in (response.get("variants") or []) if isinstance(v, dict)]
    return []


# --- helpers ---------------------------------------------------------------

# Phrase-seed tokens: alphabetic-start, 3+ chars. Excludes digits and
# underscore-first tokens because it seeds noun-phrase substring matches, not set
# overlap.
_PHRASE_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z_\-]{2,}")


def _variant_text_blob(variant: dict[str, Any]) -> str:
    """Both carriers count — on an L4 cycle the prose rides ``changes_description`` and no
    override slot, so scanning the overrides alone leaves a one-sentence blob to match against."""
    parts = [str(variant.get("changes_description") or "")]
    parts.extend(variant_prose_written(variant).values())
    for value in (variant.get("task_context_override") or {}).values():
        parts.append(str(value or ""))
    return "\n".join(parts).lower()


def _key_phrases(text: str, *, min_len: int = 4, max_phrases: int = 6) -> list[str]:
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
    for i, v in enumerate(variants):
        blob = _variant_text_blob(v)
        if not any(any(seed in blob for seed in seeds) for seeds in item_seeds if seeds):
            misses.append(f"C{i + 1}")
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
    for i, v in enumerate(variants):
        pp = v.get("pipeline_params_override") or {}
        if _touches_param_scope(pp):
            offenders.append(f"C{i + 1}")
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
    """Per the L1 contract: *no data justifying a choice ⇒ do not gamble*. The citable set is
    re-derived from the round-start layout, and the citation must also QUOTE the shown prompt."""
    variants = extract_l1_variants(round_dict)
    if not variants:
        return CheckResult("evidence_grounding_present", True, "no variants emitted")

    citable = _round_citable_fields(ctx)
    shown = _rendered_l1_prompt(round_dict)
    offenders: list[tuple[str, str]] = []
    for i, v in enumerate(variants):
        label = f"C{i + 1}"
        eg = v.get("evidence_grounding")
        if not isinstance(eg, dict):
            offenders.append((label, "missing"))
            continue
        field_name = str(eg.get("field") or "").strip()
        citation = str(eg.get("citation") or "").strip()
        if field_name not in citable:
            offenders.append((label, _uncitable_reason(field_name, ctx)))
            continue
        if not citation:
            offenders.append((label, "empty_citation"))
            continue
        if not _citation_in_prompt(citation, shown):
            offenders.append((label, "citation_not_in_prompt"))
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
    """≥1 variant per round must mutate a prompt-field axis WHEREVER it rides — asking for
    ``prompt_fields_override`` reads the carrier, which an L4 cycle structurally lacks."""
    variants = extract_l1_variants(round_dict)
    if not variants:
        return CheckResult("not_only_param_variants", True, "no variants emitted")

    for v in variants:
        if variant_prose_written(v):
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


def _check_changes_description_english(
    round_dict: dict[str, Any], ctx: ValidatorContext
) -> CheckResult:
    """The loop's ONLY record of what a candidate intended, so every downstream reader reads this
    string. A behaviour check, not a rejection — an unreadable note is a bad record, not a bad edit."""
    variants = extract_l1_variants(round_dict)
    if not variants:
        return CheckResult("changes_description_english", True, "no variants emitted")

    offenders: list[str] = []
    for i, v in enumerate(variants):
        letters = [c for c in str(v.get("changes_description") or "") if c.isalpha()]
        if letters and sum(1 for c in letters if not c.isascii()) * 2 > len(letters):
            offenders.append(f"C{i + 1}")
    if offenders:
        return CheckResult(
            "changes_description_english",
            False,
            f"{len(offenders)}/{len(variants)} descriptions are majority non-ASCII: "
            f"{offenders[:3]}",
        )
    return CheckResult(
        "changes_description_english",
        True,
        f"{len(variants)}/{len(variants)} descriptions readable",
    )


def _check_distinct_clusters(round_dict: dict[str, Any], ctx: ValidatorContext) -> CheckResult:
    """Two variants on ONE failure cluster are one hypothesis wearing two edits, and the round pays a
    full measurement per copy — at L4 a copy is a whole panel arm. Reads `targets_cluster`, which the
    schema requires, rather than the citation: a shared citation was the wrong proxy, passing 2/2 on a
    round that re-proposed both of the previous round's measured losses. SCORED, never rejected —
    rejecting the duplicate would leave the round measuring one arm, which is the worse failure."""
    variants = extract_l1_variants(round_dict)
    if not variants:
        return CheckResult("distinct_clusters", True, "no variants emitted")
    seen: dict[str, int] = {}
    for v in variants:
        cluster = str(v.get("targets_cluster") or "").strip().lower()
        if cluster and cluster != "none":
            seen[cluster] = seen.get(cluster, 0) + 1
    shared = sum(n for n in seen.values() if n > 1)
    if shared:
        return CheckResult(
            "distinct_clusters",
            False,
            f"{shared}/{len(variants)} variants target a cluster another already took",
        )
    return CheckResult(
        "distinct_clusters", True, f"{len(variants)}/{len(variants)} target distinct clusters"
    )


# --- registry --------------------------------------------------------------

CHECK_REGISTRY: dict[str, CheckFn] = {
    "context_object_honored": _check_context_object_honored,
    "param_scope_discipline": _check_param_scope_discipline,
    "not_only_param_variants": _check_not_only_param_variants,
    "evidence_grounding_present": _check_evidence_grounding_present,
    "changes_description_english": _check_changes_description_english,
    "distinct_clusters": _check_distinct_clusters,
}


def run_all_checks(round_dict: dict[str, Any], ctx: ValidatorContext) -> list[CheckResult]:
    """**Empty list when L1 emitted NO variants.** Every check short-circuits to ``passed=True``
    on an empty list, so running them anyway scores 4/4: "nothing to check" is not "nothing wrong"."""
    if not extract_l1_variants(round_dict):
        return []
    return [fn(round_dict, ctx) for fn in CHECK_REGISTRY.values()]


# --- support helpers used by checks ---------------------------------------


def _round_citable_fields(ctx: ValidatorContext) -> tuple[str, ...]:
    """The same derivation that built the round's citation menu, replayed off the round-start OSP.
    Falls open to every citable panel when the snapshot carries no layout."""
    memory = ctx.opt_sp.get("memory") or {}
    raw = memory.get("l1_layout") if isinstance(memory, dict) else None
    # A COMPLETE stored layout, NOT an edit — the snapshot names every slot — so it is parsed as
    # one. Routing it through `coerce_l1_layout` reads it as a `{panel: slot}` edit, which a dump
    # of per-slot lists is not, and every round then falls open to the whole citable registry.
    if not isinstance(raw, dict) or not raw:
        return tuple(sorted([n for n, i in INJECTIONS.items() if i.citable] + [STALL_EXPLORATION]))
    try:
        layout = L1Layout.model_validate(raw)
    except ValidationError:
        return tuple(sorted([n for n, i in INJECTIONS.items() if i.citable] + [STALL_EXPLORATION]))
    return citable_fields(layout, exploration_budget=ctx.exploration_budget)


_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")

# A quoted run shorter than this cannot adjudicate fabrication either way: it matches
# something in a 16k-char prompt by coincidence, and flagging its absence would be noise.
# The check abstains rather than guessing — a false accusation here downgrades a round's
# verdict, and the round-1 verdict is what authorises another round of real spend.
_CITATION_MIN_RUN = 12


def _normalize_quote(text: str) -> str:
    """A citation re-types a panel line, so it drifts on what carries no meaning — wrapping, smart
    quotes, an em-dash rendered as a hyphen. Normalizing both sides matches only the WORDS."""
    return _NORMALIZE_RE.sub(" ", text.lower()).strip()


def _rendered_l1_prompt(round_dict: dict[str, Any]) -> str:
    node = ((round_dict.get("nodes") or {}).get("l1_generate")) or {}
    fields = ((node.get("input") or {}).get("template_fields")) or {}
    return _normalize_quote(" ".join(str(v) for v in fields.values() if isinstance(v, str)))


def _citation_in_prompt(citation: str, shown: str) -> bool:
    """An honest citation may ELIDE, so the test is the longest ellipsis-free run. Abstains (True)
    when the prompt is unavailable — an older round file must not fail every variant it holds."""
    if not shown:
        return True
    longest = max((_normalize_quote(part) for part in re.split(r"[.]{3}|…", citation)), key=len)
    if len(longest) < _CITATION_MIN_RUN:
        return True
    return longest in shown


def _uncitable_reason(field_name: str, ctx: ValidatorContext) -> str:
    if not field_name:
        return "no_field"
    if field_name == STALL_EXPLORATION:
        return "stall_exploration_when_tight"
    injection = INJECTIONS.get(field_name)
    if injection is None:
        return f"bad_field={field_name!r}"
    if not injection.citable:
        return f"not_evidence={field_name!r}"
    # A real evidence panel — just not one L1 was shown this round. A set-membership check
    # waves this fabricated citation through.
    return f"panel_not_in_prompt={field_name!r}"


_REBUT_SIGNALS: tuple[str, ...] = (
    "priority_fix",
    "suggested_axes",
    "critique names",
    "exploration_budget=wide",
    "exploration_budget = wide",
    "wide exploration",
)


def _cited_peaked_axis(
    variant: dict[str, Any], citation: str, field_name: str, ctx: ValidatorContext
) -> str | None:
    """Permissive on purpose — the axis name as a substring of the citation, or a key inside
    ``pipeline_params_override``. Fires only when ``field_name == "axis_memory"``."""
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
    """The critique naming the axis, or an explicit wide budget. Permissive substring match —
    the prompt instructs L1 to name the rebut verbatim."""
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
    if not isinstance(pp_override, dict):
        return False
    for key, value in pp_override.items():
        if key in PARAM_SCOPE_KEYS:
            return True
        if isinstance(value, dict) and _touches_param_scope(value):
            return True
    return False


def _stale_prompt_field(ctx: ValidatorContext) -> str | None:
    """A field appearing in zero variants for two rounds is stale and triggers the param-scope lock."""
    if len(ctx.prior_rounds) < 2:
        return None
    recent_two = ctx.prior_rounds[-2:]
    mutated_fields: set[str] = set()
    for r in recent_two:
        for v in extract_l1_variants(r):
            # Leaf of `field` / `node.field` — same axis either carrier wrote it through.
            mutated_fields.update(k.rpartition(".")[2] for k in variant_prose_written(v))
    for field_name in PROMPT_STRING_FIELDS:
        if field_name not in mutated_fields:
            return field_name
    return None
