import type { PipelineView } from "@/components/workflow";
import { interiorNodes } from "@/lib/derivations";

// Operator vocabulary for in-product tooltips — one short sentence each; longer goes to docs/manual/.

export const TERMS: Record<string, string> = {
  // Thresholds match ageBucket() in lib/poll.tsx.
  status_live:        "Optimizer wrote dashboard.json in the last 30 seconds — running.",
  status_idle:        "Last write 30s–5m ago. Round between phases or paused.",
  status_snapshot:    "Last write >5m ago. No live optimizer — viewing a frozen unit.",
  status_offline:     "No active session, or dashboard.json unreachable. Run `python -m promptpotter new <dataset>` or `resume`.",
  status_nowall:      "Dashboard has no wallclock yet — optimizer probably has not started.",
  status_stamp_mismatch: "dashboard.json keeps reporting a different (campaign, cycle) than this view expects — the optimizer may be re-instantiating, or this unit's session never wrote a dashboard.",

  node_checkin:   "Check-in & origin: resolves the dataset's origin, then scores the unmodified starting prompt as the floor.",
  node_l1_generate: "L1 Generate: produces N candidate prompts from current framing + critique.",
  node_l1_score:    "L1 Score: runs each candidate over the dataset, computes composite_fitness.",
  node_l1_critique: "L1 Critique: reads round results, writes the critique L1 Generate reads next round.",
  node_l2_context:  "L2 Context: moves which panels L1 generation reads and how widely it explores. Fires on L1 stall.",
  node_l3_plan:     "L3 Plan: rewrites the strategy. Fires on L2 stall.",

  composite: "composite_fitness — the per-candidate scalar the optimizer optimizes. Recipe in the formula row.",

  mask_up:   "Higher value is better — counted positively in the masked mean.",
  mask_down: "Lower value is better — direction-corrected (1 − x) before averaging.",

  stub_inferred:      "Heuristic display — derived client-side, not authoritative. Real value lives in measurements/.",
  stub_score_freq:    "Inferred bucket counts. Real per-sample scores live in measurements/.",
  badge_top:          "Page-anchor card — primary signal for round health.",
  brand_live_preview: "This page polls dashboard.json every 2s.",

  // The TWO caches share no mechanism and must never share a word.
  cache_replayed: "Replayed — OUR content-addressed archive answered, so no provider was reached and the cell cost nothing. Counted per CANDIDATE: how many carry at least one replayed sample.",
  cache_prefix:   "Prefix cache — the PROVIDER served part of a call's input off its own prompt-prefix cache, billed at a discount. The call did happen; part of it was cheaper.",

  remote_best:   "Lift over origin — the running winner's gain in ABILITY (θ, logits), not in accuracy points: the two are different bases. 'θ +0.42' is the spend's return. Where the run stands in accuracy is the masthead's BEST chip.",
  remote_eta:    "Estimated time until spend hits the budget at the current burn rate. Renders '—' when the budget is uncapped or spend is unknown.",
  remote_eff:    "Improvement-per-spend overall: the ability gain over origin divided by spend used, in θ per dollar. The headline efficiency number.",
  remote_flight: "Calls the round has out now, how many its stop rules allow right now, and the most it could hold if nothing were cut — every candidate walking plus the parent's catch-ups. Between rounds, the most the next round could hold. Results are taken in order, so a slow call at a candidate's head holds the round; once one has run long it is named below.",
};

export function targetNodeIds(view: PipelineView | null): string[] {
  return interiorNodes(view).map((n) => n.id);
}
