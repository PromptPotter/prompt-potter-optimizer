// Each candidate's rows and why it has them. Live and historical samples never merge;
// `samplesForRow` selects one.

import { liveCandidate, type DashboardSnapshot } from "@/lib/poll";
import type { ScoredCandidate, ValidationFailure } from "@/lib/api/types";
import type { CandidateRow, SampleRow } from "@/lib/types";
import type { RoundResult } from "@/lib/types";
import { isHit } from "@/lib/fitness";
// Never via the barrel: `index.ts` re-exports this module, and the cycle leaves a `const` in the TDZ.
import { foldStepTimings } from "./sample-clock";
import { cacheShare, foldStepTokens } from "./token-account";

// Served already graded (`blocks.py::sample_row`): its status IS the verdict.
function liveSamplesFor(
  dash: DashboardSnapshot | null,
  round: number,
  candidate_id: string,
  label: string,
): SampleRow[] {
  const out: SampleRow[] = [];
  const c = liveCandidate(dash, label);
  if (!c) return out;
  c.samples.forEach((s, ord) => {
    out.push({
      key: `${round}|${candidate_id}|${s.sample_id ?? `o${ord}`}`,
      round,
      candidate_id,
      sample_id: s.sample_id,
      status: s.status,
      cached: s.cached,
      query: s.query,
      predicted: s.predicted,
      ground_truth: s.ground_truth,
      terminal_node: s.terminal_node,
      elapsed_s: s.time_s,
      cost_s: s.cost_s ?? null,
      cache_share: cacheShare(s.cache_read_tokens, s.input_tokens, s.cached),
    });
  });
  return out;
}

interface RawHistoricalSample {
  sample_id?: number;
  query?: string;
  predicted?: string;
  ground_truth?: string;
  fitness?: number;
  cached?: boolean;
  // A row's token counts and both clocks live ONLY here, per node; no top-level twin exists.
  pipeline_data?: {
    terminal_node?: unknown;
    step_tokens?: unknown;
    step_timings?: unknown;
    total_time?: unknown;
  };
  error?: unknown;
  error_category?: unknown;
}

export function historicalSamplesFor(
  roundDoc: RoundResult | null,
  round: number,
  candidate_id: string,
): SampleRow[] {
  if (!roundDoc) return [];
  const acr = roundDoc.all_candidate_results as
    | Record<string, unknown>
    | undefined;
  if (!acr) return [];
  const list = acr[candidate_id];
  if (!Array.isArray(list)) return [];
  return list.map((sample, ord) => {
    const s = sample as RawHistoricalSample;
    const sid = typeof s.sample_id === "number" ? s.sample_id : null;
    // Asked first: `rescore_results` stamps an errored row `fitness = 0.0`. `error` can be blank on
    // a real error, so the typed `error_category` decides (`shared/errors.py::is_error_result`).
    const status =
      s.error_category != null
        ? "ERR"
        : typeof s.fitness === "number"
          ? isHit(s.fitness)
            ? "HIT"
            : "MISS"
          : null;
    // A true 0.0 on a replay; `cost_s` is what the cell took when measured.
    const elapsed = typeof s.pipeline_data?.total_time === "number" ? s.pipeline_data.total_time : null;
    return {
      key: `${round}|${candidate_id}|${sid ?? `o${ord}`}`,
      round,
      candidate_id,
      sample_id: sid,
      status,
      cached: s.cached === true,
      query: typeof s.query === "string" ? s.query : "",
      predicted: typeof s.predicted === "string" ? s.predicted : "",
      ground_truth: typeof s.ground_truth === "string" ? s.ground_truth : "",
      terminal_node:
        typeof s.pipeline_data?.terminal_node === "string" ? s.pipeline_data.terminal_node : "",
      elapsed_s: elapsed,
      cost_s: foldStepTimings(s.pipeline_data?.step_timings),
      cache_share: (() => {
        const account = foldStepTokens(s.pipeline_data?.step_tokens);
        return cacheShare(account?.cacheRead, account?.input, s.cached === true);
      })(),
    };
  });
}

export function samplesForRow(
  row: CandidateRow,
  dash: DashboardSnapshot | null,
  doc: RoundResult | null,
): SampleRow[] {
  return row.source === "inflight"
    ? liveSamplesFor(dash, row.round, row.candidate_id, row.label)
    : historicalSamplesFor(doc, row.round, row.candidate_id);
}

// Both halves join on `label`: one `candidate_label(round, idx)` call mints it everywhere.
export interface CandidateVerdict {
  // `""` when absent — never a placeholder, which would read as the optimizer's words.
  changes: string;
  // Non-empty means the candidate never ran.
  failures: ValidationFailure[];
}

// Only EXPLAINS a rejection `ElectedRow.invalid` declared. Rows are the live `LiveCandidate`s or a
// round file's `candidate_scores`, whichever half the rows themselves came from.
export function candidateVerdicts(
  rows: readonly Pick<ScoredCandidate, "label" | "changes_description" | "validation_failures">[],
): Map<string, CandidateVerdict> {
  return new Map(
    rows.map((c) => [c.label, { changes: c.changes_description, failures: c.validation_failures }]),
  );
}
