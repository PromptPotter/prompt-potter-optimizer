// Every read endpoint. Response shapes come from `./types` (generated); each hand-written
// exception says why at its declaration.

import { API, jget, jgetIfModified, jgetIfNoneMatch, jpost, type Conditional } from "./client";
import { encodeCyclePath, encodeDescend, pathRoot, type CyclePath } from "../ids";
import type {
  ActiveSessionResponse,
  ActivityResponse,
  BackendHealthResponse,
  BackendResponse,
  CampaignDetailResponse,
  CampaignListResponse,
  CampaignPipelineResponse,
  CampaignStorageResponse,
  ConfigMapResponse,
  DatasetPipelineResponse,
  OptimizerPipelineResponse,
  CyclesResponse,
  DatasetIndexResponse,
  Cell,
  CellsResponse,
  DatasetStorageResponse,
  DiagnosticRunListResponse,
  FileContentResponse,
  FilesResponse,
  ForkPreviewResponse,
  HardSamplesScope,
  LineageNode,
  MachineStatusResponse,
  MechanismSchemaResponse,
  MeResponse,
  Evidence,
  OriginListResponse,
  QuotaStatus,
  RayResponse,
  SubjectReading,
  UserSettings,
  WorkspaceStorageResponse,
} from "./types";

export type ActivityWindow = ActivityResponse["window"];
export type ActivityGroupBy = ActivityResponse["group_by"];
export type HardSampleOrder = CellsResponse["order"];
export type CellStatus = CellsResponse["cells"][number]["status"];

export function fetchActive(signal?: AbortSignal): Promise<ActiveSessionResponse> {
  return jget<ActiveSessionResponse>(`${API}/sessions/active`, signal);
}

// Hand-written: `/health` (`main.py`) declares no response model. `version` is the one source of
// `APP_VERSION` in the browser.
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

export function fetchPipeline(signal?: AbortSignal): Promise<OptimizerPipelineResponse> {
  return jget<OptimizerPipelineResponse>(`${API}/optimizer-pipeline`, signal);
}

// `at` takes the `parse_subject` grammar (absent = campaign root); the server refuses a scoring
// mask there, because a mask cannot change what config a point RAN.
export function fetchCampaignPipeline(
  campaignId: string,
  at?: string | null,
  signal?: AbortSignal,
): Promise<CampaignPipelineResponse> {
  const q = at ? `?at=${encodeURIComponent(at)}` : "";
  return jget<CampaignPipelineResponse>(
    `${API}/campaigns/${encodeURIComponent(campaignId)}/pipeline${q}`,
    signal,
  );
}

// One-shot: topology is bound into the cycle identity hash and never changes mid-loop.
export function fetchDatasetPipeline(
  name: string,
  signal?: AbortSignal,
): Promise<DatasetPipelineResponse> {
  return jget<DatasetPipelineResponse>(
    `${API}/datasets/${encodeURIComponent(name)}/pipeline`,
    signal,
  );
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

// `dashboard.json` is per-session, not a campaign or cycle file: read it via `fetchDashboardByPath`.
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

// Hops below the root (an L4 inner loop) ride `?descend=`, which the server walks through each
// hop's `.inner/` sandbox.
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

// Use this rather than `fetchCycleFile` for a file of the viewed LEAF: the id form cannot reach an
// inner cycle.
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

// The server walks `descend` from the ROOT hop, so both root ids ride along whatever the scope.
// Absent `order` = the dataset's `CampaignConfig.hard_sample_order`; label from the echo, never this.
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

// Server-side filters: a preset's totals come back narrowed along with its rows.
export interface CellsFilter {
  candidateId?: string;
  round?: number;
  status?: CellStatus;
}

export function fetchCells(
  name: string,
  signal: AbortSignal | undefined,
  scope: HardSamplesScope,
  campaignId?: string,
  cycleId?: string,
  descend?: string,
  order?: HardSampleOrder,
  filter: CellsFilter = {},
  limit = 1000,
): Promise<CellsResponse> {
  const params = hardSamplesParams(limit, scope, campaignId, cycleId, descend, order);
  if (filter.candidateId) params.set("candidate_id", filter.candidateId);
  if (filter.round != null) params.set("round", String(filter.round));
  if (filter.status) params.set("status", filter.status);
  return jget<CellsResponse>(
    `${API}/datasets/${encodeURIComponent(name)}/cells?${params.toString()}`,
    signal,
  );
}

export function fetchCell(
  name: string,
  runId: string,
  sampleId: number,
  signal?: AbortSignal,
): Promise<Cell> {
  return jget<Cell>(
    `${API}/datasets/${encodeURIComponent(name)}/cells/${encodeURIComponent(runId)}/${sampleId}`,
    signal,
  );
}

// A cycle with no dashboard yet answers 200 `{warming_up: true}`. `at` folds the ledger up to that
// leaf-cycle offset (`RayItem.offset`'s space) instead of reading the materialized head.
export function fetchDashboardByPath(
  path: CyclePath,
  ifModifiedSince?: string | null,
  signal?: AbortSignal,
  at?: number | null,
): Promise<Conditional<Record<string, unknown>>> {
  return jgetIfModified<Record<string, unknown>>(
    cyclePathUrl(path, at == null ? "/dashboard" : `/dashboard?at=${at}`),
    ifModifiedSince,
    signal,
  );
}

// Hand-written query-param set: must match `manifests.py::_LIFECYCLE_FILTERS` member for member.
// Absent = "active"; "checkin" is the authoring PHASE, a narrowing of "active", not a status.
export type LifecycleFilter = "active" | "archived" | "deleted" | "checkin" | "all";

// `encodeCyclePath`, not `encodeDescend`: a forest names a STORE, so every hop in `at` is a
// descent, while a leaf ENTITY's descend drops the root hop.
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

// A read despite the POST: its subject is an overlay that exists nowhere on disk yet. The verdict
// is `fork-cycle`'s own, and the browser must not re-derive it (`frontend-surface-contract.md::I9`).
export function fetchForkPreview(
  campaignId: string,
  pipelineOverlay: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<ForkPreviewResponse> {
  return jpost<ForkPreviewResponse>(
    `${API}/campaigns/${encodeURIComponent(campaignId)}/fork-preview`,
    { pipeline_overlay: pipelineOverlay },
    signal,
  );
}

// The subject address grammar is spelled here and nowhere else in the browser; every other module
// passes the opaque string. `inside` = the hops ABOVE the leaf, on the `?descend=` codec.
export function subjectKey(
  kind: SubjectReading["kind"],
  ids: readonly string[],
  inside: CyclePath = [],
): string {
  const address = `${kind}:${ids.join("/")}`;
  return inside.length > 0 ? `${address};in=${encodeCyclePath(inside)}` : address;
}

// A reading's ids name only its leaf; a join against the lineage tree needs this whole path.
export function readingPath(reading: SubjectReading): CyclePath {
  return [
    ...reading.inside.map((h) => ({ campaignId: h.campaign_id, cycleId: h.cycle_id })),
    { campaignId: reading.campaign_id, cycleId: reading.cycle_id },
  ];
}

export function candidateSubject(path: CyclePath, candidateId: string): string {
  const leaf = path.at(-1);
  if (!leaf) return "";
  return subjectKey("candidate", [leaf.campaignId, leaf.cycleId, candidateId], path.slice(0, -1));
}

// `;` separates because it cannot appear in a safe-AST formula, which is why the server splits on
// it. A blank value drops its segment, so clearing both returns the channel to the record.
export function withMask(
  address: string,
  mask: { lens?: string | null; samples?: string | null },
): string {
  const segments = [
    mask.lens ? `lens=${mask.lens}` : "",
    mask.samples ? `samples=${mask.samples}` : "",
  ].filter(Boolean);
  return [address, ...segments].join(";");
}

export function maskedSubject(
  subject: SubjectReading,
  mask: { lens?: string | null; samples?: string | null },
): string {
  return withMask(
    subjectKey(
      subject.kind,
      [
        subject.campaign_id,
        ...(subject.kind === "campaign" ? [] : [subject.cycle_id]),
        ...(subject.kind === "candidate" ? [subject.candidate_id] : []),
      ],
      readingPath(subject).slice(0, -1),
    ),
    mask,
  );
}

export function fetchEvidence(
  subjects: readonly string[],
  opts: {
    ranking?: boolean;
    winnerChain?: boolean;
    config?: boolean;
    metric?: string;
    grid?: string;
  } = {},
  signal?: AbortSignal,
): Promise<Evidence> {
  const qs = subjects.map((s) => `subject=${encodeURIComponent(s)}`);
  if (opts.ranking) qs.push("ranking=true");
  if (opts.winnerChain) qs.push("winner_chain=true");
  if (opts.config) qs.push("config=true");
  // A catalogue key or a composed `expr:…`, opaque here — the server owns both spellings, and
  // `components/compare/MetricPicker.tsx` is the one place the browser spells the prefix.
  if (opts.metric) qs.push(`metric=${encodeURIComponent(opts.metric)}`);
  // `row,col` over two of the served `factors`. Sent rather than grouped here because the cell is
  // POOLED — an aggregate, and this layer computes none (webapp/CLAUDE.md § Scoring authority).
  if (opts.grid) qs.push(`grid=${encodeURIComponent(opts.grid)}`);
  return jget<Evidence>(`${API}/evidence?${qs.join("&")}`, signal);
}

// An ETag, not a date: the validator covers the lens/samples mask, so a masked read gets its own
// 304. No `depth` parameter: the recursion bound is the server's (`_MAX_COURSE_DEPTH`).
export function fetchLineageTree(
  path: CyclePath,
  opts: { lens?: string | null; samples?: number[] | null } = {},
  etag?: string | null,
  signal?: AbortSignal,
): Promise<Conditional<LineageNode>> {
  // `lens` is `score:<formula>` or `abort:<variant>` (a PoBB abort-contributor switch-off);
  // `samples` composes with `score:` and is ignored under `abort:`.
  const { lens = null, samples = null } = opts;
  const params = new URLSearchParams();
  if (lens) params.set("lens", lens);
  if (samples && samples.length > 0) params.set("samples", samples.join(","));
  const q = params.toString();
  return jgetIfNoneMatch<LineageNode>(cyclePathUrl(path, `/tree${q ? `?${q}` : ""}`), etag, signal);
}

// Also the replay endpoint: the SSE tail seeks to EOF and has no `since=`. Windowed newest-first,
// delivered oldest-first; `before` is a prior `cursor_prev`.
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

// The sidecars the `verify` verb writes.
export function fetchDiagnosticRuns(
  dataset?: string | null,
  signal?: AbortSignal,
): Promise<DiagnosticRunListResponse> {
  const qs = dataset ? `?dataset=${encodeURIComponent(dataset)}` : "";
  return jget<DiagnosticRunListResponse>(`${API}/diagnostic-runs${qs}`, signal);
}
