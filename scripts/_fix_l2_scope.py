"""One-shot: rescope L2_CONTEXT chain-bind rule to key_challenges only.

L2 was applying the `targeting L1 axis '<name>'` directive to all 5 task_context
fields, producing monomorphic R2 variants. Fix: chain-bind rule applies to
key_challenges ONLY; the other 4 framing fields stay strategic.
"""

import json
from pathlib import Path

P = Path("datasets/_optimizer/pipeline.json")
data = json.loads(P.read_text(encoding="utf-8"))
inst = data["resolved_prompts"]["l2_context/1"]["instruction"]

old_para = (
    "Axis-actionable task_context — the chain breaks here if you stay strategic. "
    "Each task_context value you write MUST close the loop from L1_CRITIQUE.failure_highlights "
    "into a next-round L1_GENERATE directive. Specifically each updated field MUST contain three "
    "pieces, in any order: (a) the failure cluster, quoted from L1_CRITIQUE.failure_highlights "
    '(e.g. "Coordinate geometry: parabola rotation by 60°, 3×9 grid combinatorics"); '
    "(b) the L1 axis to target next round, named verbatim as `targeting L1 axis '<axis_name>'` "
    "(axis_name MUST be a real pipeline axis — a node param key or one of "
    "persona/task_intent/problem_description/instruction/thinking_style/answer_format); "
    '(c) the concrete change to try (e.g. "add explicit coordinate-verification step before computation"). '
    'Generic framing like "increase numeric accuracy" or "handle large numbers" with no axis named '
    "is read as no-op by L1_GENERATE and counts toward the verbatim-repeat → L3 escalation. "
    'Good key_challenges example: "Coordinate-geometry transformations failing on 3/5 misses '
    "(parabola rotation, 3×9 grid). Targeting L1 axis 'persona' next round to add explicit "
    'coordinate-setup discipline before computation."'
)

new_para = (
    "Axis-actionable task_context — the chain-bind directive lives on ONE field only: "
    "`key_challenges`. That single field MUST contain three pieces, in any order: "
    "(a) the failure cluster, quoted from L1_CRITIQUE.failure_highlights "
    '(e.g. "Coordinate geometry: parabola rotation by 60°, 3×9 grid combinatorics"); '
    "(b) the L1 axis to target next round, named verbatim as `targeting L1 axis '<axis_name>'` "
    "(axis_name MUST be a real pipeline axis — a node param key or one of "
    "persona/task_intent/problem_description/instruction/thinking_style/answer_format); "
    '(c) the concrete change to try (e.g. "add explicit coordinate-verification step before computation"). '
    'A generic `key_challenges` like "increase numeric accuracy" or "handle large numbers" with no axis '
    "named is read as no-op by L1_GENERATE and counts toward the verbatim-repeat → L3 escalation. "
    'Good example: "Coordinate-geometry transformations failing on 3/5 misses (parabola rotation, '
    "3×9 grid). Targeting L1 axis 'persona' next round to add explicit coordinate-setup discipline "
    'before computation."\n\n'
    "The other four framing fields — `domain`, `pipeline_purpose`, `data_characteristics`, "
    "`optimization_goals` — stay STRATEGIC framing (WHAT the task is, WHAT the pipeline does, "
    "WHAT the data looks like, WHAT success means). They MUST NOT carry the "
    "`targeting L1 axis '<name>'` phrase or any concrete mutation directive — that pattern belongs "
    "only on `key_challenges`. Repeating the axis directive across all five fields is a structural "
    "error: L1 then sees five paraphrases of the same instruction and produces monomorphic variants, "
    "killing exploration breadth. Each strategic field evolves at most one anchor per fire "
    "(e.g. append a newly-observed data shape to `data_characteristics`), never the axis directive."
)

assert old_para in inst, "old paragraph not found verbatim — manual inspection needed"
inst_new = inst.replace(old_para, new_para)
assert inst_new != inst, "no change applied"
data["resolved_prompts"]["l2_context/1"]["instruction"] = inst_new

P.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"ok — instruction len {len(inst)} → {len(inst_new)} (+{len(inst_new) - len(inst)})")
print("scope-to-key_challenges marker:", "lives on ONE field only" in inst_new)
print("strategic-fields warning present:", "stay STRATEGIC framing" in inst_new)
print("monomorphic-warning present:", "monomorphic variants" in inst_new)
