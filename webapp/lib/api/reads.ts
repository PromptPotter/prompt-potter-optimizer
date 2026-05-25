// Read endpoints — thin GET wrappers over the FastAPI surface.

import { API, jget } from "./client";
import type {
  ActiveSessionResponse,
  CampaignDetail,
  CampaignLineageResponse,
  CampaignListResponse,
  CyclesResponse,
  DatasetPreviewResponse,
  DiagnosticRunListResponse,
  FileContentResponse,
  FilesResponse,
  HardSamplesScope,
  LeverageResponse,
  MeasurementSeriesResponse,
} from "./types";

export function fetchActive(signal?: AbortSignal): Promise<ActiveSessionResponse> {
  return jget<ActiveSessionResponse>(`${API}/active`, signal);
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

// Per-cycle file content. Files live either under the cycle dir
// (`scope=cycle`) or at the campaign dir (`scope=campaign` — campaign.json,
// log.md, hard_samples.json). `dashboard.json` is NOT a campaign artifact —
// it is per-session; fetch it via `fetchDashboard`.
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

// Live session telemetry. `dashboard.json` is per-session — it lives in
// the session's root cycle dir and is shared by that session's forks. The
// server resolves the session-family root from any cycle of the session,
// so pass the cycle currently in view. Returns the raw dashboard dict
// (cast to `DashboardSnapshot` at the use site in poll.tsx).
export function fetchDashboard(
  campaignId: string,
  cycleId: string,
  signal?: AbortSignal,
): Promise<Record<string, unknown>> {
  return jget<Record<string, unknown>>(
    `${API}/campaigns/${encodeURIComponent(campaignId)}` +
      `/cycles/${encodeURIComponent(cycleId)}/dashboard`,
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

export function fetchCampaigns(
  dataset?: string,
  signal?: AbortSignal,
): Promise<CampaignListResponse> {
  const qs = dataset ? `?dataset=${encodeURIComponent(dataset)}` : "";
  return jget<CampaignListResponse>(`${API}/campaigns${qs}`, signal);
}

export function fetchCycles(signal?: AbortSignal): Promise<CyclesResponse> {
  return jget<CyclesResponse>(`${API}/cycles`, signal);
}

export function fetchCampaignLineage(
  campaignId: string,
  signal?: AbortSignal,
): Promise<CampaignLineageResponse> {
  return jget<CampaignLineageResponse>(
    `${API}/campaigns/${encodeURIComponent(campaignId)}/lineage`,
    signal,
  );
}

export function fetchLeverage(
  limit = 200,
  signal?: AbortSignal,
): Promise<LeverageResponse> {
  return jget<LeverageResponse>(`${API}/measurements/leverage?limit=${limit}`, signal);
}

// Workspace-scope diagnostic-run records — sidecars written by `verify` CLI.
// `dataset` filters to one dataset's records; omit for everything on disk.
export function fetchDiagnosticRuns(
  dataset?: string | null,
  signal?: AbortSignal,
): Promise<DiagnosticRunListResponse> {
  const qs = dataset ? `?dataset=${encodeURIComponent(dataset)}` : "";
  return jget<DiagnosticRunListResponse>(`${API}/workspace/diagnostic-runs${qs}`, signal);
}

// Cycle detail — reads index.json via the generic cycle-file endpoint (the
// typed detail route's response schema doesn't always match the on-disk
// shape; reading the raw file dodges that mismatch).
export async function fetchCampaignDetail(
  campaignId: string,
  cycleId: string,
  signal?: AbortSignal,
): Promise<CampaignDetail> {
  const file = await fetchCycleFile(campaignId, cycleId, "cycle", "index.json", signal);
  if (!file.content) throw new Error("index.json is empty");
  return JSON.parse(file.content) as CampaignDetail;
}
