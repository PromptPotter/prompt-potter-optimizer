// What the `l1_score` block says about each candidate: the ROWS it measured, and WHY it has
// them. One module because it is one array — `nodes.l1_score.{input,output}.candidates[]`,
// built in one loop over one `RoundBuffer` (`live_dashboard/render.py`). Two readers of one
// source in two files is how the same shape gets read two ways.
//
// Per-sample: two strict functions, one per source — `liveSamplesFor` reads the in-flight
// projection in `dashboard.json`, `historicalSamplesFor` reads
// `round_NNNN.json::all_candidate_results`. Both return the same `SampleRow[]` shape so the
// unified MeasurementRun mounts a single renderer.
//
// CLAUDE.md rule: live vs historical never merge. These functions are
// deliberately separate, with no fallback chain between them — `samplesForRow`
// SELECTS one based on the row's `source` tag (never merges), so every consumer
// (the candidates card's bars, MeasurementRun's groups) routes the same way.

import {
  liveCandidate,
  type DashboardSnapshot,
} from "@/lib/poll";
import type { ValidationFailure } from "@/lib/api/types";
import type { CandidateRow, NodeBlock, SampleRow } from "@/lib/types";
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
  label: string,
): SampleRow[] {
  const out: SampleRow[] = [];
  const c = liveCandidate(dash, label);
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
    ? liveSamplesFor(dash, row.round, row.candidate_id, row.label)
    : historicalSamplesFor(doc, row.round, row.candidate_id);
}

// WHY a candidate produced the rows it produced. The block's two halves each own half the
// account: the input half carries what the candidate TRIED (`changes_description`), the output
// half what validation SAID about it (`validation_failures`). They join on `label`, exactly —
// both halves and `DashboardCandidate.label` come from one `candidate_label(round, idx)` call.
export interface CandidateVerdict {
  // The optimizer's own words. `""` when the half is absent — never a placeholder sentence,
  // which would read as something the optimizer wrote.
  changes: string;
  // Empty for a candidate that ran. Non-empty means it never did.
  failures: ValidationFailure[];
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

// The `candidates` array off one half of the block, or empty. Rows that are not objects are
// dropped rather than coerced — a malformed row has no label to file it under anyway.
function candidatesOf(half: Record<string, unknown> | undefined): Record<string, unknown>[] {
  if (!isRecord(half)) return [];
  const raw = half.candidates;
  return Array.isArray(raw) ? raw.filter(isRecord) : [];
}

function labelOf(c: Record<string, unknown>): string | null {
  return typeof c.label === "string" && c.label !== "" ? c.label : null;
}

// Only the two fields anything renders are required. `axis` / `allowed` / `owner` ride along
// untouched — checking them would reject a row over a field no surface reads.
function isFailure(v: unknown): v is ValidationFailure {
  return isRecord(v) && typeof v.value === "string" && typeof v.reason === "string";
}

// Takes the RESOLVED block rather than reaching for the live snapshot, and that is what makes it
// work on a HISTORICAL round: `useRoundNodes` is the single resolver that picks the live block vs
// the audit twin, and `AuditTrailView.set_l1_score` deposits the identical object into the twin.
//
// It deliberately does NOT answer whether a candidate is invalid — `ElectedRow.invalid` does, off
// the candidate row every other surface already reads. This only EXPLAINS a rejection the row has
// already declared, which is why a missing entry here is never a verdict: it means the block has
// not arrived, not that nothing was wrong. Read defensively throughout; the block is a plain
// `dict[str, Any]` server-side with no model behind it.
export function candidateVerdicts(
  block: NodeBlock | null | undefined,
): Map<string, CandidateVerdict> {
  const out = new Map<string, CandidateVerdict>();
  if (!block) return out;

  for (const c of candidatesOf(block.input)) {
    const label = labelOf(c);
    if (!label) continue;
    out.set(label, {
      changes: typeof c.changes_description === "string" ? c.changes_description : "",
      failures: [],
    });
  }

  // A candidate can appear in one half and not the other — the input half is seeded when scoring
  // STARTS and the output half filled as it finishes, so mid-round the two disagree by design.
  for (const c of candidatesOf(block.output)) {
    const label = labelOf(c);
    if (!label) continue;
    const raw = c.validation_failures;
    const failures = Array.isArray(raw) ? raw.filter(isFailure) : [];
    out.set(label, { changes: out.get(label)?.changes ?? "", failures });
  }

  return out;
}
