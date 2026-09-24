// Partitions rows the backend already graded — no rate, threshold or ordering is introduced.
// Joined on `sample_id` only, never by index: lists measured under different subsets do not align.

import type { SampleRow } from "@/lib/types";

export interface SampleFlip {
  sample_id: number;
  before: SampleRow;
  after: SampleRow;
}

export interface SampleFlips {
  gained: SampleFlip[];
  // Beside `gained` deliberately: an operator needs the regressions before trusting a lift.
  lost: SampleFlip[];
  // Under `per_round_resubset` the lists cover different samples, so this is NOT either's length.
  compared: number;
  // Served so the partition closes on screen: `gained + lost + unchanged === compared`.
  unchanged: number;
}

const EMPTY: SampleFlips = { gained: [], lost: [], compared: 0, unchanged: 0 };

// `ERR` carries no verdict; counted, it would bank as `unchanged` — a row nothing scored.
const graded = (r: SampleRow): boolean => r.status === "HIT" || r.status === "MISS";

function byId(rows: SampleRow[]): Map<number, SampleRow> {
  const out = new Map<number, SampleRow>();
  for (const r of rows) {
    // Last write wins: a re-measured sample's later answer is the current one.
    if (r.sample_id != null && graded(r)) out.set(r.sample_id, r);
  }
  return out;
}

export function sampleFlips(origin: SampleRow[], champion: SampleRow[]): SampleFlips {
  if (origin.length === 0 || champion.length === 0) return EMPTY;
  const before = byId(origin);
  const gained: SampleFlip[] = [];
  const lost: SampleFlip[] = [];
  let compared = 0;
  for (const after of champion) {
    if (after.sample_id == null || !graded(after)) continue;
    const b = before.get(after.sample_id);
    if (!b) continue;
    compared += 1;
    if (b.status === "MISS" && after.status === "HIT") {
      gained.push({ sample_id: after.sample_id, before: b, after });
    } else if (b.status === "HIT" && after.status === "MISS") {
      lost.push({ sample_id: after.sample_id, before: b, after });
    }
  }
  return { gained, lost, compared, unchanged: compared - gained.length - lost.length };
}
