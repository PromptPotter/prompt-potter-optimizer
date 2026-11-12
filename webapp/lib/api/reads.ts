import { API, InvalidInputError, failureKind, jgetIfNoneMatch, jpost, send, toApiError } from "./client";
import { operatorMessage } from "./errors";
import { reportIncident } from "../diagnostics";
import { encodeCyclePath, encodeDescend, pathRoot, type CyclePath } from "../ids";
import { heldRead, readThrough, type ReadDescriptor, type ReadResult } from "../read-cache";
import type {
  NestedPipelineRef,
  NodeConfigParam,
  NodeReach,
  PipelineView,
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
  CommandReading,
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

import { READ_ROUTES, type ReadKind } from "./types";

const SEP = "\x1f";
const enc = encodeURIComponent;

// The `{slot}` names a served route declares (`{name:path}` names `name`).
type SlotsOf<R extends string> = R extends `${string}{${infer S}}${infer Rest}`
  ? (S extends `${infer N}:path` ? N : S) | SlotsOf<Rest>
  : never;

// A read's URL: its served route with each slot filled, then its query; a slot it lacks fails to compile.
function readUrl<K extends ReadKind>(
  kind: K,
  slots: Record<SlotsOf<(typeof READ_ROUTES)[K]>, string>,
  query: URLSearchParams = new URLSearchParams(),
): string {
  const filled: Record<string, string> = slots;
  const route = READ_ROUTES[kind].replace(/\{(\w+)(?::path)?\}/g, (_, slot: string) => enc(filled[slot]!));
  const q = query.toString();
  return `${API}${route}${q ? `?${q}` : ""}`;
}

function once<T>(name: string, url: string): ReadDescriptor<T> {
  return {
    id: `${name}${SEP}${url}`,
    // Asked unconditionally, it answers whole: an ETag it carries is never kept.
    load: async (signal, _etag, subject) => {
      const res = await jgetIfNoneMatch<T>(url, null, signal, subject);
      return res.kind === "ok" ? { ...res, validator: null } : res;
    },
  };
}

// An ETag wherever the body depends on the query (masks, windows): a date cannot say so.
function byEtag<T>(name: string, url: string): ReadDescriptor<T> {
  return {
    id: `${name}${SEP}${url}`,
    load: (signal, etag, subject) => jgetIfNoneMatch<T>(url, etag, signal, subject),
  };
}

// The feed's subjects (`FeedFrame.subject`), matched by prefix: what the account filed (`account`),
// a store's campaigns, cycles and ledgers (`store:` and the hops above it; bare, every store), the
// machine's slots. A read naming none reads once.
const ACCOUNT = "account";
const STORES = "store:";
const MACHINE = "machine";

// The store holding the path's leaf cycle: an inner run's moves are its sandbox's.
function storeOf(path: CyclePath): string {
  return `${STORES}${encodeCyclePath(path.slice(0, -1))}`;
}

// The run to follow: a running one, else the tenant's latest launch; never the live set.
export function activeRead(): ReadDescriptor<ActiveSessionResponse> {
  return { ...once("active", readUrl("active-session", {})), auth: true, subject: STORES };
}

export function healthRead(): ReadDescriptor<HealthResponse> {
  return once("health", `${API}/health`);
}

export function meRead(): ReadDescriptor<MeResponse> {
  return { ...once<MeResponse>("me", readUrl("me", {})), subject: ACCOUNT };
}

export function userSettingsRead(): ReadDescriptor<UserSettings> {
  return { ...once<UserSettings>("user-settings", readUrl("user-settings", {})), subject: ACCOUNT };
}

export function quotaRead(): ReadDescriptor<QuotaStatus> {
  return { ...once<QuotaStatus>("quota", readUrl("quota-status", {})), auth: true, subject: STORES };
}

// The account's money: the host's allowance, what each wallet paid, and the keys it holds; every
// ledger's spend moves it.
export function couponRead(): ReadDescriptor<CouponStatus> {
  return { ...once<CouponStatus>("coupon", readUrl("coupon", {})), auth: true, subject: STORES };
}

// Start's refusal tracks the wallet, which every ledger moves.
export function draftRead(draftId: string): ReadDescriptor<DraftReopenResponse> {
  return { ...once<DraftReopenResponse>("draft", readUrl("draft", { draft_id: draftId })), subject: STORES };
}

// A command as every door reads it: a queued launch's job names its cycle once a slot mints it, and
// the minted run's appends move its store.
export function commandRead(commandId: string): ReadDescriptor<CommandReading> {
  return { ...once<CommandReading>("command", readUrl("command", { command_id: commandId })), auth: true, subject: STORES };
}

export function activityRead(
  window: ActivityWindow,
  groupBy: ActivityGroupBy,
): ReadDescriptor<ActivityResponse> {
  return {
    ...once<ActivityResponse>("activity", readUrl("activity", {}, new URLSearchParams({ window, group_by: groupBy }))),
    subject: STORES,
  };
}

export function datasetIndexRead(): ReadDescriptor<DatasetIndexResponse> {
  return { ...once<DatasetIndexResponse>("datasets", readUrl("datasets", {})), auth: true, subject: ACCOUNT };
}

export function originsRead(): ReadDescriptor<OriginListResponse> {
  return { ...once<OriginListResponse>("origins", readUrl("origins", {})), auth: true, subject: STORES };
}

export function optimizerRosterRead(): ReadDescriptor<OptimizerRoster> {
  return once("optimizer-roster", readUrl("optimizers", {}));
}

// `at` takes the `Subject.parse` grammar (absent = campaign root); the server refuses a mask there.
export function campaignPipelineRead(
  campaignId: string,
  at: string | null,
): ReadDescriptor<CampaignPipelineResponse> {
  const q = new URLSearchParams(at ? { at } : {});
  // A filed edit moves it; a run's appends do not.
  return { ...byEtag("campaign-pipeline", readUrl("campaign-pipeline", { campaign_id: campaignId }, q)), subject: ACCOUNT };
}

// One-shot: topology is bound into the cycle identity hash and never changes mid-loop.
export function datasetPipelineRead(name: string): ReadDescriptor<DatasetPipelineResponse> {
  return once("dataset-pipeline", readUrl("dataset-pipeline", { name }));
}

// One per dataset a level's cells measure, so a level holds several; `parent` is the enclosing layer's `key`.
export interface NestedLayer {
  key: string;
  parent: string;
  depth: number;
  dataset: string;
  connector: string | null;
  view: PipelineView | null;
  schema: Record<string, NodeConfigParam[]> | null;
  // Null is UNKNOWN, never shut.
  reach: Record<string, NodeReach> | null;
  nestsNode: string | null;
  read: ReadResult<unknown>;
}

export interface NestedPipelines {
  // Every layer after the one it nests in.
  layers: NestedLayer[];
  // The served reasons a nest was not read, joined; null where every one was.
  unread: string | null;
}

// The `parent` of every layer the campaign's own pipeline nests.
export const CAMPAIGN_LAYER = "campaign";

function collect(
  dataset: string,
  resp: DatasetPipelineResponse,
  parent: string,
  depth: number,
  out: { layers: NestedLayer[]; unread: string[] },
): void {
  const key = `${parent}>${dataset}`;
  out.layers.push({
    key,
    parent,
    depth,
    dataset,
    connector: resp.connector,
    view: resp.view,
    schema: resp.node_config_schema,
    reach: resp.reach,
    nestsNode: resp.nests[0]?.node ?? null,
    read: heldRead(resp),
  });
  for (const n of resp.nests) {
    if (n.pipeline !== null) collect(n.dataset, n.pipeline, key, depth + 1, out);
    else if (n.unread !== null) out.unread.push(n.unread);
  }
}

export function unread(ref: NestedPipelineRef): NestedLayer {
  return {
    key: `${CAMPAIGN_LAYER}>${ref.dataset}`,
    parent: CAMPAIGN_LAYER,
    depth: 0,
    dataset: ref.dataset,
    connector: null,
    view: null,
    schema: null,
    reach: null,
    nestsNode: null,
    read: { status: "loading", kept: null },
  };
}

export function nestedPipelinesRead(roots: NestedPipelineRef[]): ReadDescriptor<NestedPipelines> {
  return {
    id: `nested-pipelines\x1f${roots.map((r) => `${r.node}>${r.dataset}`).join("\x1e")}`,
    load: async (signal) => {
      const out = { layers: [] as NestedLayer[], unread: [] as string[] };
      for (const ref of roots) {
        let resp: DatasetPipelineResponse;
        try {
          resp = await readThrough(datasetPipelineRead(ref.dataset), signal);
        } catch (e) {
          if (signal.aborted) throw e;
          // The level stays in the stack, carrying its failure; what it nests is unknown.
          const kind = failureKind(e);
          reportIncident(e, { surface: "nested-pipelines", address: ref.dataset });
          out.layers.push({
            ...unread(ref),
            read: { status: "failed", failure: { kind, message: operatorMessage(e, kind) }, kept: null },
          });
          continue;
        }
        collect(ref.dataset, resp, CAMPAIGN_LAYER, 0, out);
      }
      const data = { layers: out.layers, unread: out.unread.length > 0 ? out.unread.join("; ") : null };
      return { kind: "ok", data, validator: null };
    },
  };
}

export function backendsRead():ReadDescriptor<BackendResponse[]> {
  return { ...once<BackendResponse[]>("backends", readUrl("backends", {})), auth: true };
}

export function backendHealthRead(backendId: string): ReadDescriptor<BackendHealthResponse> {
  return {
    ...once<BackendHealthResponse>("backend-health", readUrl("backend-health", { backend_id: backendId })),
    auth: true,
    subject: MACHINE,
  };
}

export function machineStatusRead(): ReadDescriptor<MachineStatusResponse> {
  return {
    ...once<MachineStatusResponse>("machine-status", readUrl("machine-status", {})),
    auth: true,
    subject: MACHINE,
  };
}

type CycleKind = Extract<ReadKind, `cycle-${string}`>;

// A cycle read: the ROOT hop in the route, the hops under it as `descend`.
export function cyclePathUrl<K extends CycleKind>(
  kind: K,
  path: CyclePath,
  slots: Omit<Record<SlotsOf<(typeof READ_ROUTES)[K]>, string>, "campaign_id" | "cycle_id">,
  query: Record<string, string> = {},
): string {
  const root = pathRoot(path);
  const params = new URLSearchParams(query);
  const descend = encodeDescend(path);
  if (descend) params.set("descend", descend);
  return readUrl(kind, { ...slots, campaign_id: root.campaignId, cycle_id: root.cycleId } as Record<SlotsOf<(typeof READ_ROUTES)[K]>, string>, params);
}

// One reading of the cycle per tier, serialized by the server for every surface.
export function exportUrl(path: CyclePath, tier: ExportTier): string {
  return cyclePathUrl("cycle-export", path, {}, { tier });
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
  return once("file", cyclePathUrl("cycle-file", path, {}, { scope, path: filePath }));
}

export function roundRead(
  path: CyclePath,
  round: number,
  at: number | null = null,
): ReadDescriptor<RoundView> {
  return once("round", cyclePathUrl("cycle-round", path, { round_num: String(round) }, at == null ? {} : { at: String(at) }));
}

export function roundAuditRead(path: CyclePath, round: number): ReadDescriptor<RoundAudit | null> {
  return once("round-audit", cyclePathUrl("cycle-round-audit", path, { round_num: String(round) }));
}

export function filesRead(campaignId: string, cycleId: string): ReadDescriptor<FilesResponse> {
  // The ROOT hop's files: a drilled-in leaf's live under its `.inner/`.
  return once("files", cyclePathUrl("cycle-files", [{ campaignId, cycleId }], {}));
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
    ...byEtag<CellsResponse | null>("cells", readUrl("dataset-cells", { name }, params)),
    // A 404 is an honest empty, answered null: no pooled slice exists before the first round closes.
    expects: "gone",
    subject: STORES,
    // A re-ranked, re-scoped or replayed slice still in flight dims this one rather than blanking.
    keep: `cells${SEP}${name}${SEP}${encodeCyclePath(path)}${SEP}${JSON.stringify(filter)}`,
  };
}

export function cellRead(name: string, answer: string): ReadDescriptor<Cell> {
  return once("cell", readUrl("dataset-cell", { name, answer }));
}

type DashboardBody = ServedDashboard | WarmingDashboard;

// `at` is a leaf-cycle ledger offset (`StepFacts.offset`).
export function dashboardRead(path: CyclePath, at: number | null): ReadDescriptor<ServedDashboard, DashboardBody> {
  const url = cyclePathUrl("cycle-dashboard", path, {}, at == null ? {} : { at: String(at) });
  return {
    ...byEtag<DashboardBody>("dashboard", url),
    auth: true,
    // A warming cycle has no dashboard yet.
    accept: (body): body is ServedDashboard => !("warming_up" in body),
    // Moving the viewed moment keeps the unit's last snapshot rather than blanking the page.
    keep: `dashboard${SEP}${encodeCyclePath(path)}`,
    subject: storeOf(path),
  };
}

// Every lifecycle: an address resolves whether or not the sidebar's filter lists it.
export function hierarchyRead(): ReadDescriptor<HierarchyResponse> {
  return {
    ...byEtag<HierarchyResponse>("hierarchy", readUrl("hierarchy", {}, new URLSearchParams({ depth: "1", lifecycle: "all" }))),
    auth: true,
    subject: STORES,
  };
}

export function campaignStorageRead(campaignId: string): ReadDescriptor<LeafBytes> {
  return once("campaign-storage", readUrl("campaign-storage", { campaign_id: campaignId }));
}

export function workspaceStorageRead(): ReadDescriptor<WorkspaceStorageResponse> {
  return once("workspace-storage", readUrl("workspace-storage", {}));
}

export function libraryRead(): ReadDescriptor<LibraryResponse> {
  return { ...once<LibraryResponse>("library", readUrl("library", {})), subject: ACCOUNT };
}

export function configMapRead(campaignId: string): ReadDescriptor<ConfigMapResponse> {
  return { ...once<ConfigMapResponse>("config-map", readUrl("config-map", { campaign_id: campaignId })), subject: ACCOUNT };
}

// A read despite the POST: its subject is an overlay that exists nowhere on disk yet.
export function forkPreviewRead(
  campaignId: string,
  pipelineOverlay: Record<string, unknown>,
): ReadDescriptor<ForkPreviewResponse> {
  const url = readUrl("fork-preview", { campaign_id: campaignId });
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
// Built whole on each ask, so only a live measured course (`live`) is moved by the feed.
export function evidenceRead(
  subjects: readonly string[],
  opts: {
    ranking?: boolean;
    winnerChain?: boolean;
    config?: boolean;
    metric?: string;
    grid?: string;
  },
  live: boolean,
): ReadDescriptor<Evidence> {
  const named = [...subjects].sort().map((s) => `subject=${enc(s)}`);
  const qs = new URLSearchParams(named.join("&"));
  if (opts.ranking) qs.set("ranking", "true");
  if (opts.winnerChain) qs.set("winner_chain", "true");
  if (opts.config) qs.set("config", "true");
  if (opts.metric) qs.set("metric", opts.metric);
  if (opts.grid) qs.set("grid", opts.grid);
  return {
    ...byEtag<Evidence>("evidence", readUrl("evidence", {}, qs)),
    ...(live ? { subject: STORES } : {}),
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
  const entry = byEtag<SubtreeEntry>("subtree", readUrl("hierarchy-subtree", { address }, params));
  return {
    id: entry.id,
    auth: true,
    // A mask or moment re-keys the read; the campaign's last tree stands meanwhile.
    keep,
    // Its family spans the inner stores under it.
    subject: STORES,
    load: async (signal, validator, subject) => {
      const res = await entry.load(signal, validator, subject);
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
// The head window moves with the course's family; a page never moves and reads once.
export function stepsRead(path: CyclePath, limit: number, before: string | null): ReadDescriptor<HierarchySteps> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (before) params.set("before", before);
  return {
    ...byEtag<HierarchySteps>("steps", readUrl("hierarchy-steps", { address: encodeCyclePath(path) }, params)),
    auth: true,
    ...(before === null ? { subject: STORES } : {}),
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
        const window: HierarchySteps = await readThrough(stepsRead(path, limit, cursor), signal);
        steps = [...window.steps, ...steps];
        cursor = window.cursor_prev;
      }
      return { kind: "ok", data: { steps, cursor_prev: cursor }, validator: null };
    },
  };
}
