// Read endpoints — thin GET wrappers over the FastAPI surface.

import { API, jget, jgetConditional, type Conditional } from "./client";
import type {
  ActiveSessionResponse,
  CampaignLineageResponse,
  CampaignListResponse,
  CyclesResponse,
  DatasetPreviewResponse,
  DiagnosticRunListResponse,
  FileContentResponse,
  FilesResponse,
  HardSamplesScope,
  MeasurementSeriesResponse,
} from "./types";

export function fetchActive(signal?: AbortSignal): Promise<ActiveSessionResponse> {
  return jget<ActiveSessionResponse>(`${API}/sessions/active`, signal);
}

// Server health + the single-source app version (`APP_VERSION`, surfaced by
// the `/health` route in `main.py`). The About pane reads version from here
// rather than carrying a build-time copy that could drift.
export interface HealthResponse {
  status: string;
  service: string;
  timestamp: string;
  version: string;
}
export function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return jget<HealthResponse>(`${API}/health`, signal);
}


// Run-control state for the VIEWED cycle now rides `dashboard.json::run_phase`
// (declared by the runner, projected by LiveDashboardView) on the 2 s poll — the
// separate /runstate endpoint + its freshness probe are gone. The spend cap is
// folded into the same dashboard payload as `current_spend_cap_usd`.

// Current identity envelope — drives the account modal (Profile + Security).
// 401 when no session; the caller should treat that as "redirect to /login".
export interface ConnectedAccount {
  provider: string;
  email: string | null;
}
export interface MeResponse {
  user_id: string;
  tenant_id: string;
  issuer: string | null;
  email: string | null;
  name: string | null;
  provider: string | null;
  connected_accounts: ConnectedAccount[];
  available_providers: string[];
}
export function fetchMe(signal?: AbortSignal): Promise<MeResponse> {
  return jget<MeResponse>(`${API}/auth/me`, signal);
}

// Security pane quota card — spend used today, concurrent cycles,
// campaigns today, each paired with its cap from `user.json`.
export interface QuotaStatus {
  spend_used_today_usd: number;
  spend_budget_usd_daily: number | null;
  concurrent_running: number;
  max_concurrent_cycles: number;
  campaigns_today: number;
  max_campaigns_per_day: number;
}
// Account → Preferences. `demo_mode_enabled` surfaces the built-in
// try-and-learn demo dataset in the collection.
export interface UserSettings {
  demo_mode_enabled: boolean;
}
export function fetchUserSettings(signal?: AbortSignal): Promise<UserSettings> {
  return jget<UserSettings>(`${API}/auth/user-settings`, signal);
}

export function fetchQuotaStatus(signal?: AbortSignal): Promise<QuotaStatus> {
  return jget<QuotaStatus>(`${API}/auth/quota-status`, signal);
}

// Activity pane bar charts — per-user token usage projected into 30
// evenly-spaced buckets over the selected window.
// ``window``: closed set per the backend (15m / 30m / 1h / 3h / 1d / 2d / 1w / 1mo / 1y).
// ``group_by``: "model" = exact model strings; "api_key" = derived provider slug.
export type ActivityWindow =
  | "15m"
  | "30m"
  | "1h"
  | "3h"
  | "1d"
  | "2d"
  | "1w"
  | "1mo"
  | "1y";
export type ActivityGroupBy = "model" | "api_key";
export interface ActivityBucket {
  ts: number;
  spend_usd: number;
  tokens: number;
  requests: number;
  series_spend: Record<string, number>;
  series_tokens: Record<string, number>;
  series_requests: Record<string, number>;
}
export interface ActivityResponse {
  window: ActivityWindow;
  group_by: ActivityGroupBy;
  since: number;
  until: number;
  buckets: ActivityBucket[];
  series_labels: string[];
  total_spend_usd: number;
  total_tokens: number;
  total_requests: number;
}
export function fetchActivity(
  window: ActivityWindow,
  groupBy: ActivityGroupBy = "model",
  signal?: AbortSignal,
): Promise<ActivityResponse> {
  return jget<ActivityResponse>(
    `${API}/auth/activity?window=${encodeURIComponent(window)}&group_by=${encodeURIComponent(groupBy)}`,
    signal,
  );
}

// Dataset registry — backs the Dashboard "New campaign" entry. Identity-
// scoped server-side: every tenant sees their own user-uploaded Origins
// (`tier: "yours"`); install-global benchmarks (`tier: "benchmark"`) ride
// the `datasets.benchmarks.read` capability and are hidden from web
// tenants by default. Wire shape pinned in
// `docs/specs/m12-api-openapi.yaml::DatasetIndexEntry`.
export type DatasetTier = "yours" | "benchmark" | "demo";
export interface DatasetIndexEntry {
  name: string;
  title: string | null;
  tier: DatasetTier;
  n_samples: number;
}
export interface DatasetIndexResponse {
  datasets: DatasetIndexEntry[];
}
export function fetchDatasetIndex(signal?: AbortSignal): Promise<DatasetIndexResponse> {
  return jget<DatasetIndexResponse>(`${API}/datasets`, signal);
}

// Origins registry — the runnable starting points the "Reuse an origin" picker
// shows. An origin is a content identity (resolved prompt + pipeline config),
// distinct from a dataset (raw material) and a campaign (a run). Campaign-backed
// origins carry run history (`n_campaigns`, `origin_accuracy`); a `prepared`
// origin is a ready dataset config with no campaign yet (potter-run / edited
// config). Wire shape: `GET /origins`.
export interface OriginEntry {
  origin_id: string;
  dataset_name: string;
  label: string;
  n_samples: number;
  n_campaigns: number;
  origin_accuracy: number | null;
  prepared: boolean;
  created_at: string;
}
export interface OriginListResponse {
  origins: OriginEntry[];
  total: number;
}
export function fetchOrigins(signal?: AbortSignal): Promise<OriginListResponse> {
  return jget<OriginListResponse>(`${API}/origins`, signal);
}

export function fetchPipeline(signal?: AbortSignal): Promise<unknown> {
  return jget(`${API}/optimizer-pipeline`, signal);
}

// Target connector pipeline for a dataset. One-shot — topology is bound at
// cycle-identity hash time and doesn't mutate during the loop. The server
// reads `datasets/{name}/pipeline.json` (dataset overlay = source of truth)
// and synthesises a `view` block from `pipelines.default` when one isn't
// explicit. Consumed by the ChatPane hero.
export function fetchDatasetPipeline(name: string, signal?: AbortSignal): Promise<unknown> {
  return jget(`${API}/datasets/${encodeURIComponent(name)}/pipeline`, signal);
}

// Registered backend connections — the operator-level multi-backend list.
// Mirrors `BackendConnection` (promptpotter/domain/backend.py):
// {id, name, backend_type, base_url, created_at}. Drives the connector-node
// popover on the Input→LLM arrow.
export interface BackendInfo {
  id: string;
  name: string;
  backend_type: string;
  base_url: string;
  created_at: string;
}
export function fetchBackends(signal?: AbortSignal): Promise<BackendInfo[]> {
  return jget<BackendInfo[]>(`${API}/backends`, signal);
}

// Connector reachability probe — the server pings the backend's own GET /status
// (BackendClient.check_status). `status`: 'live' (reachable), 'unreachable' (TCP
// refused), or 'error'. Polled on a slow cadence by the ConnectorProvider to show the
// backend's true up/down on the connector node. Mirrors BackendHealthResponse
// (promptpotter/presentation/api/routers/backends.py).
export interface BackendHealth {
  backend_id: string;
  base_url: string;
  status: "live" | "unreachable" | "error";
  checked_at: string;
  detail: string | null;
}
export function fetchBackendHealth(
  backendId: string,
  signal?: AbortSignal,
): Promise<BackendHealth> {
  return jget<BackendHealth>(`${API}/backends/${encodeURIComponent(backendId)}/health`, signal);
}

// Whether *another* user is currently running a campaign on this single-process
// server (it runs campaigns in sequence, one at a time). `busy` is true only
// when the holder is a DIFFERENT user than the caller; `holder` is then their
// presence record. Polled on a slow cadence to raise the machine-busy banner
// before a launch is attempted — the always-on twin of the 409 `machine_busy`
// a launch returns. Mirrors MachineStatusResponse
// (promptpotter/presentation/api/routers/active.py).
export interface MachineHolder {
  user: string;
  campaign_id: string;
  cycle_id: string;
  started_at: string | null;
}
export interface MachineStatus {
  busy: boolean;
  holder: MachineHolder | null;
}
export function fetchMachineStatus(signal?: AbortSignal): Promise<MachineStatus> {
  return jget<MachineStatus>(`${API}/machine-status`, signal);
}

// Per-cycle file content. Files live either under the cycle dir
// (`scope=cycle`) or at the campaign dir (`scope=campaign` — campaign.json,
// log.md, hard_samples.json). `dashboard.json` is NOT a campaign artifact —
// it is per-session; fetch it via `fetchDashboardConditional`.
export function fetchCycleFile(
  campaignId: string,
  cycleId: string,
  scope: string,
  path: string,
  signal?: AbortSignal,
): Promise<FileContentResponse> {
  const url =
    `${API}/campaigns/${encodeURIComponent(campaignId)}` +
    `/cycles/${encodeURIComponent(cycleId)}/file` +
    `?scope=${encodeURIComponent(scope)}&path=${encodeURIComponent(path)}`;
  return jget<FileContentResponse>(url, signal);
}

export function fetchFiles(
  campaignId: string,
  cycleId: string,
  signal?: AbortSignal,
): Promise<FilesResponse> {
  return jget<FilesResponse>(
    `${API}/campaigns/${encodeURIComponent(campaignId)}` +
      `/cycles/${encodeURIComponent(cycleId)}/files`,
    signal,
  );
}

export function fetchDatasetPreview(
  name: string,
  limit = 25,
  signal?: AbortSignal,
  scope: HardSamplesScope = "dataset",
  campaignId?: string,
  cycleId?: string,
): Promise<DatasetPreviewResponse> {
  const params = new URLSearchParams({ limit: String(limit), scope });
  if ((scope === "campaign" || scope === "cycle") && campaignId) {
    params.set("campaign_id", campaignId);
  }
  if (scope === "cycle" && cycleId) params.set("cycle_id", cycleId);
  return jget<DatasetPreviewResponse>(
    `${API}/datasets/${encodeURIComponent(name)}/preview?${params.toString()}`,
    signal,
  );
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

// Conditional variant for the 2 s poll. Pass the prior response's
// `Last-Modified` header as `ifModifiedSince`; on unchanged mtime the
// server returns 304 and the poll loop short-circuits before parsing /
// re-rendering. The fresh-campaign warming_up payload arrives here as a
// 200 with `{warming_up: true, ...}` (the route never 404s).
export function fetchDashboardConditional(
  campaignId: string,
  cycleId: string,
  ifModifiedSince?: string | null,
  signal?: AbortSignal,
): Promise<Conditional<Record<string, unknown>>> {
  return jgetConditional<Record<string, unknown>>(
    `${API}/campaigns/${encodeURIComponent(campaignId)}` +
      `/cycles/${encodeURIComponent(cycleId)}/dashboard`,
    ifModifiedSince,
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

// `lifecycle` mirrors the server's `?lifecycle=` filter — defaults to
// "active" server-side; pass "archived" to surface the archived set
// (deleted stays out of the default UI). "all" returns every status.
export type LifecycleFilter = "active" | "archived" | "deleted" | "all";

export function fetchCampaigns(
  dataset?: string,
  signal?: AbortSignal,
  lifecycle?: LifecycleFilter,
): Promise<CampaignListResponse> {
  const params = new URLSearchParams();
  if (dataset) params.set("dataset", dataset);
  if (lifecycle && lifecycle !== "active") params.set("lifecycle", lifecycle);
  const qs = params.toString();
  return jget<CampaignListResponse>(`${API}/campaigns${qs ? `?${qs}` : ""}`, signal);
}

export function fetchCycles(signal?: AbortSignal): Promise<CyclesResponse> {
  return jget<CyclesResponse>(`${API}/cycles`, signal);
}

// Campaign manifest detail — adds `config` (frozen CampaignConfig snapshot) +
// the session forest to the summary. The MechanismsPanel reads the active
// toggle states off `config.optimization.mechanisms`.
export interface CampaignDetailResponse {
  campaign_id: string;
  dataset_name: string;
  config: Record<string, unknown>;
  // …plus every CampaignSummary field + `sessions`; only `config` is consumed here.
}
export function fetchCampaignDetail(
  campaignId: string,
  signal?: AbortSignal,
): Promise<CampaignDetailResponse> {
  return jget<CampaignDetailResponse>(
    `${API}/campaigns/${encodeURIComponent(campaignId)}`,
    signal,
  );
}

// Self-describing mechanism-toggle descriptor — groups, labels, descriptions,
// defaults — derived live from the `MechanismConfig` Pydantic schema. Campaign-
// independent: zip it with a campaign's `config.optimization.mechanisms` to
// render active states. A future toggle (one bool added to a group) appears
// here automatically. Wire shape: `GET /campaigns/mechanisms-schema`.
export interface MechanismToggle {
  key: string;
  label: string;
  description: string;
  default: boolean;
}
export interface MechanismGroup {
  key: string;
  label: string;
  description: string;
  toggles: MechanismToggle[];
}
export interface MechanismSchemaResponse {
  groups: MechanismGroup[];
}
export function fetchMechanismsSchema(
  signal?: AbortSignal,
): Promise<MechanismSchemaResponse> {
  return jget<MechanismSchemaResponse>(`${API}/campaigns/mechanisms-schema`, signal);
}

// Conditional, like the dashboard poll: the unmasked request honors
// `If-Modified-Since` → 304 so revalidation on the dashboard change-signal is
// cheap during quiescent stretches. A masked request (lens/samples set) always
// computes server-side — the server never 304s those.
export function fetchCampaignLineage(
  campaignId: string,
  lens: string | null,
  samples: number[] | null,
  ifModifiedSince?: string | null,
  signal?: AbortSignal,
): Promise<Conditional<CampaignLineageResponse>> {
  // One `lens` value drives the divergence overlay: `score:<formula>` = an
  // alternative scoring formula, `abort:<variant>` = a PoBB abort-contributor
  // switch-off. `samples` = the sample-set mask (re-score accuracy over only these
  // ids); it composes with a `score:` lens and is ignored for an `abort:` lens.
  const params = new URLSearchParams();
  if (lens) params.set("lens", lens);
  if (samples && samples.length > 0) params.set("samples", samples.join(","));
  const q = params.toString();
  return jgetConditional<CampaignLineageResponse>(
    `${API}/campaigns/${encodeURIComponent(campaignId)}/lineage${q ? `?${q}` : ""}`,
    ifModifiedSince,
    signal,
  );
}

// Workspace-scope diagnostic-run records — sidecars written by `verify` CLI.
// `dataset` filters to one dataset's records; omit for everything on disk.
export function fetchDiagnosticRuns(
  dataset?: string | null,
  signal?: AbortSignal,
): Promise<DiagnosticRunListResponse> {
  const qs = dataset ? `?dataset=${encodeURIComponent(dataset)}` : "";
  return jget<DiagnosticRunListResponse>(`${API}/diagnostic-runs${qs}`, signal);
}
