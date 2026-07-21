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
import { parseSampleLine } from "@/lib/sample-line";
import type { CandidateRow, SampleRow } from "@/lib/types";
import type { RoundResult } from "@/lib/types";

// Live-mode samples for one candidate in the in-flight round. Reads
// from `dashboard.json::current_round.nodes.l1_score.output.candidates`
// only. Returns rows in source order; the caller decides if it wants
// newest-first.
function liveSamplesFor(
  dash: DashboardSnapshot | null,
  round: number,
  candidate_id: string,
): SampleRow[] {
  const out: SampleRow[] = [];
  const c = liveCandidate(dash, round, candidate_id);
  if (!c) return out;
  (c.samples ?? []).forEach((raw, ord) => {
    if (typeof raw === "string") {
      const p = parseSampleLine(raw);
      const sid = typeof p.sampleId === "number" ? p.sampleId : null;
      out.push({
        key: `${round}|${candidate_id}|${sid ?? `o${ord}`}`,
        round,
        candidate_id,
        sample_id: sid,
        status: p.status ?? null,
        cached: p.cached ?? false,
        query: p.query ?? "",
        predicted: p.predicted ?? "",
        ground_truth: p.gt ?? "",
        scorer: p.scorer ?? "",
        elapsed_s: typeof p.elapsed === "number" ? p.elapsed : null,
        has_error: false,
        raw_line: p.raw,
      });
    } else if (raw && typeof raw === "object") {
      const sid = typeof raw.sample_id === "number" ? raw.sample_id : null;
      const status =
        typeof raw.hit === "boolean" ? (raw.hit ? "HIT" : "MISS") : null;
      out.push({
        key: `${round}|${candidate_id}|${sid ?? `o${ord}`}`,
        round,
        candidate_id,
        sample_id: sid,
        status,
        cached: raw.cached === true,
        query: "",
        predicted: typeof raw.prediction === "string" ? raw.prediction : "",
        ground_truth: "",
        scorer: "",
        elapsed_s: typeof raw.time_s === "number" ? raw.time_s : null,
        has_error: false,
      });
    }
  });
  return out;
}

interface RawHistoricalSample {
  sample_id?: number;
  query?: string;
  predicted?: string;
  ground_truth?: string;
  hit?: boolean;
  cached?: boolean;
  scorer?: string;
  elapsed_s?: number;
  time_s?: number;
  error?: unknown;
}

// Historical-mode samples for one candidate. Reads
// `round_NNNN.json::all_candidate_results[candidate_id]` only.
// `roundDoc` is the document already loaded by `useRoundFile`; this
// function is pure and synchronous.
function historicalSamplesFor(
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
    const status =
      typeof s.hit === "boolean" ? (s.hit ? "HIT" : "MISS") : null;
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
      scorer: typeof s.scorer === "string" ? s.scorer : "",
      elapsed_s: elapsed,
      has_error: s.error != null && s.error !== "",
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
