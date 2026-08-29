// Observe-side resolver: the read-only "what config does THIS searchpoint run" view. Two states,
// the live in-flight candidate and a completed one out of its round file, both reading the same
// server-resolved `resolved_pipeline_params`.
//
// The ORIGIN is not a third state — C0 is a candidate of round 0 like any other, and typing it
// as its own state makes the origin a mode of LOOKING rather than a thing to look AT.
//
// No client re-merge; this module only SELECTS which searchpoint's resolved config to show. The
// STEER fork seed (`candidateSearchPoint.ts`) reads the same field for a different purpose — an
// editable seed, not a view — so the two stay separate functions over one served field.

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
import { roundHasCandidates, sortedRounds } from "./round-candidates";
import { wasElected } from "./election";

// WHICH searchpoint the observe box shows. Each state answers a question an
// operator actually asks, which is why none of them names a data source:
//
//   best     — the parent: what the search is currently expanding from.
//   latest   — the newest searchpoint touched: in-flight while running, else
//              the last candidate the last closed round measured.
//   selected — the candidate picked on another surface (a candidates-card bar,
//              a sidebar row). Offered ONLY while such a pick exists.
export type ObserveState = "best" | "latest" | "selected";

const OBSERVE_LABELS: Record<ObserveState, string> = {
  best: "Best",
  latest: "Most recent",
  selected: "Selected",
};

// The button set, shared so the two hosts (the pipeline node detail and the chat's
// run card) cannot drift on wording or order. An UNAVAILABLE state is DROPPED, not
// disabled: a permanently-dead button is the affordance dishonesty this rename was
// for. `selected` is last because it only ever appears transiently.
export function observeOptions(
  avail: Record<ObserveState, boolean>,
): { value: ObserveState; label: string }[] {
  const order: ObserveState[] = ["best", "latest", "selected"];
  return order.filter((s) => avail[s]).map((s) => ({ value: s, label: OBSERVE_LABELS[s] }));
}

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
// `resolved_pipeline_params[nodeId]` — the pp-self optimizer prompt shape, where each
// optimizer node (l1_generate / l1_critique / …) owns its own persona/instruction/…
// rather than sharing one flat prompt. Only fields actually present are returned
// (the round file carries only the optimizer's mutated delta, not the static
// `prompts/{node}.json` origin).
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
// `nodeId` (the selected node) lets a prompt-bearing optimizer node surface its OWN
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
// OBSERVE view falls back to the last completed searchpoint for that brief gap.
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

// Historical: a past candidate out of its lazily-loaded round file, located by its POSITIONAL
// label — any round including 0, which is what makes the origin an ordinary observe target.
//
// **Not `candidate_id`, and the difference is only visible after a resume.** A lineage id is a
// fresh uuid per construction and a resumed run re-scores the origin, so the tree serves C0 under
// a NEW id while the round file still holds the old one; an id join then finds nothing and the
// table blanks with no error. The live sibling above may join on id only because it cannot cross
// a run. `display` is decoration and must never be the join key.
export function candidateObserveConfig(
  doc: RoundResult | null,
  courseLabel: string,
  display: string,
  nodeId?: string | null,
): ObserveConfig | null {
  const scores = doc?.candidate_scores;
  if (!Array.isArray(scores) || !courseLabel) return null;
  const row = (scores as LiveInputCandidate[]).find((c) => c.label === courseLabel);
  return rowConfig(row, display, nodeId);
}

// WHICH past candidate a state points at, before its round file is loaded. Held
// apart from `ObserveConfig` on purpose: availability of a state is a served fact
// (does such a candidate exist), while the config is a fetch that may not have
// landed — conflating them made a button flicker with its own round file.
//
// `courseLabel` is the SERVED join key `candidateObserveConfig` needs; `label` is the
// decorated header string and must never be joined on.
export interface ObserveTarget {
  round: number;
  idx: number;
  courseLabel: string;
  label: string;
  candidateId: string;
}

// The label comes off the row, never off the position. `domain/results.py::candidate_label`
// is the sole writer of the format and answers `C0` for EVERY round-0 arm, where re-deriving
// it positionally answers `C0.2` from index 1 on — a join key matching no served row, and the
// miss is silent because `find` simply returns undefined.
function targetAt(
  round: number,
  idx: number,
  candidateId: string,
  courseLabel: string,
  prefix: string,
): ObserveTarget {
  return { round, idx, courseLabel, label: `${prefix} · ${courseLabel}`, candidateId };
}

// THE PARENT — the individual the search expands from. A WALK back to the most recent SERVED
// crown, not `rounds.at(-1)`: a round that crowned nobody is skipped rather than inventing one.
// Round 0 is included, since on a campaign that only measured its origin C0 IS the parent.
// **Under a REWIND** the most recent crown is still what the search expands from but need not be
// the highest-θ individual ever measured; a strict global best needs a SERVED pointer.
export function bestObserveTarget(dash: DashboardSnapshot | null): ObserveTarget | null {
  const rounds = sortedRounds(dash).filter(roundHasCandidates).reverse();
  for (const r of rounds) {
    const idx = r.candidates.findIndex((c) => c.is_winner);
    const w = idx >= 0 ? r.candidates[idx] : null;
    if (!w?.candidate_id) continue;
    // Only badge `best` as a win when the round it won had rivals — round 0 crowns
    // its sole arm by default and must not claim an election.
    return targetAt(
      r.round,
      idx,
      w.candidate_id,
      w.label,
      wasElected(true, r.candidates.length) ? "best" : "origin",
    );
  }
  return null;
}

// The newest searchpoint that has CLOSED — the last candidate of the last round with
// candidates. This is the historical half of `latest`; while the cycle is running the
// state resolves to the in-flight candidate through `liveObserveConfig` instead, and
// the host makes that switch (it is the only side that knows whether the run is live).
export function latestClosedTarget(dash: DashboardSnapshot | null): ObserveTarget | null {
  const rounds = sortedRounds(dash).filter(roundHasCandidates);
  const last = rounds.at(-1);
  if (!last) return null;
  const idx = last.candidates.length - 1;
  const c = last.candidates[idx];
  if (!c?.candidate_id) return null;
  return targetAt(last.round, idx, c.candidate_id, c.label, "latest");
}
