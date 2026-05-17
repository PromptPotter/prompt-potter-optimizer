"""One-shot: refine L1_CRITIQUE meta-prompt — less context poisoning, less bloat.

Three changes:

1. **Drop input bias.** Strip ``{{plan}}`` and ``{{task_context}}`` from
   ``problem_description``. L1_CRITIQUE's job is round-local observation
   over the just-completed measurements. Reading L3's plan or L2's
   already-written task_context (which often names a specific axis
   target) biases the critique toward echoing prior layers rather than
   producing an independent steer from the data. Operator framing:
   "we don't want to supplement any context poisoning into L1_CRITIQUE".

2. **Compress instruction + answer_format ~58%** (4370 chars → ~1850).
   Cuts the duplication between the chain-actionable-rules paragraph
   and the answer_format rules block; collapses the 4-case
   axis-preference table to a tight version; drops meta-guidance
   already enforced by validators (e.g. "vague labels rejected" is a
   downstream filter, not the model's job to internalize).

3. **Stop asking for dead output.** ``positive_critique`` and
   ``negative_critique`` are produced by the model on every call but
   never read downstream (only ``summary`` / ``priority_fix`` /
   ``suggested_axes`` / ``failure_highlights`` flow into
   ``format_l1_critique_for_prompt`` and from there into L2 / next-round
   L1). The schema keeps the fields optional with default="" so older
   call sites parse unchanged; we just no longer instruct the model to
   fill them — at reasoning_effort=high, that's hundreds of wasted
   tokens per call.

The cumulative effect on a long-running L1_CRITIQUE call at
reasoning_effort=high should be material: smaller input prompt (less
to attend over), smaller required output (less to generate),
fewer reasoning constraints to track.
"""

import json
from pathlib import Path

P = Path("datasets/_optimizer/pipeline.json")
data = json.loads(P.read_text(encoding="utf-8"))

block = data["resolved_prompts"]["l1_critique/1"]

# 1. Input — drop {{plan}} and {{task_context}}. Keep diagnostics + wound
#    channels. L1_CRITIQUE works from this round's evidence only.
block["problem_description"] = (
    "{{diagnostics}}\n\n{{validation_failures}}\n\n{{runtime_failures}}"
)

# 2. Instruction — compressed; one decision tree, no duplicated rules.
block["instruction"] = (
    "Read the measurements above. Your job: extract the failure cluster the "
    "next round must convert, name the L1 axis to mutate, and write the concrete "
    "change. Be observational — work from the data shown, not from priors.\n\n"
    "Diagnose each failure by reading its Predicted value:\n"
    "- Math or enumeration error mid-derivation → `instruction` or `thinking_style` "
    "(add verification or case-analysis step the parent does not already require).\n"
    "- Misread problem / missing constraint → `persona` or `task_intent` "
    "(add domain-discipline reading step).\n"
    "- Refusal, placeholder, truncated mid-derivation (e.g. \"I'm sorry\", \"\\boxed{0}\" "
    "with no work) → `instruction` (require complete derivation before final answer); "
    "only recommend `max_tokens` when the response was cut mid-token.\n"
    "- Correct number, wrong wrapper → `answer_format`.\n\n"
    "PROMPT-FIELD axes (persona, task_intent, problem_description, instruction, "
    "thinking_style) are the first choice for semantic failures and carry the real "
    "lift. PARAM-FIELD axes (temperature, max_tokens, reasoning_effort) only when "
    "`runtime_failures` pin a specific value range. `answer_format` only for true "
    "format errors. When a `runtime_failures` entry appears above, name its offending "
    "axis+value in priority_fix and steer suggested_axes away from that value.\n\n"
    "`priority_fix` MUST follow the form `<axis>: <concrete change> — addresses "
    "<quoted failure pattern>` where `<axis>` is a real pipeline axis (a node param "
    "key or one of persona/task_intent/problem_description/instruction/thinking_style/"
    "answer_format), `<concrete change>` is a specific edit (\"add coordinate-"
    "verification step\", not \"improve reasoning\"), and `<quoted failure pattern>` "
    "matches a cluster you name in `failure_highlights`.\n\n"
    "`failure_highlights`: each entry quotes the failing sample's query stem (~80 chars "
    "verbatim), the model's predicted answer (~80 chars), and the ground truth. When "
    "2+ failures share a root cause, prefix with a cluster label, e.g. \"Coordinate "
    "geometry (3/N): Query: ... | Predicted: ... | GT: ...\". These are the ONLY "
    "per-sample failures L2 and the next L1 see — quote samples, don't summarize."
)

# 3. Answer format — JSON shape only; rules live in the instruction. Drop the
#    requests for positive_critique / negative_critique. Schema keeps them
#    optional (default="") so older traces still parse; we just don't spend
#    reasoning tokens producing dead output.
block["answer_format"] = (
    "Return JSON with these fields:\n"
    "{\n"
    '  "summary": "2-3 sentence actionable steer for the next L1_GENERATE round",\n'
    '  "priority_fix": "<axis>: <concrete change> — addresses <quoted failure pattern>",\n'
    '  "suggested_axes": ["<real pipeline axis>", ...],\n'
    '  "failure_highlights": ["<cluster_label? Query: ... | Predicted: ... | GT: ...>", ...]\n'
    "}\n"
    "Caps enforced by the schema: suggested_axes ≤ 4 entries, "
    "failure_highlights ≤ 3 entries, each ≤ 140 chars."
)

P.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# Verify
b = json.loads(P.read_text(encoding="utf-8"))["resolved_prompts"]["l1_critique/1"]
sizes = {k: len(v) for k, v in b.items() if isinstance(v, str)}
total = sum(sizes.values())
print(f"L1_CRITIQUE refined — total {total} chars")
for k, n in sizes.items():
    print(f"  {k}: {n} chars")
assert "{{plan}}" not in b["problem_description"], "plan placeholder should be gone"
assert "{{task_context}}" not in b["problem_description"], "task_context placeholder should be gone"
assert "positive_critique" not in b["answer_format"], "positive_critique should not be requested"
assert "negative_critique" not in b["answer_format"], "negative_critique should not be requested"
print("ok — no plan/task_context inputs, no positive/negative critique outputs requested")
