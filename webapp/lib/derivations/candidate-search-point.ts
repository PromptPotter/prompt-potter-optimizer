// Seeds from the RESOLVED config: fork init layers it onto the dataset overlay (`entry.py`), so a sparse seed resets untouched params.

import type { NodePrompt, ServedArm } from "@/lib/api/types";

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

// One arm of a round, in flight or filed: both reads serve the one `ServedArm` shape.
export function servedArm(arms: readonly ServedArm[], key: string | null): ServedArm | null {
  if (!key) return null;
  return arms.find((a) => a.reading.arm.key === key) ?? null;
}

export function armSearchPoint(arm: ServedArm | null): CandidateSearchPoint | null {
  return arm ? searchPoint(arm.prompts, nodeConfigs(arm.resolved_params)) : null;
}
