// Per-sample readers. Two strict functions, one per source —
// `liveSamplesFor` reads the in-flight projection in `dashboard.json`,
// `historicalSamplesFor` reads `round_NNNN.json::all_candidate_results`.
// Both return the same `SampleRow[]` shape so the unified
// RoundSamplesView mounts a single renderer.
//
// CLAUDE.md rule: live vs historical never merge. These functions are
// deliberately separate, with no fallback chain between them — `samplesForRow`
// SELECTS one based on the row's `source` tag (never merges), so every consumer
// (the candidates card's bars, RoundSamplesView's groups) routes the same way.

import {
  liveCandidate,
  type DashboardSnapshot,
} from "@/lib/poll";
import type { CandidateRow, SampleRow } from "@/lib/types";
import type { RoundResult } from "@/lib/types";
import { isHit } from "@/lib/fitness";

// Live-mode samples for one candidate in the in-flight round. Reads
// `dashboard.json::current_round.nodes.l1_score.output.candidates[].samples[]`, which the
// producer serves already graded (`render.py::sample_row`), so its `HIT`/`MISS`/`ERR` IS
// the verdict and nothing re-derives one. Returns rows in source order; the caller decides
// if it wants newest-first.
function liveSamplesFor(
  dash: DashboardSnapshot | null,
  round: number,
  candidate_id: string,
): SampleRow[] {
  const out: SampleRow[] = [];
  const c = liveCandidate(dash, round, candidate_id);
  if (!c) return out;
  (c.samples ?? []).forEach((s, ord) => {
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
  pipeline_data?: { terminal_node?: unknown };
  elapsed_s?: number;
  time_s?: number;
  error?: unknown;
  error_category?: unknown;
}

// The ROUND DOCUMENT'S OWN id for a candidate, found by the served join key.
//
// Every per-candidate slice of that document — `scoreboard`, `all_candidate_results` — is keyed on
// this id, and it is NOT the id the lineage tree serves: a lineage id is a fresh uuid per
// construction and a resumed run re-scores the origin, so the tree hands out a new C0 id while
// `round_0000.json`, written by the earlier run, still holds the old one. Joining a tree id
// straight into the document finds nothing and blanks every panel with no error.
//
// So the LABEL resolves the id once, at the document boundary, and the id addresses the document
// from there. `courseLabel` is the label the MINTING course gave the candidate — the same key
// `candidateObserveConfig` joins on, and never the renumbered timeline `label`.
export function docCandidateId(doc: RoundResult | null, courseLabel: string): string | null {
  if (!doc || !courseLabel) return null;
  const scores = doc.candidate_scores as { label?: string; candidate_id?: string }[] | undefined;
  const row = Array.isArray(scores) ? scores.find((c) => c.label === courseLabel) : undefined;
  return row?.candidate_id || null;
}

// Historical-mode samples for one candidate. Reads
// `round_NNNN.json::all_candidate_results[candidate_id]` only.
// `roundDoc` is the document already loaded by `useRoundFile`; this
// function is pure and synchronous.
//
// Exported because a surface reading a point on a branch it holds no stream for has a document and
// a searchpoint but no `CandidateRow` to route with — `samplesForRow` below stays the live/history
// router where one exists, and both call this. Its `candidate_id` is the DOCUMENT's, via
// `docCandidateId`.
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
    // Asked FIRST: `rescore_results` stamps an errored row `fitness = 0.0` as a display
    // convention, so the number IS present and reading it renders a backend fault as a miss.
    // On `error_category`, the producer's typed channel — `error` is a human message that can
    // be blank on a row that genuinely errored (`shared/errors.py::is_error_result`).
    const status =
      s.error_category != null
        ? "ERR"
        : typeof s.fitness === "number"
          ? isHit(s.fitness)
            ? "HIT"
            : "MISS"
          : null;
    const elapsed =
      typeof s.elapsed_s === "number"
        ? s.elapsed_s
        : typeof s.time_s === "number"
          ? s.time_s
          : null;
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
      // `pipeline_data`, not a top-level key: no round document has ever carried one, so the
      // node tag read blank on every closed round while the live half showed it.
      terminal_node:
        typeof s.pipeline_data?.terminal_node === "string" ? s.pipeline_data.terminal_node : "",
      elapsed_s: elapsed,
    };
  });
}

// The one source switch: an in-flight row reads from `dash`; a historical row
// reads from its round file `doc`. Selects, never merges (the no-stitch rule).
// The caller resolves the doc for the row's round (a per-round map entry, or the
// single loaded round file) and hands it in.
export function samplesForRow(
  row: CandidateRow,
  dash: DashboardSnapshot | null,
  doc: RoundResult | null,
): SampleRow[] {
  return row.source === "inflight"
    ? liveSamplesFor(dash, row.round, row.candidate_id)
    : historicalSamplesFor(doc, row.round, row.candidate_id);
}
