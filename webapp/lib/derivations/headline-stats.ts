// Single source of truth for the headline run KPIs — best, origin, and the
// delta between them. Before this, TopStrip and ChatPane each re-ran
// `typeof dash?.best === "number" ? …` and `best - origin` with subtly
// different finite-guards, so the same campaign could show a different
// headline in the chat job-bar than elsewhere. One derivation, they cannot
// disagree.
//
// Origin is round 0 in `dash.rounds[]` (a one-candidate round labelled "C0");
// its accuracy is the round-0 entry's accuracy. The candidate list
// (round-candidates.ts) reads the same round-0 entry through the generic loop.

import type { DashboardSnapshot } from "@/lib/poll";

export interface HeadlineStats {
  // Current best composite/accuracy, finite or null.
  best: number | null;
  // Origin accuracy behind C0, finite or null.
  origin: number | null;
  // best − origin when both are present; null otherwise.
  delta: number | null;
}

function finite(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function headlineStats(dash: DashboardSnapshot | null): HeadlineStats {
  const best = finite(dash?.best);
  const round0 = (dash?.rounds ?? []).find((r) => r.round === 0);
  const origin = finite(round0?.accuracy);
  const delta = best != null && origin != null ? best - origin : null;
  return { best, origin, delta };
}
