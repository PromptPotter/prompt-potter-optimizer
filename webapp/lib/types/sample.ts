// One per-sample row for the live and the historical source; both readers live in
// `lib/derivations/round-samples.ts`.

import type { DashboardSample } from "@/lib/api/types";

export type { DashboardSample };

export type SampleStatus = DashboardSample["status"];

export interface SampleRow {
  key: string;
  round: number;
  candidate_id: string;
  sample_id: number | null;
  // Null only for a historical row carrying neither a fitness nor an error category.
  status: SampleStatus | null;
  cached: boolean;
  query: string;
  predicted: string;
  ground_truth: string;
  terminal_node: string;
  // A replay's true 0.0; `cost_s` is what the cell took when it was measured.
  elapsed_s: number | null;
  cost_s: number | null;
  // The provider's prefix-cache share of INPUT tokens (unrelated to `cached`). Null = no breakdown
  // reported, never 0.
  cache_share: number | null;
}
