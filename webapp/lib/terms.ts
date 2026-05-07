// Operator vocabulary — single source of truth for in-product tooltips.
// Lifted verbatim from webapp/index.html:909 (vanilla preservation list).
// Keep entries to one short sentence; longer explanations belong in docs/manual/.

export const TERMS: Record<string, string> = {
  // Status banner — thresholds match ageBucket() in lib/poll.ts
  status_live:        "Optimizer wrote dashboard.json in the last 30 seconds — running.",
  status_idle:        "Last write 30s–5m ago. Round between phases or paused.",
  status_snapshot:    "Last write >5m ago. No live optimizer — viewing a frozen cycle.",
  status_offline:     "No active session, or dashboard.json unreachable. Run `python -m promptpotter optimize`.",
  status_nowall:      "Dashboard has no wallclock yet — optimizer probably has not started.",

  // Phase tag (dash.state) → one-sentence definition
  phase_baseline:           "Baseline scoring of the unmodified starting prompt — the floor the optimizer beats.",
  phase_l1_generate:        "L1 Generate — produces candidate prompt mutations from current framing + critique.",
  phase_l1_critique:        "L1 Critique — reads round results, writes the critique L1 Generate consumes next round.",
  phase_scoring:            "Scoring — running candidates against the dataset to compute composite_fitness.",
  phase_l2_refining:        "L2 — task-context refinement. Fires when L1 stalls; rewrites the framing L1 reads.",
  phase_l3_replanning:      "L3 — strategic plan. Fires when L2 stalls; replans the optimizer approach.",
  phase_between_samples:    "Between samples — short pause between dataset queries during scoring.",
  phase_between_candidates: "Between candidates — short pause between candidate evaluations.",

  // Workflow nodes
  node_baseline:    "Baseline: the unmodified starting prompt, scored as the floor.",
  node_l1_generate: "L1 Generate: produces N candidate prompts from current framing + critique.",
  node_l1_score:    "L1 Score: runs each candidate over the dataset, computes composite_fitness.",
  node_l1_critique: "L1 Critique: reads round results, writes the critique L1 Generate reads next round.",
  node_l2_context:  "L2 Context: rewrites the task framing L1 generation reads. Fires on L1 stall.",
  node_l3_plan:     "L3 Plan: rewrites the strategy. Fires on L2 stall.",

  // Eval-table column headers
  col_id:        "Sample ID — stable identifier from the dataset.",
  col_status:    "Hit / miss for this sample. Inferred client-side from predicted vs ground truth.",
  col_query:     "Input given to the pipeline for this sample.",
  col_predicted: "Top-1 prediction returned by the pipeline.",
  col_ground:    "Ground-truth answer from the dataset.",

  // composite_fitness — wired on the "Pass Rate (composite)" card title
  composite: "composite_fitness — the per-candidate scalar the optimizer optimizes. Recipe in the formula row.",

  // What-if direction arrows
  whatif_up:   "Higher value is better — counted positively in the what-if mean.",
  whatif_down: "Lower value is better — direction-corrected (1 − x) before averaging.",

  // Stub badges + placeholder labels
  stub_inferred:      "Heuristic display — derived client-side, not authoritative. Real value lives in archive/measurements.",
  stub_score_freq:    "Inferred bucket counts. Real per-sample scores live in archive/measurements.",
  badge_top:          "Page-anchor card — primary signal for round health.",
  placeholder:        "Placeholder label. Not wired up yet; final feature shape may change.",
  brand_live_preview: "This page polls dashboard.json every 2s. Read-only — no control plane in this slice.",
};

export type TermKey = keyof typeof TERMS;
