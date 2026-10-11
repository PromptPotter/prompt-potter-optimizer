from __future__ import annotations

import difflib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any

from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import described_field, description_path
from promptpotter.domain.search_point import PROMPT_STRING_FIELDS
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
]


@shapes_optimizer_prompt
def parent_param_value(parent_cfg: dict[str, Any], param: str) -> Any:
    """A folded description key lives inside the schema: unread there, re-proposed prose reads as a mutation."""
    path = description_path(param)
    if path is None or param in parent_cfg:
        return parent_cfg.get(param)
    return (described_field(parent_cfg.get("output_schema"), path) or {}).get("description", "")


@shapes_optimizer_prompt
@dataclass(frozen=True)
class CandidateDelta:
    """A locus left alone is absent; ``""`` EMPTIED a field, ``()`` dropped every shot; empty: a clone."""

    prompt: dict[str, str]
    params: dict[tuple[str, str], Any]
    shots: tuple[int, ...] | None

    def __bool__(self) -> bool:
        return bool(self.prompt or self.params) or self.shots is not None

    def signature(self) -> tuple[Any, ...]:
        return (
            tuple(sorted(self.prompt.items())),
            tuple(
                sorted((n, p, json.dumps(v, sort_keys=True)) for (n, p), v in self.params.items())
            ),
            self.shots,
        )


@shapes_optimizer_prompt
def _is_edit(value: object, parent_value: object) -> bool:
    # An overlay is a PARTIAL in which a model spells "unchanged" as "", so an empty string is no edit.
    return value is not None and value != "" and value != parent_value


@shapes_optimizer_prompt
def candidate_delta(
    child_fields: Mapping[str, Any],
    parent_fields: Mapping[str, Any],
    pipeline_overlay: dict[str, Any] | None,
    parent_pp: Mapping[str, Any] | None,
) -> CandidateDelta:
    """Both field maps are WHOLE prompts (an absent field is empty); an absent ``shot_ids`` is no shot edit."""
    parent = parent_pp or {}
    shots = child_fields.get("shot_ids")
    return CandidateDelta(
        prompt={
            f: text
            for f in PROMPT_STRING_FIELDS
            if (text := str(child_fields.get(f) or "")) != str(parent_fields.get(f) or "")
        },
        params={
            (n, p): v
            for n, cfg in node_config_items(pipeline_overlay)
            for p, v in cfg.items()
            if _is_edit(v, parent_param_value(parent.get(n) or {}, p))
        },
        shots=(
            tuple(shots)
            if shots is not None and list(shots) != list(parent_fields.get("shot_ids", ()))
            else None
        ),
    )


@shapes_optimizer_prompt
def changed_words(parent: str, child: str) -> str:
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
IDEA_MIN_TOKEN_CHARS: Annotated[int, shapes_optimizer_prompt] = 4
# Fewer content words cannot support a ratio: at 3 tokens one shared word is already 33%.
IDEA_MIN_TOKENS: Annotated[int, shapes_optimizer_prompt] = 6
# Overlap coefficient, NOT Jaccard: the union scores a short restatement of a long idea low.
IDEA_MATCH_MARK: Annotated[float, shapes_optimizer_prompt] = 0.6
# Stricter than MARK: marking is free and reversible, a wrong rejection costs a slot invisibly.
# Never tighten it to catch missed restatements: on `changes_description` the fingerprint sits at chance.
IDEA_MATCH_REJECT = 0.70


@shapes_optimizer_prompt
def idea_fingerprint(values: Iterable[str]) -> frozenset[str]:
    """Catches a re-proposal that REUSES vocabulary only: a zero repeat count is no evidence of exploration."""
    words = re.findall(r"[a-z]+", " ".join(values).lower())
    return frozenset(w for w in words if len(w) >= IDEA_MIN_TOKEN_CHARS and w not in IDEA_STOPWORDS)


@shapes_optimizer_prompt
def same_idea(a: frozenset[str], b: frozenset[str], *, threshold: float) -> bool:
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
    """The parent is subtracted WHOLE: the task's vocabulary in unchanged fields convicts unrelated rewrites."""
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
    """A nested param flattens ONE level further (``node.param.key``); ``group_diff_keys`` splits on the first dot."""
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
    """Prompt fields land in the ``""`` group."""
    if not node_param_keys:
        return [("", diff_keys)]
    groups: dict[str, list[str]] = {sname: [] for sname in node_param_keys}
    groups[""] = []
    for k in diff_keys:
        prefix = k.split(".", 1)[0]
        groups[prefix if prefix in groups else ""].append(k)
    return [(sname, sorted(keys)) for sname, keys in groups.items() if keys]
