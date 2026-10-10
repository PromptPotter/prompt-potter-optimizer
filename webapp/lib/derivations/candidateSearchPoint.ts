// Seeds from the RESOLVED config: fork init layers it onto the dataset overlay (`entry.py`), so a sparse seed resets untouched params.

import { liveCandidate, type DashboardSnapshot } from "@/lib/poll";
import type { RoundResult } from "@/lib/types";

export interface CandidateSearchPoint {
  origin_prompt_fields: Record<string, unknown>;
  pipeline_overlay: Record<string, unknown>;
}

export function searchPoint(
  promptFields: Record<string, unknown> | null | undefined,
  overlay: Record<string, unknown> | null | undefined,
): CandidateSearchPoint {
  return {
    origin_prompt_fields: promptFields ?? {},
    pipeline_overlay: overlay ?? {},
  };
}

// Fork init shallow-merges each node dict (`entry.py`); the top-level `steps` list would break it.
function nodeConfigs(
  resolved: Record<string, unknown> | null | undefined,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [node, cfg] of Object.entries(resolved ?? {})) {
    if (cfg && typeof cfg === "object" && !Array.isArray(cfg)) out[node] = cfg;
  }
  return out;
}

export function candidateSearchPoint(
  doc: RoundResult | null,
  candidateId: string,
): CandidateSearchPoint | null {
  if (!doc || !candidateId) return null;
  const entry = doc.candidate_scores.find((c) => c.candidate_id === candidateId);
  if (!entry) return null;
  return searchPoint(entry.prompt_fields, nodeConfigs(entry.resolved_pipeline_params));
}

export function liveCandidateSearchPoint(
  dash: DashboardSnapshot | null,
  label: string,
): CandidateSearchPoint | null {
  const entry = liveCandidate(dash, label);
  if (!entry) return null;
  return searchPoint(entry.prompt_fields, nodeConfigs(entry.resolved_pipeline_params));
}
