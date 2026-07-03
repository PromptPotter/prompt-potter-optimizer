"""OptSearchPoint — optimizer working state; mutable, serialized via ``model_dump()``."""

from __future__ import annotations

import copy
import uuid
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.escalation_signals import RuntimeFailure, ValidationFailure
from promptpotter.domain.l1_layout import L1Layout, default_l1_layout
from promptpotter.domain.search_point import SearchPoint, TaskDecomposition
from promptpotter.domain.validators import ValidatorOutcome

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import JobSearchPoint


class FewShotExample(BaseModel):
    """An input/output pair used as a few-shot demonstration."""

    input: str
    output: str
    explanation: str | None = None


class PromptTemplate(SearchPoint):
    """The prompt scheme shared by job + optimizer prompts: the six ``render()``
    decomposition fields (``PROMPT_STRING_FIELDS``) plus ``few_shot_examples`` and
    ``plan`` (handled separately — few-shot appended by ``render()``, ``plan``
    injected via its own signal)."""

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
        """Assemble non-empty decomposition fields, double-newline-joined."""
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
        """Render and substitute the supplied ``{{variable}}`` placeholders.

        Any ``{{…}}`` left after substitution is kept as literal text — prompt-field
        content legitimately carries braces that are NOT optimizer slots. The chief
        case: an evolved node prompt echoed into an optimizer template (the
        ``rendered_prompt`` injection) embeds the backend's own ``{{query}}`` /
        ``{{combined_text}}`` injection placeholders, which the target backend fills,
        not PromptPotter. Authored-template slot typos are caught at load time by
        ``dispatch.validate_template`` (which knows the INJECTIONS vocabulary); this
        method only fills what it is handed.
        """
        text = self.render()
        for key, value in kwargs.items():
            text = text.replace("{{" + key + "}}", str(value))
        return text

    def prompt_fields(self) -> dict[str, str]:
        """String-only projection (no few-shot) for L1 summaries + validator diffs."""
        return {f: v for f in PROMPT_STRING_FIELDS if (v := getattr(self, f))}

    def prompt_field_dict(self) -> dict[str, Any]:
        """``prompt_fields()`` plus ``few_shot_examples`` + ``plan`` — the L1-roundtrip
        shape. ``plan`` rides here (and restores via ``from_prompt_fields``) so a seed /
        campaign-from-origin fork inherits the L3 strategic frame instead of starting at
        ``plan=""``. ``plan`` is NOT in ``render()`` (it's injected via its own signal),
        so carrying it leaves the render-identity gate + content hash untouched."""
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


# Panel names an L1 variant may cite in ``EvidenceGrounding.field`` to justify a mutation.
# Every name except the sentinel is a DispatchHub injection slot of the same name, rendered by
# ``application/optimization/dispatch/hub/injections/`` (``@signal`` decorator) — a citable
# panel MUST be renderable into L1's prompt, or the contract invites fabricated citations.
#   • stall_exploration — not a panel: the escape-hatch sentinel, valid only when
#     ``escalation_panel.exploration_budget`` ∈ {normal, wide} (enforced in ``validators/l1_behavior.py``).
EVIDENCE_GROUNDING_FIELDS: frozenset[str] = frozenset(
    {
        "axis_memory",
        "task_context",
        "plan",
        "critique",
        "archive_top_runs",
        "rare_hit_samples",
        "escalation_panel",
        # raw evidence: complete failing samples incl. the model's own reasoning —
        # the one panel that shows WHERE a deduction broke, not a distillation of it
        "sample_transcripts",
        # escape-hatch sentinel (gated on escalation_panel.exploration_budget)
        "stall_exploration",
    }
)


class EvidenceGrounding(BaseModel):
    """Panel field + citation L1 declares to justify a mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str = Field(description="One of EVIDENCE_GROUNDING_FIELDS.")
    citation: str = Field(description="Short string naming the panel entry cited.")


class L1SupplementalRule(BaseModel):
    """L2-authored situational rule rendered inline in L1's instruction."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1, max_length=40)
    body: str = Field(min_length=20, max_length=400)
    citation: str = Field(min_length=1, max_length=200)


class L1SituationalExample(BaseModel):
    """Worked example pinned to a ``trigger_id`` (auto-trigger or L2-authored rule)."""

    model_config = ConfigDict(extra="forbid")

    trigger_id: str = Field(min_length=1, max_length=40)
    parent_excerpt: str = Field(default="", max_length=300)
    rejected: str = Field(default="", max_length=300)
    accepted: str = Field(default="", max_length=300)
    why: str = Field(default="", max_length=200)


class WoundChannels(BaseModel):
    """Four wound streams + sticky L3 note; rendered by dispatch-hub injections."""

    model_config = ConfigDict(extra="forbid")

    l3_note: str = ""
    validation_failures: list[ValidationFailure] = Field(default_factory=list)
    runtime_failures: list[RuntimeFailure] = Field(default_factory=list)
    l2_guard_breaches: list[ValidatorOutcome] = Field(default_factory=list)
    l3_guard_breaches: list[ValidatorOutcome] = Field(default_factory=list)


class IndividualLineage(BaseModel):
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


class L2L3Memory(BaseModel):
    """L2/L3-authored state that travels with the candidate.

    Bundled together because all six are authored by the escalation layers
    (L2 writes most; L3 writes ``wounds.l3_note`` + ``wounds.l3_guard_breaches``)
    and consumed by the dispatch-hub injections that compose the four
    optimizer prompts. ``OptSearchPoint.copy_memory_to`` deep-copies the
    whole bundle on L2/L3 adopt; ``OptSearchPoint.mutate`` (L1 child)
    inherits ``task_context`` + ``l1_overrides`` and resets the other four
    to defaults — the propagation asymmetry lives in those two methods.
    """

    model_config = ConfigDict(extra="forbid")

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
            "``DispatchHub.fill`` walks to compose the L1 meta-prompt. "
            "L2's primary lever for changing what evidence L1 sees."
        ),
    )
    l1_overrides: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Per-individual L1 meta-prompt overrides keyed by the surface "
            "field name (``persona``, ``instruction``, …). L2 writes here "
            "to nudge L1 without rewriting the shared meta-prompt."
        ),
    )
    l1_supplemental_rules: list[L1SupplementalRule] = Field(
        default_factory=list,
        description=(
            "L2-authored situational rules rendered inline in L1's "
            "instruction. Cumulative across rounds; L3 may prune."
        ),
    )
    l1_situational_examples: list[L1SituationalExample] = Field(
        default_factory=list,
        description=(
            "Worked examples pinned to a ``trigger_id`` (auto-trigger or "
            "L2-authored rule). Rendered alongside the matching rule."
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
        schema: PipelineSchema | None = None,
    ) -> JobSearchPoint:
        """Render → inject into pipeline_params → frozen JobSearchPoint. Pipeline
        composition reads *schema*, never inferred from ``base_pipeline_params``."""
        from promptpotter.domain.search_point import JobSearchPoint

        pp = copy.deepcopy(base_pipeline_params or {})
        active_steps = schema.active_steps if schema else ()
        prompt_nodes = schema.prompt_node_names() if schema else []
        prompt_node = prompt_nodes[0] if prompt_nodes else ""
        if active_steps:
            pp["steps"] = list(active_steps)
        rendered = self.render()
        if rendered and prompt_node:
            pp.setdefault(prompt_node, {})["prompt"] = rendered

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
        """Child OSP: prompt fields + ``task_context`` + ``l1_overrides`` inherit
        from parent; ``wounds``/``l1_layout``/``l1_supplemental_rules``/
        ``l1_situational_examples`` reset to defaults. The reset four flow on
        L2/L3 adopt via ``copy_memory_to`` instead."""
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
    """Yield ``(node_name, config)`` for each node-config block in *pp*, skipping
    the reserved wire keys (``steps``) and any non-dict value. The canonical walk
    over the tunable surface of a ``pipeline_params`` dict."""
    for k, v in (pp or {}).items():
        if k in RESERVED_PIPELINE_PARAM_KEYS or not isinstance(v, dict):
            continue
        yield k, v


# --- Diff helpers (views) ---


def _fmt_pp_val(v: object) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def flatten_sp_summary(pp: dict[str, Any] | None) -> dict[str, str]:
    """``{node: {param: value}}`` → ``{node.param: value}`` display dict."""
    flat: dict[str, str] = {}
    for k, v in node_config_items(pp):
        for sub_k, sub_v in v.items():
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
    "EVIDENCE_GROUNDING_FIELDS",
    "EvidenceGrounding",
    "FewShotExample",
    "IndividualLineage",
    "L1SituationalExample",
    "L1SupplementalRule",
    "L2L3Memory",
    "OptSearchPoint",
    "PromptTemplate",
    "WoundChannels",
    "build_candidate_flat",
    "flatten_sp_summary",
    "group_diff_keys",
]
