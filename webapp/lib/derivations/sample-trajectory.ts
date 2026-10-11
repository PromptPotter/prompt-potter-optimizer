import type { ServedRound } from "@/lib/api/types";

export type SampleMovement = ServedRound["selection_movement"][number];

// Position is 1-based; a sample absent from a round's map was not in that round.
export interface SortedRounds {
  rounds: ServedRound[];
  positions: Map<number, number>[];
  movements: Map<number, SampleMovement>[];
}

export function unionFirstAppearance(rounds: ServedRound[]): number[] {
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

export function buildSorted(rounds: ServedRound[]): SortedRounds {
  const sorted = rounds.filter((r) => r.selection.length > 0);
  return {
    rounds: sorted,
    positions: sorted.map((r) => new Map(r.selection.map((sid, i) => [sid, i + 1]))),
    movements: sorted.map((r) => {
      const byId = new Map<number, SampleMovement>();
      r.selection.forEach((sid, i) => {
        const movement = r.selection_movement[i];
        if (movement !== undefined) byId.set(sid, movement);
      });
      return byId;
    }),
  };
}
