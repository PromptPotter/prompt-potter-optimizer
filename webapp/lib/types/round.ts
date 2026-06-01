// Round-axis types. The round number is the spine that ties Lineage,
// Fitness, Samples, and Inspector together; this file freezes the
// shapes that travel along it.

import type { CandidateRow } from "./candidate";

export type { RoundSummary, RoundSummaryCandidate } from "@/lib/api/types";

// `round_NNNN.json::scoreboard[]` entry. Deep-audit surface fetched
// lazily by ScoringInspector / RoundSamplesView when the operator
// drills into a specific round. The summary surface
// (`dash.rounds[].candidates[]`) doesn't carry composite / hits /
// per_sample — those live here.
export interface ScoreboardEntry {
  rank?: number;
  candidate_id?: string;
  label?: string;
  accuracy?: number;
  composite?: number;
  hits?: number;
  total?: number;
  is_winner?: boolean;
  per_sample?: Record<string, unknown>[];
  [k: string]: unknown;
}

// `round_NNNN.json::results[]` entry. The pre-bucketing per-sample
// row consumed by FreqChart (in the round-mode path) and the
// historical samples view.
export interface RawResultRow {
  sample_id?: number;
  score?: number;
  error?: unknown;
  predicted?: string;
  ground_truth?: string;
  query?: string;
  hit?: boolean;
  fitness?: number;
}

// `dashboard.json::current_round.nodes[id]` / `round_NNNN.json::nodes[id]`.
// Both surfaces share this shape — written by AuditTrailView at
// `promptpotter/infrastructure/projections/audit_trail.py:222-243`.
// `input`/`output` are loose dicts whose contents vary by node.
export interface NodeBlock {
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  model?: string;
  duration_s?: number;
  timestamp?: string;
  round?: number;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
  };
}

// Map keyed by round number → that round's candidate rows in display
// order. Round 0 holds the single origin row. The dashboard's current
// round may carry in-flight candidates that haven't closed into
// `dash.rounds[]` yet; the derivation merges them in once, here.
export type RoundCandidates = Map<number, CandidateRow[]>;

// What a round-picker surface needs to render its axis.
export interface RoundAxis {
  // Round numbers with a closed summary on `dash.rounds[]`, ascending.
  completed: number[];
  // The in-flight round to advertise as live — set only when the optimizer
  // is running (`isLive`) AND `dash.current_round` carries a round not yet
  // in `completed`. null = no live round to show (never started, already
  // closed, or the run stopped).
  live: number | null;
}
