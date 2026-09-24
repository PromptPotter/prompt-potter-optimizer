// The scoring walk as the whole declared axis plus a cursor. Past the cursor is DECLARED, not
// promised — PoBB can stop a candidate early, so no caller may word it as "will".

import { roundCandidates } from "./round-candidates";
import { samplesForRow } from "./round-samples";
import type { DashboardSnapshot } from "@/lib/poll";
import type { CandidateRow } from "@/lib/types";

export interface SampleWalk {
  // Empty when nothing is running.
  ids: number[];
  // -1 when there is no walk.
  cursor: number;
  // A new candidate is a new axis, so a scrolled window must not survive it. Ids only.
  walkKey: string;
}

const EMPTY: SampleWalk = { ids: [], cursor: -1, walkKey: "idle" };

export function sampleWalk(
  dash: DashboardSnapshot | null,
  order: number[] | null,
  isLive: boolean,
): SampleWalk {
  // `current_round` candidates linger in dashboard.json after a stop, so a stopped
  // run would otherwise keep pointing at whatever it was doing when it died.
  if (!dash || !isLive) return EMPTY;

  // The latest-seeded candidate is the one being scored; read through the spine so the tape is
  // parsed once for the whole app.
  let latest: CandidateRow | undefined;
  for (const row of roundCandidates(dash)) {
    if (row.source !== "inflight") continue;
    if (!latest || row.idx > latest.idx) latest = row;
  }
  const measured = latest
    ? samplesForRow(latest, dash, null).flatMap((s) => (s.sample_id == null ? [] : [s.sample_id]))
    : [];
  const inFlight = typeof dash.current_sample_id === "number" ? dash.current_sample_id : null;
  const walkKey = `${dash.cycle_id}:${dash.current_round.round}:${latest?.idx ?? "-"}`;

  // Same list; the stream wins only because it lands sooner, and the served copy is what a
  // reader who joined mid-candidate has.
  const served = dash.declared_sample_order;
  const axis = order && order.length > 0 ? order : served.length > 0 ? served : null;

  if (axis) {
    // Anchor on the in-flight id, else the last measured id's own position, so a walk off the
    // declared order cannot slide the cursor.
    let cursor = inFlight != null ? axis.indexOf(inFlight) : -1;
    if (cursor < 0) {
      const lastId = measured.at(-1);
      const at = lastId != null ? axis.indexOf(lastId) : -1;
      cursor = at >= 0 ? Math.min(at + 1, axis.length - 1) : Math.min(measured.length, axis.length - 1);
    }
    return { ids: axis, cursor, walkKey };
  }

  // No order yet: the past is known, the future is not invented.
  const ids = [...measured];
  if (inFlight != null && ids.at(-1) !== inFlight) ids.push(inFlight);
  if (ids.length === 0) return EMPTY;
  return { ids, cursor: ids.length - 1, walkKey };
}

export interface SampleSpread {
  measured: number;
  // Buckets over the served per-sample `mean_fitness` — a grouping, not a new score.
  never: number;
  partly: number;
  always: number;
}

export type SampleBucket = "never" | "partly" | "always";

// The one definition behind the stacked counts and the per-row colour.
export function sampleBucket(rate: number | null | undefined): SampleBucket | null {
  if (rate == null) return null;
  if (rate <= 0) return "never";
  if (rate >= 1) return "always";
  return "partly";
}

export function sampleSpread(rates: (number | null)[]): SampleSpread {
  const out: SampleSpread = { measured: 0, never: 0, partly: 0, always: 0 };
  for (const r of rates) {
    const b = sampleBucket(r);
    if (b === null) continue;
    out.measured += 1;
    out[b] += 1;
  }
  return out;
}
