// "These rows the origin got wrong are right now" — the proof-by-example a reader
// believes when an aggregate leaves them cold.
//
// Pure over two ALREADY-SERVED `SampleRow[]` lists (`round-samples.ts`), joined on
// `sample_id`. It introduces no number, no threshold and no ordering: HIT/MISS is
// the served `status`, itself the backend's `is_hit` verdict, and the output keeps
// the champion list's own order. Deriving a *rate* here would be a second scorer;
// this only partitions rows the backend already graded.
//
// The join is on `sample_id` and nothing else. A row with none (an unparsable live
// line) is dropped rather than positionally matched — two lists measured under
// different subsets do not line up by index, and pairing them that way would
// attribute one sample's flip to another.

import type { SampleRow } from "@/lib/types";

export interface SampleFlip {
  sample_id: number;
  // The two graded rows, so a renderer can show what the model said each time
  // without going back to a document.
  before: SampleRow;
  after: SampleRow;
}

export interface SampleFlips {
  // MISS at the origin, HIT at the champion — the win.
  gained: SampleFlip[];
  // HIT at the origin, MISS at the champion — the cost. Served beside `gained`
  // deliberately: a card that shows only the wins is an advertisement, and the
  // regressions are exactly what an operator needs to see before trusting a lift.
  lost: SampleFlip[];
  // Rows both sides measured — the denominator these two lists are out of. Without
  // it "4 rows fixed" is scaleless, and under `per_round_resubset` the two lists
  // routinely cover different samples, so it is NOT either list's length.
  compared: number;
  // Same verdict both times. It is the REMAINDER, and it is here rather than left to
  // the renderer because a panel that prints two numerators over one denominator
  // reads as a partition that does not close — the operator subtracts, gets a gap
  // where the largest group should be, and files a bug against the arithmetic.
  // `gained + lost + unchanged === compared`, asserted in the tests.
  unchanged: number;
}

const EMPTY: SampleFlips = { gained: [], lost: [], compared: 0, unchanged: 0 };

// Only a GRADED row may enter the partition. `ERR` passes a `!= null` status check but
// carries no verdict, so counting one would inflate `compared` and bank it as `unchanged`
// — a row nothing scored, reported as a row that scored the same twice.
const graded = (r: SampleRow): boolean => r.status === "HIT" || r.status === "MISS";

function byId(rows: SampleRow[]): Map<number, SampleRow> {
  const out = new Map<number, SampleRow>();
  for (const r of rows) {
    // Last write wins: a sample re-measured within one candidate's stream is the
    // same cell answered again, and the later answer is the current one.
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
