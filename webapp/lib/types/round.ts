// The shapes that travel the round axis.

import type { ElectedRow } from "./candidate";

export type {
  RoundResult,
  RoundSummary,
  RoundSummaryCandidate,
  ScoreboardRow,
  ScoredCandidate,
} from "@/lib/api/types";

// `round_NNNN.json::results[]` entry.
export interface RawResultRow {
  sample_id?: number;
  error?: unknown;
  predicted?: string;
  ground_truth?: string;
  query?: string;
  fitness?: number;
}

// `.runtime/cache/rounds/round_NNNN.json`: the only home of per-node LLM I/O. Hand-written because
// `AuditTrailProjection` writes a plain dict with no Pydantic model.
export interface RoundAuditDoc {
  round?: number;
  nodes?: Record<string, NodeBlock>;
  warnings?: unknown[];
  interrupted?: boolean;
}

// `output.reasoning` is the model's thinking channel, ANALYTICAL ONLY: never derive, score, sort
// or gate on it (`LLMResponse.reasoning`).
export interface NodeBlock {
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  // What was ASKED FOR: the only place a routing suffix (`:nitro`) survives.
  config?: Record<string, unknown>;
  // The provider's echo, which OpenRouter returns without the suffix.
  model?: string;
  duration_s?: number;
  timestamp?: string;
  round?: number;
  // Our own reuse cache answered, so `usage` is the BANKED call's. Absent means false.
  cached?: boolean;
  // A `TokenAccount`. `cache_read` is the PROVIDER's prefix-cache discount, a SUBSET of `input`
  // (unrelated to `cached`); `null` = no breakdown reported.
  usage?: {
    input?: number;
    output?: number;
    reasoning?: number;
    cache_read?: number | null;
    cache_write?: number;
  };
}

export type RoundCandidates = Map<number, ElectedRow[]>;

export interface RoundAxis {
  completed: number[];
  // Set only while the optimizer runs AND `current_round` is not yet in `completed`.
  live: number | null;
}
