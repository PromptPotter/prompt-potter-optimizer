import type { DashboardSample } from "@/lib/api/types";

export type { DashboardSample };

export type SampleStatus = DashboardSample["status"];

type Shown = "sample_id" | "cached" | "query" | "predicted" | "status" | "ground_truth_text";

export type SampleRow = Pick<DashboardSample, Shown> & {
  key: string;
  round: number;
  candidate_id: string;
};
