// Sample-set primitives — the shared vocabulary for "a chosen set of sample
// ids" that the Sample-trajectory and the per-candidate-fitness "fixed sample
// set" mode both speak. Pure + framework-free so any future surface (monitoring,
// export) reuses the same membership/equality logic instead of re-deriving it.

import type { RoundSummary } from "@/lib/api/types";

// Order-insensitive equality of a (nullable) selected set against a candidate
// set. `null` (mode off) never equals any set.
export function sameSampleSet(a: number[] | null, b: number[]): boolean {
  if (!a || a.length !== b.length) return false;
  const sa = new Set(a);
  return b.every((x) => sa.has(x));
}

// Add/remove one id, returning a fresh sorted set (ascending).
export function toggleInSet(set: number[], sid: number): number[] {
  const next = new Set(set);
  if (next.has(sid)) next.delete(sid);
  else next.add(sid);
  return [...next].sort((a, b) => a - b);
}

// Every sample any closed round actually measured (ascending, unique) — the
// universe the bars can be sliced over. Excludes the live picker's *planned*
// order on purpose: a planned-but-unmeasured sample reads 0 on every bar.
export function measuredUniverse(rounds: RoundSummary[]): number[] {
  const set = new Set<number>();
  for (const r of rounds) for (const sid of r.selection ?? []) set.add(sid);
  return [...set].sort((a, b) => a - b);
}

interface RoundMeasuredSet {
  round: number;
  ids: number[];
}

// Per-round measured set (the round's `selection`, unique + sorted), skipping
// rounds that measured nothing. The aggregate "pick a round" default selector.
export function roundMeasuredSets(rounds: RoundSummary[]): RoundMeasuredSet[] {
  return rounds
    .filter((r) => (r.selection?.length ?? 0) > 0)
    .map((r) => ({ round: r.round, ids: [...new Set(r.selection ?? [])].sort((a, b) => a - b) }));
}
