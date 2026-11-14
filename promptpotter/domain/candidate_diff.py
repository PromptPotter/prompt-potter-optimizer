from __future__ import annotations

import difflib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from promptpotter.domain.opt_search_point import Locus, NodePrompt
from promptpotter.domain.pipeline_params import PipelineParams
from promptpotter.domain.pipeline_schema import described_field, description_path
from promptpotter.domain.search_point import SHOTS_KEY


def parent_param_value(parent_cfg: dict[str, Any], param: str) -> Any:
    """A folded description key lives inside the schema: unread there, re-proposed prose reads as a mutation."""
    path = description_path(param)
    if path is None or param in parent_cfg:
        return parent_cfg.get(param)
    return (described_field(parent_cfg.get("output_schema"), path) or {}).get("description", "")


@dataclass(frozen=True)
class CandidateDelta:
    """A locus left alone is absent; ``""`` EMPTIED a field, ``()`` dropped a node's every shot; empty: a clone."""

    prompt: dict[Locus, str]
    params: dict[tuple[str, str], Any]
    shots: dict[str, tuple[str, ...]]

    def __bool__(self) -> bool:
        return bool(self.prompt or self.params or self.shots)

    def signature(self) -> tuple[Any, ...]:
        return (
            tuple(sorted(self.prompt.items())),
            tuple(
                sorted((n, p, json.dumps(v, sort_keys=True)) for (n, p), v in self.params.items())
            ),
            tuple(sorted(self.shots.items())),
        )


def _is_edit(value: object, parent_value: object) -> bool:
    # An overlay is a PARTIAL in which a model spells "unchanged" as "", so an empty string is no edit.
    return value is not None and value != "" and value != parent_value


def candidate_delta(
    child: Mapping[str, NodePrompt],
    parent: Mapping[str, NodePrompt],
    pipeline_overlay: Mapping[str, Mapping[str, Any]] | None,
    parent_pp: PipelineParams | None,
) -> CandidateDelta:
    """Both prompt maps are WHOLE (an absent field is empty, an absent node sets nothing)."""
    parent_cfg = parent_pp.laid_nodes if parent_pp else {}
    nodes = sorted(child.keys() | parent.keys())
    empty = NodePrompt()

    def fields(prompts: Mapping[str, NodePrompt], node: str) -> Mapping[str, str]:
        return prompts.get(node, empty).fields

    return CandidateDelta(
        prompt={
            Locus(node, f): text
            for node in nodes
            for f in sorted(fields(child, node).keys() | fields(parent, node).keys())
            if (text := fields(child, node).get(f, "")) != fields(parent, node).get(f, "")
        },
        params={
            (n, p): v
            for n, cfg in (pipeline_overlay or {}).items()
            for p, v in cfg.items()
            if _is_edit(v, parent_param_value(parent_cfg.get(n) or {}, p))
        },
        shots={
            node: tuple(shots)
            for node in nodes
            if (shots := child.get(node, empty).shots) != parent.get(node, empty).shots
        },
    )


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
IDEA_MIN_TOKEN_CHARS: int = 4
# Fewer content words cannot support a ratio: at 3 tokens one shared word is already 33%.
IDEA_MIN_TOKENS: int = 6
# Overlap coefficient, NOT Jaccard: the union scores a short restatement of a long idea low.
IDEA_MATCH_MARK: float = 0.6
# Stricter than MARK: marking is free and reversible, a wrong rejection costs a slot invisibly.
# Never tighten it to catch missed restatements: on `changes_description` the fingerprint sits at chance.
IDEA_MATCH_REJECT = 0.70


def idea_fingerprint(values: Iterable[str]) -> frozenset[str]:
    """Catches a re-proposal that REUSES vocabulary only: a zero repeat count is no evidence of exploration."""
    words = re.findall(r"[a-z]+", " ".join(values).lower())
    return frozenset(w for w in words if len(w) >= IDEA_MIN_TOKEN_CHARS and w not in IDEA_STOPWORDS)


def same_idea(a: frozenset[str], b: frozenset[str], *, threshold: float) -> bool:
    if len(a) < IDEA_MIN_TOKENS or len(b) < IDEA_MIN_TOKENS:
        return False
    return len(a & b) / min(len(a), len(b)) >= threshold


def candidate_idea(
    child: Mapping[str, NodePrompt],
    parent_prompts: Mapping[str, NodePrompt],
    pipeline_overlay: Mapping[str, Mapping[str, Any]] | None,
    parent_pp: PipelineParams | None,
) -> frozenset[str]:
    """The parent is subtracted WHOLE: the task's vocabulary in unchanged fields convicts unrelated rewrites."""
    delta = candidate_delta(child, parent_prompts, pipeline_overlay, parent_pp)
    parent = parent_pp.laid_nodes if parent_pp else {}
    written = idea_fingerprint([*delta.prompt.values(), *map(str, delta.params.values())])
    carried = idea_fingerprint(
        [text for prompt in parent_prompts.values() for text in prompt.fields.values()]
        + [
            str(prior)
            for n, p in delta.params
            if (prior := parent_param_value(parent.get(n) or {}, p)) is not None
        ]
    )
    return written - carried


def _fmt_pp_val(v: object) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def flatten_sp_summary(pp: Mapping[str, Mapping[str, Any]] | None) -> dict[str, str]:
    """A nested param flattens ONE level further (``node.param.key``); ``group_diff_keys`` splits on the first dot."""
    flat: dict[str, str] = {}
    for k, v in (pp or {}).items():
        for sub_k, sub_v in v.items():
            if isinstance(sub_v, dict):
                for leaf_k, leaf_v in sub_v.items():
                    flat[f"{k}.{sub_k}.{leaf_k}"] = _fmt_pp_val(leaf_v)
            else:
                flat[f"{k}.{sub_k}"] = _fmt_pp_val(sub_v)
    return flat


def flatten_prompts(prompts: Mapping[str, NodePrompt]) -> dict[str, str]:
    """A prompt field flattens as ``node.field``, its shots as ``node.shots``, beside its node's params."""
    flat: dict[str, str] = {}
    for node, prompt in prompts.items():
        flat.update({Locus(node, f).name: text for f, text in prompt.fields.items()})
        if prompt.shots:
            flat[Locus(node, SHOTS_KEY).name] = ", ".join(prompt.shots)
    return flat


def build_candidate_flat(
    parent: dict[str, str],
    *,
    pipeline_overlay: dict[str, dict[str, Any]] | None,
    prompt_flat: Mapping[str, str],
) -> dict[str, str]:
    """*prompt_flat* is ``flatten_prompts`` of the candidate's prompts."""
    flat = parent.copy()
    if pipeline_overlay:
        flat.update(flatten_sp_summary(pipeline_overlay))
    flat.update(prompt_flat)
    return flat


def group_diff_keys(
    diff_keys: list[str],
    node_param_keys: dict[str, list[str]] | None,
) -> list[tuple[str, list[str]]]:
    """A key whose node prefix is not among *node_param_keys* lands in the ``""`` group."""
    if not node_param_keys:
        return [("", diff_keys)]
    groups: dict[str, list[str]] = {sname: [] for sname in node_param_keys}
    groups[""] = []
    for k in diff_keys:
        prefix = k.split(".", 1)[0]
        groups[prefix if prefix in groups else ""].append(k)
    return [(sname, sorted(keys)) for sname, keys in groups.items() if keys]
