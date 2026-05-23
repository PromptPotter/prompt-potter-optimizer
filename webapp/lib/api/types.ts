// Shapes of the FastAPI surface — request results and the domain objects
// they carry. Mirrors the server's pydantic models. No runtime code.
//
// A campaign is a forest: `campaign_id` identifies one optimization effort
// (a dataset + pipeline origin + context), holding N sessions (re-runs of
// the same declaration) plus their fork descendants — every cycle flat
// under `campaigns/{campaign_id}/cycles/`. A `cycle_id` is unique only
// within its campaign, so every per-cycle call carries both ids.

export interface ActiveSession {
  tenant_id: string;
  session_id: string;
  campaign_id: string;
  cycle_id: string;
}

export interface FileEntry {
  scope: string;
  path: string;
  size?: number;
  mtime?: string;
}

export interface FileResponse {
  campaign_id: string;
  cycle_id: string;
  path: string;
  scope: string;
  content: string | null;
  content_type?: string;
  size?: number;
  mtime?: string;
}

export interface FilesListing {
  campaign_id: string;
  cycle_id: string;
  entries: FileEntry[];
}

export interface DatasetItem {
  sample_id: number;
  query: string;
  ground_truth: string;
  task?: string | null;
  n_obs: number;
  // Expected information gain of measuring this sample on a brand-new
  // candidate (ability prior N(0, σ_θ²)). Reads the Rasch δ_s standard
  // error, so a barely-measured sample scores high. The live picker
  // recomputes per step against the candidate's running θ̂_c posterior,
  // so this is a descriptive snapshot — not the iteration order.
  // null when the sample has no measurement yet.
  pick_score: number | null;
  // Rasch difficulty δ_s and its standard error — fed to the Info gain
  // column's debug tooltip and the δ / se(δ) diagnostic columns. null when
  // the sample has no measurement yet.
  delta: number | null;
  delta_se: number | null;
  // Marginal hit probability ``σ((θ_seed − δ_s) / scale)`` — what the
  // seed-centred decision-IG actually reads. 0.5 = contested at the seed's
  // ability; 0 or 1 = predictable. Explains why a low-hit-rate row can rank
  // high on Info gain (tight ``se_δ_s`` from EB shrinkage on boundary data).
  // null when the sample has no measurement yet.
  p_hat: number | null;
}

export interface DatasetPreview {
  name: string;
  row_count: number;
  // Declared train/test fold sizes from datasets/{name}/campaign.json.
  // null when the dataset declares no split. Scope-independent.
  split_train: number | null;
  split_test: number | null;
  items: DatasetItem[];
}

// Three named data scopes — same vocabulary as the heatmap artifacts and
// the API's `scope` query param. `cycle` = one cycle's own Rasch fit;
// `campaign` = the campaign's pooled fit; `dataset` = the cross-campaign
// archive snapshot. A workspace-scope heatmap is meaningless (samples
// differ per dataset), so the heatmap tier stops at `dataset`.
export type HardSamplesScope = "cycle" | "campaign" | "dataset";

export interface MeasurementDot {
  ord: string;
  hit: boolean;
  label: string;
}

export interface SampleSeries {
  sample_id: number;
  measurements: MeasurementDot[];
}

export interface MeasurementSeriesResponse {
  name: string;
  scope: HardSamplesScope;
  items: SampleSeries[];
}

export type SiblingKind = "root" | "fork" | "diag" | "sweep";

// Operator-facing unit kind — the time-horizon taxonomy the sidebar
// badges by, derived server-side from (sibling_kind, fork trigger).
// `session` = the root run (resume extends it); `divergent_resume` = a
// fork-on-divergence branch; `user_fork` = any operator-initiated branch
// (HITL fork, diagnostic, sweep); `l3_fork` = reserved for L3
// auto-forking (not emitted yet).
export type UnitKind = "session" | "divergent_resume" | "user_fork" | "l3_fork";

// One campaign — a declared optimization effort (dataset + pipeline origin
// + context). `campaign_id` is `{dataset}__{origin hash}` — stable, so a
// re-run of `new` on an unchanged declaration joins the same campaign as a
// new session. `session_count` is how many sessions the campaign holds.
export interface Campaign {
  campaign_id: string;
  dataset_name: string;
  label: string;
  status: string;
  created_at: string;
  root_cycle_id: string;
  backend_id: string;
  session_count: number;
}

export interface CampaignListResponse {
  campaigns: Campaign[];
  total: number;
}

export interface CycleListEntry {
  campaign_id: string;
  cycle_id: string;
  parent_session_id: string;
  // Immediate parent cycle id for siblings (forks/sweeps/diag); null for
  // roots. The sidebar uses this to nest sibling rows under their parent
  // within the campaign.
  parent_cycle_id: string | null;
  dataset_name: string;
  backend_id: string;
  sibling_kind: SiblingKind;
  unit_kind: UnitKind;
  is_root: boolean;
  status: string;
  best_accuracy: number | null;
  n_rounds: number;
  created_at: string;
  updated_at: string;
}

export interface CyclesResponse {
  tenant_id: string;
  active_campaign_id: string | null;
  active_cycle_id: string | null;
  cycles: CycleListEntry[];
}

// Campaign lineage — every cycle in one campaign + each cycle's per-round
// candidates + the parent-round each fork was cut at. One request returns
// the whole lineage tree. Mirrors the server's CampaignLineageResponse.

export interface CampaignLineageCandidate {
  candidate_id: string;
  label: string;
  accuracy: number | null;
  rank: number | null;
  is_winner: boolean;
}

export interface CampaignLineageRound {
  round: number;
  label: string;
  accuracy: number | null;
  candidates: CampaignLineageCandidate[];
}

export interface CampaignLineageCycle {
  cycle_id: string;
  sibling_kind: SiblingKind;
  immediate_parent_cycle_id: string | null;
  fork_from_round: number | null;
  fork_from_candidate_id: string | null;
  // Fork creation trigger — drives the round-numbering convention.
  trigger: string;
  // Add this to each round's `round` number to get its absolute column
  // in the campaign cladogram. The server computes per-trigger so the
  // client doesn't need to know HITL-vs-divergence semantics itself.
  round_column_offset: number;
  status: string;
  dataset_name: string;
  best_accuracy: number | null;
  rounds: CampaignLineageRound[];
}

export interface CampaignLineageResponse {
  campaign_id: string;
  cycles: CampaignLineageCycle[];
}

// Mutating-endpoint responses. The mutations ride existing I/O kinds
// (Persistence's `inherit_from`, Control-local's `stop_check` flag-poll);
// they introduce no new I/O kind. See promptpotter/presentation/CLAUDE.md.

export interface CreateForkResponse {
  campaign_id: string;
  fork_cycle_id: string;
  cli_command: string;
  active_pointer_retargeted: boolean;
}

export interface StopCycleResponse {
  campaign_id: string;
  cycle_id: string;
  flag_written: boolean;
}

export interface CleanupEmptyResponse {
  campaign_id: string;
  root_cycle_id: string;
  deleted_cycle_ids: string[];
  skipped: { cycle_id: string; reason: string }[];
}

// Cross-campaign measurement leverage — read-only aggregation over the
// tenant's archive ("your data accumulates").

export interface PerQueryRow {
  query: string;
  sample_id: number;
  n_measurements: number;
  n_unique_configs: number;
  mean_fitness: number | null;
  hit_rate: number;
  last_seen: string;
}

export interface LeverageResponse {
  n_runs: number;
  n_measurements: number;
  n_unique_queries: number;
  per_query: PerQueryRow[];
}

// Workspace-scope diagnostic-run records — one row per `verify` CLI invocation.
// Sidecar JSON lives at archive/diagnostic_runs/; feeds the Verify tab.

export interface DiagnosticRunRecord {
  ts: string;
  dataset: string;
  source_campaign: string;
  source_cycle: string;
  source_label: string;
  source_candidate_id: string;
  config_hash: string;
  samples_requested: number;
  samples_added: number;
  workspace_n: number;
  workspace_accuracy: number;
  workspace_composite: number;
  source_campaign_accuracy: number;
  source_campaign_composite: number;
  source_campaign_n: number;
}

export interface DiagnosticRunListResponse {
  n: number;
  runs: DiagnosticRunRecord[];
}

// Cycle detail — feeds the compare-campaigns view, read from index.json.

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
