// Unified per-sample row, plus the view-local address a renderer keys on. Live mode is the
// served `DashboardSample` (`dashboard.json::current_round.nodes.l1_score.output.candidates[]
// .samples[]`); historical mode is the structured dicts in
// `round_NNNN.json::all_candidate_results[candidate_id][]`. Both readers live in
// `lib/derivations/round-samples.ts`.

import type { DashboardSample } from "@/lib/api/types";

// The served row surfaces through this module, beside the view shape built from it.
export type { DashboardSample };

// Read back off the served row rather than re-declared — a closed set belongs on the server
// (`domain/dashboard_rows.py::SampleStatus`).
export type SampleStatus = DashboardSample["status"];

export interface SampleRow {
  // Stable React key — `${round}|${candidate_id}|${sample_id ?? ord}`.
  key: string;
  // Round this sample was scored under.
  round: number;
  // Owning candidate.
  candidate_id: string;
  // Dataset sample id when present; null where the source carries only an ordinal.
  sample_id: number | null;
  // Null only in historical mode, where a row can carry neither a fitness nor an error
  // category. The live row is served already graded.
  status: SampleStatus | null;
  // True when this measurement was reused from a prior identical searchpoint
  // (📖) rather than a fresh backend call. Both source readers populate it.
  cached: boolean;
  query: string;
  predicted: string;
  ground_truth: string;
  // Pipeline node the row terminated at. Empty when the historical dict omits it.
  terminal_node: string;
  // Wall-clock duration in seconds; null when the source omits it.
  elapsed_s: number | null;
}
