import type { ServedRound } from "@/lib/api/types";

type SampleMovement = ServedRound["selection_movement"][number];

// Position is 1-based; a sample absent from a round's map was not in that round. `columns` is
// served (`SampleCoverage.first_measured`).
export interface SortedRounds {
  rounds: ServedRound[];
  columns: readonly number[];
  positions: Map<number, number>[];
  movements: Map<number, SampleMovement>[];
}

export function buildSorted(rounds: ServedRound[], columns: readonly number[]): SortedRounds {
  const sorted = rounds.filter((r) => r.selection.length > 0);
  return {
    rounds: sorted,
    columns,
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
