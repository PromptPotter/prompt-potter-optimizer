// Re-export the dashboard.json surface from its definition site. This
// file is the import target for anything that needs the typed snapshot
// without pulling in the polling provider — keep components from
// reaching into `@/lib/poll` for shapes alone (the hook layer moves
// under `lib/hooks/` in a later workstream; the type surface stays
// stable here).

export type {
  BucketResult,
  CycleStreamState,
  DashboardSnapshot,
  L1ScoreOutput,
  LiveCandidate,
  LiveSample,
  StatusKind,
} from "@/lib/poll";

export type {
  CurrentRound,
  DashboardCandidate,
  LiveDashboardState,
  RoundSummary,
  RoundSummaryCandidate,
} from "@/lib/api/types";
