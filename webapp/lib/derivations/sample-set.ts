import type { ServedRound } from "@/lib/api/types";

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
export function measuredUniverse(rounds: ServedRound[]): number[] {
  const set = new Set<number>();
  for (const r of rounds) for (const sid of r.selection) set.add(sid);
  return [...set].sort((a, b) => a - b);
}

interface RoundMeasuredSet {
  round: number;
  ids: number[];
}

export function roundMeasuredSets(rounds: ServedRound[]): RoundMeasuredSet[] {
  return rounds
    .filter((r) => r.selection.length > 0)
    .map((r) => ({ round: r.round, ids: [...new Set(r.selection)].sort((a, b) => a - b) }));
}

export function roundsCoveringSample(rounds: ServedRound[]): Map<number, number> {
  const out = new Map<number, number>();
  for (const r of rounds) {
    for (const sid of new Set(r.selection)) out.set(sid, (out.get(sid) ?? 0) + 1);
  }
  return out;
}
