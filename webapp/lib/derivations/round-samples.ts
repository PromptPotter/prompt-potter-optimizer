// Per-sample readers. Two strict functions, one per source —
// `liveSamplesFor` reads the in-flight projection in `dashboard.json`,
// `historicalSamplesFor` reads `round_NNNN.json::all_candidate_results`.
// Both return the same `SampleRow[]` shape so the unified
// RoundSamplesView mounts a single renderer.
//
// AGENTS.md rule: live vs historical never merge. These functions are
// deliberately separate, with no fallback chain between them — the
// component picks one based on whether `selection.round` matches the
// live in-flight round.

import {
  liveCandidate,
  liveL1Candidates,
  type DashboardSnapshot,
  type RoundFileDoc,
} from "@/lib/poll";
import { liveCandidateId } from "@/lib/candidate-label";
import { parseSampleLine } from "@/lib/sample-line";
import type { SampleRow } from "@/lib/types/sample";

// Live-mode samples for one candidate in the in-flight round. Reads
// from `dashboard.json::current_round.nodes.l1_score.output.candidates`
// only. Returns rows in source order; the caller decides if it wants
// newest-first.
export function liveSamplesFor(
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

// All live-mode samples across every in-flight candidate. Used by
// RoundSamplesView when the operator wants the whole live tape rather
// than one candidate's stream.
export function liveSamplesAll(
  dash: DashboardSnapshot | null,
  round: number,
): SampleRow[] {
  const out: SampleRow[] = [];
  for (const c of liveL1Candidates(dash)) {
    const i = Number(c.idx);
    if (!Number.isFinite(i) || i < 0) continue;
    const candidate_id = liveCandidateId(round, i);
    out.push(...liveSamplesFor(dash, round, candidate_id));
  }
  return out;
}

interface RawHistoricalSample {
  sample_id?: number;
  query?: string;
  predicted?: string;
  ground_truth?: string;
  hit?: boolean;
  fitness?: number;
  scorer?: string;
  elapsed_s?: number;
  time_s?: number;
  error?: unknown;
}

// Historical-mode samples for one candidate. Reads
// `round_NNNN.json::all_candidate_results[candidate_id]` only.
// `roundDoc` is the document already loaded by `useRoundFile`; this
// function is pure and synchronous.
export function historicalSamplesFor(
  roundDoc: RoundFileDoc | null,
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
      query: typeof s.query === "string" ? s.query : "",
      predicted: typeof s.predicted === "string" ? s.predicted : "",
      ground_truth: typeof s.ground_truth === "string" ? s.ground_truth : "",
      scorer: typeof s.scorer === "string" ? s.scorer : "",
      elapsed_s: elapsed,
      has_error: s.error != null && s.error !== "",
    };
  });
}
