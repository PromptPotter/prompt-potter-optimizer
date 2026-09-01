// Shapes of the FastAPI surface — request results and the domain objects
// they carry. Mirrors the server's Pydantic models.
//
// Most shapes come from `types.generated.ts`, which `scripts/build_ts_types.py`
// keeps in sync with the Pydantic source of truth. Edits to those shapes go
// in the Python model + regenerate; hand-editing the generated file is
// forbidden.

export type {
  AbilityReading,
  ArchiveReport,
  ActiveSessionResponse,
  CampaignListResponse,
  CampaignSummary,
  RankedEdit,
  EffectProvenance,
  EditSpread,
  SubjectReading,
  TrajectoryPoint,
  Comparability,
  ArmReplicate,
  EvidenceVariance,
  EvidencePower,
  OrderConfound,
  MetricSpec,
  PairwiseComparison,
  MetricReading,
  Evidence,
  CommandAcceptedBody,
  NodeSearchNarrowing,
  CycleHop,
  CycleListEntry,
  CyclesResponse,
  DatasetItem,
  DatasetPipelineResponse,
  NestedPipelineRef,
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
  CurrentRound,
  DashboardCandidate,
  MeasurementSeriesResponse,
  NodeConfigParam,
  NodeOutputSchema,
  PipelineView,
  PipelineViewEdge,
  PipelineViewNode,
  OverlapMember,
  OverlapReading,
  PanelPrecision,
  RayItem,
  RayResponse,
  ProjectionEnvelope,
  NonActivityKind,
  DashboardSample,
  RoundResult,
  RoundSummary,
  RoundSummaryCandidate,
  ScoreboardRow,
  ScoredCandidate,
  SampleSeries,
  SpendBucket,
  SpendRollup,
  ActivityBucket,
  ActivityResponse,
  BackendHealthResponse,
  BackendResponse,
  CampaignDetailResponse,
  CampaignStorageResponse,
  ConfigCoupling,
  ConfigEstimandGroup,
  ConfigKnob,
  ConfigMapResponse,
  ConnectedAccount,
  DatasetIndexEntry,
  DatasetIndexResponse,
  DatasetStorageEntry,
  DatasetStorageResponse,
  MachineHolder,
  MachineStatusResponse,
  MechanismGroup,
  MechanismSchemaResponse,
  MechanismToggle,
  MeResponse,
  OriginEntry,
  OriginListResponse,
  QuotaStatus,
  UserSettings,
  WorkspaceStorageEntry,
  WorkspaceStorageResponse,
} from "./types.generated";

import type { CycleListEntry, DegradationHealth, LiveDashboardState } from "./types.generated";

// Three named data scopes — hand-maintained because `HeatmapScope` reaches the wire only as a
// query param, so there is no response model to generate it from. Same vocabulary as the heatmap
// artifacts and
// the API's `scope` query param. `cycle` = one cycle's own Rasch fit;
// `campaign` = the campaign's pooled fit; `dataset` = the cross-campaign
// archive snapshot. A workspace-scope heatmap is meaningless (samples
// differ per dataset), so the heatmap tier stops at `dataset`.
export type HardSamplesScope = "cycle" | "campaign" | "dataset";

// What minted this cycle, as the sidebar badges it — derived server-side from the cycle id's own
// kind plus the fork trigger. READ BACK off the generated interface: `session` = the root run
// (resume extends it); `divergent_resume` = a fork-on-divergence branch; `user_fork` = any
// operator-initiated branch (HITL fork, diagnostic, sweep); `auto_rebase` = an automatic
// layer-driven rebase branch (an L2/L3 `fork_proposal`, fork trigger `l2_rebase`/`l3_rebase`).
export type MintKind = CycleListEntry["mint_kind"];

// WHY a round graded below healthy — one cause, closed server-side
// (`domain/results.py::HealthCause`). READ BACK off the generated interface, never re-typed, so a
// cause no producer emits cannot be branched on here. `null` is the `healthy` grade.
export type HealthCause = NonNullable<DegradationHealth["cause"]>;

// What ONE measured row is called (`Connector.measured_unit`) — `cell` on the recursion, where a
// row is a whole inner campaign. READ BACK off the generated interface: the engine declares it.
export type MeasuredUnit = LiveDashboardState["measured_unit"];

