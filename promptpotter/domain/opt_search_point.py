"""OptSearchPoint — optimizer working state; mutable, serialized via ``model_dump()``."""

from __future__ import annotations

import copy
import re
import uuid
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.escalation_signals import RuntimeFailure, ValidationFailure
from promptpotter.domain.l1_layout import L1Layout, default_l1_layout
from promptpotter.domain.pipeline_schema import SCHEMA_DESCRIPTIONS_PARAM
from promptpotter.domain.search_point import (
    PARAM_FORBIDDEN_KEYS,
    SearchPoint,
    TaskDecomposition,
)
from promptpotter.domain.validators import ValidatorOutcome

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import JobSearchPoint


# The `{{token}}` shape `compile_prompt` substitutes — the ONE definition every
# reader of a template's token set shares (dispatch-hub fill/validate, the
# meta-prompt port guard in `validators/l1_strict.py`).
TEMPLATE_TOKEN_RE = re.compile(r"\{\{(\w+)\}\}")


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


class EvidenceGrounding(BaseModel):
    """Panel field + citation L1 declares to justify a mutation.

    The set of citable panels is not declared anywhere: it is DERIVED per round from the
    node's live layout (``dispatch.hub.injections.registry.citable_fields``), so L1 can only
    cite a panel it was actually shown."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str = Field(description="A citable panel named in the prompt, or stall_exploration.")
    citation: str = Field(description="Short string naming the panel entry cited.")


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

    Bundled together because all four are authored by the escalation layers
    (L2 writes most; L3 writes ``wounds.l3_note`` + ``wounds.l3_guard_breaches``)
    and consumed by the dispatch-hub injections that compose the four
    optimizer prompts. ``OptSearchPoint.copy_memory_to`` deep-copies the
    whole bundle on L2/L3 adopt; ``OptSearchPoint.mutate`` (L1 child)
    inherits ``task_context`` + ``l1_overrides`` and resets the other two
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
        """Child OSP: prompt fields + ``task_context`` + ``l1_overrides`` inherit
        from parent; ``wounds``/``l1_layout`` reset to defaults. The reset two flow
        on L2/L3 adopt via ``copy_memory_to`` instead."""
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


def overlay_is_locked_axis_only(overlay: dict[str, Any] | None) -> bool:
    """True iff *overlay* edits at least one node AND every key it sets is a
    locked axis (``model``/``provider``). A pure model/provider steer — the origin
    is unchanged in every other respect, so a param-only fork **inherits** the done
    origin's C0 rather than re-scoring it (a non-locked param edit genuinely changes
    the origin measurement → re-score). Gates the inherit path, not the taint."""
    keys = [k for _node, cfg in node_config_items(overlay) for k in cfg]
    return bool(keys) and all(k in PARAM_FORBIDDEN_KEYS for k in keys)


def overlay_sets_model_outside_allowed(
    overlay: dict[str, Any] | None, allowed_models: list[str] | None
) -> bool:
    """True iff a seed ``pipeline_overlay`` steers the inner-optimizer model/provider
    to a value the origin has NOT sanctioned — the ADR-0005 babysit trigger.

    ``allowed_models`` is the origin's declared allow-list (``CampaignConfig``).
    A steer to a model IN the set is a clean human choice (no taint); a steer to a
    model OUTSIDE it — or ANY model steer when the set is empty (nothing sanctioned,
    the restrictive default) — taints the branch. A ``provider`` edit has no allow-list
    to sanction it, so it always counts. The optimizer never searches these axes at all
    (``PARAM_FORBIDDEN_KEYS`` invariant); this governs only the direct human steer."""
    allowed = set(allowed_models or [])
    for _node, cfg in node_config_items(overlay):
        if "provider" in cfg:
            return True
        model = cfg.get("model")
        if model is not None and model not in allowed:
            return True
    return False


def fold_schema_descriptions(
    pp: dict[str, Any] | None, schema: PipelineSchema | None = None
) -> None:
    """Fold each node's virtual ``output_schema_descriptions`` into its wire
    ``output_schema`` prose, in place, then remove the virtual key.

    This is the apply site for the core structured-output `description` lever
    (`docs/concepts/structured-output.md`): the optimizer accumulates
    ``output_schema_descriptions`` as a normal ``object`` param (so
    ``apply_node_overlay`` merges it one level across generations), and here — at the
    render→wire seam — its entries are written onto the node's REAL
    ``output_schema.properties[field].description`` before the connector sends it.
    Assignment onto EXISTING properties only: a field the optimizer invented is dropped
    rather than reaching the wire (names are the locked contract). The virtual key is then
    deleted, so the backend receives a valid schema and no pseudo-param, and identity still
    separates two description sets because the folded ``output_schema`` differs.

    A node may declare its schema INLINE (``config.output_schema``) or by REGISTRY
    IDENTITY (``config.schema_family`` — TermNorm's ``entity_profiling`` / ``llm_ranking``).
    In the registry case the config carries no schema to write on, so *schema* supplies the
    resolved one (``PipelineNode.output_schema.json_schema``, parsed from the same
    ``GET /pipeline`` payload) and the fold materializes it inline. Without this the
    descriptions were popped and dropped: two opposite steers produced a byte-identical wire
    payload, so the searchpoint hashes collided and the optimizer scored one measurement
    twice — a lever it could propose but never pull.

    Materialization happens ONLY for a node that actually carries descriptions, so a normal
    cycle's payload is byte-identical to the pre-lever one and C0 identity is unchanged.

    Idempotent + safe on any pp: a node without the virtual key, or with no schema from
    either source, is untouched. Mutates in place — callers pass a deep copy.
    """
    for node, cfg in node_config_items(pp):
        descriptions = cfg.pop(SCHEMA_DESCRIPTIONS_PARAM, None)
        if not isinstance(descriptions, dict) or not descriptions:
            continue
        out_schema = cfg.get("output_schema")
        if not isinstance(out_schema, dict):
            resolved = schema.get_node(node) if schema else None
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
    """The parent's CURRENT value for *param*, shaped like the override that proposes it.

    Every param but one reads straight off the parent's node config. ``output_schema_descriptions``
    is virtual: the prose it edits lives folded inside ``output_schema.properties[f].description``
    (:func:`fold_schema_descriptions`), so the parent never carries the key and a naive lookup
    returns ``None`` — making an exact re-proposal of the existing prose read as a mutation. It is
    reconstructed here, keyed by the fields the override actually names.
    """
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
    """What a candidate ACTUALLY changed vs its parent — the ONE delta definition.

    Prompt-field component: the ``PROMPT_STRING_FIELDS`` whose child value differs.
    Param component: per-``(node, param)`` overrides that MOVE the parent's resolved
    value — restating what the parent already holds is not a mutation, and the
    virtual ``output_schema_descriptions`` compares against the folded schema prose
    (:func:`parent_param_value`). Dedup (``detect_invariants``) hashes this and the
    ALREADY TRIED panel renders it, so a delta rule honored in one cannot desert
    the other (the panel would show a re-proposal as new).
    """
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


def _fmt_pp_val(v: object) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def flatten_sp_summary(pp: dict[str, Any] | None) -> dict[str, str]:
    """``{node: {param: value}}`` → ``{node.param: value}`` display dict.

    A nested param flattens ONE level further, to ``node.param.key`` — the depth its
    declaration lets the merge accumulate at, so the searchpoint diff names the single
    ``output_schema_descriptions`` entry a candidate rewrote instead of printing two
    dict reprs and asking the operator to spot the difference. ``group_diff_keys``
    splits on the first dot, so the extra segment still groups under its node.

    Sniffing the value is right *here* and wrong in the merge: this is display depth,
    not semantics, and a dict never reads better flattened than as its own repr.
    """
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
]
