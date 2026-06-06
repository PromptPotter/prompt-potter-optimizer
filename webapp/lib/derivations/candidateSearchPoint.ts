// Read side of the operator-steered fork: pick the selected candidate's
// *evolved* searchpoint out of the lazily-loaded round file, so the steer
// panel can seed its editors from THAT candidate's prompt + node config
// (not the dataset origin). Pure data → data, synchronous — the document
// is already loaded by `useRoundFile` (Decision F: no new endpoint).
//
// Source: `round_NNNN.json::candidate_scores[]`, where each entry carries
// `prompt_fields` (OptSearchPoint.prompt_field_dict() shape) and
// `pipeline_params_override` (the candidate's node-config delta over the
// dataset overlay). Together they ARE the fork seed `{origin_prompt_fields,
// pipeline_overlay}` an operator edits before confirming the fork — the
// override delta layers back on top of the inherited dataset overlay at
// fork bootstrap, keeping the dataset file immutable.

import {
  liveInputCandidate,
  roundOf,
  type DashboardSnapshot,
  type RoundFileDoc,
} from "@/lib/poll";

// The seed-able half of a candidate's searchpoint. `limit_overrides` is
// NOT here — it comes from the reconcile dialog, not the candidate.
export interface CandidateSearchPoint {
  origin_prompt_fields: Record<string, unknown>;
  pipeline_overlay: Record<string, unknown>;
}

interface RawScoredCandidate {
  candidate_id?: string;
  prompt_fields?: Record<string, unknown>;
  pipeline_params_override?: Record<string, unknown> | null;
}

// Locate the candidate by id and project its evolved searchpoint. Returns
// null when the round file isn't loaded, carries no `candidate_scores`, or
// has no entry for `candidateId` — the caller renders the unseeded state
// rather than a stale or empty editor.
export function candidateSearchPoint(
  doc: RoundFileDoc | null,
  candidateId: string,
): CandidateSearchPoint | null {
  if (!doc || !candidateId) return null;
  const scores = doc.candidate_scores;
  if (!Array.isArray(scores)) return null;
  const entry = scores.find(
    (c): c is RawScoredCandidate =>
      !!c &&
      typeof c === "object" &&
      (c as RawScoredCandidate).candidate_id === candidateId,
  );
  if (!entry) return null;
  return {
    origin_prompt_fields: entry.prompt_fields ?? {},
    pipeline_overlay: entry.pipeline_params_override ?? {},
  };
}

// Live peer of `candidateSearchPoint`: for a candidate in the *in-flight*
// round the seed lives in `dashboard.json`'s l1_score input candidates (not
// the round file, which isn't written until round close). Seeded at
// `candidate_started` (`_RoundBuffer.seed_candidate`), so it's available the
// moment a candidate begins scoring — lets the steer panel fork from a
// still-running candidate. Matches by the live candidate id `r{round}_{idx}`.
export function liveCandidateSearchPoint(
  dash: DashboardSnapshot | null,
  candidateId: string,
): CandidateSearchPoint | null {
  if (!candidateId) return null;
  const liveRound = roundOf(dash);
  if (liveRound == null) return null;
  const entry = liveInputCandidate(dash, liveRound, candidateId);
  if (!entry) return null;
  return {
    origin_prompt_fields: entry.prompt_fields ?? {},
    pipeline_overlay: entry.pp_override ?? {},
  };
}
