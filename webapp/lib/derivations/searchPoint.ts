// Observe-side resolver: the read-only "what config does THIS searchpoint run"
// view behind the chat-hero LLM node. Three searchpoint states — the dataset
// origin, the live in-flight candidate, the last completed round's winner —
// each reading the SAME server-resolved, config-only `resolved_pipeline_params`
// (origin floor ⊕ candidate delta, prompt stripped; JobSearchPoint.config_params).
// No client re-merge: the server computes the effective config once and projects
// it; this module only selects which searchpoint's resolved config to show. Pure
// data → data. The STEER fork seed (`candidateSearchPoint.ts`) reads the same
// `resolved_pipeline_params` but for a different purpose — an editable fork seed,
// not a read-only view — so the two stay separate functions over one served field.

import {
  liveInputCandidate,
  liveL1InputCandidates,
  roundOf,
  type DashboardSnapshot,
  type LiveInputCandidate,
} from "@/lib/poll";
import { candidateLabel } from "@/lib/candidate-label";
import { PROMPT_STRING_FIELDS } from "@/lib/prompt-fields";
import type { RoundResult } from "@/lib/types";

export type ObserveState = "origin" | "live" | "historical";

export interface ObserveConfig {
  // The searchpoint's evolved prompt fields (OptSearchPoint.prompt_field_dict()
  // shape) — rendered read-only beneath the config.
  promptFields: Record<string, unknown>;
  // The server-resolved config-only params `{node:{param:value}, steps:[…]}`,
  // fed verbatim to the values-mode editor as its seed (never re-merged client-side).
  config: Record<string, unknown>;
  // Header sub-line naming which searchpoint this is (origin / live — C1.3 / …).
  label: string;
}

// Project the six PromptTemplate string fields out of ONE node's resolved params.
// Used when the flat `prompt_fields` carries no prompt content but the selected
// node IS prompt-bearing and its evolved fields live per-node inside
// `resolved_pipeline_params[nodeId]` — the pp-self meta-prompt shape, where each
// meta node (l1_generate / l1_critique / …) owns its own persona/instruction/…
// rather than sharing one flat prompt. Only fields actually present are returned
// (the round file carries only the optimizer's mutated delta, not the static
// `prompts/{node}.json` baseline).
function nodePromptFields(
  resolved: Record<string, unknown> | undefined | null,
  nodeId: string | null | undefined,
): Record<string, unknown> {
  if (!nodeId || !resolved) return {};
  const slice = resolved[nodeId];
  if (!slice || typeof slice !== "object") return {};
  const rec = slice as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  for (const k of PROMPT_STRING_FIELDS) {
    if (k in rec) out[k] = rec[k];
  }
  return out;
}

// A candidate row carrying the two observe fields — the same `LiveInputCandidate`
// shape backs both the round-file `candidate_scores[]` and the live in-flight input.
// `nodeId` (the selected node) lets a prompt-bearing meta node surface its OWN
// evolved prompt when the flat `prompt_fields` is empty; single-prompt pipelines
// (`llm_only`, every normal dataset) keep their flat prompt and ignore it.
function rowConfig(
  row: LiveInputCandidate | undefined | null,
  label: string,
  nodeId?: string | null,
): ObserveConfig | null {
  if (!row) return null;
  const flat = (row.prompt_fields ?? {}) as Record<string, unknown>;
  const hasFlatPrompt = PROMPT_STRING_FIELDS.some((k) => k in flat);
  return {
    promptFields: hasFlatPrompt ? flat : nodePromptFields(row.resolved_pipeline_params, nodeId),
    config: row.resolved_pipeline_params ?? {},
    label,
  };
}

// Live: the latest-seeded (max idx) in-flight candidate of the running round.
// The candidate buffer persists THROUGH L2/L3 (the backend resets it only at the
// next `L1_GENERATE:enter`, view.py:374), so this stays non-null there. The one
// null window is l1_generate-after-reset-before-first-candidate-started; the
// OBSERVE view falls back to origin for that brief gap.
export function liveObserveConfig(
  dash: DashboardSnapshot | null,
  nodeId?: string | null,
): ObserveConfig | null {
  const candidates = liveL1InputCandidates(dash);
  let latest = candidates[0];
  if (!latest) return null;
  for (const c of candidates) {
    if (Number(c.idx ?? -1) > Number(latest.idx ?? -1)) latest = c;
  }
  const label = latest.label || candidateLabel(roundOf(dash), latest.idx);
  return rowConfig(latest, `live — ${label}`, nodeId);
}

// Origin: the round-0 C0 row — the sole origin candidate, always index 0. Its
// resolved config is the dataset's starting program, born complete server-side;
// this is what retired the `{}` origin fake that dropped model/provider whenever
// the overlay file was thin. Null until round 0 has been written.
export function originObserveConfig(
  round0: RoundResult | null,
  nodeId?: string | null,
): ObserveConfig | null {
  const scores = round0?.candidate_scores;
  if (!Array.isArray(scores)) return null;
  return rowConfig(scores[0] as LiveInputCandidate, "origin", nodeId);
}

// Live, by id: the SELECTED in-flight candidate out of the live l1_score input
// (not the latest-seeded one `liveObserveConfig` shows). Null until that
// candidate has been seeded (`candidate_started`). The scoring inspector reads
// this when the operator drills into a candidate of the still-running round.
export function liveCandidateObserveConfig(
  dash: DashboardSnapshot | null,
  candidateId: string,
  label: string,
  nodeId?: string | null,
): ObserveConfig | null {
  const liveRound = roundOf(dash);
  if (liveRound == null || !candidateId) return null;
  return rowConfig(liveInputCandidate(dash, liveRound, candidateId), `live — ${label}`, nodeId);
}

// Historical: a specific past candidate (the last completed round's winner) out
// of its lazily-loaded round file, located by `candidate_id`.
export function candidateObserveConfig(
  doc: RoundResult | null,
  candidateId: string,
  label: string,
  nodeId?: string | null,
): ObserveConfig | null {
  const scores = doc?.candidate_scores;
  if (!Array.isArray(scores) || !candidateId) return null;
  const row = (scores as LiveInputCandidate[]).find((c) => c.candidate_id === candidateId);
  return rowConfig(row, label, nodeId);
}
