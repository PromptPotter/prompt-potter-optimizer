import { API, InvalidInputError, jget, jgetIfModified, jgetIfNoneMatch, jpost, send, toApiError } from "./client";
import { encodeCyclePath, encodeDescend, pathRoot, type CyclePath } from "../ids";
import {
  ACTIVE_POINTER_POLL_MS,
  CONNECTOR_HEALTH_POLL_MS,
  DRAFT_POLL_MS,
  MACHINE_STATUS_POLL_MS,
  REGISTRY_POLL_MS,
} from "../cadence";
import { invalidateReads, readThrough, type CadenceState, type ReadDescriptor } from "../read-cache";
import type {
  ActiveSessionResponse,
  ActivityResponse,
  ActivityGroupBy,
  ActivityWindow,
  BackendHealthResponse,
  CellStatus,
  ExportTier,
  HardSampleOrder,
  HealthResponse,
  BackendResponse,
  CampaignPipelineResponse,
  ConfigMapResponse,
  CouponStatus,
  DatasetPipelineResponse,
  DatasetIndexResponse,
  DraftReopenResponse,
  Cell,
  CellsResponse,
  FileContentResponse,
  FilesResponse,
  ForkPreviewResponse,
  HardSamplesScope,
  SubtreeCampaign,
  SubtreeEntry,
  HierarchyResponse,
  LeafBytes,
  MachineStatusResponse,
  LibraryResponse,
  OptimizerRoster,
  MeResponse,
  Evidence,
  OriginListResponse,
  QuotaStatus,
  HierarchySteps,
  RoundAudit,
  RoundView,
  ServedDashboard,
  SubjectReading,
  UserSettings,
  WarmingDashboard,
  WorkspaceStorageResponse,
} from "./types";

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

// The interval whatever the read's state; null reads once.
const every = (ms: number | null) => () => ms;

// The tenant's LATEST launch, not the live set.
export function activeRead(): ReadDescriptor<ActiveSessionResponse> {
  return { ...once("active", `${API}/sessions/active`), auth: true, cadence: every(ACTIVE_POINTER_POLL_MS) };
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
  return { ...once<QuotaStatus>("quota", `${API}/auth/quota-status`), auth: true };
}

// The account's money: the host's allowance, what each wallet paid, and the keys it holds. No
// poll: a moved bill on the listing re-asks it (`hierarchyRead`), as does a write naming it.
export function couponRead(): ReadDescriptor<CouponStatus> {
  return { ...once<CouponStatus>("coupon", `${API}/auth/coupon`), auth: true };
}

// The served fields the draft's cadence reads.
type DraftStage = Pick<DraftReopenResponse, "start"> & {
  draft: Pick<DraftReopenResponse["draft"], "raw_task_description">;
};

// The draft's cadence off the served stage the flow shows (`useIngestFlow`'s `land`). A draft past
// its task and before Start is in review — a refused resolver turn's included — and re-reads
// Start's refusal, which tracks the wallet. One awaiting its task, a held Start (the listing
// follows it) and a write in flight on it rest: the write's answer re-asks it. A lost reopen is
// re-asked on the cache's own beat.
export function draftCadence({ body, writing }: CadenceState<DraftStage>): number | null {
  if (writing || body === null) return null;
  const review = body.start === null && body.draft.raw_task_description !== null;
  return review ? DRAFT_POLL_MS : null;
}

export function draftRead(draftId: string): ReadDescriptor<DraftReopenResponse> {
  return { ...once<DraftReopenResponse>("draft", `${API}/drafts/${enc(draftId)}`), cadence: draftCadence };
}

export function activityRead(
  window: ActivityWindow,
  groupBy: ActivityGroupBy,
): ReadDescriptor<ActivityResponse> {
  return once("activity", `${API}/auth/activity?window=${enc(window)}&group_by=${enc(groupBy)}`);
}

export function datasetIndexRead(): ReadDescriptor<DatasetIndexResponse> {
  return { ...once<DatasetIndexResponse>("datasets", `${API}/datasets`), auth: true };
}

export function originsRead(): ReadDescriptor<OriginListResponse> {
  return { ...once<OriginListResponse>("origins", `${API}/origins`), auth: true };
}

export function optimizerRosterRead(): ReadDescriptor<OptimizerRoster> {
  return once("optimizer-roster", `${API}/optimizers`);
}

// `at` takes the `Subject.parse` grammar (absent = campaign root); the server refuses a mask there.
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
  return { ...once<BackendResponse[]>("backends", `${API}/backends`), auth: true };
}

export function backendHealthRead(backendId: string): ReadDescriptor<BackendHealthResponse> {
  return {
    ...once<BackendHealthResponse>("backend-health", `${API}/backends/${enc(backendId)}/health`),
    auth: true,
    cadence: every(CONNECTOR_HEALTH_POLL_MS),
  };
}

export function machineStatusRead(): ReadDescriptor<MachineStatusResponse> {
  return {
    ...once<MachineStatusResponse>("machine-status", `${API}/machine-status`),
    auth: true,
    cadence: every(MACHINE_STATUS_POLL_MS),
  };
}

export function cyclePathUrl(path: CyclePath, suffix: string): string {
  const root = pathRoot(path);
  const base = `${API}/campaigns/${enc(root.campaignId)}/cycles/${enc(root.cycleId)}${suffix}`;
  const descend = encodeDescend(path);
  if (!descend) return base;
  const sep = suffix.includes("?") ? "&" : "?";
  return `${base}${sep}descend=${enc(descend)}`;
}

// One reading of the cycle per tier, serialized by the server for every surface.
export function exportUrl(path: CyclePath, tier: ExportTier): string {
  return cyclePathUrl(path, `/export?tier=${enc(tier)}`);
}

export async function exportText(path: CyclePath, tier: ExportTier): Promise<string> {
  const r = await send(exportUrl(path, tier));
  if (!r.ok) throw await toApiError(r);
  return r.text();
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
): ReadDescriptor<RoundView> {
  return once("round", cyclePathUrl(path, `/rounds/${round}${at == null ? "" : `?at=${at}`}`));
}

export function roundAuditRead(path: CyclePath, round: number): ReadDescriptor<RoundAudit | null> {
  return once("round-audit", cyclePathUrl(path, `/rounds/${round}/audit`));
}

export function filesRead(campaignId: string, cycleId: string): ReadDescriptor<FilesResponse> {
  return once("files", `${API}/campaigns/${enc(campaignId)}/cycles/${enc(cycleId)}/files`);
}

export interface CellsFilter {
  candidateId?: string | undefined;
  round?: number | undefined;
  status?: CellStatus | undefined;
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
  at: number | null,
  intervalMs: number | null,
): ReadDescriptor<CellsResponse | null> {
  const root = pathRoot(path);
  const descend = encodeDescend(path);
  const params = new URLSearchParams({ limit: "1000", scope });
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
  return {
    ...byEtag<CellsResponse | null>("cells", `${API}/datasets/${enc(name)}/cells?${params.toString()}`),
    // A 404 is an honest empty, answered null: no pooled slice exists before the first round closes.
    expects: "gone",
    cadence: every(intervalMs),
    // A re-ranked, re-scoped or replayed slice still in flight dims this one rather than blanking.
    keep: `cells${SEP}${name}${SEP}${encodeCyclePath(path)}${SEP}${JSON.stringify(filter)}`,
  };
}

export function cellRead(name: string, answer: string): ReadDescriptor<Cell> {
  return once("cell", `${API}/datasets/${enc(name)}/cells/${enc(answer)}`);
}

type DashboardBody = ServedDashboard | WarmingDashboard;

// `at` is a leaf-cycle ledger offset (`StepFacts.offset`); one file, so it validates on `Last-Modified`.
export function dashboardRead(
  path: CyclePath,
  at: number | null,
  intervalMs: number | null,
): ReadDescriptor<ServedDashboard, DashboardBody> {
  const url = cyclePathUrl(path, at == null ? "/dashboard" : `/dashboard?at=${at}`);
  return {
    id: `dashboard${SEP}${url}`,
    load: (signal, validator) => jgetIfModified<DashboardBody>(url, validator, signal),
    auth: true,
    // A warming cycle has no dashboard yet.
    accept: (body): body is ServedDashboard => !("warming_up" in body),
    // Moving the viewed moment keeps the unit's last snapshot rather than blanking the page.
    keep: `dashboard${SEP}${encodeCyclePath(path)}`,
    // A lost read reconnects on the cache's own beat.
    cadence: every(intervalMs),
  };
}

// The viewed cycle's phase change re-asks every mounted read: `run_phase` also rides `/hierarchy`
// and `/hierarchy/{address}`. Another cycle's dashboard carries none (`poll.tsx::useDashboard`).
export function onViewedPhaseChange(prev: ServedDashboard, next: ServedDashboard): void {
  if (prev.run_phase !== next.run_phase) invalidateReads();
}

// Every lifecycle: an address resolves whether or not the sidebar's filter lists it.
export function hierarchyRead(): ReadDescriptor<HierarchyResponse> {
  return {
    ...byEtag<HierarchyResponse>("hierarchy", `${API}/hierarchy?depth=1&lifecycle=all`),
    auth: true,
    cadence: every(REGISTRY_POLL_MS),
    // A moved bill re-asks the account's money.
    onChange: (prev, next) => {
      if (billOf(prev) !== billOf(next)) invalidateReads("coupon");
    },
  };
}

// Every entry's lifetime bill, an open check-in's included.
function billOf(body: HierarchyResponse): string {
  return body.entries.map((e) => e.facts.spend_lifetime.billed_usd).join(",");
}

export function campaignStorageRead(campaignId: string): ReadDescriptor<LeafBytes> {
  return once("campaign-storage", `${API}/campaigns/${enc(campaignId)}/storage`);
}

export function workspaceStorageRead(): ReadDescriptor<WorkspaceStorageResponse> {
  return once("workspace-storage", `${API}/workspace/storage`);
}

export function libraryRead(): ReadDescriptor<LibraryResponse> {
  return once("library", `${API}/library`);
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
  },
  intervalMs: number | null,
): ReadDescriptor<Evidence> {
  const named = [...subjects].sort().map((s) => `subject=${enc(s)}`);
  const qs = [...named];
  if (opts.ranking) qs.push("ranking=true");
  if (opts.winnerChain) qs.push("winner_chain=true");
  if (opts.config) qs.push("config=true");
  if (opts.metric) qs.push(`metric=${enc(opts.metric)}`);
  if (opts.grid) qs.push(`grid=${enc(opts.grid)}`);
  return {
    ...byEtag<Evidence>("evidence", `${API}/evidence?${qs.join("&")}`),
    cadence: every(intervalMs),
    // A refused metric or grid keeps the same subjects' last reading.
    keep: `evidence${SEP}${named.join("&")}`,
  };
}

// `lens` is `score:<formula>` or `abort:<variant>`; `samples` is ignored under `abort:`.
export interface SubtreeMask {
  lens: string | null;
  samples: readonly number[] | null;
}

// `cycle` owns the ledger `at` is an offset into: the viewed leaf, not the campaign's root.
export interface SubtreeMoment {
  at: number;
  cycle: CyclePath;
}

// The campaign at `address` with every level under it; any other level there is a failed read.
// `"seeding"`: the mask is the viewed cycle's own seed, not landed yet — the read waits on its
// keep body rather than sending a tree under another cycle's mask.
export function subtreeRead(
  address: string,
  mask: SubtreeMask | "seeding" | null,
  moment: SubtreeMoment | null,
  intervalMs: number | null,
): ReadDescriptor<SubtreeCampaign> {
  const keep = `subtree${SEP}${address}`;
  if (mask === "seeding") {
    return {
      id: `subtree${SEP}${address}${SEP}seeding`,
      auth: true,
      held: true,
      keep,
      load: () => Promise.reject(new Error(`a seeding tree asks nothing: ${address}`)),
    };
  }
  const params = new URLSearchParams();
  if (mask?.lens) params.set("lens", mask.lens);
  if (mask?.samples && mask.samples.length > 0) params.set("samples", mask.samples.join(","));
  if (moment) {
    params.set("at", String(moment.at));
    params.set("at_cycle", encodeCyclePath(moment.cycle));
  }
  const q = params.toString();
  const entry = byEtag<SubtreeEntry>("subtree", `${API}/hierarchy/${enc(address)}${q ? `?${q}` : ""}`);
  return {
    id: entry.id,
    auth: true,
    // A mask or moment re-keys the read; the campaign's last tree stands meanwhile.
    keep,
    // A lost tree is re-asked on the cache's own beat.
    cadence: every(intervalMs),
    load: async (signal, validator) => {
      const res = await entry.load(signal, validator);
      if (res.kind === "not_modified") return res;
      if (res.data.level !== "campaign") {
        // The address named a level this read does not serve: a verdict, never a retried outage.
        throw new InvalidInputError(`not a campaign: ${res.data.level}`);
      }
      return { ...res, data: res.data };
    },
  };
}

// A course's steps, windowed newest-first, delivered oldest-first; `before` is a prior `cursor_prev`.
// `intervalMs` is the head window's (`poll.tsx::useTimeRay`); a page never moves and reads once.
export function stepsRead(
  path: CyclePath,
  limit: number,
  before: string | null,
  intervalMs: number | null,
): ReadDescriptor<HierarchySteps> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (before) params.set("before", before);
  return {
    ...byEtag<HierarchySteps>(
      "steps",
      `${API}/hierarchy/${enc(encodeCyclePath(path))}/@?${params.toString()}`,
    ),
    auth: true,
    cadence: every(intervalMs),
  };
}

// The `pages` windows before `anchor` (a head window's `cursor_prev`), as one body oldest-first:
// each page is its own `stepsRead`, so asking one more page re-validates the ones already held.
export function earlierStepsRead(
  path: CyclePath,
  limit: number,
  anchor: string,
  pages: number,
): ReadDescriptor<HierarchySteps> {
  const unit = encodeCyclePath(path);
  return {
    id: `earlier-steps${SEP}${unit}${SEP}${anchor}${SEP}${pages}`,
    auth: true,
    // One more page asked keeps the pages already shown.
    keep: `earlier-steps${SEP}${unit}${SEP}${anchor}`,
    load: async (signal) => {
      let steps: HierarchySteps["steps"] = [];
      let cursor: string | null = anchor;
      for (let page = 0; page < pages && cursor !== null; page++) {
        const window: HierarchySteps = await readThrough(stepsRead(path, limit, cursor, null), signal);
        steps = [...window.steps, ...steps];
        cursor = window.cursor_prev;
      }
      return { kind: "ok", data: { steps, cursor_prev: cursor }, validator: null };
    },
  };
}
