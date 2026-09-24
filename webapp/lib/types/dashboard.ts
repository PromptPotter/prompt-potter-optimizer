// Import `dashboard.json` shapes from here, never from `@/lib/poll`.

export type {
  BucketResult,
  CycleStreamState,
  DashboardSnapshot,
  L1ScoreOutput,
  LiveCandidate,
  StatusKind,
} from "@/lib/poll";

export type {
  CurrentRound,
  DashboardCandidate,
  LiveDashboardState,
  RoundSummary,
  RoundSummaryCandidate,
} from "@/lib/api/types";
