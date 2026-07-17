// Shapes of the FastAPI surface — request results and the domain objects
// they carry. Mirrors the server's Pydantic models.
//
// Most shapes come from `types.generated.ts`, which `scripts/build_ts_types.py`
// keeps in sync with the Pydantic source of truth. Edits to those shapes go
// in the Python model + regenerate; hand-editing the generated file is
// forbidden.

export type {
  ActiveSessionResponse,
  CampaignListResponse,
  CampaignSummary,
  CommandAcceptedBody,
  CycleHop,
  CycleListEntry,
  CyclesResponse,
  DatasetItem,
  DatasetPipelineResponse,
  DatasetPreviewResponse,
  DegradationHealth,
  DiagnosticRunListResponse,
  DiagnosticRunRecord,
  FileContentResponse,
  FileEntry,
  FilesResponse,
  BackendWarning,
  BackfillLogEntry,
  DashboardError,
  InFlightCall,
  LineageDivergence,
  LineageNode,
  LiveDashboardState,
  LoopWarning,
  RunLimits,
  MeasurementDot,
  MeasurementSeriesResponse,
  NodeConfigParam,
  NodeOutputSchema,
  OuterCellEffect,
  OuterVerdict,
  RoundResult,
  RoundSummary,
  RoundSummaryCandidate,
  SampleOrderStep,
  ScoreboardRow,
  ScoredCandidate,
  SampleSeries,
  SpendBucket,
  SpendRollup,
} from "./types.generated";

// Literal-type unions — not derivable from Pydantic; hand-maintained.
// Three named data scopes — same vocabulary as the heatmap artifacts and
// the API's `scope` query param. `cycle` = one cycle's own Rasch fit;
// `campaign` = the campaign's pooled fit; `dataset` = the cross-campaign
// archive snapshot. A workspace-scope heatmap is meaningless (samples
// differ per dataset), so the heatmap tier stops at `dataset`.
export type HardSamplesScope = "cycle" | "campaign" | "dataset";

export type SiblingKind = "root" | "fork" | "diag" | "sweep";

// Operator-facing unit kind — the time-horizon taxonomy the sidebar
// badges by, derived server-side from (sibling_kind, fork trigger).
// `session` = the root run (resume extends it); `divergent_resume` = a
// fork-on-divergence branch; `user_fork` = any operator-initiated branch
// (HITL fork, diagnostic, sweep); `auto_rebase` = an automatic layer-driven
// rebase branch (an L2/L3 `fork_proposal`, fork trigger `l2_rebase`/`l3_rebase`).
export type UnitKind = "session" | "divergent_resume" | "user_fork" | "auto_rebase";

