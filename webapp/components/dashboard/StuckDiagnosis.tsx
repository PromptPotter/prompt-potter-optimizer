"use client";
import type { DashboardSnapshot } from "@/lib/poll";

interface Props {
  dash: DashboardSnapshot | null;
}

interface SignalSnapshot {
  round?: number | null;
  rule_name?: string | null;
  next_action?: string | null;
  signal_inputs?: Record<string, unknown>;
}

// Phase 4 — names which signal is at floor/ceiling so the operator
// reads *why* the loop is in its current state. Reads
// ``dashboard.json::current_signals`` (latest snapshot per layer: post_round).
//
// Rule-floor / ceiling diagnostics:
//   * axes_with_positive_yield = 0 (post-round) → "axis drought"
//   * l1_stall_count == l1_patience → "L1 patience exhausted, escalating"
//
// One sentence each, derived from observed numbers — no static help text.

interface Diagnosis {
  severity: "ok" | "info" | "warn";
  headline: string;
  detail?: string;
}

function diagnose(current: Record<string, SignalSnapshot>): Diagnosis | null {
  const post = current["post_round"];

  if (post?.signal_inputs) {
    const s = post.signal_inputs as Record<string, number | boolean | null>;
    const stall = Number(s.l1_stall_count ?? 0);
    const patience = Number(s.l1_patience ?? 0);
    const axes = s.axes_with_positive_yield;
    if (typeof axes === "number" && axes === 0 && stall >= 1) {
      return {
        severity: "warn",
        headline: "Axis drought — 0 axes with effect above noise",
        detail: `L1 stall ${stall}/${patience} · ${post.rule_name} fired`,
      };
    }
    if (patience > 0 && stall >= patience) {
      return {
        severity: "warn",
        headline: `L1 patience exhausted (${stall}/${patience})`,
        detail: `${post.rule_name} → ${post.next_action}`,
      };
    }
    if (stall === 0 && Boolean(s.improved)) {
      return {
        severity: "ok",
        headline: "Improving — L1 stall reset",
        detail: `${post.rule_name} (continue)`,
      };
    }
    if (stall > 0) {
      return {
        severity: "info",
        headline: `L1 stalling (${stall}/${patience})`,
        detail: `${post.rule_name} fired this round`,
      };
    }
  }

  return null;
}

export function StuckDiagnosis({ dash }: Props) {
  const current = ((dash as { current_signals?: Record<string, SignalSnapshot> } | null)?.current_signals)
    ?? {};
  const verdict = diagnose(current);

  if (!verdict) {
    return (
      <div className="card stuck-diagnosis">
        <h2 className="card-title">Diagnosis</h2>
        <div className="kv-val muted" style={{ padding: "8px 0" }}>
          Waiting for the first cadence-rule firing.
        </div>
      </div>
    );
  }

  return (
    <div className={`card stuck-diagnosis sev-${verdict.severity}`}>
      <h2 className="card-title">Diagnosis</h2>
      <div className="diagnosis-headline">{verdict.headline}</div>
      {verdict.detail && <div className="diagnosis-detail">{verdict.detail}</div>}
    </div>
  );
}
