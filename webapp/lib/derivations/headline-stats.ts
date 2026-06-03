// Single source of truth for the headline run KPIs — best, origin, and the
// delta between them. Before this, TopStrip and ChatPane each re-ran
// `typeof dash?.best === "number" ? …` and `best - origin` with subtly
// different finite-guards, so the same campaign could show a different
// headline in the chat job-bar than elsewhere. One derivation, they cannot
// disagree.
//
// Note the `origin` here is the headline "is-a-number" reading. The candidate
// list (round-candidates.ts) keeps its own `accuracy > 0` guard — there
// "origin" means "scored, render the C0 bar", a different question — so the
// two intentionally do not share this helper.

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
  const origin = finite(dash?.origin?.accuracy);
  const delta = best != null && origin != null ? best - origin : null;
  return { best, origin, delta };
}
