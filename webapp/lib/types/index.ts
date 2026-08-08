// The `@/lib/types` surface — the shapes that travel the round spine.
// `dashboard.ts` and `round.ts` both surface RoundSummary[/Candidate];
// re-export them once (via round.ts) and name the rest of dashboard.ts.

export * from "./candidate";
export * from "./connector";
export * from "./round";
export * from "./sample";
export * from "./selection";

// dashboard.ts also re-exports RoundSummary / RoundSummaryCandidate from
// `@/lib/api/types` — already surfaced via ./round, so name the rest here.
export type {
  BucketResult,
  CurrentRound,
  CycleStreamState,
  DashboardCandidate,
  DashboardSnapshot,
  LiveDashboardState,
  L1ScoreOutput,
  LiveCandidate,
  LiveSample,
  StatusKind,
} from "./dashboard";
