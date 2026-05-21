// Thin wrappers over the FastAPI surface (read-only).
//
// A campaign is a forest: `campaign_id` identifies one optimization effort
// (a dataset + pipeline origin + context), and it holds N *sessions*
// (re-runs of the same declaration) plus their fork descendants — every
// cycle flat under `campaigns/{campaign_id}/cycles/`. A `cycle_id` is
// unique only within its campaign, so every per-cycle fetch carries both
// ids. `dashboard.json` is per-session — see `fetchDashboard`.

const API = "/api/v1";

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

// All reads are live (poll-driven). Pair with the server-side
// ``Cache-Control: no-store`` header on ``/api/v1/*`` so neither layer
// can serve a stale response — the webapp is a real-time view of disk,
// not a cached copy.
async function jget<T>(url: string, signal?: AbortSignal): Promise<T> {
  const init: RequestInit = { cache: "no-store" };
  if (signal) init.signal = signal;
  const r = await fetch(url, init);
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return (await r.json()) as T;
}

export function fetchActive(signal?: AbortSignal): Promise<ActiveSession> {
  return jget<ActiveSession>(`${API}/active`, signal);
}

export function fetchPipeline(signal?: AbortSignal): Promise<unknown> {
  return jget(`${API}/optimizer/pipeline`, signal);
}

// Target connector pipeline for a dataset. One-shot — topology is bound at
// cycle-identity hash time and doesn't mutate during the loop. The server
// reads `datasets/{name}/pipeline.json` (dataset overlay = source of truth)
// and synthesises a `view` block from `pipelines.default` when one isn't
// explicit. Consumed by the ChatPane hero.
export function fetchDatasetPipeline(name: string, signal?: AbortSignal): Promise<unknown> {
  return jget(`${API}/datasets/${encodeURIComponent(name)}/pipeline`, signal);
}

// Per-cycle file content. Files live either under the cycle dir
// (`scope=cycle`) or at the campaign dir (`scope=campaign` — campaign.json,
// log.md, hard_samples.json). `dashboard.json` is NOT a campaign artifact —
// it is per-session; fetch it via `fetchDashboard`.
export function fetchCycleFile(
  campaignId: string,
  cycleId: string,
  scope: string,
  path: string,
  signal?: AbortSignal,
): Promise<FileResponse> {
  const url =
    `${API}/campaigns/${encodeURIComponent(campaignId)}` +
    `/cycles/${encodeURIComponent(cycleId)}/file` +
    `?scope=${encodeURIComponent(scope)}&path=${encodeURIComponent(path)}`;
  return jget<FileResponse>(url, signal);
}

export interface FilesListing {
  campaign_id: string;
  cycle_id: string;
  entries: FileEntry[];
}

export function fetchFiles(
  campaignId: string,
  cycleId: string,
  signal?: AbortSignal,
): Promise<FilesListing> {
  return jget<FilesListing>(
    `${API}/campaigns/${encodeURIComponent(campaignId)}` +
      `/cycles/${encodeURIComponent(cycleId)}/files`,
    signal,
  );
}

export interface DatasetItem {
  sample_id: number;
  query: string;
  ground_truth: string;
  task?: string | null;
  n_obs: number;
  // Miss-probability for an average candidate, sigmoid(δ_s) from Rasch.
  // 0.5 prior for unmeasured samples. Drives the static-mode sort.
  miss_prob: number;
  // Expected information gain of measuring this sample on a brand-new
  // candidate (ability prior N(0, σ_θ²)). Reads the Rasch δ_s standard
  // error, so a barely-measured sample scores high. The live picker
  // recomputes per step against the candidate's running θ̂_c posterior,
  // so this is a descriptive snapshot — not the iteration order.
  // null when the sample has no measurement yet.
  pick_score: number | null;
}

export interface DatasetPreview {
  name: string;
  row_count: number;
  train_count: number;
  test_count: number;
  items: DatasetItem[];
}

// Three named data scopes — same vocabulary as the heatmap artifacts and
// the API's `scope` query param. `cycle` = one cycle's own Rasch fit;
// `campaign` = the campaign's pooled fit; `dataset` = the cross-campaign
// archive snapshot. A workspace-scope heatmap is meaningless (samples
// differ per dataset), so the heatmap tier stops at `dataset`.
export type HardSamplesScope = "cycle" | "campaign" | "dataset";

export function fetchDatasetPreview(
  name: string,
  limit = 25,
  signal?: AbortSignal,
  scope: HardSamplesScope = "dataset",
  campaignId?: string,
  cycleId?: string,
): Promise<DatasetPreview> {
  const params = new URLSearchParams({ limit: String(limit), scope });
  if ((scope === "campaign" || scope === "cycle") && campaignId) {
    params.set("campaign_id", campaignId);
  }
  if (scope === "cycle" && cycleId) params.set("cycle_id", cycleId);
  return jget<DatasetPreview>(
    `${API}/datasets/${encodeURIComponent(name)}/preview?${params.toString()}`,
    signal,
  );
}

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

export function fetchMeasurementSeries(
  name: string,
  limit = 1000,
  signal?: AbortSignal,
  scope: HardSamplesScope = "dataset",
  campaignId?: string,
  cycleId?: string,
): Promise<MeasurementSeriesResponse> {
  const params = new URLSearchParams({ limit: String(limit), scope });
  if ((scope === "campaign" || scope === "cycle") && campaignId) {
    params.set("campaign_id", campaignId);
  }
  if (scope === "cycle" && cycleId) params.set("cycle_id", cycleId);
  return jget<MeasurementSeriesResponse>(
    `${API}/datasets/${encodeURIComponent(name)}/measurement-series?${params.toString()}`,
    signal,
  );
}

// Live session telemetry. `dashboard.json` is per-session — it lives in
// the session's root cycle dir and is shared by that session's forks. The
// server resolves the session-family root from any cycle of the session,
// so pass the cycle currently in view. Returns the raw dashboard dict
// (cast to `DashboardSnapshot` at the use site in poll.tsx).
export function fetchDashboard(
  campaignId: string,
  cycleId: string,
  signal?: AbortSignal,
): Promise<Record<string, unknown>> {
  return jget<Record<string, unknown>>(
    `${API}/campaigns/${encodeURIComponent(campaignId)}` +
      `/cycles/${encodeURIComponent(cycleId)}/dashboard`,
    signal,
  );
}

export async function fetchActiveDatasetName(
  campaignId: string,
  cycleId: string,
  signal?: AbortSignal,
): Promise<string | null> {
  const file = await fetchCycleFile(campaignId, cycleId, "cycle", "index.json", signal);
  if (!file.content) return null;
  const parsed = JSON.parse(file.content) as {
    dataset_name?: string;
    header?: { dataset_name?: string };
  };
  return parsed.header?.dataset_name ?? parsed.dataset_name ?? null;
}

export type SiblingKind = "root" | "fork" | "diag" | "sweep";

// Operator-facing unit kind — the time-horizon taxonomy the sidebar
// badges by, derived server-side from (sibling_kind, fork trigger).
// `session` = the root run (resume extends it); `divergent_resume` = a
// fork-on-divergence branch; `user_fork` = any operator-initiated branch
// (HITL fork, diagnostic, sweep); `l3_fork` = reserved for L3
// auto-forking (not emitted yet).
export type UnitKind =
  | "session"
  | "divergent_resume"
  | "user_fork"
  | "l3_fork";

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

export function fetchCampaigns(
  dataset?: string,
  signal?: AbortSignal,
): Promise<CampaignListResponse> {
  const qs = dataset ? `?dataset=${encodeURIComponent(dataset)}` : "";
  return jget<CampaignListResponse>(`${API}/campaigns${qs}`, signal);
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

export function fetchCycles(signal?: AbortSignal): Promise<CyclesResponse> {
  return jget<CyclesResponse>(`${API}/cycles`, signal);
}

// Campaign lineage — every cycle in one campaign + each cycle's per-round
// candidates + the parent-round each fork was cut at. One request returns
// the whole lineage tree. Mirrors the server's CampaignLineageResponse
// pydantic models verbatim.

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

export function fetchCampaignLineage(
  campaignId: string,
  signal?: AbortSignal,
): Promise<CampaignLineageResponse> {
  return jget<CampaignLineageResponse>(
    `${API}/campaigns/${encodeURIComponent(campaignId)}/lineage`,
    signal,
  );
}

// Sanctioned mutating endpoints — see promptpotter/presentation/CLAUDE.md
// for the charter. All ride existing I/O kinds (Persistence's
// `inherit_from` and Control-local's `stop_check` flag-poll); they do not
// introduce a new I/O kind.

export interface CreateForkResponse {
  campaign_id: string;
  fork_cycle_id: string;
  cli_command: string;
  active_pointer_retargeted: boolean;
}

export async function postCreateFork(
  campaignId: string,
  cycleId: string,
  round: number,
  candidateId: string,
): Promise<CreateForkResponse> {
  const r = await fetch(
    `${API}/campaigns/${encodeURIComponent(campaignId)}` +
      `/cycles/${encodeURIComponent(cycleId)}/forks`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ round, candidate_id: candidateId }),
      cache: "no-store",
    },
  );
  if (!r.ok) throw new Error(`${r.status} POST /forks`);
  return (await r.json()) as CreateForkResponse;
}

export interface StopCycleResponse {
  campaign_id: string;
  cycle_id: string;
  flag_written: boolean;
}

export interface DeleteCycleResponse {
  campaign_id: string;
  cycle_id: string;
  deleted: boolean;
  reason: string;
}

export async function deleteCycle(
  campaignId: string,
  cycleId: string,
): Promise<DeleteCycleResponse> {
  const r = await fetch(
    `${API}/campaigns/${encodeURIComponent(campaignId)}` +
      `/cycles/${encodeURIComponent(cycleId)}`,
    { method: "DELETE", cache: "no-store" },
  );
  if (!r.ok) {
    let msg = `${r.status} DELETE /cycles`;
    try {
      const body = (await r.json()) as { detail?: string };
      if (body?.detail) msg = body.detail;
    } catch {
      /* keep status-only message */
    }
    throw new Error(msg);
  }
  return (await r.json()) as DeleteCycleResponse;
}

export interface CleanupEmptyResponse {
  campaign_id: string;
  root_cycle_id: string;
  deleted_cycle_ids: string[];
  skipped: { cycle_id: string; reason: string }[];
}

export async function postCleanupEmpty(
  campaignId: string,
  cycleId: string,
): Promise<CleanupEmptyResponse> {
  const r = await fetch(
    `${API}/campaigns/${encodeURIComponent(campaignId)}` +
      `/cycles/${encodeURIComponent(cycleId)}/cleanup-empty`,
    { method: "POST", cache: "no-store" },
  );
  if (!r.ok) {
    let msg = `${r.status} POST /cleanup-empty`;
    try {
      const body = (await r.json()) as { detail?: string };
      if (body?.detail) msg = body.detail;
    } catch {
      /* keep status-only message */
    }
    throw new Error(msg);
  }
  return (await r.json()) as CleanupEmptyResponse;
}

export async function postStopCycle(
  campaignId: string,
  cycleId: string,
): Promise<StopCycleResponse> {
  const r = await fetch(
    `${API}/campaigns/${encodeURIComponent(campaignId)}` +
      `/cycles/${encodeURIComponent(cycleId)}/stop`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
    },
  );
  if (!r.ok) throw new Error(`${r.status} POST /stop`);
  return (await r.json()) as StopCycleResponse;
}

// Cross-campaign measurement leverage — read-only aggregation over the
// tenant's archive. Backs the M13 leverage panel ("your data accumulates").

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

export function fetchLeverage(
  limit = 200,
  signal?: AbortSignal,
): Promise<LeverageResponse> {
  return jget<LeverageResponse>(`${API}/measurements/leverage?limit=${limit}`, signal);
}

// Cycle detail — feeds the compare-campaigns view. Reads index.json via the
// generic cycle-file endpoint (the typed detail route's response schema
// doesn't always match the on-disk shape; reading the raw file dodges that
// mismatch).

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

export async function fetchCampaignDetail(
  campaignId: string,
  cycleId: string,
  signal?: AbortSignal,
): Promise<CampaignDetail> {
  const file = await fetchCycleFile(campaignId, cycleId, "cycle", "index.json", signal);
  if (!file.content) throw new Error("index.json is empty");
  return JSON.parse(file.content) as CampaignDetail;
}
