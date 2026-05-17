"""One-shot v2: re-instate positive_critique + negative_critique with sharpened roles.

v1 dropped them as dead output. Operator pushback: refine them and wire
them through downstream rather than discard. ``format_l1_critique_for_prompt``
now renders both into the `critique` signal that L1_GENERATE + L2_CONTEXT
both consume.

Sharpened semantics in the prompt:
- ``positive_critique``: 1-2 sentence summary of which prompt patterns or
  config are converting hits — next round's mutations MUST preserve them.
  Companion to ``origin_strengths`` but at the pattern level, not the
  per-sample level.
- ``negative_critique``: 1-2 sentence summary of failure modes BEYOND
  ``priority_fix`` that the next round still needs to address. Avoids the
  trap where priority_fix names one fix and the second/third clusters get
  silently dropped from L1's attention.

Both fields are now ≤180 chars effective (model can write more; renderer
keeps it tight). They stay optional in the schema so empties don't break
parsing.
"""

import json
from pathlib import Path

P = Path("datasets/_optimizer/pipeline.json")
data = json.loads(P.read_text(encoding="utf-8"))
block = data["resolved_prompts"]["l1_critique/1"]

# Re-add positive/negative to the requested JSON shape, between summary and
# priority_fix so the render order matches the prompt order.
block["answer_format"] = (
    "Return JSON with these fields:\n"
    "{\n"
    '  "summary": "2-3 sentence actionable steer for the next L1_GENERATE round",\n'
    '  "positive_critique": "1-2 sentences: which prompt patterns or config are converting '
    'hits this round — next round must PRESERVE them",\n'
    '  "negative_critique": "1-2 sentences: failure modes BEYOND priority_fix that are still '
    "on the work list — keep them in scope so they aren't silently dropped\",\n"
    '  "priority_fix": "<axis>: <concrete change> — addresses <quoted failure pattern>",\n'
    '  "suggested_axes": ["<real pipeline axis>", ...],\n'
    '  "failure_highlights": ["<cluster_label? Query: ... | Predicted: ... | GT: ...>", ...]\n'
    "}\n"
    "Caps enforced by the schema: positive_critique ≤ 300 chars, negative_critique ≤ 400 chars, "
    "suggested_axes ≤ 4 entries, failure_highlights ≤ 3 entries each ≤ 140 chars."
)

# Add one line to the instruction tail explaining when these companion
# fields earn their tokens — kept tight (no decision trees, no examples).
prev_instruction = block["instruction"]
companion_paragraph = (
    "\n\n`positive_critique` and `negative_critique` are companion summaries L2 and the next "
    "L1 read alongside `priority_fix`. `positive_critique` names the prompt patterns or config "
    "earning hits this round so the next mutation does not strip them (companion to the "
    "origin_strengths panel L1 already sees). `negative_critique` names failure modes BEYOND "
    "`priority_fix` so the 2nd/3rd-priority clusters stay on the work list. Be concrete and "
    "data-grounded in both — generic phrasing wastes the slot."
)
if companion_paragraph.strip() not in prev_instruction:
    block["instruction"] = prev_instruction + companion_paragraph

P.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

b = json.loads(P.read_text(encoding="utf-8"))["resolved_prompts"]["l1_critique/1"]
sizes = {k: len(v) for k, v in b.items() if isinstance(v, str)}
total = sum(sizes.values())
print(f"L1_CRITIQUE v2 — total {total} chars")
for k, n in sizes.items():
    print(f"  {k}: {n} chars")
assert "positive_critique" in b["answer_format"]
assert "negative_critique" in b["answer_format"]
assert "companion summaries" in b["instruction"]
print("ok — positive/negative critique re-instated with sharpened roles")
