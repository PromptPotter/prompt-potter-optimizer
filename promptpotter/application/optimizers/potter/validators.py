"""Twins of constraints the wire schema declares: not every provider enforces structured output."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from itertools import pairwise
from typing import Any

from promptpotter.application import optimizers
from promptpotter.application.optimizers.potter.dispatch.bundle import (
    L1_HELD_PROMPT_FIELDS,
    prose_ceiling,
)
from promptpotter.application.optimizers.potter.dispatch.injections.registry import (
    STALL_EXPLORATION,
)
from promptpotter.application.optimizers.potter.dispatch.layout import (
    NODE_LAYOUTS,
    resolve_layout_override,
)
from promptpotter.domain.arms import Proposal
from promptpotter.domain.candidate_diff import (
    IDEA_MATCH_REJECT,
    candidate_delta,
    candidate_idea,
    same_idea,
)
from promptpotter.domain.l4.cells import InnerCells
from promptpotter.domain.opt_search_point import EvidenceGrounding, Locus, OptSearchPoint
from promptpotter.domain.optimizer_template import OptimizerPromptTemplate
from promptpotter.domain.pipeline_params import WHO_ANSWERS_KEYS, PipelineParams
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.results import RoundResult
from promptpotter.domain.search_point import SHOTS_KEY, missing_template_vars
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.domain.wounds import (
    INVARIANT_REASONS,
    RuntimeFailure,
    ValidationFailure,
    ValidationReason,
)


def l1_blocks_in_pool(
    updates: Mapping[str, Mapping[str, str]], *, offered: Mapping[str, Collection[str]]
) -> list[ValidationFailure]:
    """Under ``closed`` every value is an id of its field's active entries: free text, or a field the pool holds nothing for, is no entry."""
    return [
        ValidationFailure(
            axis=Locus(node, field).name,
            value=value[:300],
            allowed=list(offered.get(field, ())),
            reason=ValidationReason.NOT_IN_BLOCK_POOL,
        )
        for node, fields in updates.items()
        for field, value in fields.items()
        if value not in offered.get(field, ())
    ]


def l1_prompt_fields_open(
    prompt_edit: Mapping[Locus, str], *, pipeline_schema: PipelineSchema
) -> list[ValidationFailure]:
    """Potter's own `L1_HELD_PROMPT_FIELDS` alone, each node's open fields its ``allowed``: an
    operator's hold and an undeclared field are the child door's (`carried_writes`)."""
    nodes = {n.name: n for n in pipeline_schema.prompt_nodes()}
    return [
        ValidationFailure(
            axis=locus.name,
            value=value[:300],
            allowed=node.open_prompt_fields,
            reason=ValidationReason.FORBIDDEN_AXIS,
        )
        for locus, value in prompt_edit.items()
        if locus.key in L1_HELD_PROMPT_FIELDS
        and (node := nodes.get(locus.node)) is not None
        and locus.key in node.prompt_fields
    ]


_NOT_A_WORD_RE = re.compile(r"[^a-z0-9]+")


def _words(text: str) -> str:
    return _NOT_A_WORD_RE.sub(" ", text.lower()).strip()


def l1_citation_in_panel(
    grounding: EvidenceGrounding | None, *, rendered: Mapping[str, str]
) -> list[ValidationFailure]:
    """Matched by its words in the panel it NAMES, each ellipsis-free part: a citation re-types a line and may elide."""
    if grounding is None or grounding.field == STALL_EXPLORATION:
        return []
    panel = _words(rendered.get(grounding.field, ""))
    parts = [words for part in re.split(r"[.]{3}|…", grounding.citation) if (words := _words(part))]
    if parts and all(words in panel for words in parts):
        return []
    return [
        ValidationFailure(
            axis="evidence_grounding",
            value=grounding.citation[:300],
            allowed=[grounding.field],
            reason=ValidationReason.CITATION_NOT_IN_PANEL,
        )
    ]


def l1_shots_in_demo_pool(
    shots_by_node: Mapping[str, Sequence[int]], *, demo_ids: Collection[int], k_max: int
) -> list[ValidationFailure]:
    """An id outside the demo pool is a SCORED row pasted into the prompt as a worked answer; a node bearing no prompt is the bench's admission."""
    failures: list[ValidationFailure] = []
    for node, shots in shots_by_node.items():
        axis = Locus(node, SHOTS_KEY).name
        failures += [
            ValidationFailure(
                axis=axis, value=str(i), allowed=[], reason=ValidationReason.SHOT_NOT_IN_DEMO_POOL
            )
            for i in shots
            if i not in demo_ids
        ]
        if len(set(shots)) != len(shots):
            failures.append(
                ValidationFailure(
                    axis=axis,
                    value=str(list(shots)),
                    allowed=[],
                    reason=ValidationReason.SHOT_REPEATED,
                )
            )
        if len(shots) > k_max:
            failures.append(
                ValidationFailure(
                    axis=axis,
                    value=str(len(shots)),
                    allowed=[str(k_max)],
                    reason=ValidationReason.SHOTS_OVER_CAP,
                )
            )
    return failures


def l1_config_not_in_runtime_failures(
    overlay: Mapping[str, Any],
    *,
    runtime_failures: Sequence[RuntimeFailure],
    pipeline_params: PipelineParams,
) -> list[ValidationFailure]:
    """A wound convicts a ``(responder, param, value)``: one endpoint's 400 says nothing of the next model's."""
    out_failures: list[ValidationFailure] = []
    for node_name, node_params in overlay.items():
        if not isinstance(node_params, dict):
            continue
        effective = {**pipeline_params.node(node_name).laid_keys(), **node_params}
        for param, value in node_params.items():
            for rf in runtime_failures:
                obs_cfg = rf.observed_config or {}
                if not all(
                    obs_cfg.get(k) == effective.get(k) for k in WHO_ANSWERS_KEYS if k in obs_cfg
                ):
                    continue
                if param in obs_cfg and obs_cfg[param] == value:
                    out_failures.append(
                        ValidationFailure(
                            axis=f"{node_name}.{param}",
                            value=str(value),
                            allowed=[],
                            reason=ValidationReason.REPROPOSES_KNOWN_FAILING_CONFIG,
                        )
                    )
                    break
    return out_failures


def l1_inner_placeholders_intact(
    pipeline_params: PipelineParams, *, inner_cells: InnerCells | None
) -> list[ValidationFailure]:
    """Checks the MERGED params: a child inherits token-less prose without re-proposing it."""
    if inner_cells is None:
        return []
    failures: list[ValidationFailure] = []
    for node_name, cfg in pipeline_params.laid_nodes.items():
        prose = {
            k: v
            for k, v in cfg.items()
            if k in OptimizerPromptTemplate.model_fields and isinstance(v, str)
        }
        base = inner_cells.template(node_name) if prose else None
        if base is None:
            continue
        declared = sorted(base.slots())
        if not declared:
            continue
        merged = base.model_copy(update=prose)
        missing = missing_template_vars(
            merged.render(), declared, fields=OptimizerPromptTemplate.model_fields
        )
        if missing:
            failures.append(
                ValidationFailure(
                    axis=f"{node_name}.prompt",
                    value="dropped:" + ",".join(missing),
                    allowed=declared,
                    reason=ValidationReason.DROPPED_MANDATORY_PLACEHOLDER,
                )
            )
    return failures


# PHRASES, never tokens: "stop proposing the same axis in consecutive rounds" is a legal steer.
_FORBIDDEN_INNER_STEERS: tuple[tuple[ValidationReason, tuple[str, ...]], ...] = (
    (
        ValidationReason.STEERS_INNER_STOPPING,
        (
            "early stop",
            "stop early",
            "stops early",
            "stopping early",
            "terminate early",
            "stop the loop",
            "stop the run",
            "stop the search",
            "stop the campaign",
            "halt the loop",
            "halt the run",
            "exit the loop",
            "end the run",
            "end the search",
            "abort the run",
            "no further rounds",
            "skip the remaining rounds",
            "stop optimizing",
            "stop iterating",
        ),
    ),
    # Quantified forms only: a seed IS one whole inner run, and bare "seed" is legal ("seed prompt").
    (
        ValidationReason.STEERS_ACROSS_SEEDS,
        (
            "each seed",
            "every seed",
            "per seed",
            "per-seed",
            "all seeds",
            "across seeds",
            "seed-level",
            "each inner run",
            "every inner run",
        ),
    ),
)


def l1_inner_steer_is_legal(
    overlay: Mapping[str, Any], *, inner_nodes: Collection[str]
) -> list[ValidationFailure]:
    """A prompt that stops the loop early edits the frame the measurand averages over, not the search
    it measures. Judged on the inner optimizer's nodes alone: elsewhere the phrase is target prose."""
    failures: list[ValidationFailure] = []
    for node_name, node_params in overlay.items():
        if node_name not in inner_nodes or not isinstance(node_params, dict):
            continue
        for field, value in node_params.items():
            if field not in OptimizerPromptTemplate.model_fields or not isinstance(value, str):
                continue
            prose = " ".join(value.lower().split())
            for reason, phrases in _FORBIDDEN_INNER_STEERS:
                hit = next((p for p in phrases if p in prose), None)
                if hit is not None:
                    failures.append(
                        ValidationFailure(
                            axis=f"{node_name}.{field}",
                            value=hit,
                            allowed=[],
                            reason=reason,
                        )
                    )
    return failures


_GUTTABLE_MIN_CHARS = 1000
_GUT_RATIO = 0.35


def _parent_field_text(
    cells: InnerCells, node: str, field: str, pipeline_params: PipelineParams
) -> str:
    inherited = pipeline_params.node(node).laid_keys().get(field)
    if isinstance(inherited, str) and inherited:
        return inherited
    template = cells.template(node)
    current = getattr(template, field, "") if template is not None else ""
    return current if isinstance(current, str) else ""


def l1_prompt_field_not_gutted(
    overlay: Mapping[str, Any],
    *,
    inner_cells: InnerCells | None,
    pipeline_params: PipelineParams,
) -> list[ValidationFailure]:
    """An override REPLACES its field whole, so a short one deletes the contracts the parent's prose carried."""
    if inner_cells is None:
        return []
    failures: list[ValidationFailure] = []
    for node_name, node_params in overlay.items():
        if not isinstance(node_params, dict):
            continue
        for field, value in node_params.items():
            if field not in OptimizerPromptTemplate.model_fields or not isinstance(value, str):
                continue
            parent = _parent_field_text(inner_cells, node_name, field, pipeline_params)
            if len(parent) < _GUTTABLE_MIN_CHARS:
                continue
            if len(value) >= _GUT_RATIO * len(parent):
                continue
            failures.append(
                ValidationFailure(
                    axis=f"{node_name}.{field}",
                    value=f"{len(value)}B replaces {len(parent)}B",
                    allowed=[],
                    reason=ValidationReason.GUTS_INHERITED_CONTRACT,
                )
            )
    return failures


def l1_prose_within_ceiling(
    overlay: Mapping[str, Any], *, schema: PipelineSchema, inner_cells: InnerCells | None
) -> list[ValidationFailure]:
    """The wire states each ceiling as prose: a host enforcing a ``maxLength`` cuts mid-sentence, unmarked."""
    failures: list[ValidationFailure] = []
    for node_name, node_params in overlay.items():
        node = schema.get_node(node_name)
        if node is None or not isinstance(node_params, dict):
            continue
        for param, value in node_params.items():
            ceiling = prose_ceiling(node, param, inner_cells=inner_cells)
            if ceiling is not None and isinstance(value, str) and len(value) > ceiling:
                failures.append(
                    ValidationFailure(
                        axis=f"{node_name}.{param}",
                        value=f"{len(value)} chars",
                        allowed=[str(ceiling)],
                        reason=ValidationReason.OVER_CEILING,
                    )
                )
    return failures


def l1_inner_layout_applies(overlay: Mapping[str, Any]) -> list[ValidationFailure]:
    """Convicted on the PROPOSAL: the apply site sits inside the inner task, below every wound channel."""
    failures: list[ValidationFailure] = []
    for node_name, node_params in overlay.items():
        spec = NODE_LAYOUTS.get(node_name)
        if spec is None or not isinstance(node_params, dict) or "layout" not in node_params:
            continue
        axis, edit = f"{node_name}.layout", node_params["layout"]
        offered = {n for n in NODE_LAYOUTS if "layout" in optimizers.llm_nodes()[n].outer_levers}
        if node_name not in offered:
            # Convicted rather than raised: it is a proposal like any other.
            failures.append(
                ValidationFailure(
                    axis=axis,
                    value=str(edit)[:80],
                    allowed=sorted(offered),
                    reason=ValidationReason.LAYOUT_NODE_NOT_L4_EDITABLE,
                )
            )
            continue
        for outcome in resolve_layout_override(node_name, edit)[1]:
            named = [str(v) for vs in outcome.evidence.values() if isinstance(vs, list) for v in vs]
            failures.append(
                ValidationFailure(
                    axis=axis,
                    value=", ".join(sorted(named)),
                    allowed=sorted(spec.possible),
                    reason=ValidationReason(outcome.validator_id),
                )
            )
    return failures


def lost_ideas(prior_rounds: Sequence[RoundResult]) -> list[tuple[int, frozenset[str]]]:
    """Measured losses only, each read against the round BEFORE its own: ONE bar, the arm's lift
    over its reference wholly below, for a finished arm and a cut one alike."""
    out: list[tuple[int, frozenset[str]]] = []
    for parent_round, rr in pairwise(prior_rounds):
        if (head := parent_round.head) is None:
            continue
        parent, parent_pp = head.individual.prompts, head.individual.pipeline_params
        for key, arm in rr.panel.items():
            fold, fit = rr.folds.get(key), rr.fit(key)
            reading = None if fit is None else fit.vs_reference
            lift = None if reading is None else reading.headline
            if not (fold and fold.total and lift is not None and lift.side == "below"):
                continue

            prompts = arm.individual.prompts
            if fp := candidate_idea(prompts, parent, arm.overlay, parent_pp):
                out.append((rr.round, fp))
    return out


def detect_invariants(
    proposals: list[Proposal],
    parent_opt_sp: OptSearchPoint,
    parent_pp: PipelineParams,
    prior_rounds: Sequence[RoundResult] = (),
) -> list[Proposal]:
    """Each proposal with its invariant failures re-derived: any it carried in are dropped first."""
    failures = [
        [vf for vf in cp.validation_failures if vf.reason not in INVARIANT_REASONS]
        for cp in proposals
    ]
    seen: dict[tuple[Any, ...], int] = {}
    tried = lost_ideas(prior_rounds)
    # Collected, not applied inline: rejecting depends on how many SURVIVE the other two gates.
    repeats: list[tuple[int, int]] = []
    n_live = 0
    # Whole prompts, so a variant that only moved its shots, or dropped every one, is an edit.
    parent_fields = parent_opt_sp.prompts
    for i, cp in enumerate(proposals):
        child_fields = cp.individual.prompts
        delta = candidate_delta(child_fields, parent_fields, cp.overlay, parent_pp)
        if not delta:
            failures[i].append(
                ValidationFailure(
                    axis="variant",
                    value="(no mutation)",
                    allowed=["non-empty mutation"],
                    reason=ValidationReason.NO_OP_VARIANT,
                )
            )
            continue
        sig = delta.signature()
        if sig in seen:
            failures[i].append(
                ValidationFailure(
                    axis="variant",
                    value=f"duplicate of C{seen[sig] + 1}",
                    allowed=["unique mutation"],
                    reason=ValidationReason.DUPLICATE_VARIANT,
                )
            )
            continue
        seen[sig] = i
        n_live += 1
        # The words ADDED to the parent, never field names or whole values (see `candidate_idea`).
        fp = candidate_idea(child_fields, parent_fields, cp.overlay, parent_pp)
        echo = next(
            (rnd for rnd, prev in tried if same_idea(fp, prev, threshold=IDEA_MATCH_REJECT)),
            None,
        )
        if echo is not None:
            repeats.append((i, echo))

    # A repeat may cost the round a candidate, never the whole round.
    if len(repeats) < n_live:
        for i, echo in repeats:
            failures[i].append(
                ValidationFailure(
                    axis="variant",
                    value=f"re-proposes the idea measured and lost in round {echo}",
                    allowed=["an idea this cycle has not already lost with"],
                    reason=ValidationReason.REPEAT_VARIANT,
                )
            )
    return [
        cp.model_copy(update={"validation_failures": vfs})
        for cp, vfs in zip(proposals, failures, strict=True)
    ]


PLAN_LENGTH_FLOOR_CHARS = 60


# Soft signals; the HARD layout validators live in `dispatch/layout.py`.
def l3_plan_breaches(plan: str, *, prior_plan: str) -> list[ValidatorOutcome]:
    text, prev = plan.strip(), prior_plan.strip()
    breaches: list[ValidatorOutcome] = []
    if len(text) < PLAN_LENGTH_FLOOR_CHARS:
        breaches.append(
            ValidatorOutcome(
                validator_id="l3_plan_length_floor",
                evidence={"length": len(text), "floor": PLAN_LENGTH_FLOOR_CHARS},
            )
        )
    if text and prev and text == prev:
        breaches.append(
            ValidatorOutcome(validator_id="l3_plan_verbatim_repeat", evidence={"plan": plan})
        )
    return breaches
