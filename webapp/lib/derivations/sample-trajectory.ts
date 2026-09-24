
import type { RoundSummary } from "@/lib/api/types";

export interface SortedRounds {
  rounds: RoundSummary[];
  positions: Map<number, number>[];
}

export type CellKind = "new" | "gained" | "lost" | "kept" | "absent";

function positionMap(bank: number[] | undefined): Map<number, number> {
  const m = new Map<number, number>();
  if (!bank) return m;
  bank.forEach((sid, i) => m.set(sid, i + 1));
  return m;
}

export function unionFirstAppearance(rounds: RoundSummary[]): number[] {
  const out: number[] = [];
  const seen = new Set<number>();
  for (const r of rounds) {
    for (const sid of r.selection) {
      if (seen.has(sid)) continue;
      seen.add(sid);
      out.push(sid);
    }
  }
  return out;
}

export function buildSorted(rounds: RoundSummary[]): SortedRounds {
  const sorted = [...rounds]
    .filter((r) => Array.isArray(r.selection) && r.selection.length > 0)
    .sort((a, b) => a.round - b.round);
  return {
    rounds: sorted,
    positions: sorted.map((r) => positionMap(r.selection)),
  };
}

export function cumulativeEverSeen(rounds: RoundSummary[]): Set<number>[] {
  const out: Set<number>[] = [];
  let seen = new Set<number>();
  for (const r of rounds) {
    seen = new Set(seen);
    r.selection.forEach((s) => seen.add(s));
    out.push(seen);
  }
  return out;
}

export function classifyCell(
  sid: number,
  pos: Map<number, number>,
  prev: Map<number, number> | null,
  everPrev: Set<number>,
): CellKind {
  const p = pos.get(sid);
  if (p === undefined) return "absent";
  if (!everPrev.has(sid)) return "new";
  const pp = prev?.get(sid);
  if (pp === undefined) return "new"; // re-added after a drop
  if (p < pp) return "gained";
  if (p > pp) return "lost";
  return "kept";
}
