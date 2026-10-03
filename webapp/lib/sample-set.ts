// Sample-set membership and equality, shared so no surface re-derives it.

import type { RoundSummary } from "@/lib/api/types";

// `null` (mode off) never equals any set.
export function sameSampleSet(a: number[] | null, b: number[]): boolean {
  if (!a || a.length !== b.length) return false;
  const sa = new Set(a);
  return b.every((x) => sa.has(x));
}

export function toggleInSet(set: number[], sid: number): number[] {
  const next = new Set(set);
  if (next.has(sid)) next.delete(sid);
  else next.add(sid);
  return [...next].sort((a, b) => a - b);
}

// Excludes the planned order on purpose: a planned-but-unmeasured sample reads 0 on every bar.
export function measuredUniverse(rounds: RoundSummary[]): number[] {
  const set = new Set<number>();
  for (const r of rounds) for (const sid of r.selection) set.add(sid);
  return [...set].sort((a, b) => a - b);
}

interface RoundMeasuredSet {
  round: number;
  ids: number[];
}

export function roundMeasuredSets(rounds: RoundSummary[]): RoundMeasuredSet[] {
  return rounds
    .filter((r) => r.selection.length > 0)
    .map((r) => ({ round: r.round, ids: [...new Set(r.selection)].sort((a, b) => a - b) }));
}

// Set membership over the served `selection` lists — a count, never a score.
export function roundsCoveringSample(rounds: RoundSummary[]): Map<number, number> {
  const out = new Map<number, number>();
  for (const r of rounds) {
    for (const sid of new Set(r.selection)) out.set(sid, (out.get(sid) ?? 0) + 1);
  }
  return out;
}
