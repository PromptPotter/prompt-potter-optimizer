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

async function jget<T>(url: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(url, signal ? { signal } : undefined);
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
