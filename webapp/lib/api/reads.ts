// Read endpoints — thin GET wrappers over the FastAPI surface.

import { API, jget, jgetIfModified, jgetIfNoneMatch, type Conditional } from "./client";
import { encodeCyclePath, encodeDescend, pathRoot, type CyclePath } from "../ids";
import type {
  ActiveSessionResponse,
  CampaignListResponse,
  ChampionRegistry,
  CyclesResponse,
  DatasetPreviewResponse,
  DiagnosticRunListResponse,
  FileContentResponse,
  FilesResponse,
  HardSamplesScope,
  LineageNode,
  MeasurementSeriesResponse,
  RayResponse,
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
  // RBAC permit set for this identity — the honest `/auth/me` envelope. Empty for a
  // normal user; the pinned developer carries the admin caps. No webapp surface reads
  // it today (the outer-loop dashboard boxes gate on data, not a capability), but the
  // next capability gate will.
  capabilities: string[];
  // Consent gate. `terms_version` is the live required version; the app blocks
  // behind the consent gate while `terms_accepted_version` (what this user last
  // accepted, null = never) differs from it.
  terms_version: string;
  terms_accepted_version: string | null;
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
// scoped server-side: every tenant sees their own uploaded Origins
// (`tier: "yours"`), then the content that ships with the product
// (`tier: "install"` — benchmarks, demos, `promptpotter-self`), which is
// tracked in git and so needs no capability. A "yours" slug shadows an
// "install" one. Wire shape pinned in
// `docs/specs/m12-api-openapi.yaml::DatasetIndexEntry`.
export type DatasetTier = "yours" | "install";
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

// The `${API}/campaigns/{root}/cycles/{root}{suffix}` URL for a cycle PATH. The
// URL carries the path's ROOT ids; deeper hops (an L4 inner loop, L5+) ride
// `?descend=`, which the server walks into each hop's `.inner/<previous cycle id>`
// sandbox. `suffix` is the sub-route (`/dashboard`, `/events:subscribe`,
// `/file?scope=…`); descend appends with `?` or `&` depending on whether the
// suffix already opened a query. At depth 1 (no descend) the URL is byte-identical
// to a plain per-cycle read — the one builder every path-addressed read/subscribe
// shares (dashboard poll, SSE feed, deep-audit file).
export function cyclePathUrl(path: CyclePath, suffix: string): string {
  const root = pathRoot(path);
  const base =
    `${API}/campaigns/${encodeURIComponent(root.campaignId)}` +
    `/cycles/${encodeURIComponent(root.cycleId)}${suffix}`;
  const descend = encodeDescend(path);
  if (!descend) return base;
  const sep = suffix.includes("?") ? "&" : "?";
  return `${base}${sep}descend=${encodeURIComponent(descend)}`;
}

// Per-cycle file content, addressed by a CYCLE PATH (mirrors
// `fetchDashboardByPath`). Follows the viewed leaf cycle, so use this (not the
// bare id form) for any deep-audit file, e.g. `rounds/round_NNNN.json`.
export function fetchCycleFileByPath(
  path: CyclePath,
  scope: string,
  filePath: string,
  signal?: AbortSignal,
): Promise<FileContentResponse> {
  const suffix =
    `/file?scope=${encodeURIComponent(scope)}&path=${encodeURIComponent(filePath)}`;
  return jget<FileContentResponse>(cyclePathUrl(path, suffix), signal);
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

// `descend` (the `~`-joined `campaign::cycle` tail below the root hop) makes the
// hard-samples slice follow an L4 inner drill-in: present → the server reads the
// scope artifact from the inner `.inner/` sandbox instead of the outer archive.
// It only walks from the ROOT hop, so when descend is set both root ids ride
// along regardless of scope. Empty/absent → byte-identical to a top-level read.
function hardSamplesParams(
  limit: number,
  scope: HardSamplesScope,
  campaignId?: string,
  cycleId?: string,
  descend?: string,
): URLSearchParams {
  const params = new URLSearchParams({ limit: String(limit), scope });
  if ((scope === "campaign" || scope === "cycle") && campaignId) {
    params.set("campaign_id", campaignId);
  }
  if (scope === "cycle" && cycleId) params.set("cycle_id", cycleId);
  if (descend) {
    params.set("descend", descend);
    if (campaignId) params.set("campaign_id", campaignId);
    if (cycleId) params.set("cycle_id", cycleId);
  }
  return params;
}

export function fetchDatasetPreview(
  name: string,
  limit = 25,
  signal?: AbortSignal,
  scope: HardSamplesScope = "dataset",
  campaignId?: string,
  cycleId?: string,
  descend?: string,
): Promise<DatasetPreviewResponse> {
  const params = hardSamplesParams(limit, scope, campaignId, cycleId, descend);
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
  descend?: string,
): Promise<MeasurementSeriesResponse> {
  const params = hardSamplesParams(limit, scope, campaignId, cycleId, descend);
  return jget<MeasurementSeriesResponse>(
    `${API}/datasets/${encodeURIComponent(name)}/measurement-series?${params.toString()}`,
    signal,
  );
}

// Conditional dashboard fetch for the 2 s poll, addressed by a CYCLE PATH.
// The path's ROOT hop is the top-level cycle; deeper hops (an L4 inner loop, or
// L5+) ride the `?descend=` query, which the server walks into each hop's
// `.inner/<previous cycle id>` sandbox. At depth 1 the URL is byte-identical to
// a plain per-cycle read — so `If-Modified-Since`/304 behavior is unchanged.
// Pass the prior response's `Last-Modified` as `ifModifiedSince`; the
// fresh-campaign warming_up payload arrives as a 200 `{warming_up: true, ...}`.
export function fetchDashboardByPath(
  path: CyclePath,
  ifModifiedSince?: string | null,
  signal?: AbortSignal,
): Promise<Conditional<Record<string, unknown>>> {
  return jgetIfModified<Record<string, unknown>>(
    cyclePathUrl(path, "/dashboard"),
    ifModifiedSince,
    signal,
  );
}

// `lifecycle` mirrors the server's `?lifecycle=` filter — defaults to
// "active" server-side; pass "archived" to surface the archived set
// (deleted stays out of the default UI). "all" returns every status.
export type LifecycleFilter = "active" | "archived" | "deleted" | "all";

// The forest reads. `at` is the chain of cycles to descend INTO — `[]` (the
// default) is the tenant's own tree, one hop is an L4 cycle's inner fan-out, two
// is an L5 descendant. A sandbox is structurally a normal projects tree, so these
// are the SAME two endpoints at every depth; nothing here is depth-aware.
//
// Note this is `encodeCyclePath`, not `encodeDescend`: the dashboard names a leaf
// ENTITY (so its descend drops the root hop), while a forest names a STORE — every
// hop is a descent.
export function fetchCampaigns(
  dataset?: string,
  signal?: AbortSignal,
  lifecycle?: LifecycleFilter,
  at: CyclePath = [],
): Promise<CampaignListResponse> {
  const params = new URLSearchParams();
  if (dataset) params.set("dataset", dataset);
  if (lifecycle && lifecycle !== "active") params.set("lifecycle", lifecycle);
  if (at.length) params.set("descend", encodeCyclePath(at));
  const qs = params.toString();
  return jget<CampaignListResponse>(`${API}/campaigns${qs ? `?${qs}` : ""}`, signal);
}

export function fetchCycles(
  signal?: AbortSignal,
  at: CyclePath = [],
): Promise<CyclesResponse> {
  const qs = at.length ? `?descend=${encodeURIComponent(encodeCyclePath(at))}` : "";
  return jget<CyclesResponse>(`${API}/cycles${qs}`, signal);
}

// The six MECE leaves every storage surface shares — they sum to the on-disk total.
// Top-level axis is Connector / Loop / Dataset; Loop = state + trace + history + reports.
export interface StorageLeaves {
  dataset_bytes: number; // langfuse ground-truth mirror (input-data copy)
  connector_bytes: number; // backend: node-I/O cache + per-sample round arrays
  state_bytes: number; // loop resume point: round state + overrides
  trace_bytes: number; // loop telemetry: streams, prompts, langfuse loop trace
  history_bytes: number; // loop event spine: ledger.jsonl
  reports_bytes: number; // readable output: manifest + reports + hard_samples
}

// Per-campaign on-disk size — the figures the sidebar hover card shows.
// Wire shape: `GET /campaigns/{id}/storage`.
export interface CampaignStorageResponse extends StorageLeaves {
  campaign_id: string;
  on_disk_bytes: number;
}
export function fetchCampaignStorage(
  campaignId: string,
  signal?: AbortSignal,
): Promise<CampaignStorageResponse> {
  return jget<CampaignStorageResponse>(
    `${API}/campaigns/${encodeURIComponent(campaignId)}/storage`,
    signal,
  );
}

// Workspace-wide rollup — per-campaign on-disk slices, fattest first, plus the shared
// caches and a residual `other` slice so total == the tenant's real footprint.
// Wire shape: `GET /workspace/storage`.
export interface WorkspaceStorageEntry extends StorageLeaves {
  campaign_id: string;
  dataset_name: string;
  lifecycle_status: string;
  on_disk_bytes: number;
}
export interface WorkspaceStorageResponse {
  total_bytes: number;
  shared_cache_bytes: number; // measurements/ + optimizer_calls/ — reused, survive delete
  other_bytes: number; // sessions, workspace ledger, dataset/backend stores
  campaigns: WorkspaceStorageEntry[];
}
export function fetchWorkspaceStorage(
  signal?: AbortSignal,
): Promise<WorkspaceStorageResponse> {
  return jget<WorkspaceStorageResponse>(`${API}/workspace/storage`, signal);
}

// Per-dataset on-disk leaf breakdown — the Files-view "cake" (one pie per dataset,
// sliced by the six MECE leaves, summing to total = on disk).
// Wire shape: `GET /workspace/storage-by-dataset`.
export interface DatasetStorageEntry extends StorageLeaves {
  dataset_name: string;
  total_bytes: number;
}
export interface DatasetStorageResponse {
  total_bytes: number;
  datasets: DatasetStorageEntry[];
}
export function fetchStorageByDataset(
  signal?: AbortSignal,
): Promise<DatasetStorageResponse> {
  return jget<DatasetStorageResponse>(`${API}/workspace/storage-by-dataset`, signal);
}

// Campaign manifest detail — adds `config` (frozen CampaignConfig snapshot) +
// `root_content_hash` to the summary. The MechanismsPanel reads the active
// toggle states off `config.optimization.mechanisms`.
export interface CampaignDetailResponse {
  campaign_id: string;
  dataset_name: string;
  config: Record<string, unknown>;
  // …plus every CampaignSummary field; only `config` is consumed here.
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

// Config map — every optimization knob grouped by the statistical estimand it
// moves (with effective value + the layer that value came from), plus the
// declared couplings between knobs flagged active when this campaign's config
// sits in their violating combination. Answers "what overwrites what" and "what
// clashes with what". Server-authored from the one `application/knobs` registry —
// the same source the CLI `config_map` diagnostic + the pre-run preflight warning
// read, so the panel never disagrees with the engine. Wire: `GET
// /campaigns/{id}/config-map`.
export interface ConfigKnob {
  path: string;
  label: string;
  value: unknown;
  source: string; // default | campaign | required | constant
}
export interface ConfigEstimandGroup {
  key: string;
  label: string;
  doc: string;
  knobs: ConfigKnob[];
}
export interface ConfigCoupling {
  name: string;
  knobs: string[];
  labels: string[];
  estimand: string;
  relation: string;
  consequence: string;
  severity: string; // collision | inert | info
  active: boolean;
}
export interface ConfigMapResponse {
  groups: ConfigEstimandGroup[];
  couplings: ConfigCoupling[];
}
export function fetchConfigMap(
  campaignId: string,
  signal?: AbortSignal,
): Promise<ConfigMapResponse> {
  return jget<ConfigMapResponse>(
    `${API}/campaigns/${encodeURIComponent(campaignId)}/config-map`,
    signal,
  );
}

// --- L4 champion registry (outer-loop dashboard box) --------------------------
// The ranked table of candidate meta-prompt states, reduced fresh from the
// tenant's on-disk pp-self cycles. Empty (n_cycles_scanned=0) for every tenant
// with no pp-self campaigns — i.e. every whitelabeled end-user; the dashboard
// renders the box only when viewing the outer pp-self loop, so the read is
// self-gating on data.
// The four shapes are GENERATED from the Pydantic source (`ChampionRegistry` &c in
// `application/meta_champion/reducer.py`) — they were hand-mirrored here and bypassed
// `build_ts_types.py` entirely, the same setup that let the resource-matrix types drift
// two fields behind their model before that arc was retired.
export function fetchChampionRegistry(signal?: AbortSignal): Promise<ChampionRegistry> {
  return jget<ChampionRegistry>(`${API}/champion-registry`, signal);
}

// THE lineage read — one recursive tree rooted at a COURSE, nodes alternating
// `course → candidate → (course | sample)` at every depth. It is the only
// genealogy the webapp fetches; nothing here re-derives one.
//
// Rooted at a course, not a campaign: a campaign is a bag of courses, and the
// tree's own recursion (a fork or an L4 inner run is a course hanging off a
// candidate) already reaches every one of them. `/campaigns` + `/cycles` stay the
// flat registry the sidebar groups by.
//
// Conditional on an **ETag**, not a date: the validator covers the lens/samples mask
// as well as the subtree mtime, so a MASKED read gets its own 304 instead of
// recomputing the whole tree on every poll while a lens is open.
//
// There is no `depth`: one tree per campaign serves every consumer, and the recursion
// bound is the server's (`lineage_views._MAX_COURSE_DEPTH`). Two clients picking
// different depths for the same served object is what let them disagree.
//
// `path` addresses the ROOT COURSE of the tree — the same CyclePath every other
// path-addressed read uses, so an L4 inner course's tree rides `?descend=` through
// the one URL builder rather than a second convention.
export function fetchLineageTree(
  path: CyclePath,
  opts: { lens?: string | null; samples?: number[] | null } = {},
  etag?: string | null,
  signal?: AbortSignal,
): Promise<Conditional<LineageNode>> {
  // One `lens` value drives the counterfactual: `score:<formula>` = an alternative
  // scoring formula, `abort:<variant>` = a PoBB abort-contributor switch-off.
  // `samples` = the sample-set mask (re-score accuracy over only these ids); it
  // composes with a `score:` lens and is ignored for an `abort:` lens. Both land
  // ON the node they describe — there is no parallel array to re-join.
  const { lens = null, samples = null } = opts;
  const params = new URLSearchParams();
  if (lens) params.set("lens", lens);
  if (samples && samples.length > 0) params.set("samples", samples.join(","));
  const q = params.toString();
  return jgetIfNoneMatch<LineageNode>(cyclePathUrl(path, `/tree${q ? `?${q}` : ""}`), etag, signal);
}

// THE CHRONOLOGY — one merged order across a course, its forks and its inner runs; also
// the replay endpoint (the SSE tail seeks to EOF and has no `since=`). Not a second
// `/tree`: genealogy vs sequence — see webapp/CLAUDE.md § "/ray is the CHRONOLOGY".
// Windowed newest-first, delivered oldest-first; `before` = a prior `cursor_prev`.
export function fetchTimeRay(
  path: CyclePath,
  opts: { limit?: number; before?: string | null } = {},
  etag?: string | null,
  signal?: AbortSignal,
): Promise<Conditional<RayResponse>> {
  const { limit = null, before = null } = opts;
  const params = new URLSearchParams();
  if (limit != null) params.set("limit", String(limit));
  if (before) params.set("before", before);
  const q = params.toString();
  return jgetIfNoneMatch<RayResponse>(cyclePathUrl(path, `/ray${q ? `?${q}` : ""}`), etag, signal);
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
