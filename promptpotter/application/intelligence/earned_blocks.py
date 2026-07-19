"""Earned prompt-block library — the reusable field values that MEASURED lift, task-fit.

The `guidance` block catalogue used to serve a static seed set (`config/prompt_blocks.py`)
to every task, so a logic run saw ranking blocks ("identify the single best match from the
candidates"): low-value information in the wrong place. This mines the library from run
history instead — a short field value (`persona` / `task_intent` / `thinking_style` /
`answer_format`) that a candidate *changed* AND that beat its matched origin by a
**credible** margin (`composite_ci_lo` clears the origin, not a noise win), tagged by the
run's **answer-space signature** so a block earned on one task shape never lands on another.

Silent when nothing clears the bar: the dispatch renders only earned, fitting signal. The
long task-specific fields (`instruction` / `problem_description`) are deliberately excluded —
they carry per-task detail and are where prompt bloat accumulates, not reusable material.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from promptpotter.config.settings import ANSWER_SPACE_CAP
from promptpotter.domain.opt_search_point import candidate_delta
from promptpotter.infrastructure.store.io import read_json_optional
from promptpotter.infrastructure.store.layout import campaign_cycles_dir

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.stores import Stores

__all__ = [
    "OPEN_ANSWER_SPACE",
    "EarnedBlock",
    "answer_space_signature",
    "earned_library_for",
    "mine_earned_blocks",
]

# The short, reusable framing fields — the only ones a block library should carry. The long
# fields (instruction, problem_description) are task-specific detail, not transferable material.
_REUSABLE_FIELDS: frozenset[str] = frozenset(
    {"persona", "task_intent", "thinking_style", "answer_format"}
)

# A run whose distinct ground truths exceed the enumerable cap has an open answer space
# (free-text / ranking); its blocks fit only other open-space runs, never a closed-label task.
OPEN_ANSWER_SPACE = "OPEN"


class EarnedBlock:
    """One reusable field value that earned credible lift, aggregated across its occurrences."""

    __slots__ = ("field", "mean_lift", "n", "text")

    def __init__(self, field: str, text: str, mean_lift: float, n: int) -> None:
        self.field = field
        self.text = text
        self.mean_lift = mean_lift
        self.n = n


def answer_space_signature(labels: Iterable[Any]) -> str:
    """The task-fit key for a set of ground-truth labels: sorted distinct labels, or ``OPEN``.

    Mirrors ``answer_distribution``'s notion of an enumerable answer space — above
    ``ANSWER_SPACE_CAP`` distinct labels the space is open (free-text / ranking) and carries
    no closed-label identity to match on. The one place a run's fit key is derived, so a block
    earned under a signature and a live cycle looking up its own signature draw the same line.
    """
    distinct = {label for label in labels if isinstance(label, str) and label}
    if not distinct or len(distinct) > ANSWER_SPACE_CAP:
        return OPEN_ANSWER_SPACE
    return "|".join(sorted(distinct))


def _answer_space_signature(round_doc: dict[str, Any]) -> str:
    """A stored round's fit key — :func:`answer_space_signature` over its observed truths."""
    return answer_space_signature(
        row.get("ground_truth")
        for rows in (round_doc.get("all_candidate_results") or {}).values()
        for row in rows or []
    )


def _credible_lift(cand: dict[str, Any]) -> float | None:
    """A candidate's lift over its matched origin, kept only when credibly positive.

    ``composite_ci_lo`` is the lower bound of the candidate's composite CI; when it clears
    the matched origin's composite the gain is real signal, not a noise win. Returns the
    point lift (``composite − matched_origin``) or ``None`` when uncredible / unpaired.
    """
    origin = cand.get("matched_origin_composite")
    comp = cand.get("composite_fitness")
    ci_lo = cand.get("composite_ci_lo")
    if not isinstance(origin, (int, float)) or not isinstance(comp, (int, float)):
        return None
    if not isinstance(ci_lo, (int, float)) or ci_lo <= origin:
        return None
    return float(comp) - float(origin)


def _accumulate(round_doc: dict[str, Any], acc: dict[tuple[str, str, str], list[float]]) -> None:
    fit = _answer_space_signature(round_doc)
    # The round's own ``prompt_fields`` IS the parent every candidate in it was mutated from —
    # the same anchor ``mutation_memory`` diffs against. A ``ScoredCandidate`` persists its
    # RESOLVED ``prompt_fields`` (never the L1 ``prompt_fields_override`` delta, which lives only
    # on the generate schema), so "what it changed" is candidate-vs-parent through the ONE shared
    # delta rule (:func:`candidate_delta`) — reading a key the serialized candidate never carries
    # mined nothing, silently, on every real run.
    parent = round_doc.get("prompt_fields") or {}
    for cand in round_doc.get("candidate_scores") or []:
        fields = cand.get("prompt_fields")
        if not isinstance(fields, dict):
            continue
        lift = _credible_lift(cand)
        if lift is None:
            continue
        changed, _ = candidate_delta(fields, parent, None, None)
        for field, text in changed.items():
            if field not in _REUSABLE_FIELDS or not isinstance(text, str):
                continue
            block = text.strip()
            if block:
                acc[(fit, field, block)].append(lift)


def mine_earned_blocks(store: Stores) -> dict[str, list[EarnedBlock]]:
    """Walk every campaign's rounds and return earned blocks keyed by answer-space fit.

    ``{fit_signature: [EarnedBlock, ...]}`` — each fit's blocks ranked by mean credible lift,
    highest first. A caller looks up its own run's fit signature (see
    :func:`_answer_space_signature`) to get only blocks that fit its task shape.
    """
    acc: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for campaign_dir in store.campaigns.iter_campaign_dirs():
        cycles_dir = campaign_cycles_dir(campaign_dir)
        if not cycles_dir.is_dir():
            continue
        for cycle_dir in sorted(cycles_dir.iterdir()):
            rounds_dir = cycle_dir / "rounds"
            if not rounds_dir.is_dir():
                continue
            for round_file in sorted(rounds_dir.glob("round_*.json")):
                doc = read_json_optional(round_file)
                if isinstance(doc, dict):
                    _accumulate(doc, acc)

    by_fit: dict[str, list[EarnedBlock]] = defaultdict(list)
    for (fit, field, block), lifts in acc.items():
        by_fit[fit].append(EarnedBlock(field, block, sum(lifts) / len(lifts), len(lifts)))
    for blocks in by_fit.values():
        blocks.sort(key=lambda b: b.mean_lift, reverse=True)
    return dict(by_fit)


def earned_library_for(
    store: Stores, fit_signature: str, *, per_field_cap: int = 3
) -> dict[str, tuple[str, ...]]:
    """The `guidance` library for one run: ``{field: (block, ...)}``, earned + fitting only.

    Empty dict when no block earned credible lift on this answer-space shape — the injection
    renders nothing, which is the correct silence for a task with no transferable history yet.
    """
    earned = mine_earned_blocks(store).get(fit_signature, [])
    by_field: dict[str, list[str]] = defaultdict(list)
    for block in earned:
        if len(by_field[block.field]) < per_field_cap:
            by_field[block.field].append(block.text)
    return {field: tuple(texts) for field, texts in by_field.items() if texts}
