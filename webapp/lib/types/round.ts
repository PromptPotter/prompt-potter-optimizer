// Round-axis types. The round number is the spine that ties Lineage,
// Fitness, Samples, and Inspector together; this file freezes the
// shapes that travel along it.

import type { ElectedRow } from "./candidate";

// The round document and its rows are GENERATED from the Pydantic models
// (`RoundResult.model_dump()` IS `rounds/round_NNNN.json`). They were hand-mirrored
// here three times over, and one copy spelled `composite_fitness` as `composite` — a
// field the server has never sent.
export type {
  RoundResult,
  RoundSummary,
  RoundSummaryCandidate,
  ScoreboardRow,
  ScoredCandidate,
} from "@/lib/api/types";

// `round_NNNN.json::results[]` entry. The pre-bucketing per-sample
// row consumed by FreqChart (in the round-mode path) and the
// historical samples view.
export interface RawResultRow {
  sample_id?: number;
  error?: unknown;
  predicted?: string;
  ground_truth?: string;
  query?: string;
  fitness?: number;
}

// The AUDIT TWIN of a round — `.runtime/cache/rounds/round_NNNN.json`, written by
// `AuditTrailView`. Same basename as the round document, different tree, different
// shape: this one carries the per-node LLM I/O, which the round document does NOT.
// Not generated — the audit trail is written as a plain dict, with no Pydantic model.
export interface RoundAuditDoc {
  round?: number;
  nodes?: Record<string, NodeBlock>;
  warnings?: unknown[];
  interrupted?: boolean;
}

// `dashboard.json::current_round.nodes[id]` / the audit twin's `nodes[id]`.
// Both surfaces share this shape — written by AuditTrailView
// (`promptpotter/infrastructure/projections/audit_trail.py`).
// `input`/`output` are loose dicts whose contents vary by node.
// `output.reasoning` (when present) is the model's own thinking channel — prose for a
// human, rendered in its own pane by `OptimizerNodeDetail`. It is ANALYTICAL ONLY:
// never derive, score, sort or gate on it (see Python `LLMResponse.reasoning`).
export interface NodeBlock {
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  // The node's resolved call config — what was ASKED FOR, and the only place a routing suffix
  // survives (`:nitro` picks which provider serves the call, at that provider's own price).
  config?: Record<string, unknown>;
  // The provider's ECHO of the model it served, which OpenRouter returns without the suffix.
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
export type RoundCandidates = Map<number, ElectedRow[]>;

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
