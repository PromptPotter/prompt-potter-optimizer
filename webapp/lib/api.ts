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
  surprise: number;
}

export interface DatasetPreview {
  name: string;
  row_count: number;
  train_count: number;
  test_count: number;
  items: DatasetItem[];
}

export function fetchDatasetPreview(
  name: string,
  limit = 25,
  signal?: AbortSignal,
): Promise<DatasetPreview> {
  return jget<DatasetPreview>(
    `${API}/datasets/${encodeURIComponent(name)}/preview?limit=${limit}`,
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
