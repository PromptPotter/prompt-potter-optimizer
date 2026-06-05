// Unified per-sample row. Both live-mode (compact string lines parsed
// from `dashboard.json::current_round.nodes.l1_score.output.candidates[].samples[]`)
// and historical-mode (structured dicts from
// `round_NNNN.json::all_candidate_results[candidate_id][]`) collapse
// to this shape. Renderers see one shape; the two source readers live
// in `lib/derivations/round-samples.ts`.

export type SampleStatus = "HIT" | "MISS";

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
  // Set when the scored result carried an error payload (historical
  // mode); always false in live mode unless the parser reports raw.
  has_error: boolean;
  // The raw compact line (live mode only) — kept for the fallback
  // render when `parseSampleLine` couldn't match the regex.
  raw_line?: string;
}
