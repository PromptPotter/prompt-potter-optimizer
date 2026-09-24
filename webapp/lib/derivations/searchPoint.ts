// Selects which searchpoint's served `resolved_pipeline_params` the read-only observe view shows;
// never re-merges. The origin is a round-0 candidate, not a third state.

import {
  liveInputCandidate,
  liveL1InputCandidates,
  roundOf,
  type DashboardSnapshot,
  type LiveInputCandidate,
} from "@/lib/poll";
import { candidateLabel } from "@/lib/candidate-label";
import { PROMPT_STRING_FIELDS } from "@/lib/prompt-fields";
import type { ElectedRow, RoundResult, SampleRow } from "@/lib/types";
import { roundHasCandidates, sortedRounds } from "./round-candidates";
import { wasElected } from "./election";

// best = the parent the search expands from; latest = in-flight while running, else the last
// measured; selected = a pick made on another surface, offered only while one exists.
export type ObserveState = "best" | "latest" | "selected";

const OBSERVE_LABELS: Record<ObserveState, string> = {
  best: "Best",
  latest: "Most recent",
  selected: "Selected",
};

// An unavailable state is DROPPED, not disabled — no permanently-dead button.
export function observeOptions(
  avail: Record<ObserveState, boolean>,
): { value: ObserveState; label: string }[] {
  const order: ObserveState[] = ["best", "latest", "selected"];
  return order.filter((s) => avail[s]).map((s) => ({ value: s, label: OBSERVE_LABELS[s] }));
}

export interface ObserveConfig {
  // `OptSearchPoint.prompt_field_dict()` shape.
  promptFields: Record<string, unknown>;
  config: Record<string, unknown>;
  // Decorated header ("live — C1.3"); never a join key.
  label: string;
}

// Keys are the served names so a paste greps against `round_NNNN.json`. Samples are ALL of them,
// never the render cap: a cap is a screen budget and a copy has none.
export function searchpointCopyChoices({
  cfg,
  row,
  samples = [],
  arms = null,
}: {
  cfg: ObserveConfig | null;
  row?: ElectedRow | null;
  samples?: readonly SampleRow[];
  arms?: number | null;
}): { key: string; label: string; data: unknown }[] {
  const spec = cfg
    ? {
        // `label` is the join key, so only a row answers for it; the decorated header rides `shown_as`.
        ...(row ? { label: row.label } : { shown_as: cfg.label }),
        resolved_pipeline_params: cfg.config,
        prompt_fields: cfg.promptFields,
      }
    : null;
  const scored = row ? { ...(spec ?? { label: row.label }), arms, scored: row } : null;

  const choices: { key: string; label: string; data: unknown }[] = [];
  if (spec) choices.push({ key: "spec", label: "Searchpoint spec", data: spec });
  if (scored) choices.push({ key: "scored", label: "Spec + scores", data: scored });
  if (scored && samples.length > 0) {
    choices.push({
      key: "full",
      label: `Spec + scores + ${samples.length} samples`,
      data: { ...scored, samples },
    });
  }
  return choices;
}

// The promptpotter-self shape: each optimizer node owns its prompt fields per-node, and the round
// file carries only the mutated delta, not the static `prompts/{node}.json` origin.
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

// Null only between `L1_GENERATE:enter` (which resets the buffer, `projection.py::_apply_phase`)
// and the first candidate starting; the host falls back to the last closed searchpoint.
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

// Null until that candidate is seeded (`candidate_started`).
export function liveCandidateObserveConfig(
  dash: DashboardSnapshot | null,
  label: string,
  nodeId?: string | null,
): ObserveConfig | null {
  return rowConfig(liveInputCandidate(dash, label), `live — ${label}`, nodeId);
}

// Joined on the positional label, never `candidate_id`: a resume re-scores C0 under a NEW id while
// the round file keeps the old one, and the miss is silent.
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

// Apart from `ObserveConfig`: a state's availability is served, its config a fetch that may not
// have landed. `courseLabel` is the join key; `label` is decoration.
export interface ObserveTarget {
  round: number;
  idx: number;
  courseLabel: string;
  label: string;
  candidateId: string;
}

// The label comes off the row, never the position: `domain/results.py::candidate_label` answers
// `C0` for EVERY round-0 arm.
function targetAt(
  round: number,
  idx: number,
  candidateId: string,
  courseLabel: string,
  prefix: string,
): ObserveTarget {
  return { round, idx, courseLabel, label: `${prefix} · ${courseLabel}`, candidateId };
}

// The most recent served crown, not the highest θ ever: after a rewind the two differ, and a strict
// global best would need a served pointer.
export function bestObserveTarget(dash: DashboardSnapshot | null): ObserveTarget | null {
  const rounds = sortedRounds(dash).filter(roundHasCandidates).reverse();
  for (const r of rounds) {
    const idx = r.candidates.findIndex((c) => c.is_winner);
    const w = idx >= 0 ? r.candidates[idx] : null;
    if (!w?.candidate_id) continue;
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

// The closed half of `latest`; the host, which alone knows the run is live, switches to `liveObserveConfig`.
export function latestClosedTarget(dash: DashboardSnapshot | null): ObserveTarget | null {
  const rounds = sortedRounds(dash).filter(roundHasCandidates);
  const last = rounds.at(-1);
  if (!last) return null;
  const idx = last.candidates.length - 1;
  const c = last.candidates[idx];
  if (!c?.candidate_id) return null;
  return targetAt(last.round, idx, c.candidate_id, c.label, "latest");
}
