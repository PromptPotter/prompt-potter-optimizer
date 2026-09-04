import type { PipelineView } from "@/components/workflow";
import { interiorNodes } from "@/lib/derivations";

// Operator vocabulary — single source of truth for in-product tooltips.
// Keep entries to one short sentence; longer explanations belong in docs/manual/.

export const TERMS: Record<string, string> = {
  // Status banner — thresholds match ageBucket() in lib/poll.ts
  status_live:        "Optimizer wrote dashboard.json in the last 30 seconds — running.",
  status_idle:        "Last write 30s–5m ago. Round between phases or paused.",
  status_snapshot:    "Last write >5m ago. No live optimizer — viewing a frozen unit.",
  status_offline:     "No active session, or dashboard.json unreachable. Run `python -m promptpotter new <dataset>` or `resume`.",
  status_nowall:      "Dashboard has no wallclock yet — optimizer probably has not started.",
  status_stamp_mismatch: "dashboard.json keeps reporting a different (campaign, cycle) than this view expects — the optimizer may be re-instantiating, or this unit's session never wrote a dashboard.",

  // Workflow nodes
  node_checkin:   "Check-in & origin: resolves the dataset's origin, then scores the unmodified starting prompt as the floor.",
  node_l1_generate: "L1 Generate: produces N candidate prompts from current framing + critique.",
  node_l1_score:    "L1 Score: runs each candidate over the dataset, computes composite_fitness.",
  node_l1_critique: "L1 Critique: reads round results, writes the critique L1 Generate reads next round.",
  node_l2_context:  "L2 Context: rewrites the task framing L1 generation reads. Fires on L1 stall.",
  node_l3_plan:     "L3 Plan: rewrites the strategy. Fires on L2 stall.",

  // composite_fitness — wired on the "Pass Rate (composite)" card title
  composite: "composite_fitness — the per-candidate scalar the optimizer optimizes. Recipe in the formula row.",

  // Mask direction arrows
  mask_up:   "Higher value is better — counted positively in the masked mean.",
  mask_down: "Lower value is better — direction-corrected (1 − x) before averaging.",

  // Stub badges + placeholder labels
  stub_inferred:      "Heuristic display — derived client-side, not authoritative. Real value lives in measurements/.",
  stub_score_freq:    "Inferred bucket counts. Real per-sample scores live in measurements/.",
  badge_top:          "Page-anchor card — primary signal for round health.",
  brand_live_preview: "This page polls dashboard.json every 2s.",

  // Remote control — the Lift chip's readouts, served on the strip and its panel
  remote_best:   "Lift over origin — the running winner's gain (best − origin). '+12% · best 62%' reads as '+12 points over origin, now at 62%' — the gain is the spend's return.",
  remote_eta:    "Estimated time until spend hits the budget at the current burn rate. Renders '—' when the budget is uncapped or spend is unknown.",
  remote_eff:    "Improvement-per-spend overall: (best − origin) / spend_used, in percentage points per dollar. The headline efficiency number.",
};

// The target/backend node ids of a connector view — every node that isn't a
// pure I/O port. These are the ids the selection store uses for the backend
// node panel (PipelineStack click → BackendNodeDetail). Used by ChatPane
// as the membership gate for which node clicks open the read-only detail.
//
// No view means no target nodes. This used to return a synthetic `["llm"]`,
// claiming to match "the hero's own placeholder" — but the placeholder is inert
// and never sets a selection, so nothing could ever equal that id.
export function targetNodeIds(view: PipelineView | null): string[] {
  return interiorNodes(view).map((n) => n.id);
}
