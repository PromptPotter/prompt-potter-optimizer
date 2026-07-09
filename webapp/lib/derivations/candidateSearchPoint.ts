// Read side of the operator-steered fork: pick the selected candidate's
// *evolved* searchpoint out of the lazily-loaded round file, so the steer
// panel can seed its editors from THAT candidate's prompt + node config
// (not the dataset origin). Pure data → data, synchronous — the document
// is already loaded by `useRoundFile` (Decision F: no new endpoint).
//
// Source: `round_NNNN.json::candidate_scores[]`, where each entry carries
// `prompt_fields` (OptSearchPoint.prompt_field_dict() shape) and
// `resolved_pipeline_params` (the candidate's COMPLETE server-resolved
// config, `{node:{param:value}, steps}`, prompt stripped — the same field the
// OBSERVE view reads). Together they ARE the fork seed `{origin_prompt_fields,
// pipeline_overlay}` an operator edits before confirming the fork. We seed from
// the RESOLVED config, not the sparse delta: the fork bootstrap layers this
// overlay onto the inherited dataset overlay (`entry.py`), so a sparse seed
// silently reset every evolved-but-untouched param (model/provider/…) back to
// the dataset floor. The resolved config carries each param's actual running
// value, so the fork faithfully continues from the candidate — the dataset file
// stays immutable. (`steps`, the top-level list `config_params` also carries, is
// NOT a node config and is dropped so the per-node merge can't choke on it.)

import {
  liveInputCandidate,
  roundOf,
  type DashboardSnapshot,
  type LiveInputCandidate,
  type RoundFileDoc,
} from "@/lib/poll";

// The seed-able half of a candidate's searchpoint. `config_overrides` is
// NOT here — it comes from the reconcile dialog, not the candidate.
export interface CandidateSearchPoint {
  origin_prompt_fields: Record<string, unknown>;
  pipeline_overlay: Record<string, unknown>;
}

// The one projection every searchpoint source funnels through: prompt fields +
// node-config overlay → the normalized seed shape, each defaulting to `{}`. The
// only thing that varies across sources (round file, live input candidate,
// draft, origin) is which raw field carries the overlay; the mapping is here.
export function searchPoint(
  promptFields: Record<string, unknown> | null | undefined,
  overlay: Record<string, unknown> | null | undefined,
): CandidateSearchPoint {
  return {
    origin_prompt_fields: promptFields ?? {},
    pipeline_overlay: overlay ?? {},
  };
}

// The fork `pipeline_overlay` is per-node config only — keep object-valued node
// entries, drop the top-level `steps` list (and any scalar) the resolved config
// also carries. The fork bootstrap shallow-merges each node dict onto the
// inherited overlay (`entry.py`); a list under a node key would break that spread.
function nodeConfigs(
  resolved: Record<string, unknown> | null | undefined,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [node, cfg] of Object.entries(resolved ?? {})) {
    if (cfg && typeof cfg === "object" && !Array.isArray(cfg)) out[node] = cfg;
  }
  return out;
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
    (c): c is LiveInputCandidate =>
      !!c &&
      typeof c === "object" &&
      (c as LiveInputCandidate).candidate_id === candidateId,
  );
  if (!entry) return null;
  return searchPoint(entry.prompt_fields, nodeConfigs(entry.resolved_pipeline_params));
}

// Live peer of `candidateSearchPoint`: for a candidate in the *in-flight*
// round the seed lives in `dashboard.json`'s l1_score input candidates (not
// the round file, which isn't written until round close). Seeded at
// `candidate_started` (`_RoundBuffer.seed_candidate`), so it's available the
// moment a candidate begins scoring — lets the steer panel fork from a
// still-running candidate. Matches by the live candidate id `r{round}_{idx}`.
// Reads the same complete `resolved_pipeline_params` as the round-file path.
export function liveCandidateSearchPoint(
  dash: DashboardSnapshot | null,
  candidateId: string,
): CandidateSearchPoint | null {
  if (!candidateId) return null;
  const liveRound = roundOf(dash);
  if (liveRound == null) return null;
  const entry = liveInputCandidate(dash, liveRound, candidateId);
  if (!entry) return null;
  return searchPoint(entry.prompt_fields, nodeConfigs(entry.resolved_pipeline_params));
}
