"""One-shot: require L2 to populate all 5 task_context fields with per-field roles.

Observed in cycle_926e2029d11a_fork_80d0254d R2-R4: only `key_challenges` was
written; the other 4 strategic fields stayed empty across all rounds, depriving
L1_GENERATE of strategic framing context. The chain-bind directive worked but
L1 stalled at 0.600 for three rounds with no broader context to draw on.

Fix: extend the strategic-fields paragraph with explicit per-field templates
and a mandatory non-empty requirement.
"""

import json
from pathlib import Path

P = Path("datasets/_optimizer/pipeline.json")
data = json.loads(P.read_text(encoding="utf-8"))
inst = data["resolved_prompts"]["l2_context/1"]["instruction"]

old_para = (
    "The other four framing fields — `domain`, `pipeline_purpose`, `data_characteristics`, "
    "`optimization_goals` — stay STRATEGIC framing (WHAT the task is, WHAT the pipeline does, "
    "WHAT the data looks like, WHAT success means). They MUST NOT carry the "
    "`targeting L1 axis '<name>'` phrase or any concrete mutation directive — that pattern belongs "
    "only on `key_challenges`. Repeating the axis directive across all five fields is a structural "
    "error: L1 then sees five paraphrases of the same instruction and produces monomorphic variants, "
    "killing exploration breadth. Each strategic field evolves at most one anchor per fire "
    "(e.g. append a newly-observed data shape to `data_characteristics`), never the axis directive."
)

new_para = (
    "The other four framing fields — `domain`, `pipeline_purpose`, `data_characteristics`, "
    "`optimization_goals` — stay STRATEGIC framing (WHAT the task is, WHAT the pipeline does, "
    "WHAT the data looks like, WHAT success means). They MUST NOT carry the "
    "`targeting L1 axis '<name>'` phrase or any concrete mutation directive — that pattern belongs "
    "only on `key_challenges`. Repeating the axis directive across all five fields is a structural "
    "error: L1 then sees five paraphrases of the same instruction and produces monomorphic variants, "
    "killing exploration breadth. Each strategic field evolves at most one anchor per fire "
    "(e.g. append a newly-observed data shape to `data_characteristics`), never the axis directive.\n\n"
    "Mandatory population — all five task_context fields MUST be non-empty after L2 fires. Empty "
    "strategic fields starve L1_GENERATE of the framing context it needs to write concrete reasoning "
    "in `evidence_grounding.citation`. Observed on AIME 2025 cycle_926e2029d11a_fork_80d0254d "
    "R2-R4: only `key_challenges` was populated, the other four stayed empty, and L1 plateaued at "
    "0.600 for three rounds despite a clean axis-directive — strategic context was the missing input. "
    "Per-field role contracts (write one to three sentences each, no placeholders, no empty strings):\n"
    "  - `domain`: the task domain in operator terms — what kind of inputs the pipeline sees and what "
    "answer shape it produces (e.g. \"AIME 2025 competition mathematics — integer answers in [0,999] "
    "across algebra, number theory, combinatorics, geometry\").\n"
    "  - `pipeline_purpose`: what the active pipeline nodes are supposed to accomplish end-to-end, "
    "expressed against the domain (e.g. \"single-step llm_only solves each AIME problem and returns "
    "the boxed integer; no retrieval or ranking stage\").\n"
    "  - `data_characteristics`: observed shape of the dataset and recurring failure-cluster patterns "
    "across rounds so far — name the concrete recurring miss types as you see them in L1_CRITIQUE "
    "across rounds (e.g. \"30 samples; recurring misses cluster around combinatorial counting on "
    "grids, parabola-rotation coordinate geometry, and 3×9 grid casework — these reappear each round\").\n"
    "  - `optimization_goals`: what `0.60 → 0.70+` requires in concrete terms tied to the current "
    "plateau — name the specific failure clusters that have to convert to hits (e.g. \"convert the "
    "two combinatorial-counting misses by injecting explicit case-enumeration discipline; recover "
    "the refusal on the 3×9 grid problem\").\n"
    "  - `key_challenges`: the chain-bind axis directive (the (a)+(b)+(c) format above) — this is "
    "the ONLY field that names an L1 axis and a concrete mutation.\n"
    "An L2 output where any of the five fields is the empty string, `null`, or generic boilerplate "
    "(\"general optimization\", \"various tasks\", \"the pipeline\") is a structural error and counts "
    "the same as an axis-directive repeated across all five fields — it produces monomorphic L1 "
    "variants for the same reason: no informational input to differentiate against."
)

assert old_para in inst, "old paragraph not found verbatim — manual inspection needed"
inst_new = inst.replace(old_para, new_para)
assert inst_new != inst, "no change applied"
data["resolved_prompts"]["l2_context/1"]["instruction"] = inst_new

P.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"ok len {len(inst)} -> {len(inst_new)} (+{len(inst_new) - len(inst)})")
print("populate-all marker:", "Mandatory population" in inst_new)
print("per-field role contracts:", "Per-field role contracts" in inst_new)
print("60pct citation:", "fork_80d0254d" in inst_new)
