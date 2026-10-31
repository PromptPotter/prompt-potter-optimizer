from __future__ import annotations

import re
from typing import Any

from promptpotter.application.optimizers.nodes import CheckResult
from promptpotter.application.optimizers.potter.dispatch.injections.layer_state import (
    HELD_PROMPT_FIELD_MARK,
)
from promptpotter.application.optimizers.potter.dispatch.injections.registry import (
    STALL_EXPLORATION,
    injection_table,
)
from promptpotter.application.optimizers.potter.dispatch.schemas import CLUSTER_ROWS_MIN
from promptpotter.application.optimizers.potter.validators.behavior_base import (
    CheckFn,
    ValidatorContext,
    judged,
)
from promptpotter.domain.opt_search_point import Locus, OptimizerPromptTemplate
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.round_audit import NodeBlock, RoundAudit
from promptpotter.domain.search_point import PARAM_SCOPE_KEYS


def _l1_block(audit: RoundAudit | None) -> NodeBlock | None:
    return None if audit is None else audit.nodes.get("l1_generate")


def extract_l1_variants(audit: RoundAudit | None) -> list[dict[str, Any]]:
    block = _l1_block(audit)
    response = None if block is None else block.output.response
    if isinstance(response, dict):
        return [v for v in (response.get("variants") or []) if isinstance(v, dict)]
    return []


def _shown_l1_prompt(audit: RoundAudit) -> str:
    block = _l1_block(audit)
    fields = {} if block is None else block.input.template_fields
    return " ".join(str(v) for v in fields.values() if isinstance(v, str))


# The mark `panels.py::clustered_sample_ids` rosters: a row a cluster, and so a variant, may name.
_ROW_MARK_RE = re.compile(r"\[#(\d+)\]")

# Alphabetic-start, 3+ chars: it seeds substring matches, not set overlap.
_PHRASE_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z_\-]{2,}")


def _variant_prose_written(variant: dict[str, Any]) -> dict[str, str]:
    """Prose rides two carriers by campaign kind: reading one answers inverted on the other."""
    updates = variant.get("prompt_fields_updates") or {}
    written = {
        Locus(n, f).name: str(v)
        for n, fields in updates.items()
        if isinstance(fields, dict)
        for f, v in fields.items()
        if v
    }
    for n, cfg in node_config_items(variant.get("pipeline_overlay")):
        for p, v in cfg.items():
            if p in OptimizerPromptTemplate.model_fields and v:
                written[Locus(n, p).name] = str(v)
    return written


def _variant_text_blob(variant: dict[str, Any]) -> str:
    parts = [str(variant.get("changes_description") or "")]
    parts.extend(_variant_prose_written(variant).values())
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


Variants = list[dict[str, Any]]


def _check_context_object_honored(
    variants: Variants, _audit: RoundAudit, ctx: ValidatorContext
) -> tuple[bool, str]:
    items = [c for c in ctx.context_object if c.strip()]
    if not items:
        return True, "no context_object items to honour"
    item_seeds = [_key_phrases(item) for item in items]
    misses: list[str] = []
    for i, v in enumerate(variants):
        blob = _variant_text_blob(v)
        if not any(any(seed in blob for seed in seeds) for seeds in item_seeds if seeds):
            misses.append(f"C{i + 1}")
    if misses:
        return False, f"{len(misses)}/{len(variants)} variants ignored context_object: {misses[:3]}"
    return True, f"{len(variants)}/{len(variants)} variants reference ≥1 context_object item"


def _check_param_scope_discipline(
    variants: Variants, audit: RoundAudit, ctx: ValidatorContext
) -> tuple[bool, str]:
    stale_field = _stale_prompt_field(ctx, _shown_prompt_loci(audit))
    if stale_field is None:
        return True, "unlocked: no stale prompt field"
    offenders = [
        f"C{i + 1}"
        for i, v in enumerate(variants)
        if _touches_param_scope(v.get("pipeline_overlay") or {})
    ]
    if offenders:
        return False, (
            f"{len(offenders)} variant(s) touched param scope (prompt field {stale_field!r} "
            f"unchanged for ≥2 rounds): {offenders[:3]}"
        )
    return True, "no variant touched temperature/max_tokens/reasoning_effort"


def _check_evidence_grounding_present(
    variants: Variants, _audit: RoundAudit, ctx: ValidatorContext
) -> tuple[bool, str]:
    """Whether the quote is in its panel is `l1_strict.py::l1_citation_in_panel`'s, which rejects."""
    citable = ctx.citable
    offenders: list[tuple[str, str]] = []
    for i, v in enumerate(variants):
        label = f"C{i + 1}"
        eg = v.get("evidence_grounding")
        if not isinstance(eg, dict):
            offenders.append((label, "missing"))
            continue
        field_name = str(eg.get("field") or "").strip()
        citation = str(eg.get("citation") or "").strip()
        if citable is not None and field_name not in citable:
            offenders.append((label, _uncitable_reason(field_name)))
            continue
        if not citation:
            offenders.append((label, "empty_citation"))
            continue
        peaked_hit = _cited_peaked_axis(v, citation, field_name, ctx)
        if peaked_hit and not _has_peaked_rebut(v, citation, ctx):
            offenders.append((label, f"axis_memory_peaked={peaked_hit}"))
            continue

    if offenders:
        sample = ", ".join(f"{label}({reason})" for label, reason in offenders[:3])
        return False, f"{len(offenders)}/{len(variants)} variants lack valid evidence: {sample}"
    return True, f"{len(variants)}/{len(variants)} variants cite a panel field"


def _check_not_only_param_variants(
    variants: Variants, audit: RoundAudit, _ctx: ValidatorContext
) -> tuple[bool, str]:
    shown = _shown_prompt_loci(audit)
    if shown and all(shown.values()):
        return True, "every prompt field is held"
    if any(_variant_prose_written(v) for v in variants):
        return True, "≥1 variant mutates a prompt-field axis"
    return False, f"all {len(variants)} variants mutate only non-prompt-field params"


def _check_changes_description_english(
    variants: Variants, _audit: RoundAudit, _ctx: ValidatorContext
) -> tuple[bool, str]:
    offenders: list[str] = []
    for i, v in enumerate(variants):
        letters = [c for c in str(v.get("changes_description") or "") if c.isalpha()]
        if letters and sum(1 for c in letters if not c.isascii()) * 2 > len(letters):
            offenders.append(f"C{i + 1}")
    if offenders:
        return False, (
            f"{len(offenders)}/{len(variants)} descriptions are majority non-ASCII: {offenders[:3]}"
        )
    return True, f"{len(variants)}/{len(variants)} descriptions readable"


def _check_targets_a_cluster(
    variants: Variants, audit: RoundAudit, ctx: ValidatorContext
) -> tuple[bool, str]:
    """The seam for ``targets_rows``: the wire's ``minItems`` binds nothing, and an id is checkable where a label is not."""
    marked = {int(m) for m in _ROW_MARK_RE.findall(_shown_l1_prompt(audit))}
    offenders: list[tuple[str, str]] = []
    for i, v in enumerate(variants):
        rows = {r for r in v.get("targets_rows") or [] if isinstance(r, int)}
        if len(rows) < CLUSTER_ROWS_MIN:
            offenders.append((f"C{i + 1}", f"{len(rows)}_rows"))
        elif not rows <= marked:
            offenders.append((f"C{i + 1}", f"unmarked={sorted(rows - marked)[:3]}"))
        elif ctx.clusters and not any(rows <= cluster for cluster in ctx.clusters):
            offenders.append((f"C{i + 1}", "spans_clusters"))
    if offenders:
        sample = ", ".join(f"{label}({reason})" for label, reason in offenders[:3])
        return False, f"{len(offenders)}/{len(variants)} variants target no cluster: {sample}"
    return True, (
        f"{len(variants)}/{len(variants)} variants target ≥{CLUSTER_ROWS_MIN} rows of one cluster"
    )


CHECK_REGISTRY: dict[str, CheckFn[Variants]] = {
    "targets_a_cluster": _check_targets_a_cluster,
    "context_object_honored": _check_context_object_honored,
    "param_scope_discipline": _check_param_scope_discipline,
    "not_only_param_variants": _check_not_only_param_variants,
    "evidence_grounding_present": _check_evidence_grounding_present,
    "changes_description_english": _check_changes_description_english,
}


def run_all_checks(audit: RoundAudit, ctx: ValidatorContext) -> list[CheckResult]:
    """Empty on no variants: every check passes on none, and "nothing to check" is not a pass."""
    variants = extract_l1_variants(audit)
    return judged(CHECK_REGISTRY, variants, audit, ctx) if variants else []


def _uncitable_reason(field_name: str) -> str:
    if not field_name:
        return "no_field"
    if field_name == STALL_EXPLORATION:
        return "stall_exploration_when_tight"
    injection = injection_table().get(field_name)
    if injection is None:
        return f"bad_field={field_name!r}"
    if not injection.citable:
        return f"not_evidence={field_name!r}"
    # A real evidence panel, just not one L1 was shown this round.
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
    if not ctx.peaked_axes or field_name != "axis_memory":
        return None
    pp = variant.get("pipeline_overlay") or {}
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
    if ctx.exploration_budget == "wide":
        return True
    blob = " ".join(
        [
            citation,
            str(variant.get("changes_description") or ""),
        ]
    ).lower()
    return any(sig in blob for sig in _REBUT_SIGNALS)


def _touches_param_scope(pipeline_overlay: dict[str, Any]) -> bool:
    for key, value in pipeline_overlay.items():
        if key in PARAM_SCOPE_KEYS:
            return True
        if isinstance(value, dict) and _touches_param_scope(value):
            return True
    return False


# One `[node.field]` section header per prose locus `rendered_prompt` showed, marked when held.
_LOCUS_SECTION_RE = re.compile(
    r"^\[([\w\-]+\.\w+)(" + re.escape(HELD_PROMPT_FIELD_MARK) + r")?\]$", re.MULTILINE
)


def _shown_prompt_loci(audit: RoundAudit) -> dict[str, bool]:
    """Read off the prompt the round rendered, in its order: locus → held. A held field is not stale."""
    block = _l1_block(audit)
    fields = {} if block is None else block.input.template_fields
    return {
        m.group(1): m.group(2) is not None
        for text in fields.values()
        if isinstance(text, str)
        for m in _LOCUS_SECTION_RE.finditer(text)
    }


def _stale_prompt_field(ctx: ValidatorContext, shown: dict[str, bool]) -> str | None:
    if len(ctx.prior_rounds) < 2:
        return None
    recent_two = ctx.prior_rounds[-2:]
    mutated: set[str] = set()
    for r in recent_two:
        for v in extract_l1_variants(r):
            mutated.update(_variant_prose_written(v))
    return next((locus for locus, held in shown.items() if not held and locus not in mutated), None)
