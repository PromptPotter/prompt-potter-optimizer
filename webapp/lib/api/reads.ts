import { API, jget, jgetIfModified, jgetIfNoneMatch, jpost } from "./client";
import { encodeCyclePath, encodeDescend, pathRoot, type CyclePath } from "../ids";
import type { ReadDescriptor } from "../read-cache";
import type {
  ActiveSessionResponse,
  ActivityResponse,
  BackendHealthResponse,
  HealthResponse,
  LifecycleFilter,
  BackendResponse,
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
  FileContentResponse,
  FilesResponse,
  ForkPreviewResponse,
  HardSamplesScope,
  CourseNode,
  MachineStatusResponse,
  OptimizerKnobsResponse,
  OptimizerRoster,
  MeResponse,
  Evidence,
  OriginListResponse,
  QuotaStatus,
  RayResponse,
  RoundAudit,
  RoundResult,
  ServedDashboard,
  SubjectReading,
  UserSettings,
  WarmingDashboard,
  WorkspaceStorageResponse,
} from "./types";

export type ActivityWindow = ActivityResponse["window"];
export type ActivityGroupBy = ActivityResponse["group_by"];
export type HardSampleOrder = CellsResponse["order"];
export type CellStatus = CellsResponse["cells"][number]["status"];

const SEP = "\x1f";
const enc = encodeURIComponent;

function once<T>(name: string, url: string): ReadDescriptor<T> {
  return {
    id: `${name}${SEP}${url}`,
    load: async (signal) => ({ kind: "ok", data: await jget<T>(url, signal), validator: null }),
  };
}

// An ETag wherever the body depends on the query (masks, windows): a date cannot say so.
function byEtag<T>(name: string, url: string): ReadDescriptor<T> {
  return {
    id: `${name}${SEP}${url}`,
    load: (signal, etag) => jgetIfNoneMatch<T>(url, etag, signal),
  };
}

export function activeRead(): ReadDescriptor<ActiveSessionResponse> {
  return once("active", `${API}/sessions/active`);
}

export function healthRead(): ReadDescriptor<HealthResponse> {
  return once("health", `${API}/health`);
}

export function meRead(): ReadDescriptor<MeResponse> {
  return once("me", `${API}/auth/me`);
}

export function userSettingsRead(): ReadDescriptor<UserSettings> {
  return once("user-settings", `${API}/auth/user-settings`);
}

export function quotaRead(): ReadDescriptor<QuotaStatus> {
  return once("quota", `${API}/auth/quota-status`);
}

export function activityRead(
  window: ActivityWindow,
  groupBy: ActivityGroupBy,
): ReadDescriptor<ActivityResponse> {
  return once("activity", `${API}/auth/activity?window=${enc(window)}&group_by=${enc(groupBy)}`);
}

export function datasetIndexRead(): ReadDescriptor<DatasetIndexResponse> {
  return once("datasets", `${API}/datasets`);
}

export function originsRead(): ReadDescriptor<OriginListResponse> {
  return once("origins", `${API}/origins`);
}

export function optimizerPipelineRead(optimizer: string): ReadDescriptor<OptimizerPipelineResponse> {
  return once("optimizer-pipeline", `${API}/optimizer-pipeline?optimizer=${enc(optimizer)}`);
}

export function optimizerRosterRead(): ReadDescriptor<OptimizerRoster> {
  return once("optimizer-roster", `${API}/optimizers`);
}

// `at` takes the `parse_subject` grammar (absent = campaign root); the server refuses a mask there.
export function campaignPipelineRead(
  campaignId: string,
  at: string | null,
): ReadDescriptor<CampaignPipelineResponse> {
  const q = at ? `?at=${enc(at)}` : "";
  return byEtag("campaign-pipeline", `${API}/campaigns/${enc(campaignId)}/pipeline${q}`);
}

// One-shot: topology is bound into the cycle identity hash and never changes mid-loop.
export function datasetPipelineRead(name: string): ReadDescriptor<DatasetPipelineResponse> {
  return once("dataset-pipeline", `${API}/datasets/${enc(name)}/pipeline`);
}

export function backendsRead(): ReadDescriptor<BackendResponse[]> {
  return once("backends", `${API}/backends`);
}

export function backendHealthRead(backendId: string): ReadDescriptor<BackendHealthResponse> {
  return once("backend-health", `${API}/backends/${enc(backendId)}/health`);
}

export function machineStatusRead(): ReadDescriptor<MachineStatusResponse> {
  return once("machine-status", `${API}/machine-status`);
}

export function cyclePathUrl(path: CyclePath, suffix: string): string {
  const root = pathRoot(path);
  const base = `${API}/campaigns/${enc(root.campaignId)}/cycles/${enc(root.cycleId)}${suffix}`;
  const descend = encodeDescend(path);
  if (!descend) return base;
  const sep = suffix.includes("?") ? "&" : "?";
  return `${base}${sep}descend=${enc(descend)}`;
}

// `dashboard.json` is not a cycle file — read it through `dashboardRead`.
export function cycleFileRead(
  path: CyclePath,
  scope: string,
  filePath: string,
): ReadDescriptor<FileContentResponse> {
  return once("file", cyclePathUrl(path, `/file?scope=${enc(scope)}&path=${enc(filePath)}`));
}

export function roundRead(
  path: CyclePath,
  round: number,
  at: number | null = null,
): ReadDescriptor<RoundResult> {
  return once("round", cyclePathUrl(path, `/rounds/${round}${at == null ? "" : `?at=${at}`}`));
}

export function roundAuditRead(path: CyclePath, round: number): ReadDescriptor<RoundAudit | null> {
  return once("round-audit", cyclePathUrl(path, `/rounds/${round}/audit`));
}

export function filesRead(campaignId: string, cycleId: string): ReadDescriptor<FilesResponse> {
  return once("files", `${API}/campaigns/${enc(campaignId)}/cycles/${enc(cycleId)}/files`);
}

export interface CellsFilter {
  candidateId?: string;
  round?: number;
  status?: CellStatus;
}

// The server walks `descend` from the ROOT hop, so both root ids ride along whatever the scope.
export function cellsRead(
  name: string,
  path: CyclePath,
  scope: HardSamplesScope,
  // Null is the dataset's `CampaignConfig.hard_sample_order`; label from the echo.
  order: HardSampleOrder | null,
  filter: CellsFilter = {},
  // The server replays the CYCLE scope alone and refuses `at` elsewhere.
  at: number | null = null,
  limit = 1000,
): ReadDescriptor<CellsResponse> {
  const root = pathRoot(path);
  const descend = encodeDescend(path);
  const params = new URLSearchParams({ limit: String(limit), scope });
  if (order) params.set("order", order);
  if (scope === "campaign" || scope === "cycle" || descend) {
    params.set("campaign_id", root.campaignId);
  }
  if (scope === "cycle" || descend) params.set("cycle_id", root.cycleId);
  if (descend) params.set("descend", descend);
  if (filter.candidateId) params.set("candidate_id", filter.candidateId);
  if (filter.round != null) params.set("round", String(filter.round));
  if (filter.status) params.set("status", filter.status);
  if (at != null && scope === "cycle") params.set("at", String(at));
  return byEtag("cells", `${API}/datasets/${enc(name)}/cells?${params.toString()}`);
}

export function cellRead(name: string, answer: string): ReadDescriptor<Cell> {
  return once("cell", `${API}/datasets/${enc(name)}/cells/${enc(answer)}`);
}

export type DashboardBody = ServedDashboard | WarmingDashboard;

export function isWarming(body: DashboardBody): body is WarmingDashboard {
  return "warming_up" in body;
}

// `at` is a leaf-cycle ledger offset (`RayItem.offset`); one file, so it validates on `Last-Modified`.
export function dashboardRead(path: CyclePath, at: number | null): ReadDescriptor<DashboardBody> {
  const url = cyclePathUrl(path, at == null ? "/dashboard" : `/dashboard?at=${at}`);
  return {
    id: `dashboard${SEP}${url}`,
    load: (signal, validator) => jgetIfModified<DashboardBody>(url, validator, signal),
  };
}

export function campaignsRead(lifecycle: LifecycleFilter): ReadDescriptor<CampaignListResponse> {
  const qs = lifecycle !== "active" ? `?lifecycle=${enc(lifecycle)}` : "";
  return byEtag("campaigns", `${API}/campaigns${qs}`);
}

export function cyclesRead(
  filter: { campaign?: string; attached?: boolean } = {},
): ReadDescriptor<CyclesResponse> {
  const params = new URLSearchParams();
  if (filter.campaign) params.set("campaign", filter.campaign);
  if (filter.attached) params.set("attached", "true");
  const qs = params.toString();
  return byEtag("cycles", `${API}/cycles${qs ? `?${qs}` : ""}`);
}

export function campaignStorageRead(campaignId: string): ReadDescriptor<CampaignStorageResponse> {
  return byEtag("campaign-storage", `${API}/campaigns/${enc(campaignId)}/storage`);
}

export function workspaceStorageRead(): ReadDescriptor<WorkspaceStorageResponse> {
  return byEtag("workspace-storage", `${API}/workspace/storage`);
}

export function storageByDatasetRead(): ReadDescriptor<DatasetStorageResponse> {
  return byEtag("storage-by-dataset", `${API}/workspace/storage-by-dataset`);
}

export function optimizerKnobsRead(optimizer: string): ReadDescriptor<OptimizerKnobsResponse> {
  return once("optimizer-knobs", `${API}/optimizers/${enc(optimizer)}/knobs`);
}

export function configMapRead(campaignId: string): ReadDescriptor<ConfigMapResponse> {
  return once("config-map", `${API}/campaigns/${enc(campaignId)}/config-map`);
}

// A read despite the POST: its subject is an overlay that exists nowhere on disk yet.
export function forkPreviewRead(
  campaignId: string,
  pipelineOverlay: Record<string, unknown>,
): ReadDescriptor<ForkPreviewResponse> {
  const url = `${API}/campaigns/${enc(campaignId)}/fork-preview`;
  return {
    id: `fork-preview${SEP}${url}${SEP}${JSON.stringify(pipelineOverlay)}`,
    load: async (signal) => ({
      kind: "ok",
      data: await jpost<ForkPreviewResponse>(url, { pipeline_overlay: pipelineOverlay }, signal),
      validator: null,
    }),
  };
}

// The browser's only spelling of the subject grammar; `inside` is the hops ABOVE the leaf.
export function subjectKey(
  kind: SubjectReading["kind"],
  ids: readonly string[],
  inside: CyclePath = [],
): string {
  const address = `${kind}:${ids.join("/")}`;
  return inside.length > 0 ? `${address};in=${encodeCyclePath(inside)}` : address;
}

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

// `;` separates because it cannot appear in a safe-AST formula; the server splits on it.
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

// Subjects are sorted into the id, so one selection is one read whatever order it was picked in.
export function evidenceRead(
  subjects: readonly string[],
  opts: {
    ranking?: boolean;
    winnerChain?: boolean;
    config?: boolean;
    metric?: string;
    grid?: string;
  } = {},
): ReadDescriptor<Evidence> {
  const qs = [...subjects].sort().map((s) => `subject=${enc(s)}`);
  if (opts.ranking) qs.push("ranking=true");
  if (opts.winnerChain) qs.push("winner_chain=true");
  if (opts.config) qs.push("config=true");
  if (opts.metric) qs.push(`metric=${enc(opts.metric)}`);
  if (opts.grid) qs.push(`grid=${enc(opts.grid)}`);
  return byEtag("evidence", `${API}/evidence?${qs.join("&")}`);
}

// `lens` is `score:<formula>` or `abort:<variant>`; `samples` is ignored under `abort:`.
export interface TreeMask {
  lens: string | null;
  samples: readonly number[] | null;
}

// `cycle` owns the ledger `at` is an offset into: the viewed leaf, not the tree's root.
export interface TreeMoment {
  at: number;
  cycle: CyclePath;
}

export function treeRead(
  path: CyclePath,
  mask: TreeMask | null,
  moment: TreeMoment | null = null,
): ReadDescriptor<CourseNode> {
  const params = new URLSearchParams();
  if (mask?.lens) params.set("lens", mask.lens);
  if (mask?.samples && mask.samples.length > 0) params.set("samples", mask.samples.join(","));
  if (moment) {
    params.set("at", String(moment.at));
    params.set("at_cycle", encodeCyclePath(moment.cycle));
  }
  const q = params.toString();
  return byEtag("tree", cyclePathUrl(path, `/tree${q ? `?${q}` : ""}`));
}

// Windowed newest-first, delivered oldest-first; `before` is a prior `cursor_prev`.
export function rayRead(
  path: CyclePath,
  limit: number,
  before: string | null = null,
): ReadDescriptor<RayResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (before) params.set("before", before);
  return byEtag("ray", cyclePathUrl(path, `/ray?${params.toString()}`));
}
