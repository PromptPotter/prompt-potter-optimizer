"use client";
import type { DashboardSnapshot } from "@/lib/poll";

interface Props {
  dash: DashboardSnapshot | null;
}

interface RuleEntry {
  round?: number | null;
  timestamp?: string | null;
  layer?: string | null;
  rule_name?: string | null;
  rule_priority?: number | null;
  next_action?: string | null;
  reason?: string | null;
  signal_inputs?: Record<string, unknown>;
}

// Phase 4 — operator-facing readout of the cadence-rule firings.
// Reads ``dashboard.json::recent_rules`` (rolling buffer, last 8 firings)
// — same data SignalsProjection writes to ``.runtime/signals.jsonl``.
// One row per fire, newest first; signal_inputs render inline as
// ``key=value`` chips.
function fmtVal(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(3);
  if (typeof v === "boolean") return v ? "true" : "false";
  return String(v);
}

function fmtTime(ts: string | null | undefined): string {
  if (!ts) return "—";
  try { return new Date(ts).toLocaleTimeString(); } catch { return ts; }
}

export function SignalsPanel({ dash }: Props) {
  const rules: RuleEntry[] = Array.isArray((dash as { recent_rules?: unknown } | null)?.recent_rules)
    ? ((dash as { recent_rules: RuleEntry[] }).recent_rules)
    : [];
  const newestFirst = [...rules].reverse();

  return (
    <div className="card signals-panel">
      <h2 className="card-title">
        Cadence signals
        <span style={{ fontSize: 11, color: "var(--color-text-tertiary)", fontWeight: 400 }}>
          recent rule firings · dashboard.json::recent_rules
        </span>
      </h2>
      {newestFirst.length === 0 ? (
        <div className="kv-val muted" style={{ padding: "8px 0" }}>
          No cadence rules have fired yet — the first round-end emit lands here.
        </div>
      ) : (
        <div className="signals-list">
          {newestFirst.map((r, i) => {
            const inputs = r.signal_inputs || {};
            return (
              <div key={`${r.timestamp ?? ""}_${i}`} className="signal-row">
                <div className="signal-row-head">
                  <span className="signal-rule">{r.rule_name ?? "?"}</span>
                  <span className="signal-layer">{r.layer ?? "?"}</span>
                  <span className="signal-action">→ {r.next_action ?? "?"}</span>
                  <span className="signal-meta">
                    r{r.round ?? "—"} · {fmtTime(r.timestamp)}
                  </span>
                </div>
                {r.reason && <div className="signal-reason">{r.reason}</div>}
                {Object.keys(inputs).length > 0 && (
                  <div className="signal-inputs">
                    {Object.entries(inputs).map(([k, v]) => (
                      <span key={k} className="signal-chip" title={`${k}=${fmtVal(v)}`}>
                        {k.replace(/_/g, " ")} <b>{fmtVal(v)}</b>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
