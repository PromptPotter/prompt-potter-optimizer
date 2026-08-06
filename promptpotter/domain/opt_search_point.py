from __future__ import annotations

import copy
import re
import uuid
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any, Self

from pydantic import ConfigDict, Field, field_validator

from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.escalation_signals import RuntimeFailure, ValidationFailure
from promptpotter.domain.l1_layout import L1Layout, default_l1_layout
from promptpotter.domain.pipeline_schema import SCHEMA_DESCRIPTIONS_PARAM
from promptpotter.domain.search_point import (
    PARAM_FORBIDDEN_KEYS,
    SearchPoint,
    TaskDecomposition,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.validators import ValidatorOutcome

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import JobSearchPoint


# The `{{token}}` shape `compile_prompt` substitutes — the ONE definition every
# reader of a template's token set shares (dispatch-hub fill/validate, the
# optimizer prompt port guard in `validators/l1_strict.py`).
TEMPLATE_TOKEN_RE = re.compile(r"\{\{(\w+)\}\}")


class FewShotExample(StrictModel):
    """An input/output pair used as a few-shot demonstration."""

    input: str
    output: str
    explanation: str | None = None


class PromptTemplate(SearchPoint):
    """The scheme shared by job + optimizer prompts: the six ``render()`` decomposition fields
    (``PROMPT_STRING_FIELDS``), plus ``few_shot_examples`` and ``plan``, which render separately."""

    persona: str = ""
    task_intent: str = ""
    problem_description: str = ""
    instruction: str = ""
    thinking_style: str = ""
    answer_format: str = ""
    few_shot_examples: list[FewShotExample] = Field(default_factory=list)
    plan: str = Field(
        default="",
        description=(
            "Strategic frame written by ``l3_plan`` and read by every layer "
            "next round; persistent until the next L3 fire. Empty until L3 "
            "fires for the first time."
        ),
    )

    def render(self) -> str:
        parts = [v for f in PROMPT_STRING_FIELDS if (v := self._field_value(f))]
        block = self._render_few_shot_block()
        if block:
            parts.append(block)
        return "\n\n".join(parts)

    def _field_value(self, name: str) -> str:
        """Subclass override point — see ``OptSearchPoint`` for task-context splicing."""
        value: str = getattr(self, name)
        return value

    def _render_few_shot_block(self) -> str:
        if not self.few_shot_examples:
            return ""
        lines: list[str] = []
        for ex in self.few_shot_examples:
            lines.append(f"Input: {ex.input}\nOutput: {ex.output}")
            if ex.explanation:
                lines.append(f"Explanation: {ex.explanation}")
        return "\n".join(lines)

    def compile_prompt(self, **kwargs: str | int) -> str:
        """Any ``{{…}}`` left after substitution stays LITERAL: an evolved node prompt echoed into an
        optimizer template carries the backend's own placeholders, which the backend fills, not us."""
        text = self.render()
        for key, value in kwargs.items():
            text = text.replace("{{" + key + "}}", str(value))
        return text

    def prompt_fields(self) -> dict[str, str]:
        """String-only projection (no few-shot) for L1 summaries + validator diffs."""
        return {f: v for f in PROMPT_STRING_FIELDS if (v := getattr(self, f))}

    def prompt_field_dict(self) -> dict[str, Any]:
        """``plan`` rides here — and restores through ``from_prompt_fields`` — so a seed or fork inherits
        the L3 frame. It is not in ``render()``, so carrying it leaves render identity untouched."""
        d: dict[str, Any] = dict(self.prompt_fields())
        if self.few_shot_examples:
            d["few_shot_examples"] = [ex.model_dump() for ex in self.few_shot_examples]
        if self.plan:
            d["plan"] = self.plan
        return d

    @classmethod
    def from_prompt_fields(cls, fields: dict[str, Any], **kwargs: Any) -> Self:
        fields = dict(fields)
        fse = fields.pop("few_shot_examples", [])
        if fse and isinstance(fse[0], dict):
            fse = [FewShotExample(**ex) for ex in fse]
        return cls(few_shot_examples=fse, **fields, **kwargs)


class EvidenceGrounding(StrictModel):
    """Panel field + citation L1 declares to justify a mutation.

    The set of citable panels is not declared anywhere: it is DERIVED per round from the
    node's live layout (``dispatch.injections.registry.citable_fields``), so L1 can only
    cite a panel it was actually shown."""

    model_config = ConfigDict(frozen=True)

    field: str = Field(description="A citable panel named in the prompt, or stall_exploration.")
    citation: str = Field(description="Short string naming the panel entry cited.")


class WoundChannels(StrictModel):
    """Four wound streams + sticky L3 note; rendered by dispatch-hub injections."""

    l3_note: str = ""
    validation_failures: list[ValidationFailure] = Field(default_factory=list)
    runtime_failures: list[RuntimeFailure] = Field(default_factory=list)
    l2_guard_breaches: list[ValidatorOutcome] = Field(default_factory=list)
    l3_guard_breaches: list[ValidatorOutcome] = Field(default_factory=list)


class IndividualLineage(StrictModel):
    """Identity + provenance — set once at creation, never mutated."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    parent_id: str | None = None
    changes_description: str = ""
    source: str = Field(
        default="",
        description=(
            "'origin' / 'l1_generate' / 'l2_context' / 'l3_plan' / 'fork_seed' / 'campaign_origin'."
        ),
    )
    evidence_grounding: EvidenceGrounding | None = None


class L2L3Memory(StrictModel):
    """L2/L3-authored state that travels with the candidate.

    Bundled together because all four are authored by the escalation layers
    (L2 writes most; L3 writes ``wounds.l3_note`` + ``wounds.l3_guard_breaches``)
    and consumed by the dispatch-hub injections that compose the four
    optimizer prompts. ``OptSearchPoint.copy_memory_to`` deep-copies the
    whole bundle on L2/L3 adopt; ``OptSearchPoint.mutate`` (L1 child)
    inherits ``task_context`` + ``l1_overrides`` and resets the other two
    to defaults — the propagation asymmetry lives in those two methods.
    """

    wounds: WoundChannels = Field(
        default_factory=WoundChannels,
        description=(
            "Four wound streams (validation/runtime/l2-guard/l3-guard) + "
            "sticky L3 note. Rendered by dispatch-hub injections; absorbed "
            "by L2 next round."
        ),
    )
    l1_layout: L1Layout = Field(
        default_factory=default_l1_layout,
        description=(
            "L2-authored ordered list of injection slots that "
            "``DispatchHub.fill`` walks to compose the L1 optimizer prompt. "
            "L2's primary lever for changing what evidence L1 sees."
        ),
    )
    l1_overrides: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Per-individual L1 optimizer prompt overrides keyed by the surface "
            "field name (``persona``, ``instruction``, …). L2 writes here "
            "to nudge L1 without rewriting the shared optimizer prompt."
        ),
    )
    task_context: TaskDecomposition = Field(
        default_factory=TaskDecomposition,
        description=(
            "Persistent task-framing dict refined by ``l2_context`` and "
            "spliced around ``problem_description`` at render time. "
            "Accumulative: each L2 fire merges deltas rather than "
            "rewriting wholesale."
        ),
    )

    @field_validator("task_context", mode="before")
    @classmethod
    def _coerce_task_context(cls, v: Any) -> TaskDecomposition:
        return TaskDecomposition.coerce(v)


class OptSearchPoint(PromptTemplate):
    """Optimizer working state: prompt fields + lineage + L2/L3 memory."""

    model_config = ConfigDict(extra="forbid")

    lineage: IndividualLineage = Field(default_factory=IndividualLineage)
    memory: L2L3Memory = Field(default_factory=L2L3Memory)

    def copy_memory_to(self, target: OptSearchPoint) -> None:
        """Deep-copy the L2/L3 memory onto *target* for L2/L3 adopt."""
        target.memory = self.memory.model_copy(deep=True)

    def _field_value(self, name: str) -> str:
        """Splice ``task_context`` up/downstream context around ``problem_description``."""
        v: str = getattr(self, name)
        if name != "problem_description" or not v:
            return v
        tc = self.memory.task_context
        if not (tc.upstream_context or tc.downstream_context):
            return v
        return "\n\n".join(p for p in (tc.upstream_context, v, tc.downstream_context) if p)

    def to_job_search_point(
        self,
        base_pipeline_params: dict[str, Any] | None = None,
        *,
        schema: PipelineSchema,
    ) -> JobSearchPoint:
        """*schema* is REQUIRED: without one this produced a valid-looking point carrying neither the
        rendered prompt nor ``steps``, and that point is scored and archived like any other."""
        from promptpotter.domain.search_point import JobSearchPoint

        pp = copy.deepcopy(base_pipeline_params or {})
        active_steps = schema.active_steps
        prompt_nodes = schema.prompt_node_names()
        prompt_node = prompt_nodes[0] if prompt_nodes else ""
        if active_steps:
            pp["steps"] = list(active_steps)
        rendered = self.render()
        if rendered and prompt_node:
            pp.setdefault(prompt_node, {})["prompt"] = rendered

        # Fold the accumulated `output_schema_descriptions` into each node's real
        # `output_schema` prose and drop the virtual key — the wire carries a valid schema.
        # `schema` resolves the registry-declared case (`schema_family`, no inline schema).
        fold_schema_descriptions(pp, schema)

        pf = None
        if rendered and prompt_node:
            pf = {f: v for f, v in self.prompt_field_dict().items() if f != "few_shot_examples"}
            block = self._render_few_shot_block()
            if block:
                pf["few_shot_block"] = block

        return JobSearchPoint(
            pipeline_params=pp,
            prompt_fields=pf or None,
        )

    def mutate(self, **changes: Any) -> OptSearchPoint:
        """Prompt fields, ``task_context`` and ``l1_overrides`` inherit; ``wounds`` / ``l1_layout`` reset.
        Those two flow on L2/L3 adopt through ``copy_memory_to`` instead."""
        data: dict[str, Any] = {}
        for f in PROMPT_STRING_FIELDS:
            data[f] = changes.pop(f, getattr(self, f))
        fse = changes.pop("few_shot_examples", None)
        if fse is not None:
            if fse and isinstance(fse[0], dict):
                fse = [FewShotExample(**ex) for ex in fse]
            data["few_shot_examples"] = fse
        else:
            data["few_shot_examples"] = [ex.model_copy() for ex in self.few_shot_examples]
        data["memory"] = L2L3Memory(
            l1_overrides=changes.pop("l1_overrides", dict(self.memory.l1_overrides)),
            task_context=changes.pop("task_context", self.memory.task_context.to_dict()),
        )
        data["plan"] = changes.pop("plan", self.plan)
        data["lineage"] = IndividualLineage(
            parent_id=self.lineage.id,
            changes_description=changes.pop("changes_description", ""),
            source=changes.pop("source", ""),
            evidence_grounding=changes.pop("evidence_grounding", None),
        )
        data.update(changes)
        return OptSearchPoint(**data)


# --- pipeline_params shape (one declared source) ---

RESERVED_PIPELINE_PARAM_KEYS: frozenset[str] = frozenset({"steps"})
"""Keys in ``pipeline_params`` that are NOT node-config dicts. ``steps`` is the
wire scaffold (the active-node list every connector's outbound payload reads);
everything else is a ``{node: {param: value}}`` config block. The single source
of truth for the "is this a node config or reserved?" question — read this or
``node_config_items`` instead of re-deriving ``k == "steps" and isinstance(...)``
at each site."""


def node_config_items(pp: dict[str, Any] | None) -> Iterator[tuple[str, dict[str, Any]]]:
    """The canonical walk over a ``pipeline_params`` dict's tunable surface — skips the reserved
    wire keys and any non-dict value."""
    for k, v in (pp or {}).items():
        if k in RESERVED_PIPELINE_PARAM_KEYS or not isinstance(v, dict):
            continue
        yield k, v


def overlay_is_locked_axis_only(overlay: dict[str, Any] | None) -> bool:
    """A pure model/provider steer leaves the origin unchanged in every other respect, so the fork
    INHERITS the done C0 instead of re-scoring it. Gates the inherit path, not the taint."""
    keys = [k for _node, cfg in node_config_items(overlay) for k in cfg]
    return bool(keys) and all(k in PARAM_FORBIDDEN_KEYS for k in keys)


def overlay_sets_model_outside_allowed(
    overlay: dict[str, Any] | None, allowed_models: list[str] | None
) -> bool:
    """A ``provider`` edit has no allow-list that could sanction it, so it always counts."""
    allowed = set(allowed_models or [])
    for _node, cfg in node_config_items(overlay):
        if "provider" in cfg:
            return True
        model = cfg.get("model")
        if model is not None and model not in allowed:
            return True
    return False


def fold_schema_descriptions(pp: dict[str, Any] | None, schema: PipelineSchema) -> None:
    """*schema* is REQUIRED — a node declaring its schema by registry identity carries none to write
    on, and without it two opposite steers produced a byte-identical payload whose hashes collided."""
    for node, cfg in node_config_items(pp):
        descriptions = cfg.pop(SCHEMA_DESCRIPTIONS_PARAM, None)
        if not isinstance(descriptions, dict) or not descriptions:
            continue
        out_schema = cfg.get("output_schema")
        if not isinstance(out_schema, dict):
            resolved = schema.get_node(node)
            out_schema = (
                copy.deepcopy(resolved.output_schema.json_schema)
                if resolved and resolved.output_schema
                else None
            )
            if not isinstance(out_schema, dict) or not out_schema:
                continue
            cfg["output_schema"] = out_schema
        props = out_schema.get("properties")
        if not isinstance(props, dict):
            continue
        for field, text in descriptions.items():
            if (
                field in props
                and isinstance(props[field], dict)
                and isinstance(text, str)
                and text.strip()
            ):
                props[field] = {**props[field], "description": text}


# --- Diff helpers (views) ---


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
#
# `candidate_delta` answers "what changed". This answers "is that the same thing we already
# tried" — which is a different question, because a re-proposal never arrives as a repeated
# string. It arrives as the same idea rewritten into a DIFFERENT FIELD. Measured on
# `justlogic-d234`: one idea ("exhaust modus tollens / disjunctive syllogism before answering
# Uncertain") was proposed in 8 consecutive rounds, landing in `instruction`, then
# `thinking_style`, then `output_schema_descriptions.reasoning`, then `task_intent`. Every
# exact-match mechanism in the loop saw eight distinct mutations.
#
# The signal that survives the rewrite is vocabulary: an idea keeps its content words when it
# changes field and phrasing. So the fingerprint is the content-word SET of the VALUES written
# — never the field names (that inverts the test into "touched the same field") and never the
# render stem (sized for a human to recognise a row, far too short to carry an idea).
#
# Deliberately a blunt lexical test, not a semantic one: it runs on every candidate and every
# render with no LLM call. It lives here, beside `candidate_delta`, because all three consumers
# of "already tried" must share one definition — the round-local dedup, the cross-round repeat
# gate (`detect_invariants`), and the ALREADY TRIED panel. Split, they drift, and a re-proposal
# rejected by one is rendered as new by another.
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
    evidence the generator is exploring. Left lexical: the alternatives buy little for their cost."""
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


__all__ = [
    "EvidenceGrounding",
    "FewShotExample",
    "IndividualLineage",
    "L2L3Memory",
    "OptSearchPoint",
    "PromptTemplate",
    "WoundChannels",
    "build_candidate_flat",
    "flatten_sp_summary",
    "group_diff_keys",
    "node_config_items",
    "overlay_is_locked_axis_only",
    "overlay_sets_model_outside_allowed",
    "variant_prose_written",
]
