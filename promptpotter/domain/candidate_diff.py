"""What a candidate CHANGED, and whether that change is an idea the cycle already tried.

Two questions, deliberately in one module: ``candidate_delta`` answers *what changed* and
``candidate_idea`` answers *is that the same thing* — and all three consumers of "already tried"
(round-local dedup, the cross-round repeat gate, the ALREADY TRIED panel) must share both
definitions or a re-proposal one rejects is rendered as new by another. The flatten/group helpers
below are the render side of the same delta."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import SCHEMA_DESCRIPTIONS_PARAM

__all__ = [
    "IDEA_MATCH_MARK",
    "IDEA_MATCH_REJECT",
    "IDEA_MIN_TOKENS",
    "IDEA_MIN_TOKEN_CHARS",
    "IDEA_STOPWORDS",
    "build_candidate_flat",
    "candidate_delta",
    "candidate_idea",
    "flatten_sp_summary",
    "group_diff_keys",
    "idea_fingerprint",
    "parent_param_value",
    "same_idea",
    "variant_prose_written",
]


def parent_param_value(parent_cfg: dict[str, Any], param: str, proposed: Any) -> Any:
    """``output_schema_descriptions`` is virtual — its prose lives folded inside the schema, so the
    parent never carries the key and a naive lookup makes re-proposing existing prose read as a mutation."""
    if param == SCHEMA_DESCRIPTIONS_PARAM and isinstance(proposed, dict):
        props = (parent_cfg.get("output_schema") or {}).get("properties") or {}
        return {f: (props.get(f) or {}).get("description", "") for f in proposed}
    return parent_cfg.get(param)


def candidate_delta(
    child_fields: dict[str, Any],
    parent_fields: dict[str, Any],
    pp_override: dict[str, Any] | None,
    parent_pp: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[tuple[str, str], Any]]:
    """The ONE delta definition: dedup hashes it and the ALREADY TRIED panel renders it, so a rule
    honoured in one cannot desert the other. Restating the parent's value is not a mutation."""
    pf = {
        f: child_fields.get(f)
        for f in PROMPT_STRING_FIELDS
        if child_fields.get(f) != parent_fields.get(f)
    }
    parent = parent_pp or {}
    pp = {
        (n, p): v
        for n, cfg in node_config_items(pp_override)
        for p, v in cfg.items()
        if v != parent_param_value(parent.get(n) or {}, p, v)
    }
    return pf, pp


def variant_prose_written(variant: dict[str, Any]) -> dict[str, str]:
    """A prose mutation rides two different carriers depending on whether the campaign evolves a
    target prompt or a node's own template — reading one answers inverted on the other kind."""
    written: dict[str, str] = {}
    for f, v in (variant.get("prompt_fields_override") or {}).items():
        if f in PROMPT_STRING_FIELDS:
            written[f] = str(v or "")
    for n, cfg in node_config_items(variant.get("pipeline_params_override")):
        for p, v in cfg.items():
            if p in PROMPT_STRING_FIELDS:
                written[f"{n}.{p}"] = str(v or "")
    return written


# --- the IDEA a delta carries ----------------------------------------------
IDEA_STOPWORDS: frozenset[str] = frozenset(
    [
        "about",
        "after",
        "also",
        "always",
        "answer",
        "answers",
        "before",
        "being",
        "both",
        "cannot",
        "check",
        "could",
        "does",
        "each",
        "either",
        "else",
        "every",
        "from",
        "give",
        "given",
        "have",
        "here",
        "into",
        "itself",
        "just",
        "more",
        "most",
        "must",
        "never",
        "only",
        "other",
        "over",
        "same",
        "should",
        "show",
        "some",
        "such",
        "than",
        "that",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "thus",
        "using",
        "very",
        "what",
        "when",
        "where",
        "which",
        "while",
        "will",
        "with",
        "without",
        "would",
        "your",
    ]
)
# Below this length a token is structural, not distinguishing ("the", "and", "not").
IDEA_MIN_TOKEN_CHARS = 4
# A fingerprint below this many content words cannot support a ratio: with 3 tokens one
# shared word is 33% and two is 67%, so short mutations would pair with anything.
IDEA_MIN_TOKENS = 6
# Overlap-coefficient floor for "the same idea". NOT Jaccard: the two values compared are
# routinely very different lengths — a one-clause `thinking_style` nudge against a rewritten
# `reasoning` paragraph — and Jaccard divides by the union, so a short restatement of a long
# idea scores low however completely it is contained. Overlap asks what matters: is the
# smaller essentially a subset of the larger?
IDEA_MATCH_MARK = 0.6
# The REJECT threshold is deliberately stricter than the MARK threshold. Marking a row is
# free and reversible — the row renders either way. Rejecting costs a candidate slot outright,
# and a wrong rejection is invisible (the variant simply never existed). Two thresholds, two
# consequences; collapsing them would price a destructive act at an informational rate.
#
# Swept over the 17 candidates of the `justlogic-d234` cycle that motivated this (flagged /
# rounds the safety valve would have had to rescue): 0.60 → 4, 1 · 0.65 → 2, 0 · 0.70 → 1, 0 ·
# 0.80 → 0. Note that run offers only 3 measured losses to match against (the probe-round bug
# left six candidates unmeasured, and `lost_ideas` rightly refuses to convict on those), so
# these counts are a floor — a clean run gives the gate far more evidence and it will fire more
# often. 0.70 is the point that still catches a real re-proposal while leaving the valve idle.
IDEA_MATCH_REJECT = 0.70


def idea_fingerprint(values: Iterable[str]) -> frozenset[str]:
    """It catches a re-proposal that REUSES vocabulary, and nothing else — a zero repeat count is not
    evidence the generator is exploring.

    It IS blind to within-cycle continuity, measured: a forced-choice judge matches an edit to its
    own cycle's next round well above chance across pairs this test calls distinct. That argues for
    a better signal on the VALUES, never for tightening ``IDEA_MATCH_REJECT`` — the same reading
    cannot separate a re-proposal from the critique steering two rounds at one failure. Run on
    ``changes_description`` instead it sits at chance, so build no successor on that prose."""
    words = re.findall(r"[a-z]+", " ".join(values).lower())
    return frozenset(w for w in words if len(w) >= IDEA_MIN_TOKEN_CHARS and w not in IDEA_STOPWORDS)


def same_idea(a: frozenset[str], b: frozenset[str], *, threshold: float) -> bool:
    """*threshold* is explicit at every call site on purpose — see :data:`IDEA_MATCH_REJECT`."""
    if len(a) < IDEA_MIN_TOKENS or len(b) < IDEA_MIN_TOKENS:
        return False
    return len(a & b) / min(len(a), len(b)) >= threshold


def candidate_idea(
    child_fields: dict[str, Any],
    parent_fields: dict[str, Any],
    pp_override: dict[str, Any] | None,
    parent_pp: dict[str, Any] | None,
) -> frozenset[str]:
    """The parent is subtracted WHOLE, every prompt field: the task's own vocabulary lives across the
    fields this candidate did not change, and left in it convicts unrelated rewrites of repeating."""
    pf, pp = candidate_delta(child_fields, parent_fields, pp_override, parent_pp)
    parent = parent_pp or {}
    written = idea_fingerprint([str(v) for v in pf.values() if v] + [str(v) for v in pp.values()])
    carried = idea_fingerprint(
        [str(v) for v in parent_fields.values() if v]
        + [
            str(prior)
            for (n, p), v in pp.items()
            if (prior := parent_param_value(parent.get(n) or {}, p, v)) is not None
        ]
    )
    return written - carried


def _fmt_pp_val(v: object) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def flatten_sp_summary(pp: dict[str, Any] | None) -> dict[str, str]:
    """A nested param flattens ONE level further, to ``node.param.key`` — the depth its declaration
    lets the merge accumulate at. ``group_diff_keys`` splits on the first dot, so it still groups."""
    flat: dict[str, str] = {}
    for k, v in node_config_items(pp):
        for sub_k, sub_v in v.items():
            if isinstance(sub_v, dict):
                for leaf_k, leaf_v in sub_v.items():
                    flat[f"{k}.{sub_k}.{leaf_k}"] = _fmt_pp_val(leaf_v)
            else:
                flat[f"{k}.{sub_k}"] = _fmt_pp_val(sub_v)
    return flat


def build_candidate_flat(parent: dict[str, str], candidate_meta: dict[str, Any]) -> dict[str, str]:
    """Merge candidate overrides onto parent across the three disjoint keyspaces:
    ``node.param`` (pipeline_params), bare prompt fields, ``tc.<key>`` (task_context)."""
    flat = parent.copy()
    if pp := candidate_meta.get("pipeline_params_override"):
        flat.update(flatten_sp_summary(pp))
    for field_name, value in (candidate_meta.get("prompt_fields") or {}).items():
        if value:
            flat[field_name] = str(value)
    for field_name, value in (candidate_meta.get("task_context") or {}).items():
        if value:
            flat[f"tc.{field_name}"] = str(value)
    return flat


def group_diff_keys(
    diff_keys: list[str],
    node_param_keys: dict[str, list[str]] | None,
) -> list[tuple[str, list[str]]]:
    """Group ``node.param`` diff keys by node in execution order; prompt fields land in the ``""`` group."""
    if not node_param_keys:
        return [("", diff_keys)]
    groups: dict[str, list[str]] = {sname: [] for sname in node_param_keys}
    groups[""] = []
    for k in diff_keys:
        prefix = k.split(".", 1)[0]
        groups[prefix if prefix in groups else ""].append(k)
    return [(sname, sorted(keys)) for sname, keys in groups.items() if keys]
