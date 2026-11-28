"""Twins of constraints the wire schema declares: not every provider enforces structured output."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from itertools import pairwise
from typing import Any

from promptpotter.application.optimizer_contract import llm_nodes
from promptpotter.application.optimizers.potter.dispatch.bundle import (
    L1_HELD_PROMPT_FIELDS,
    SCHEMA_DESCRIPTION_MAX_CHARS,
)
from promptpotter.application.optimizers.potter.dispatch.injections.panels import (
    holds_run,
    word_tokens,
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
from promptpotter.domain.failures import (
    COLLAPSE_WORDS,
    RuntimeFailure,
    ValidationFailure,
    ValidationReason,
    fatal_validation_failures,
)
from promptpotter.domain.opt_search_point import EvidenceGrounding, Locus, OptSearchPoint
from promptpotter.domain.paired_reading import ReadingState
from promptpotter.domain.pipeline_params import WHO_ANSWERS_KEYS, PipelineParams
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.results import RoundResult
from promptpotter.domain.search_point import SHOTS_KEY
from promptpotter.domain.validators import ValidatorOutcome


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


def l1_evidence_grounding(
    citation: str | None, *, citable: Sequence[str], rendered: Mapping[str, str]
) -> tuple[EvidenceGrounding | None, list[ValidationFailure]]:
    """Its panel the first of *citable* holding each ellipsis-free part as a run of its words: a citation
    re-types a line and may elide. An empty one is ``STALL_EXPLORATION`` where *citable* offers it."""
    if citation is None:
        return None, []
    citation = citation.strip()
    parts = [words for part in re.split(r"[.]{3}|…", citation) if (words := word_tokens(part))]
    panels = [name for name in citable if name != STALL_EXPLORATION]
    if parts:
        held = next(
            (n for n in panels if all(holds_run(word_tokens(rendered[n]), w) for w in parts)), None
        )
    else:
        held = STALL_EXPLORATION if STALL_EXPLORATION in citable else None
    if held is not None:
        return EvidenceGrounding(field=held, citation=citation), []
    return None, [
        ValidationFailure(
            axis="evidence_grounding",
            value=citation[:300],
            allowed=panels,
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


def l1_prose_within_ceiling(
    overlay: Mapping[str, Any], *, schema: PipelineSchema
) -> list[ValidationFailure]:
    """The wire states each ceiling as prose: a host enforcing a ``maxLength`` cuts mid-sentence, unmarked."""
    failures: list[ValidationFailure] = []
    for node_name, node_params in overlay.items():
        node = schema.get_node(node_name)
        if node is None or not isinstance(node_params, dict):
            continue
        for param, value in node_params.items():
            if (
                param in node.description_keys
                and isinstance(value, str)
                and len(value) > SCHEMA_DESCRIPTION_MAX_CHARS
            ):
                failures.append(
                    ValidationFailure(
                        axis=f"{node_name}.{param}",
                        value=f"{len(value)} chars",
                        allowed=[str(SCHEMA_DESCRIPTION_MAX_CHARS)],
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
        offered = {n for n in NODE_LAYOUTS if "layout" in llm_nodes()[n].outer_levers}
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
        for key, arm in rr.proposed.items():
            fold, reading = rr.folds.get(key), rr.vs_reference(key)
            if not (fold and fold.total and reading and reading.state is ReadingState.READ):
                continue
            if reading.headline.side != "below":
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
        [vf for vf in cp.validation_failures if vf.reason not in COLLAPSE_WORDS] for cp in proposals
    ]
    seen: dict[tuple[Any, ...], int] = {}
    tried = lost_ideas(prior_rounds)
    # Collected, not applied inline: rejecting depends on how many SURVIVE the other two gates.
    repeats: list[tuple[int, int]] = []
    n_live = 0
    # Whole prompts, so a variant that only moved its shots, or dropped every one, is an edit.
    parent_fields = parent_opt_sp.prompts
    for i, cp in enumerate(proposals):
        # Refused for its own reason, it is no collapse: a missed anchor is not also a no-op.
        if fatal_validation_failures(failures[i]):
            continue
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
