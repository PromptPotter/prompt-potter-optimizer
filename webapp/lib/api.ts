// Thin wrappers over the FastAPI surface (read-only).

const API = "/api/v1";

export interface ActiveSession {
  cycle_id: string;
  session_id: string;
  tenant_id?: string;
}

export interface FileEntry {
  scope: string;
  path: string;
  size?: number;
  mtime?: number;
}

export interface FileResponse {
  path: string;
  scope: string;
  content: string;
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

export function fetchCycleFile(
  cycleId: string,
  scope: string,
  path: string,
  signal?: AbortSignal,
): Promise<FileResponse> {
  const url = `${API}/campaigns/${cycleId}/file?scope=${scope}&path=${encodeURIComponent(path)}`;
  return jget<FileResponse>(url, signal);
}

export interface FilesListing {
  entries: FileEntry[];
}

export function fetchFiles(cycleId: string, signal?: AbortSignal): Promise<FilesListing> {
  return jget<FilesListing>(`${API}/campaigns/${cycleId}/files`, signal);
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
  // Chernoff information vs the seed prior. Drives the live PoBB
  // sample-iteration order. null when scope=workspace (no seed concept
  // across cycles) or when the sample has no measurement.
  pick_score: number | null;
}

export interface DatasetPreview {
  name: string;
  row_count: number;
  train_count: number;
  test_count: number;
  items: DatasetItem[];
}

export type HardSamplesScope = "workspace" | "campaign";

export function fetchDatasetPreview(
  name: string,
  limit = 25,
  signal?: AbortSignal,
  scope: HardSamplesScope = "workspace",
  cycleId?: string,
): Promise<DatasetPreview> {
  const params = new URLSearchParams({ limit: String(limit), scope });
  if (scope === "campaign" && cycleId) params.set("cycle_id", cycleId);
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
  scope: HardSamplesScope = "workspace",
  cycleId?: string,
): Promise<MeasurementSeriesResponse> {
  const params = new URLSearchParams({ limit: String(limit), scope });
  if (scope === "campaign" && cycleId) params.set("cycle_id", cycleId);
  return jget<MeasurementSeriesResponse>(
    `${API}/datasets/${encodeURIComponent(name)}/measurement-series?${params.toString()}`,
    signal,
  );
}

export async function fetchActiveDatasetName(
  cycleId: string,
  signal?: AbortSignal,
): Promise<string | null> {
  const file = await fetchCycleFile(cycleId, "family", "index.json", signal);
  const parsed = JSON.parse(file.content) as { header?: { dataset_name?: string } };
  return parsed.header?.dataset_name ?? null;
}

export type SiblingKind = "root" | "fork" | "diag" | "sweep";

export interface CycleListEntry {
  cycle_id: string;
  parent_session_id: string;
  // Family-root id for siblings (forks/sweeps/diag); null for roots. The
  // sidebar uses this to nest sibling rows under their root entry instead
  // of scattering them as 60+ top-level rows.
  parent_cycle_id: string | null;
  dataset_name: string;
  backend_id: string;
  sibling_kind: SiblingKind;
  is_root: boolean;
  status: string;
  best_accuracy: number | null;
  n_rounds: number;
  created_at: string;
  updated_at: string;
}

export interface CyclesResponse {
  tenant_id: string;
  active_cycle_id: string | null;
  cycles: CycleListEntry[];
}

export function fetchCycles(signal?: AbortSignal): Promise<CyclesResponse> {
  return jget<CyclesResponse>(`${API}/cycles`, signal);
}

// Family lineage — the cross-cycle search-point graph rooted at the
// family root. One request returns every cycle in the family + each
// cycle's per-round candidates + the parent-round each fork was cut at.
// Mirrors the server's FamilyLineageResponse pydantic models verbatim.

export interface FamilyLineageCandidate {
  candidate_id: string;
  label: string;
  accuracy: number | null;
  rank: number | null;
  is_winner: boolean;
}

export interface FamilyLineageRound {
  round: number;
  label: string;
  accuracy: number | null;
  candidates: FamilyLineageCandidate[];
}

export interface FamilyLineageCycle {
  cycle_id: string;
  sibling_kind: SiblingKind;
  immediate_parent_cycle_id: string | null;
  fork_from_round: number | null;
  fork_from_candidate_id: string | null;
  // Fork creation trigger — drives the round-numbering convention.
  trigger: string;
  // Add this to each round's `round` number to get its absolute column
  // in the family cladogram. The server computes per-trigger so the
  // client doesn't need to know HITL-vs-divergence semantics itself.
  round_column_offset: number;
  status: string;
  dataset_name: string;
  best_accuracy: number | null;
  rounds: FamilyLineageRound[];
}

export interface FamilyLineageResponse {
  root_cycle_id: string;
  cycles: FamilyLineageCycle[];
}

export function fetchFamilyLineage(
  cycleId: string,
  signal?: AbortSignal,
): Promise<FamilyLineageResponse> {
  return jget<FamilyLineageResponse>(
    `${API}/campaigns/${encodeURIComponent(cycleId)}/family-lineage`,
    signal,
  );
}

// Sanctioned mutating endpoints — see promptpotter/presentation/CLAUDE.md
// for the charter. Both ride existing I/O kinds (Persistence's
// `inherit_from` and Control-local's `stop_check` flag-poll); they do not
// introduce a new I/O kind.

export interface CreateForkResponse {
  fork_cycle_id: string;
  cli_command: string;
  active_pointer_retargeted: boolean;
}

export async function postCreateFork(
  cycleId: string,
  round: number,
  candidateId: string,
): Promise<CreateForkResponse> {
  const r = await fetch(`${API}/cycles/${encodeURIComponent(cycleId)}/forks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ round, candidate_id: candidateId }),
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`${r.status} POST /forks`);
  return (await r.json()) as CreateForkResponse;
}

export interface StopCycleResponse {
  cycle_id: string;
  flag_written: boolean;
}

export interface DeleteCycleResponse {
  cycle_id: string;
  deleted: boolean;
  reason: string;
}

export async function deleteCycle(cycleId: string): Promise<DeleteCycleResponse> {
  const r = await fetch(`${API}/cycles/${encodeURIComponent(cycleId)}`, {
    method: "DELETE",
    cache: "no-store",
  });
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
  family_root_cycle_id: string;
  deleted_cycle_ids: string[];
  skipped: { cycle_id: string; reason: string }[];
}

export async function postCleanupEmpty(
  cycleId: string,
): Promise<CleanupEmptyResponse> {
  const r = await fetch(
    `${API}/campaigns/${encodeURIComponent(cycleId)}/cleanup-empty`,
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

export async function postStopCycle(cycleId: string): Promise<StopCycleResponse> {
  const r = await fetch(`${API}/cycles/${encodeURIComponent(cycleId)}/stop`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
  });
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

// Campaign detail — feeds the M13 compare-campaigns view. Reads
// index.json via the generic cycle-file endpoint (the backend-scoped
// detail route's response schema doesn't always match the on-disk
// shape; reading the raw file dodges that mismatch).

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
  cycleId: string,
  signal?: AbortSignal,
): Promise<CampaignDetail> {
  const file = await fetchCycleFile(cycleId, "cycle", "index.json", signal);
  return JSON.parse(file.content) as CampaignDetail;
}
