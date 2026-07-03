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
  liveL1InputCandidates,
  roundOf,
  type DashboardSnapshot,
  type LiveInputCandidate,
  type RoundFileDoc,
} from "@/lib/poll";
import { candidateLabel } from "@/lib/candidate-label";

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

// A candidate row carrying the two observe fields — the same `LiveInputCandidate`
// shape backs both the round-file `candidate_scores[]` and the live in-flight input.
function rowConfig(row: LiveInputCandidate | undefined | null, label: string): ObserveConfig | null {
  if (!row) return null;
  return {
    promptFields: row.prompt_fields ?? {},
    config: row.resolved_pipeline_params ?? {},
    label,
  };
}

// Live: the latest-seeded (max idx) in-flight candidate of the running round.
// The candidate buffer persists THROUGH L2/L3 (the backend resets it only at the
// next `L1_GENERATE:enter`, view.py:374), so this stays non-null there. The one
// null window is l1_generate-after-reset-before-first-candidate-started; the
// OBSERVE view falls back to origin for that brief gap.
export function liveObserveConfig(dash: DashboardSnapshot | null): ObserveConfig | null {
  const candidates = liveL1InputCandidates(dash);
  if (candidates.length === 0) return null;
  let latest = candidates[0];
  for (const c of candidates) {
    if (Number(c.idx ?? -1) > Number(latest.idx ?? -1)) latest = c;
  }
  const label = latest.label || candidateLabel(roundOf(dash), latest.idx);
  return rowConfig(latest, `live — ${label}`);
}

// Origin: the round-0 C0 row — the sole origin candidate, always index 0. Its
// resolved config is the dataset's starting program, born complete server-side;
// this is what retired the `{}` origin fake that dropped model/provider whenever
// the overlay file was thin. Null until round 0 has been written.
export function originObserveConfig(round0: RoundFileDoc | null): ObserveConfig | null {
  const scores = round0?.candidate_scores;
  if (!Array.isArray(scores)) return null;
  return rowConfig(scores[0] as LiveInputCandidate, "origin");
}

// Historical: a specific past candidate (the last completed round's winner) out
// of its lazily-loaded round file, located by `candidate_id`.
export function candidateObserveConfig(
  doc: RoundFileDoc | null,
  candidateId: string,
  label: string,
): ObserveConfig | null {
  const scores = doc?.candidate_scores;
  if (!Array.isArray(scores) || !candidateId) return null;
  const row = (scores as LiveInputCandidate[]).find((c) => c && c.candidate_id === candidateId);
  return rowConfig(row, label);
}
