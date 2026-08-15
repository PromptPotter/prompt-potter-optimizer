// Read endpoints — thin GET wrappers over the FastAPI surface.
//
// **The response shapes are GENERATED, not declared here.** They used to be ~25 hand-mirrored
// interfaces that bypassed `scripts/build_ts_types.py` entirely — the same setup that let the
// resource-matrix types drift two fields behind their model. Everything below imports from
// `./types`; adding a field to a Pydantic response model now reaches this file by regeneration.
//
// Three exceptions, each because the server has nothing to generate FROM, and each named so
// the gap reads as a gap:
//   - `HealthResponse` — `/health` returns a bare `dict[str, str]` with no `response_model`.
//   - `LifecycleFilter` — the server's set is a module-private tuple and its members genuinely
//     differ (it accepts `checkin`, and has no `all`), so this is a different closed set.
//   - the three narrow aliases below are DERIVED from generated interfaces, not re-declared.

import { API, jget, jgetIfModified, jgetIfNoneMatch, type Conditional } from "./client";
import { encodeCyclePath, encodeDescend, pathRoot, type CyclePath } from "../ids";
import type {
  ActiveSessionResponse,
  ActivityResponse,
  BackendHealthResponse,
  BackendResponse,
  CampaignDetailResponse,
  CampaignListResponse,
  CampaignStorageResponse,
  ConfigMapResponse,
  CyclesResponse,
  DatasetIndexEntry,
  DatasetIndexResponse,
  DatasetPreviewResponse,
  DatasetStorageResponse,
  DiagnosticRunListResponse,
  FileContentResponse,
  FilesResponse,
  HardSamplesScope,
  LineageNode,
  MachineStatusResponse,
  MeasurementSeriesResponse,
  MechanismSchemaResponse,
  MeResponse,
  OptimizerPromptRanking,
  OriginListResponse,
  QuotaStatus,
  RayResponse,
  UserSettings,
  WorkspaceStorageResponse,
} from "./types";

// Server health + the single-source app version (`APP_VERSION`, surfaced by the `/health` route
// in `main.py`). Hand-written because that route declares no response model.
export interface HealthResponse {
  status: string;
  service: string;
  timestamp: string;
  version: string;
}

// Derived, never re-declared: the closed sets live on the server's own response model
// (`auth.py::ActivityWindow` / `ActivityGroupBy`, `datasets/index.py::DatasetIndexEntry.tier`),
// and these read them back off the generated interface so a member added there arrives here.
export type ActivityWindow = ActivityResponse["window"];
export type ActivityGroupBy = ActivityResponse["group_by"];
export type DatasetTier = DatasetIndexEntry["tier"];
// `domain/results.py::HardSampleOrder`, the key the leaderboard ranks by. Both hard-sample
// responses echo it, so either one can be read back for the alias.
export type HardSampleOrder = DatasetPreviewResponse["order"];

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


export function fetchMe(signal?: AbortSignal): Promise<MeResponse> {
  return jget<MeResponse>(`${API}/auth/me`, signal);
}

export function fetchUserSettings(signal?: AbortSignal): Promise<UserSettings> {
  return jget<UserSettings>(`${API}/auth/user-settings`, signal);
}

export function fetchQuotaStatus(signal?: AbortSignal): Promise<QuotaStatus> {
  return jget<QuotaStatus>(`${API}/auth/quota-status`, signal);
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

export function fetchDatasetIndex(signal?: AbortSignal): Promise<DatasetIndexResponse> {
  return jget<DatasetIndexResponse>(`${API}/datasets`, signal);
}

export function fetchOrigins(signal?: AbortSignal): Promise<OriginListResponse> {
  return jget<OriginListResponse>(`${API}/origins`, signal);
}

export function fetchPipeline(signal?: AbortSignal): Promise<unknown> {
  return jget(`${API}/optimizer-pipeline`, signal);
}

// Target connector pipeline for a dataset. One-shot — topology is bound at
// cycle-identity hash time and doesn't mutate during the loop. The server
// reads `datasets/{name}/pipeline.yaml` (dataset overlay = source of truth)
// and synthesises a `view` block from `pipelines.default` when one isn't
// explicit. Consumed by the ChatPane hero.
export function fetchDatasetPipeline(name: string, signal?: AbortSignal): Promise<unknown> {
  return jget(`${API}/datasets/${encodeURIComponent(name)}/pipeline`, signal);
}

export function fetchBackends(signal?: AbortSignal): Promise<BackendResponse[]> {
  return jget<BackendResponse[]>(`${API}/backends`, signal);
}

export function fetchBackendHealth(
  backendId: string,
  signal?: AbortSignal,
): Promise<BackendHealthResponse> {
  return jget<BackendHealthResponse>(`${API}/backends/${encodeURIComponent(backendId)}/health`, signal);
}

export function fetchMachineStatus(signal?: AbortSignal): Promise<MachineStatusResponse> {
  return jget<MachineStatusResponse>(`${API}/machine-status`, signal);
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
// `order` overrides the ranking key for this read; absent → the server resolves the
// dataset's `CampaignConfig.hard_sample_order`. Either way the resolved value comes back
// on the response, and that echo — not this argument — is what a label may be drawn from.
function hardSamplesParams(
  limit: number,
  scope: HardSamplesScope,
  campaignId?: string,
  cycleId?: string,
  descend?: string,
  order?: HardSampleOrder,
): URLSearchParams {
  const params = new URLSearchParams({ limit: String(limit), scope });
  if (order) params.set("order", order);
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
  order?: HardSampleOrder,
): Promise<DatasetPreviewResponse> {
  const params = hardSamplesParams(limit, scope, campaignId, cycleId, descend, order);
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
  order?: HardSampleOrder,
): Promise<MeasurementSeriesResponse> {
  const params = hardSamplesParams(limit, scope, campaignId, cycleId, descend, order);
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

export function fetchCampaignStorage(
  campaignId: string,
  signal?: AbortSignal,
): Promise<CampaignStorageResponse> {
  return jget<CampaignStorageResponse>(
    `${API}/campaigns/${encodeURIComponent(campaignId)}/storage`,
    signal,
  );
}

export function fetchWorkspaceStorage(
  signal?: AbortSignal,
): Promise<WorkspaceStorageResponse> {
  return jget<WorkspaceStorageResponse>(`${API}/workspace/storage`, signal);
}

export function fetchStorageByDataset(
  signal?: AbortSignal,
): Promise<DatasetStorageResponse> {
  return jget<DatasetStorageResponse>(`${API}/workspace/storage-by-dataset`, signal);
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

export function fetchMechanismsSchema(
  signal?: AbortSignal,
): Promise<MechanismSchemaResponse> {
  return jget<MechanismSchemaResponse>(`${API}/campaigns/mechanisms-schema`, signal);
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

// --- Optimizer-prompt ranking (outer-loop dashboard box) --------------------------
// The ranked table of candidate optimizer prompt states, reduced fresh from the
// tenant's on-disk pp-self cycles. Empty (n_cycles_scanned=0) for every tenant
// with no pp-self campaigns — i.e. every whitelabeled end-user; the dashboard
// renders the box only when viewing the outer pp-self loop, so the read is
// self-gating on data.
// The four shapes are GENERATED from the Pydantic source (`OptimizerPromptRanking` &c in
// `application/optimizer_prompt_ranking/reducer.py`) — they were hand-mirrored here and bypassed
// `build_ts_types.py` entirely, the same setup that let the resource-matrix types drift
// two fields behind their model before that arc was retired.
export function fetchOptimizerPromptRanking(signal?: AbortSignal): Promise<OptimizerPromptRanking> {
  return jget<OptimizerPromptRanking>(`${API}/optimizer-prompt-ranking`, signal);
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
