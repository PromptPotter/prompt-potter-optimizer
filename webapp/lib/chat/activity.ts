// The chat's curated translators over the cycle event stream, also run on every ray item. A field
// read here must be declared in `projection_envelope.py::RAY_PAYLOAD_FIELDS`, or ray items lack it.

import { candidateLabel } from "@/lib/candidate-label";
import { cacheShare, prefixReading } from "@/lib/derivations";
import { fmtDuration, fmtPct0 } from "@/lib/format";
import type { NonActivityKind, ProjectionEnvelope } from "@/lib/api/types";

// The faster copy of `dashboard.json::declared_sample_order`. A DECLARED order: PoBB can stop a
// candidate early, so a surface says "next", never "will".
export function sampleOrderFrom(env: ProjectionEnvelope): number[] | null {
  if (env.kind !== "snapshot") return null;
  const p = env.payload;
  if (str(p.event) !== "sample_order_preview") return null;
  const raw = asRec(p.payload).sample_order ?? p.sample_order;
  if (!Array.isArray(raw)) return null;
  const order = raw.map((v) => Number(v)).filter((v) => Number.isFinite(v));
  return order.length > 0 ? order : null;
}

// `payload` is the record's `model_dump`, so a record's own nested `payload` is at
// `env.payload.payload`.

// State is an icon AND a label, never colour alone; `tone` only styles on top.
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
  return v == null ? undefined : fmtPct0(v);
}

// Per-candidate rows only: a round headline reads the round's `accuracy`, as the header does.
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

function candidateItem(label: string, detail: string | undefined): ActivityItem {
  return { id: `cand-${label}`, kind: "candidate", icon: "◆", label, detail, tone: "good" };
}

export function snapshotToActivity(payload: Record<string, unknown>): ActivityItem[] {
  const out: ActivityItem[] = [];

  const rounds = Array.isArray(payload.rounds) ? payload.rounds : [];
  for (const rr of rounds) {
    const r = asRec(rr);
    const round = num(r.round);
    if (round == null) continue;
    out.push(roundItem(round, pct0(num(r.accuracy))));
  }

  // Round 0 enumerates nothing only because the engine scores the origin without firing
  // `candidate_started`: an engine gap, not a rule of this reader.
  const cr = asRec(payload.current_round);
  const crRound = num(cr.round);
  if (crRound != null && crRound >= 0) {
    // The l1_score INPUT is the only place a candidate appears before it has a number.
    const l1 = asRec(asRec(cr.nodes).l1_score);
    const inputs = Array.isArray(asRec(l1.input).candidates) ? asRec(l1.input).candidates : [];
    const rows = Array.isArray(cr.candidates) ? (cr.candidates as unknown[]) : [];
    const rowByLabel = new Map<string, Record<string, unknown>>();
    for (const c of rows) {
      const label = str(asRec(c).label);
      if (label) rowByLabel.set(label, asRec(c));
    }
    (inputs as unknown[]).forEach((c, i) => {
      const label = str(asRec(c).label) ?? candidateLabel(crRound, i);
      const row = rowByLabel.get(label);
      out.push(candidateItem(label, row ? fitPct(row) : undefined));
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

export function sampleScoredCandidate(env: ProjectionEnvelope): ActivityItem | null {
  if (env.kind !== "snapshot") return null;
  const p = env.payload;
  if (str(p.event) !== "sample_scored") return null;
  const running = asRec(asRec(asRec(p.payload).result)._running);
  if (Object.keys(running).length === 0) return null;
  return candidateItem(candidateLabel(num(p.round) ?? 0, num(p.candidate_idx) ?? 0), fitPct(running));
}

// Narrower than `ProjectionEnvelope`: a `RayItem` carries no `version` and no `cycle_id`.
export type ActivitySource = Pick<ProjectionEnvelope, "kind" | "sequence" | "payload">;

export function projectionToActivity(env: ActivitySource): ActivityItem | null {
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
      // Derived, never read: the account carries the two halves and stores no sum.
      const tok = (num(usage.input) ?? 0) + (num(usage.output) ?? 0);
      const bits: string[] = [];
      if (dur != null) bits.push(`${dur.toFixed(1)}s`);
      if (tok > 0) bits.push(`${tok} tok`);
      const prefix = prefixReading(
        cacheShare(num(usage.cache_read), num(usage.input), !!inner.cached),
        !!inner.cached,
      );
      if (prefix.state === "unreported") bits.push("prefix not reported");
      else if (prefix.share != null) bits.push(`${Math.round(prefix.share * 100)}% prefix cached`);
      // "replayed", not "cached": OUR archive served it, and `cached` names the provider discount.
      // The terminal (`live/display.py`) uses the same two words.
      if (inner.cached) bits.push("replayed");
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
      // Curated out; `sample_order_preview` is state, read by `sampleOrderFrom`.
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
      // Only the L4 inner heartbeat carries `detail`. The ray relies on a bare one returning null:
      // it keeps them as liveness proof and must never draw them as steps.
      const detail = str(p.detail);
      if (!detail) return null;
      // Formatted here, never composed into `detail` upstream.
      const secs = num(p.elapsed_s);
      return {
        id: "inner-progress",
        kind: "progress",
        icon: "·",
        label: detail,
        detail: secs == null ? undefined : fmtDuration(secs),
        tone: "muted",
      };
    }
    case "candidate_minted": {
      return candidateItem(str(p.label) ?? "candidate", undefined);
    }
    case "cycle_seed": {
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
    case "stream_snapshot": {
      // Synthesized by the tail; `useCycleEvents` routes it to `snapshotToActivity` first.
      return null;
    }
    default: {
      // Compiles only while every kind the server declares activity-bearing has a case above.
      const nonBearing: NonActivityKind = env.kind;
      void nonBearing;
      return null;
    }
  }
}
