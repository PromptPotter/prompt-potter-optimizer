// Seeds from the RESOLVED config: fork init layers it onto the dataset overlay (`entry.py`), so a sparse seed resets untouched params.

import type { NodePrompt } from "@/lib/api/types";
import { liveCandidate, type DashboardSnapshot } from "@/lib/poll";
import type { RoundView } from "@/lib/types";

export interface CandidateSearchPoint {
  origin_prompts: Record<string, NodePrompt>;
  pipeline_overlay: Record<string, unknown>;
}

export function searchPoint(
  prompts: Record<string, NodePrompt> | null | undefined,
  overlay: Record<string, unknown> | null | undefined,
): CandidateSearchPoint {
  return {
    origin_prompts: prompts ?? {},
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

export interface ArmSearchpoint {
  prompts: Record<string, NodePrompt> | null;
  resolved_params: Record<string, unknown> | null;
}

// A proposal has resolved no config yet; every later state carries it.
export function armSearchpoint(doc: RoundView, key: string): ArmSearchpoint | null {
  const arm = doc.arms[key];
  if (!arm) return null;
  return {
    prompts: arm.individual.prompts,
    resolved_params: arm.state === "proposed" ? null : arm.resolved_params,
  };
}

export function candidateSearchPoint(
  doc: RoundView | null,
  key: string,
): CandidateSearchPoint | null {
  if (!doc || !key) return null;
  const entry = armSearchpoint(doc, key);
  if (!entry) return null;
  return searchPoint(entry.prompts, nodeConfigs(entry.resolved_params));
}

export function liveCandidateSearchPoint(
  dash: DashboardSnapshot | null,
  key: string | null,
): CandidateSearchPoint | null {
  const entry = liveCandidate(dash, key);
  if (!entry) return null;
  return searchPoint(entry.prompts, nodeConfigs(entry.resolved_params));
}
