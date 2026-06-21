"""Validators on L2-parsed outputs (soft signals; layout HARD validators live in :mod:`domain.l1_layout`).

* :data:`L2_TASK_CONTEXT_VERBATIM_REPEAT` — proposed ``task_context``
  refinement produced no semantic change vs the prior OSP framing
  (either the LLM repeated the existing fields or sent a no-op merge).
* :data:`L2_DUPLICATE_INSERT` — proposed ``task_context`` re-asserts
  ≥``DUPLICATE_INSERT_LINE_THRESHOLD`` lines already in the prior
  framing. Merge still succeeds, but L2 is pasting back what's there.
* :data:`L2_TASK_CONTEXT_PARAPHRASE_REPEAT` — paraphrase middle ground;
  per-field token-set Jaccard ≥ ``PARAPHRASE_REPEAT_JACCARD_THRESHOLD``.
* :data:`L2_SUPPLEMENTAL_RULE_DUP_ID` / :data:`L2_SITUATIONAL_EXAMPLE_DANGLING_TRIGGER` /
  :data:`L2_SUPPLEMENTAL_RULE_DUPLICATES_AUTO_TRIGGER` — rule/example
  authoring guards.

Outcomes append to ``opt_sp.l2_guard_breaches`` and surface to L3's next
fire as self-healing evidence via the ``l2_guard_breaches`` dispatch-hub
signal.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.validators import LLMOutputValidator, ValidatorOutcome, run_validators

DUPLICATE_INSERT_LINE_THRESHOLD = 3
PARAPHRASE_REPEAT_JACCARD_THRESHOLD = 0.5

_WORD_RE = re.compile(r"\w+")


def _word_set(text: str, *, min_len: int = 3) -> set[str]:
    """Lower-cased word tokens of ``text`` at least ``min_len`` chars long."""
    return {w for w in _WORD_RE.findall(text.lower()) if len(w) >= min_len}


def word_set_jaccard(a: str, b: str, *, min_len: int = 3) -> float:
    """Jaccard overlap of the two strings' significant-word sets.

    Returns ``0.0`` when either side has no qualifying words (an empty set has no
    meaningful overlap), so callers can compare against a threshold without a
    separate empty-guard.
    """
    wa = _word_set(a, min_len=min_len)
    wb = _word_set(b, min_len=min_len)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _attr_or_key(entry: Any, name: str) -> Any:
    """Read ``name`` off a pydantic obj or a plain dict — L2 proposals ride both
    shapes (typed when freshly parsed, dict on disk-replay)."""
    return getattr(entry, name, None) or (entry.get(name) if isinstance(entry, dict) else None)


def _check_task_context_verbatim_repeat(
    source_output: Mapping[str, Any],
    *,
    opt_sp: OptSearchPoint | None = None,
    **_: Any,
) -> ValidatorOutcome | None:
    """Fire when L2 *proposed* a task_context update but it merged to a no-op.

    ``escalation._parse_l2`` passes ``task_context_proposed`` (the raw
    dict L2 emitted) and ``task_context_applied`` (the merged
    TaskDecomposition or ``None`` when the merge produced no change).
    A non-empty proposal that yielded ``None`` means the LLM tried but
    repeated the prior framing — the no-op task_context analogue of a
    verbatim signal repeat.
    """
    proposed = source_output.get("task_context_proposed")
    applied = source_output.get("task_context_applied")
    if not isinstance(proposed, dict) or not proposed:
        return None
    if applied is not None:
        return None
    return ValidatorOutcome(
        validator_id=L2_TASK_CONTEXT_VERBATIM_REPEAT.id,
        evidence={"proposed_keys": sorted(proposed.keys())},
    )


def _check_task_context_paraphrase_repeat(
    source_output: Mapping[str, Any],
    *,
    opt_sp: OptSearchPoint | None = None,
    **_: Any,
) -> ValidatorOutcome | None:
    """Fire when L2's proposed task_context paraphrases the prior framing.

    Sibling of ``_check_task_context_verbatim_repeat`` (no-op merge) and
    ``_check_duplicate_insert`` (≥3 verbatim lines). This catches the
    middle ground: the proposed update *is* a new string, the merge
    *succeeds*, but the word-set overlap with the prior framing is so
    high that no real semantic delta lands. Same recovery as the other
    two — surface as evidence; L3 force-trigger via
    ``executor.py::escalate_l2`` reads ``l2_guard_breaches`` and replans.

    Token-set Jaccard above
    :data:`PARAPHRASE_REPEAT_JACCARD_THRESHOLD` on any single updated
    field is sufficient to fire. Per-field rather than aggregate so a
    legitimate refinement on one field isn't drowned by a stale repeat
    on another.
    """
    if opt_sp is None:
        return None
    proposed = source_output.get("task_context_proposed")
    if not isinstance(proposed, dict) or not proposed:
        return None
    prior = opt_sp.memory.task_context.to_dict()
    worst_overlap = 0.0
    worst_field = ""
    for field_name, new_value in proposed.items():
        if not isinstance(new_value, str) or not new_value:
            continue
        prior_value = prior.get(field_name, "")
        if not isinstance(prior_value, str) or not prior_value:
            continue
        overlap = word_set_jaccard(new_value, prior_value)
        if overlap > worst_overlap:
            worst_overlap = overlap
            worst_field = field_name
    if worst_overlap < PARAPHRASE_REPEAT_JACCARD_THRESHOLD:
        return None
    return ValidatorOutcome(
        validator_id=L2_TASK_CONTEXT_PARAPHRASE_REPEAT.id,
        evidence={
            "field": worst_field,
            "jaccard": round(worst_overlap, 3),
            "threshold": PARAPHRASE_REPEAT_JACCARD_THRESHOLD,
        },
    )


def _check_duplicate_insert(
    source_output: Mapping[str, Any],
    *,
    opt_sp: OptSearchPoint | None = None,
    **_: Any,
) -> ValidatorOutcome | None:
    """Fire when L2's proposed task_context re-asserts ≥3 lines already
    in the prior OSP framing. Distinct from :data:`L2_TASK_CONTEXT_VERBATIM_REPEAT`
    (no-op merge): here the merge succeeds, but L2 still pasted back lines
    that were already there — the refinement surface is exhausted.
    """
    if opt_sp is None:
        return None
    proposed = source_output.get("task_context_proposed")
    if not isinstance(proposed, dict) or not proposed:
        return None
    prior = opt_sp.memory.task_context.to_dict()
    duplicate_lines = 0
    overlapped_fields: list[str] = []
    for field_name, new_value in proposed.items():
        if not isinstance(new_value, str) or not new_value:
            continue
        prior_value = prior.get(field_name, "")
        if not isinstance(prior_value, str) or not prior_value:
            continue
        new_lines = {ln.strip() for ln in new_value.splitlines() if ln.strip()}
        prior_lines = {ln.strip() for ln in prior_value.splitlines() if ln.strip()}
        overlap = new_lines & prior_lines
        if overlap:
            duplicate_lines += len(overlap)
            overlapped_fields.append(field_name)
    if duplicate_lines < DUPLICATE_INSERT_LINE_THRESHOLD:
        return None
    return ValidatorOutcome(
        validator_id=L2_DUPLICATE_INSERT.id,
        evidence={
            "duplicate_lines": duplicate_lines,
            "threshold": DUPLICATE_INSERT_LINE_THRESHOLD,
            "fields": sorted(overlapped_fields),
        },
    )


L2_TASK_CONTEXT_VERBATIM_REPEAT: LLMOutputValidator = LLMOutputValidator(
    id="l2_task_context_verbatim_repeat",
    check=_check_task_context_verbatim_repeat,
)


L2_DUPLICATE_INSERT: LLMOutputValidator = LLMOutputValidator(
    id="l2_duplicate_insert",
    check=_check_duplicate_insert,
)


L2_TASK_CONTEXT_PARAPHRASE_REPEAT: LLMOutputValidator = LLMOutputValidator(
    id="l2_task_context_paraphrase_repeat",
    check=_check_task_context_paraphrase_repeat,
)


# Canonical auto-trigger IDs the dispatch hub emits when their evidence
# fires. L2's situational examples must pin to one of these or to a
# currently-authored rule_id; L2's supplemental rules must not duplicate
# the auto-trigger registry's rule bodies.
_AUTO_TRIGGER_IDS: frozenset[str] = frozenset(
    {
        "peaked_axis",
        "runtime_failure",
        "continuous_envelope",
        "chain_bind",
        "latex_repair",
        "l2_stall_diversity",
    }
)


def _check_supplemental_rule_dup_id(
    source_output: Mapping[str, Any],
    *,
    opt_sp: OptSearchPoint | None = None,
    **_: Any,
) -> ValidatorOutcome | None:
    """Fire when two L2-authored supplemental rules share a ``rule_id``.

    Duplicate rule_ids confuse the situational-examples filter (it can't
    decide which body to render). The renderer would silently emit both,
    bloating L1's prompt with paraphrases.
    """
    proposed = source_output.get("l1_supplemental_rules_proposed")
    if not isinstance(proposed, list) or len(proposed) < 2:
        return None
    seen: set[str] = set()
    dups: list[str] = []
    for entry in proposed:
        rid = _attr_or_key(entry, "rule_id")
        if not rid:
            continue
        if rid in seen:
            dups.append(rid)
        else:
            seen.add(rid)
    if not dups:
        return None
    return ValidatorOutcome(
        validator_id=L2_SUPPLEMENTAL_RULE_DUP_ID.id,
        evidence={"duplicate_rule_ids": sorted(set(dups))},
    )


def _check_situational_example_dangling_trigger(
    source_output: Mapping[str, Any],
    *,
    opt_sp: OptSearchPoint | None = None,
    **_: Any,
) -> ValidatorOutcome | None:
    """Fire when a proposed example's ``trigger_id`` matches neither an
    auto-trigger nor a currently-authored rule_id.

    Dangling triggers are silently filtered by the renderer — the
    operator never sees the example. Surface as evidence so L2 either
    fixes the trigger_id or removes the example.
    """
    proposed = source_output.get("l1_situational_examples_proposed")
    if not isinstance(proposed, list) or not proposed:
        return None
    # Allowed trigger IDs include auto-triggers + currently-authored rule_ids
    # (either the rule layer L2 just proposed, or the carried-over layer on
    # opt_sp if L2 didn't propose this fire).
    rules_proposed = source_output.get("l1_supplemental_rules_proposed")
    if isinstance(rules_proposed, list) and rules_proposed:
        rule_ids = {_attr_or_key(r, "rule_id") for r in rules_proposed}
    elif opt_sp is not None:
        rule_ids = {r.rule_id for r in opt_sp.memory.l1_supplemental_rules}
    else:
        rule_ids = set()
    allowed: set[str] = set(_AUTO_TRIGGER_IDS) | {r for r in rule_ids if r}
    dangling: list[str] = []
    for entry in proposed:
        tid = _attr_or_key(entry, "trigger_id")
        if tid and tid not in allowed:
            dangling.append(tid)
    if not dangling:
        return None
    return ValidatorOutcome(
        validator_id=L2_SITUATIONAL_EXAMPLE_DANGLING_TRIGGER.id,
        evidence={"dangling_trigger_ids": sorted(set(dangling)), "allowed": sorted(allowed)},
    )


def _check_supplemental_rule_duplicates_auto_trigger(
    source_output: Mapping[str, Any],
    *,
    opt_sp: OptSearchPoint | None = None,
    **_: Any,
) -> ValidatorOutcome | None:
    """Fire when an L2-authored rule body paraphrases a canonical auto-rule.

    Token-set Jaccard ≥ :data:`PARAPHRASE_REPEAT_JACCARD_THRESHOLD` (0.5)
    against any entry in ``AUTO_RULES`` means L2 is re-asserting a rule
    the dispatch hub already injects automatically. The L2 slot is wasted.
    """
    # Local import — avoid pulling the auto-rules registry at module-load.
    from promptpotter.application.optimization.dispatch.hub.auto_rules import AUTO_RULES

    proposed = source_output.get("l1_supplemental_rules_proposed")
    if not isinstance(proposed, list) or not proposed:
        return None
    offenders: list[tuple[str, str, float]] = []
    for entry in proposed:
        body = _attr_or_key(entry, "body")
        rid = _attr_or_key(entry, "rule_id")
        if not body or not rid:
            continue
        for auto_id, auto_body in AUTO_RULES.items():
            overlap = word_set_jaccard(body, auto_body)
            if overlap >= PARAPHRASE_REPEAT_JACCARD_THRESHOLD:
                offenders.append((rid, auto_id, round(overlap, 3)))
                break
    if not offenders:
        return None
    return ValidatorOutcome(
        validator_id=L2_SUPPLEMENTAL_RULE_DUPLICATES_AUTO_TRIGGER.id,
        evidence={
            "duplicates": [
                {"rule_id": rid, "auto_trigger": atid, "jaccard": j} for rid, atid, j in offenders
            ],
            "threshold": PARAPHRASE_REPEAT_JACCARD_THRESHOLD,
        },
    )


L2_SUPPLEMENTAL_RULE_DUP_ID: LLMOutputValidator = LLMOutputValidator(
    id="l2_supplemental_rule_dup_id",
    check=_check_supplemental_rule_dup_id,
)


L2_SITUATIONAL_EXAMPLE_DANGLING_TRIGGER: LLMOutputValidator = LLMOutputValidator(
    id="l2_situational_example_dangling_trigger",
    check=_check_situational_example_dangling_trigger,
)


L2_SUPPLEMENTAL_RULE_DUPLICATES_AUTO_TRIGGER: LLMOutputValidator = LLMOutputValidator(
    id="l2_supplemental_rule_duplicates_auto_trigger",
    check=_check_supplemental_rule_duplicates_auto_trigger,
)


L2_OUTPUT_VALIDATORS: tuple[LLMOutputValidator, ...] = (
    L2_TASK_CONTEXT_VERBATIM_REPEAT,
    L2_DUPLICATE_INSERT,
    L2_TASK_CONTEXT_PARAPHRASE_REPEAT,
    L2_SUPPLEMENTAL_RULE_DUP_ID,
    L2_SITUATIONAL_EXAMPLE_DANGLING_TRIGGER,
    L2_SUPPLEMENTAL_RULE_DUPLICATES_AUTO_TRIGGER,
)


def run_l2_output_validators(
    source_output: Mapping[str, Any],
    opt_sp: OptSearchPoint,
) -> list[ValidatorOutcome]:
    """Run every registered L2-output validator; return non-None outcomes."""
    return run_validators(L2_OUTPUT_VALIDATORS, source_output, opt_sp)


__all__ = [
    "DUPLICATE_INSERT_LINE_THRESHOLD",
    "L2_DUPLICATE_INSERT",
    "L2_OUTPUT_VALIDATORS",
    "L2_SITUATIONAL_EXAMPLE_DANGLING_TRIGGER",
    "L2_SUPPLEMENTAL_RULE_DUPLICATES_AUTO_TRIGGER",
    "L2_SUPPLEMENTAL_RULE_DUP_ID",
    "L2_TASK_CONTEXT_PARAPHRASE_REPEAT",
    "L2_TASK_CONTEXT_VERBATIM_REPEAT",
    "PARAPHRASE_REPEAT_JACCARD_THRESHOLD",
    "run_l2_output_validators",
]
