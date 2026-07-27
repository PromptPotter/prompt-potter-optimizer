// The chat's curated layer over the cycle event stream. Pure translators feed
// the one ordered thread:
//
//   • snapshotToActivity() — paints state-so-far from the leading stream_snapshot
//     frame (the cycle's dashboard.json, read as-is): completed rounds + the
//     in-flight current round's candidates, so opening mid-run shows the
//     candidate being scored, not the last finished round.
//   • projectionToActivity() — maps each live tailed record to one item, 1:1 with
//     the CLI's LiveDisplay handlers (presentation/views/live/display.py).
//   • sampleScoredCandidate() — updates the in-flight candidate's fitness from the
//     running composite the scorer rides on each sample (result._running), so the
//     candidate line moves in real time instead of sitting at 0 until it completes.
//
// CURATED, not firehose: only high-signal records become items; the per-sample
// torrent folds into one progress chip; diagnostics + spend telemetry map to null.
// Items carry stable ids so the snapshot paint, the candidate start/scored, and
// the per-sample running update all upsert to one row.
//
// `projectionToActivity` has a SECOND caller now: the time-ray (`lib/derivations/time-ray.ts`)
// maps a `RayItem` through it unchanged, because a RayItem's `kind` + `payload` are
// byte-identical to an envelope's. That is deliberate — one curated event vocabulary, two
// surfaces — and it means a change here moves the ray too. At depth 0 the server's ray
// filter is kept strictly COARSER than this one (it drops only by kind, never by payload)
// so this file stays the single place deciding what is worth showing; at depth >= 1 the
// server deliberately cuts inner runs to milestones (`store/family_ray_views.py`).

import { candidateLabel } from "@/lib/candidate-label";
import { fmtPct0 } from "@/lib/format";

// One outbound SSE frame. Mirrors `domain/projection_envelope.py::ProjectionEnvelope`.
// `payload` is the underlying record's `model_dump` (so a record's own nested
// `payload` field is reached at `envelope.payload.payload`); for stream_snapshot
// it is the cycle's dashboard.json body + `snapshot_at_offset`.
export interface ProjectionEnvelope {
  kind: string;
  version?: number;
  cycle_id: string;
  sequence: number;
  payload: Record<string, unknown>;
}

// State pairs an icon AND a label (never colour alone) per the frontend
// accessibility invariant; `tone` is a styling hint layered on top.
type ActivityKind =
  | "running" // llm_call_start  — an optimizer call in flight
  | "done" //    llm_call        — a call completed (dur · tok)
  | "candidate" // a scored / in-flight individual (C0, C1.1, …)
  | "round" //   round summary
  | "warning" // round_warning   — a self-healed degradation
  | "error" //   error           — a runner failure
  | "merge" //   command / command_ack — a fired control on the trace
  | "progress"; // snapshot sample_scored — the transient "scoring i/n" chip

type ActivityTone = "good" | "warn" | "bad" | "muted";

// `id` is stable per logical event so every source upserts by id rather than
// duplicate.
export interface ActivityItem {
  id: string;
  kind: ActivityKind;
  icon: string;
  label: string;
  detail?: string;
  tone?: ActivityTone;
}

function asRec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" ? (v as Record<string, unknown>) : {};
}
function str(v: unknown): string | undefined {
  return typeof v === "string" ? v : undefined;
}
function num(v: unknown): number | undefined {
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}
function pct0(v: number | null | undefined): string | undefined {
  // `undefined` (omit the chip) where fmtPct0 would render an em-dash.
  return v == null ? undefined : fmtPct0(v);
}

// A candidate's subset-relative fitness as a %. Per-candidate rows only — round/origin
// headlines read the round's measured `accuracy` (the header/trend basis) so the thread agrees.
function fitPct(rec: Record<string, unknown>): string | undefined {
  return pct0(num(rec.composite_fitness));
}

// `{node}` or `{node}·r{round}` — the same label shape `LiveDisplay` prints.
function nodeLabel(p: Record<string, unknown>): string {
  const node = str(p.node) ?? "node";
  const round = num(p.round);
  return round != null ? `${node}·r${round}` : node;
}

function roundItem(round: number, detail: string | undefined): ActivityItem {
  return { id: `round-${round}`, kind: "round", icon: "★", label: `Round ${round}`, detail, tone: "good" };
}

// One candidate row, keyed by its canonical label so snapshot + started + scored
// + per-sample running update all upsert to the same line.
function candidateItem(label: string, detail: string | undefined): ActivityItem {
  return { id: `cand-${label}`, kind: "candidate", icon: "◆", label, detail, tone: "good" };
}

// Paint history from the leading stream_snapshot frame (the cycle's
// dashboard.json): completed rounds + the in-flight current round's candidates +
// recent warnings + a crash, if any — the state the file already carries.
export function snapshotToActivity(payload: Record<string, unknown>): ActivityItem[] {
  const out: ActivityItem[] = [];

  const rounds = Array.isArray(payload.rounds) ? payload.rounds : [];
  for (const rr of rounds) {
    const r = asRec(rr);
    const round = num(r.round);
    if (round == null) continue;
    out.push(roundItem(round, pct0(num(r.accuracy))));
  }

  // The in-flight round: every planned candidate (so the one being scored shows
  // immediately), fitness from the scored set where available. Round 0 is a round
  // like any other here; it enumerates nothing only because the engine scores the
  // origin without firing `candidate_started`, so no C0 row reaches the l1_score
  // input. That is an engine gap, not a rule of this reader.
  const cr = asRec(payload.current_round);
  const crRound = num(cr.round);
  if (crRound != null && crRound >= 0) {
    const l1 = asRec(asRec(cr.nodes).l1_score);
    const inputs = Array.isArray(asRec(l1.input).candidates) ? asRec(l1.input).candidates : [];
    const outputs = Array.isArray(asRec(l1.output).candidates) ? asRec(l1.output).candidates : [];
    const statByLabel = new Map<string, Record<string, unknown>>();
    for (const c of outputs as unknown[]) {
      const label = str(asRec(c).label);
      if (label) statByLabel.set(label, asRec(asRec(c).stats));
    }
    (inputs as unknown[]).forEach((c, i) => {
      const label = str(asRec(c).label) ?? candidateLabel(crRound, i);
      const stats = statByLabel.get(label);
      out.push(candidateItem(label, stats ? fitPct(stats) : undefined));
    });
  }

  const warnings = Array.isArray(payload.recent_loop_warnings) ? payload.recent_loop_warnings : [];
  warnings.forEach((w, i) => {
    const rec = asRec(w);
    const error = str(rec.severity) === "error";
    out.push({
      id: `warn-${i}`,
      kind: "warning",
      icon: error ? "✗" : "⚠",
      label: str(rec.message) ?? "round degraded",
      tone: error ? "bad" : "warn",
    });
  });

  const err = asRec(payload.error);
  const errMsg = str(err.message);
  if (errMsg) {
    out.push({ id: "error", kind: "error", icon: "✗", label: errMsg, detail: str(err.stop_reason), tone: "bad" });
  }
  return out;
}

// The in-flight candidate's live fitness from a sample_scored frame's running
// composite (`result._running`). `null` when the frame isn't a sample_scored or
// carries no running block.
export function sampleScoredCandidate(env: ProjectionEnvelope): ActivityItem | null {
  if (env.kind !== "snapshot") return null;
  const p = env.payload;
  if (str(p.event) !== "sample_scored") return null;
  const running = asRec(asRec(asRec(p.payload).result)._running);
  if (Object.keys(running).length === 0) return null;
  return candidateItem(candidateLabel(num(p.round) ?? 0, num(p.candidate_idx) ?? 0), fitPct(running));
}

// Map one live tailed envelope to at most one item. `null` = a deliberately
// dropped kind (firehose / non-item). Every `ProjectionKind` is handled.
export function projectionToActivity(env: ProjectionEnvelope): ActivityItem | null {
  const p = env.payload;
  const id = `${env.kind}-${env.sequence}`;
  switch (env.kind) {
    case "llm_call_start": {
      return { id, kind: "running", icon: "↻", label: nodeLabel(p), detail: str(p.model) ?? "default", tone: "muted" };
    }
    case "llm_call": {
      const inner = asRec(p.payload);
      const usage = asRec(inner.usage);
      const dur = num(inner.duration_s);
      const tok = num(usage.total_tokens);
      const bits: string[] = [];
      if (dur != null) bits.push(`${dur.toFixed(1)}s`);
      if (tok != null && tok > 0) bits.push(`${tok} tok`);
      if (inner.cached) bits.push("cached");
      return { id, kind: "done", icon: "✓", label: nodeLabel(p), detail: bits.join(" · ") || undefined, tone: "muted" };
    }
    case "snapshot": {
      const event = str(p.event);
      if (event === "candidate_started") {
        return candidateItem(candidateLabel(num(p.round) ?? 0, num(p.candidate_idx) ?? 0), undefined);
      }
      if (event === "candidate_scored") {
        const scores = asRec(asRec(p.payload).scores);
        return candidateItem(str(scores.label) ?? "candidate", fitPct(scores));
      }
      if (event === "sample_scored") {
        const i = num(p.sample_idx);
        const n = num(p.sample_total);
        return { id, kind: "progress", icon: "·", label: n ? `scoring ${i ?? 0}/${n}` : "scoring", tone: "muted" };
      }
      // p_best_update / sample_order_preview / pobb_backfill — curated-out diagnostics.
      return null;
    }
    case "phase": {
      if (str(p.phase) !== "round" || str(p.event) !== "display") return null;
      const rr = asRec(asRec(p.payload).round_result);
      const round = num(p.round) ?? num(rr.round) ?? 0;
      return roundItem(round, pct0(num(rr.accuracy)));
    }
    case "round_warning": {
      const error = str(p.severity) === "error";
      return { id, kind: "warning", icon: error ? "✗" : "⚠", label: str(p.message) ?? "round degraded", tone: error ? "bad" : "warn" };
    }
    case "error": {
      return { id, kind: "error", icon: "✗", label: str(p.message) ?? "run error", detail: str(p.stop_reason), tone: "bad" };
    }
    case "command": {
      return { id, kind: "merge", icon: "⚡", label: str(p.kind) ?? "control", tone: "muted" };
    }
    case "command_ack": {
      if (str(p.status) === "rejected") {
        return { id, kind: "merge", icon: "⚠", label: "control rejected", detail: str(p.detail) || undefined, tone: "warn" };
      }
      return null;
    }
    case "llm_call_progress": {
      // Ordinary optimizer heartbeats carry no detail — curated out (the elapsed
      // counter rides freshness, not the thread). The L4 inner-campaign heartbeat
      // sets `detail` ("inner rX/Y · best Z%"), which becomes one ticking chip
      // (stable id ⇒ one upserted row) so the outer chat never reads as silent.
      //
      // The ray relies on this returning null for a bare heartbeat: it keeps those items
      // to prove the process was alive across a silent stretch, then drops them here so
      // they never become steps. Making them items would fill the ray with nothing.
      const detail = str(p.detail);
      if (!detail) return null;
      return { id: "inner-progress", kind: "progress", icon: "·", label: detail, tone: "muted" };
    }
    case "candidate_minted": {
      // The candidate exists but has not been scored — the earliest point anything can
      // name it, and on the ray the first step that says "this attempt was proposed".
      // Upserts to the SAME `cand-{label}` id the later `candidate_started` snapshot
      // writes, so the row simply appears one beat earlier and nothing duplicates.
      return candidateItem(str(p.label) ?? "candidate", undefined);
    }
    case "cycle_seed": {
      // At most one per cycle, at the head of its ledger — a fork or a
      // campaign-from-origin declaring what it starts from. Reads as one marker at the
      // top of that cycle's thread, which is exactly where it sits.
      const source = str(asRec(p.seed).origin_source);
      return {
        id,
        kind: "merge",
        icon: "⚡",
        label: "cycle seeded",
        detail: source || undefined,
        tone: "muted",
      };
    }
    // token_usage, decision, stream_snapshot — non-items here.
    default:
      return null;
  }
}
