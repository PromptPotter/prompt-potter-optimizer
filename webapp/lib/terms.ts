import type { PipelineView } from "@/lib/types";
import { interiorNodes } from "@/lib/derivations";

// Operator vocabulary for in-product tooltips — one short sentence each; longer goes to docs/manual/.

export const TERMS: Record<string, string> = {
  composite: "composite_fitness — the per-candidate scalar the optimizer optimizes. Recipe in the criterion.",

  criterion_accuracy: "Whether the answer is right, as the matcher grades it. Every dial below scales this — a dial at 0 is off.",

  stub_inferred:      "Heuristic display — derived client-side, not authoritative. Real value lives in measurements/.",
  stub_score_freq:    "Inferred bucket counts. Real per-sample scores live in measurements/.",
  badge_top:          "Page-anchor card — primary signal for round health.",
  brand_live_preview: "This page polls dashboard.json every 2s.",

  // The TWO caches share no mechanism and must never share a word.
  cache_replayed: "Replayed — OUR content-addressed archive answered, so no provider was reached and the cell cost nothing. Counted per CANDIDATE: how many carry at least one replayed sample.",
  cache_prefix:   "Prefix cache — the PROVIDER served part of a call's input off its own prompt-prefix cache, billed at a discount. The call did happen; part of it was cheaper.",

  masthead_best: "The run's selection against its origin: both rates on the ORIGIN's panel, over the cells the two both answered — the one pair the two may be compared on. Where the run stands as of its last closed round, never a high-water mark; a round's own accuracy is read on the subset that round bought, so this can sit above or below every round on the chart. '—' while the selection is the origin, or until it has been read against it.",
  remote_eff:    "Bench lift per dollar: the selection's lift over the origin, in the bench's headline column, on held-out rows no optimizer node read — the masthead's BENCH lift — divided by what its search incurred, never the bench's own pass; '—' where the search carries tokens no rate priced. The same reading for every optimizer, potter included; θ is potter's election signal and never stands in for it. '—' until the bench grades the pick, when the run ends.",
  arm_broken:     "Its measurements kept failing, so the bench stopped it. That is the candidate's own fault, and it is charged to it.",
  arm_eliminated: "The optimizer's eliminator stopped buying it: a budget call, not a verdict on the idea.",
  arm_locked_in:  "The optimizer's eliminator stopped it far enough ahead to call.",
  arm_skipped:    "An operator cut it short.",
  remote_flight: "Calls the round has out now, how many its stop rules allow right now, and the most it could hold if nothing were cut — every candidate walking plus the parent's catch-ups. Between rounds, the most the next round could hold. Results are taken in order, so a slow call at a candidate's head holds the round; once one has run long it is named below.",
};

export function targetNodeIds(view: PipelineView | null): string[] {
  return interiorNodes(view).map((n) => n.id);
}
