import type { LiveCandidate, ServedDashboard } from "@/lib/api/types";

// Stable reference: a fresh `[]` per poll drives the candidates card into a setState loop.
const NO_ROWS: LiveCandidate[] = Object.freeze([] as LiveCandidate[]) as LiveCandidate[];

export function liveCandidates(dash: ServedDashboard | null): LiveCandidate[] {
  return dash?.current_round.candidates ?? NO_ROWS;
}

export function liveCandidate(
  dash: ServedDashboard | null,
  key: string | null,
): LiveCandidate | null {
  if (!key) return null;
  return liveCandidates(dash).find((c) => c.reading.arm.key === key) ?? null;
}

export function roundOf(dash: ServedDashboard | null): number | null {
  return dash?.current_round.round ?? null;
}
