// Unified per-sample row. Both live-mode (compact string lines parsed
// from `dashboard.json::current_round.nodes.l1_score.output.candidates[].samples[]`)
// and historical-mode (structured dicts from
// `round_NNNN.json::all_candidate_results[candidate_id][]`) collapse
// to this shape. Renderers see one shape; the two source readers live
// in `lib/derivations/round-samples.ts`.

// The tape's three marks (`live_dashboard/render.py::fmt_sample_line`). `ERR` is a THIRD
// state, not a bad `MISS`: an errored row was never graded, so it belongs on neither side
// of a hit/miss partition and must not reach one as a zero.
export type SampleStatus = "HIT" | "MISS" | "ERR";

export interface SampleRow {
  // Stable React key — `${round}|${candidate_id}|${sample_id ?? ord}`.
  key: string;
  // Round this sample was scored under.
  round: number;
  // Owning candidate.
  candidate_id: string;
  // Dataset sample id when present; null when the line is unparsable
  // or pre-stamped with only an ordinal.
  sample_id: number | null;
  status: SampleStatus | null;
  // True when this measurement was reused from a prior identical searchpoint
  // (📖) rather than a fresh backend call. Both source readers populate it.
  cached: boolean;
  query: string;
  predicted: string;
  ground_truth: string;
  // Scorer label (`[ai]📖`, etc. in the compact line). Empty when
  // the historical dict doesn't carry one.
  scorer: string;
  // Wall-clock duration in seconds; null when the source omits it.
  elapsed_s: number | null;
  // The raw compact line (live mode only) — kept for the fallback
  // render when `parseSampleLine` couldn't match the regex.
  raw_line?: string;
}
