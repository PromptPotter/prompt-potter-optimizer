// Shapes of the FastAPI surface — request results and the domain objects
// they carry. Mirrors the server's Pydantic models.
//
// Most shapes come from `types.generated.ts`, which `scripts/build_ts_types.py`
// keeps in sync with the Pydantic source of truth. Edits to those shapes go
// in the Python model + regenerate; hand-editing the generated file is
// forbidden.
//
// Old webapp-side names (`Campaign`, `ActiveSession`, `DatasetPreview`,
// `Dashboard*`, `FileResponse`, `FilesListing`) are kept as aliases over
// the generated server-side names so consumers don't have to be touched
// here. Collapsing the aliases is tracked as Tier 5 cosmetic in
// `docs/specs/code-debt-cleanup.md`.

import type {
  ActiveSessionResponse,
  CampaignSummary,
  DatasetItem as GeneratedDatasetItem,
  DatasetPreviewResponse,
  FileContentResponse,
  FilesResponse,
  MeasurementDot as GeneratedMeasurementDot,
  MeasurementSeriesResponse as GeneratedMeasurementSeriesResponse,
  OriginSummary,
  RoundSummary,
  RoundSummaryCandidate,
} from "./types.generated";

export type {
  BackendWarning,
  CampaignLineageCandidate,
  CampaignLineageCycle,
  CampaignLineageResponse,
  CampaignLineageRound,
  CampaignListResponse,
  CleanupEmptyResponse,
  CreateForkRequest,
  CreateForkResponse,
  CycleListEntry,
  CyclesResponse,
  DatasetPipelineResponse,
  DeleteCycleResponse,
  DiagnosticRunListResponse,
  DiagnosticRunRecord,
  FileEntry,
  InFlightCall,
  LeverageResponse,
  LiveDashboardState,
  PerQueryRow,
  SampleSeries,
  SessionSummary,
  SpendBucket,
  SpendRollup,
  StopCycleResponse,
} from "./types.generated";

// Old-name aliases — webapp consumers still use the prefix-free names.
// Collapse target: Tier 5 cosmetic in docs/specs/code-debt-cleanup.md.
export type ActiveSession = ActiveSessionResponse;
export type Campaign = CampaignSummary;
export type DatasetItem = GeneratedDatasetItem;
export type DatasetPreview = DatasetPreviewResponse;
export type MeasurementDot = GeneratedMeasurementDot;
export type MeasurementSeriesResponse = GeneratedMeasurementSeriesResponse;
export type DashboardOrigin = OriginSummary;
export type DashboardRoundCandidate = RoundSummaryCandidate;
export type DashboardRoundSummary = RoundSummary;
export type FileResponse = FileContentResponse;
export type FilesListing = FilesResponse;

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
// (HITL fork, diagnostic, sweep); `l3_fork` = reserved for L3
// auto-forking (not emitted yet).
export type UnitKind = "session" | "divergent_resume" | "user_fork" | "l3_fork";

// Cycle detail — feeds the compare-campaigns view, read from index.json
// (not a router response, so not in the generator scope).

export interface CampaignRoundSummary {
  round: number;
  round_id?: string;
  label?: string;
  accuracy: number | null;
  hits?: number | null;
  total?: number | null;
  improved?: boolean;
  created_at?: string;
}

export interface CampaignDetail {
  campaign_id: string;
  status: string;
  best_accuracy: number | null;
  origin_accuracy: number | null;
  n_rounds: number;
  rounds: CampaignRoundSummary[];
  header?: { dataset_name?: string; dataset_size?: number };
  created_at?: string;
  updated_at?: string;
  finished_at?: string;
}
