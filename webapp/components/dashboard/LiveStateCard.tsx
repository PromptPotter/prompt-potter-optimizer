"use client";
import type { DashboardSnapshot } from "@/lib/poll";

interface Props {
  dash: DashboardSnapshot | null;
}

// Fields surfaced elsewhere (header, payload block, dedicated cards, workflow toolbar).
const SHOWN_ELSEWHERE = new Set([
  "cycle_id", "wallclock_serialized_at",
  "best", "total_queries_scored", "last_query_elapsed_s",
  "composite_fitness_formula",
  "current_round",
  "current_query_payload",
  "state", "round", "candidate", "query",
]);

const KNOWN_ORDER = [
  "patience",
  "baseline", "current_acc", "n_variants", "sp_budget_ttest",
  "total_backend_calls", "error_count", "degraded_count",
  "state_since", "stop_reason",
];

const WARN_IF_POSITIVE = new Set(["error_count", "degraded_count"]);

function fmtNum(v: unknown, digits = 3): string {
  if (v == null) return "—";
  if (typeof v === "number") return v.toFixed(digits);
  return String(v);
}

function fmtTime(s: unknown): string {
  if (!s) return "—";
  try { return new Date(String(s)).toLocaleTimeString(); } catch { return String(s); }
}

const FORMATTERS: Record<string, (v: unknown) => string> = {
  baseline: (v) => fmtNum(v),
  current_acc: (v) => fmtNum(v),
  state_since: fmtTime,
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
    <div className="card live-state-card">
      <h2 className="card-title">
        Live state
        <span style={{ fontSize: 11, color: "var(--color-text-tertiary)", fontWeight: 400 }}>
          all dashboard.json fields
        </span>
      </h2>
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
    </div>
  );
}
