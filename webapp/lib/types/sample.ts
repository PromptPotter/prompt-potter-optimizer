import type { DashboardSample } from "@/lib/api/types";

export type { DashboardSample };

export type SampleStatus = DashboardSample["status"];

type Shown =
  | "position"
  | "sample_key"
  | "cached"
  | "query"
  | "predicted"
  | "status"
  | "ground_truth_text";

export type SampleRow = Pick<DashboardSample, Shown> & {
  key: string;
  round: number;
  candidate_id: string;
};
