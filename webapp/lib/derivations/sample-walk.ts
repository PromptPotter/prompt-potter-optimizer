// The scoring walk as a SEQUENCE — the axis a run moves along, plus where on it the
// run currently is. A reader wants three lines, but three lines they can slide: what
// was just measured, what is being measured, what the order says comes next, and one
// step further in either direction on demand.
//
// So this returns the whole axis and a cursor rather than three picked ids. The
// picked-ids version could not answer "and what was before that", which is the
// question an operator asks the moment a row surprises them.
//
// Two sources, each the only one that answers its question, neither recomputed here:
//   • the AXIS — the declared order off the SSE stream (`sample_order_preview`), which
//     nothing persists, so the stream is the only channel that carries it.
//   • the CURSOR — `dashboard.json::current_sample_id` while a sample is in flight,
//     falling back to how far the live candidate's own tape has got when it is not.
//
// Everything past the cursor is DECLARED, not promised: PoBB can stop a candidate
// before the order is exhausted. No caller may word it as "will".

import { liveL1Candidates, type DashboardSnapshot, type LiveCandidate } from "@/lib/poll";
import { parseSampleLine } from "@/lib/sample-line";

export interface SampleWalk {
  // The axis, in walk order. Empty when nothing is running.
  ids: number[];
  // Where the run is on it. -1 when there is no walk.
  cursor: number;
  // Identity of THIS walk — a new candidate is a new axis, and a window scrolled
  // away from the cursor must not survive the change. Ids only, never a measurement.
  walkKey: string;
}

const EMPTY: SampleWalk = { ids: [], cursor: -1, walkKey: "idle" };

// The ids the live candidate has measured so far, in tape order. The tape is TEXT
// ("0.4s #001 sid:099 HIT …") or a dict, exactly as the heat-map's live merge reads
// it. `#NNN` is the loop POSITION — the dataset id rides `sid:`, so a line without
// one contributes nothing rather than reporting a position as an id.
function measuredIds(c: { samples?: unknown[] } | undefined): number[] {
  const out: number[] = [];
  for (const s of c?.samples ?? []) {
    if (typeof s === "string") {
      const p = parseSampleLine(s);
      if (typeof p.sampleId === "number") out.push(p.sampleId);
    } else if (s && typeof s === "object") {
      const id = (s as { sample_id?: unknown }).sample_id;
      if (typeof id === "number") out.push(id);
    }
  }
  return out;
}

export function sampleWalk(
  dash: DashboardSnapshot | null,
  order: number[] | null,
  isLive: boolean,
): SampleWalk {
  // `current_round` candidates linger in dashboard.json after a stop, so a stopped
  // run would otherwise keep pointing at whatever it was doing when it died.
  if (!dash || !isLive) return EMPTY;

  // The latest-seeded candidate is the one being scored; earlier ones in this round
  // have already finished their own walk.
  let latest: LiveCandidate | undefined;
  for (const c of liveL1Candidates(dash)) {
    if (!latest || Number(c.idx ?? -1) > Number(latest.idx ?? -1)) latest = c;
  }
  const measured = measuredIds(latest);
  const inFlight = typeof dash.current_sample_id === "number" ? dash.current_sample_id : null;
  const walkKey = `${dash.cycle_id}:${dash.current_round.round}:${latest?.idx ?? "-"}`;

  if (order && order.length > 0) {
    // Anchor on the in-flight id when there is one — it survives a walk that did not
    // follow the declared order exactly. Between samples, anchor on the last measured
    // id's own position, so a re-ordered tail cannot slide the cursor.
    let cursor = inFlight != null ? order.indexOf(inFlight) : -1;
    if (cursor < 0) {
      const lastId = measured.at(-1);
      const at = lastId != null ? order.indexOf(lastId) : -1;
      cursor = at >= 0 ? Math.min(at + 1, order.length - 1) : Math.min(measured.length, order.length - 1);
    }
    return { ids: order, cursor, walkKey };
  }

  // No order (a reconnect that has not seen a candidate start). The past is still
  // known; the future is not invented — the window simply cannot scroll forward.
  const ids = [...measured];
  if (inFlight != null && ids.at(-1) !== inFlight) ids.push(inFlight);
  if (ids.length === 0) return EMPTY;
  return { ids, cursor: ids.length - 1, walkKey };
}

export interface SampleSpread {
  // Rows with at least one measurement in the scope in view.
  measured: number;
  // Of those: never solved, solved sometimes, solved every time. Buckets over the
  // SERVED per-sample `mean_fitness` — a grouping of served values, not a new score.
  never: number;
  partly: number;
  always: number;
}

export type SampleBucket = "never" | "partly" | "always";

// Which bucket one served rate falls in. The single definition behind both the
// stacked counts and the per-row colour, so the legend and the rows cannot disagree.
export function sampleBucket(rate: number | null | undefined): SampleBucket | null {
  if (rate == null) return null;
  if (rate <= 0) return "never";
  if (rate >= 1) return "always";
  return "partly";
}

// The abstract shape of the roster, for a reader who wants the situation rather than
// a list: "12 never solved · 34 partly · 8 always". Counting rows by their served
// rate; the thresholds are display buckets, not a scorer.
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
