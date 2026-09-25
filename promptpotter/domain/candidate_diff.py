"""What a candidate CHANGED, and whether that change is an idea the cycle already tried.

Two questions, deliberately in one module: ``candidate_delta`` answers *what changed* and
``candidate_idea`` answers *is that the same thing* — and all three consumers of "already tried"
(round-local dedup, the cross-round repeat gate, the ALREADY TRIED panel) must share both
definitions or a re-proposal one rejects is rendered as new by another. The flatten/group helpers
below are the render side of the same delta."""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any

from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import described_field, description_path
from promptpotter.shared.hashing import shapes_optimizer_prompt

__all__ = [
    "IDEA_MATCH_MARK",
    "IDEA_MATCH_REJECT",
    "IDEA_MIN_TOKENS",
    "IDEA_MIN_TOKEN_CHARS",
    "IDEA_STOPWORDS",
    "CandidateDelta",
    "build_candidate_flat",
    "candidate_delta",
    "candidate_idea",
    "changed_words",
    "flatten_sp_summary",
    "group_diff_keys",
    "idea_fingerprint",
    "parent_param_value",
    "same_idea",
    "variant_prose_written",
]


@shapes_optimizer_prompt
def parent_param_value(parent_cfg: dict[str, Any], param: str) -> Any:
    """A description key is virtual once folded — its prose then lives inside the schema, so a
    parent carrying none reads it off its field, or re-proposing existing prose reads as a mutation."""
    path = description_path(param)
    if path is None or param in parent_cfg:
        return parent_cfg.get(param)
    return (described_field(parent_cfg.get("output_schema"), path) or {}).get("description", "")


@shapes_optimizer_prompt
@dataclass(frozen=True)
class CandidateDelta:
    """What a candidate changed against its parent: prompt field → new text, ``(node, param)`` →
    new value. Empty ⇔ the candidate is a clone."""

    prompt: dict[str, str]
    params: dict[tuple[str, str], Any]

    def __bool__(self) -> bool:
        return bool(self.prompt or self.params)

    def signature(self) -> tuple[Any, ...]:
        """Hashable identity — two siblings with one signature are one candidate."""
        return (
            tuple(sorted(self.prompt.items())),
            tuple(
                sorted((n, p, json.dumps(v, sort_keys=True)) for (n, p), v in self.params.items())
            ),
        )


@shapes_optimizer_prompt
def _is_edit(value: object, parent_value: object) -> bool:
    # An empty string is "no edit", never "clear the field": every reader of a delta — the gates,
    # the SP table, the memory panels — must agree on this, and a model spells "unchanged" as "".
    return value is not None and value != "" and value != parent_value


@shapes_optimizer_prompt
def candidate_delta(
    child_fields: Mapping[str, Any],
    parent_fields: Mapping[str, Any],
    pipeline_overlay: dict[str, Any] | None,
    parent_pp: Mapping[str, Any] | None,
) -> CandidateDelta:
    """The ONE definition of what a candidate changed — the parse guard, round-local dedup, the
    repeat gate, the ALREADY TRIED panel and earned blocks all read it. ``child_fields`` may be the
    child's whole prompt or only the fields a variant wrote."""
    parent = parent_pp or {}
    return CandidateDelta(
        prompt={
            f: str(v)
            for f in PROMPT_STRING_FIELDS
            if _is_edit(v := child_fields.get(f), parent_fields.get(f))
        },
        params={
            (n, p): v
            for n, cfg in node_config_items(pipeline_overlay)
            for p, v in cfg.items()
            if _is_edit(v, parent_param_value(parent.get(n) or {}, p))
        },
    )


@shapes_optimizer_prompt
def changed_words(parent: str, child: str) -> str:
    """What an edit to ONE prose field wrote and cut, word by word — ``+"…"`` / ``-"…"`` spans. A
    stem of the new VALUE names nothing: an edit that keeps the parent's opening renders the
    parent's own words, so every such attempt reads as the same untouched text."""
    old, new = parent.split(), child.split()
    spans: list[str] = []
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        if i2 > i1:
            spans.append(f'-"{" ".join(old[i1:i2])}"')
        if j2 > j1:
            spans.append(f'+"{" ".join(new[j1:j2])}"')
    return " ".join(spans)


def variant_prose_written(variant: dict[str, Any]) -> dict[str, str]:
    """A prose mutation rides two different carriers depending on whether the campaign evolves a
    target prompt or a node's own template — reading one answers inverted on the other kind."""
    written = {
        f: str(v)
        for f, v in (variant.get("prompt_fields_updates") or {}).items()
        if f in PROMPT_STRING_FIELDS and v
    }
    for n, cfg in node_config_items(variant.get("pipeline_overlay")):
        for p, v in cfg.items():
            if p in PROMPT_STRING_FIELDS and v:
                written[f"{n}.{p}"] = str(v)
    return written


# --- the IDEA a delta carries ----------------------------------------------
IDEA_STOPWORDS: Annotated[frozenset[str], shapes_optimizer_prompt] = frozenset(
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
IDEA_MIN_TOKEN_CHARS: Annotated[int, shapes_optimizer_prompt] = 4
# A fingerprint below this many content words cannot support a ratio: with 3 tokens one
# shared word is 33% and two is 67%, so short mutations would pair with anything.
IDEA_MIN_TOKENS: Annotated[int, shapes_optimizer_prompt] = 6
# Overlap-coefficient floor for "the same idea". NOT Jaccard: the two values compared are
# routinely very different lengths — a one-clause `thinking_style` nudge against a rewritten
# `reasoning` paragraph — and Jaccard divides by the union, so a short restatement of a long
# idea scores low however completely it is contained. Overlap asks what matters: is the
# smaller essentially a subset of the larger?
IDEA_MATCH_MARK: Annotated[float, shapes_optimizer_prompt] = 0.6
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


@shapes_optimizer_prompt
def idea_fingerprint(values: Iterable[str]) -> frozenset[str]:
    """It catches a re-proposal that REUSES vocabulary, and nothing else — a zero repeat count is not
    evidence the generator is exploring.

    It IS blind to within-cycle continuity, measured: a forced-choice judge matches an edit to its
    own cycle's next round well above chance across pairs this test calls distinct. That argues for
    a better signal on the VALUES, never for tightening ``IDEA_MATCH_REJECT`` — the same reading
    cannot separate a re-proposal from the critique steering two rounds at one failure. Run on
    ``changes_description`` instead it sits at chance, so build no successor on that prose.

    **Measured miss rate, and the successor is HELD rather than owed:** content-word overlap caught
    **0 of 15** real re-proposal pairs on the banked corpus, so ``l1_n_repeat`` reads 0 while the
    generator restates one hypothesis indefinitely — worse than no counter, because a 0 reads as
    hygiene. Every fix is a NEW mechanism (embeddings, an LLM judge per pair) and the closing phase
    opens none. The cheaper twin is already planned — ``l1_generate`` semantic widening, owed
    anyway. Revisit only if widening lands and repeats persist."""
    words = re.findall(r"[a-z]+", " ".join(values).lower())
    return frozenset(w for w in words if len(w) >= IDEA_MIN_TOKEN_CHARS and w not in IDEA_STOPWORDS)


@shapes_optimizer_prompt
def same_idea(a: frozenset[str], b: frozenset[str], *, threshold: float) -> bool:
    """*threshold* is explicit at every call site on purpose — see :data:`IDEA_MATCH_REJECT`."""
    if len(a) < IDEA_MIN_TOKENS or len(b) < IDEA_MIN_TOKENS:
        return False
    return len(a & b) / min(len(a), len(b)) >= threshold


@shapes_optimizer_prompt
def candidate_idea(
    child_fields: Mapping[str, Any],
    parent_fields: Mapping[str, Any],
    pipeline_overlay: dict[str, Any] | None,
    parent_pp: Mapping[str, Any] | None,
) -> frozenset[str]:
    """The parent is subtracted WHOLE, every prompt field: the task's own vocabulary lives across the
    fields this candidate did not change, and left in it convicts unrelated rewrites of repeating."""
    delta = candidate_delta(child_fields, parent_fields, pipeline_overlay, parent_pp)
    parent = parent_pp or {}
    written = idea_fingerprint([*delta.prompt.values(), *map(str, delta.params.values())])
    carried = idea_fingerprint(
        [str(v) for v in parent_fields.values() if v]
        + [
            str(prior)
            for n, p in delta.params
            if (prior := parent_param_value(parent.get(n) or {}, p)) is not None
        ]
    )
    return written - carried


@shapes_optimizer_prompt
def _fmt_pp_val(v: object) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


@shapes_optimizer_prompt
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
    """Merge candidate overrides onto parent across the two disjoint keyspaces: ``node.param``
    (pipeline_params) and bare prompt fields."""
    flat = parent.copy()
    if pp := candidate_meta.get("pipeline_overlay"):
        flat.update(flatten_sp_summary(pp))
    for field_name, value in (candidate_meta.get("prompt_fields") or {}).items():
        if value:
            flat[field_name] = str(value)
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
