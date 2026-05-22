"use client";
import type { DashboardSnapshot } from "@/lib/poll";
import { fmtNum, fmtClock } from "@/lib/format";
import { CardFrame } from "@/components/ui/card";

interface Props {
  dash: DashboardSnapshot | null;
}

// Fields surfaced elsewhere (header, payload block, dedicated cards, workflow
// toolbar) — or withheld from the UI entirely. `patience` is withheld pending
// a redesign that gives it one dedicated location.
const SHOWN_ELSEWHERE = new Set([
  "cycle_id", "wallclock_serialized_at",
  "best", "total_queries_scored", "last_query_elapsed_s",
  "composite_fitness_formula",
  "current_round",
  "current_query_payload",
  "state", "round", "candidate", "query",
  "patience",
]);

const KNOWN_ORDER = [
  "origin_accuracy", "current_acc", "n_variants", "sp_budget_ttest",
  "total_backend_calls", "error_count", "degraded_count", "backend_retry_count",
  "state_since", "stop_reason",
];

const WARN_IF_POSITIVE = new Set(["error_count", "degraded_count", "backend_retry_count"]);

interface BackendWarning {
  ts?: string;
  kind?: string;
  attempt?: number;
  max_attempts?: number;
  wait_s?: number;
  error_class?: string;
  status_code?: number;
  final?: boolean;
  query?: string;
}

const FORMATTERS: Record<string, (v: unknown) => string> = {
  origin_accuracy: (v) => fmtNum(v),
  current_acc: (v) => fmtNum(v),
  state_since: fmtClock,
};

export function LiveStateCard({ dash }: Props) {
  const formula = (dash as { composite_fitness_formula?: string } | null)?.composite_fitness_formula || "—";

  // Build the KV grid: known-order fields first (when present), then any
  // remaining scalar fields in the dashboard.json snapshot.
  const items: [string, unknown][] = [];
  const seen = new Set(SHOWN_ELSEWHERE);
  if (dash) {
    for (const k of KNOWN_ORDER) {
      if (!(k in dash)) continue;
      seen.add(k);
      items.push([k, (dash as Record<string, unknown>)[k]]);
    }
    for (const [k, v] of Object.entries(dash)) {
      if (seen.has(k)) continue;
      if (typeof v === "object" && v !== null) continue;
      items.push([k, v]);
    }
  }

  const payload = (dash as { current_query_payload?: unknown } | null)?.current_query_payload;
  const payloadEmpty = payload == null || payload === "";
  const payloadText = payloadEmpty
    ? ((dash as { state?: string } | null)?.state === "scoring" ? "in flight, payload not exposed" : "no query in flight")
    : (typeof payload === "string" ? payload : JSON.stringify(payload, null, 2));

  return (
    <CardFrame
      className="live-state-card"
      headingTag="h2"
      title="Live state"
      actions={
        <span style={{ fontSize: 11, color: "var(--color-text-tertiary)", fontWeight: 400 }}>
          all dashboard.json fields
        </span>
      }
    >
      <div className="formula-row" title="composite_fitness_formula">{formula}</div>
      <div className="kv-grid">
        {items.map(([k, v]) => {
          const fmt = FORMATTERS[k] ?? ((x: unknown) => (x == null ? "—" : String(x)));
          const display = fmt(v);
          let cls = "";
          if (v == null || v === "" || display === "—") cls = "muted";
          else if (WARN_IF_POSITIVE.has(k) && Number(v) > 0) cls = "warn";
          else if (k === "state" && v === "scoring") cls = "ok";
          else if (k === "state" && v === "stopped") cls = "warn";
          const label = k.replace(/_/g, " ");
          return (
            <div key={k} className="kv">
              <div className="kv-label">{label}</div>
              <div className={`kv-val ${cls}`} title={String(v ?? "")}>{display}</div>
            </div>
          );
        })}
        {items.length === 0 && (
          <div className="kv">
            <div className="kv-val muted">Waiting for first dashboard.json poll…</div>
          </div>
        )}
      </div>
      <div className="var-label" style={{ marginTop: 14 }}>In-flight query payload</div>
      <div className={`payload-block${payloadEmpty ? " empty" : ""}`}>{payloadText}</div>
      <BackendWarnings dash={dash} />
      <PoBBBackfillLog dash={dash} />
    </CardFrame>
  );
}

interface BackfillEntry {
  round?: number;
  candidate_idx?: number;
  candidate_total?: number;
  backfilled?: Record<string, string[]>;
  n_priors?: number;
  n_measurements?: number;
}

function PoBBBackfillLog({ dash }: { dash: DashboardSnapshot | null }) {
  // ``backfill_log`` is the paired-PoBB telemetry stream: each entry names
  // the round/candidate the backfill ran ahead of and which prior gained
  // measurements on which samples. Absence of entries = every prior was
  // cached on this round's hard samples (the cheap path); presence means
  // the leader was just measured on samples it had never seen, so the
  // PoBB comparison is genuinely paired going forward.
  // See docs/concepts/paired-sample-pobb.md.
  const log = (dash as { backfill_log?: BackfillEntry[] } | null)?.backfill_log;
  if (!log || log.length === 0) return null;
  return (
    <>
      <div className="var-label" style={{ marginTop: 14 }}>
        Paired-PoBB backfill (last {log.length})
      </div>
      <div className="payload-block" style={{ fontSize: 11, lineHeight: 1.5 }}>
        {log.slice().reverse().map((e, i) => {
          const round = e.round ?? "?";
          const cidx = e.candidate_idx ?? 0;
          const ctot = e.candidate_total ?? 0;
          const nm = e.n_measurements ?? 0;
          const priors = e.backfilled ?? {};
          const summary = Object.entries(priors)
            .map(([pid, sids]) => `${pid}+${sids.length}`)
            .join("  ");
          return (
            <div key={i} style={{ marginBottom: 2 }}>
              <span style={{ color: "var(--color-text-tertiary)" }}>
                R{round} C{cidx + 1}/{ctot}
              </span>{" "}
              <span style={{ color: "var(--color-accent, #2563eb)" }}>↻</span>{" "}
              {nm} new {nm === 1 ? "measurement" : "measurements"}
              {summary && <span style={{ color: "var(--color-text-tertiary)" }}> — {summary}</span>}
            </div>
          );
        })}
      </div>
    </>
  );
}

function BackendWarnings({ dash }: { dash: DashboardSnapshot | null }) {
  // ``recent_backend_warnings`` is a list payload from
  // ``dashboard.json``; the operator needs to see retries land in
  // real time so a transient stall isn't mistaken for a stuck loop.
  // Surfaces nothing when no retries have happened — zero visual cost
  // in the happy path.
  const warnings = (dash as { recent_backend_warnings?: BackendWarning[] } | null)?.recent_backend_warnings;
  if (!warnings || warnings.length === 0) return null;
  return (
    <>
      <div className="var-label" style={{ marginTop: 14 }}>Recent backend retries (last {warnings.length})</div>
      <div className="payload-block" style={{ fontSize: 11, lineHeight: 1.5 }}>
        {warnings.slice().reverse().map((w, i) => {
          const ts = w.ts ? new Date(w.ts).toLocaleTimeString() : "—";
          const code = w.status_code != null ? ` HTTP ${w.status_code}` : "";
          const err = w.error_class ? ` ${w.error_class}` : "";
          const wait = w.wait_s != null ? ` · wait ${Number(w.wait_s).toFixed(1)}s` : "";
          const final = w.final ? " · FINAL" : "";
          const attempts = w.attempt != null ? ` (attempt ${w.attempt}/${w.max_attempts ?? "?"})` : "";
          const q = w.query ? ` · q="${w.query.slice(0, 40)}${w.query.length > 40 ? "…" : ""}"` : "";
          return (
            <div key={i} style={{ marginBottom: 2 }}>
              <span style={{ color: "var(--color-text-tertiary)" }}>{ts}</span>{" "}
              <span style={{ color: "var(--color-warn, #d97706)" }}>{w.kind ?? "warning"}</span>
              {code}{err}{attempts}{wait}{final}{q}
            </div>
          );
        })}
      </div>
    </>
  );
}
