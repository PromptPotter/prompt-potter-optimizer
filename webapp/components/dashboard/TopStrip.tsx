/* eslint-disable react-hooks/refs -- qps is poll-driven in a useEffect
   below; reading the ref during render is the canonical "rolling average
   across renders" pattern this strip needs. File-level disable matches
   the prior ProgressCard convention. */
"use client";
import { useEffect, useMemo, useRef } from "react";
import { TERMS } from "@/lib/terms";
import { useCycleStream, type DashboardSnapshot } from "@/lib/poll";
import { cycleStatusLabel } from "@/lib/cycle-status";
import { fmtSecs } from "@/lib/format";

// Merged Hero + Progress card. Replaces the prior dash-hero double-card
// (HeroSummary on the right of the breadcrumb + ProgressCard underneath).
// Three columns at full width, collapses to stacked rows below 640px:
//
//   ┌──────────────────┬───────────────────────┬──────────────────┐
//   │ Best 42% ╱╲╱╲    │ scoring · R3          │ Queries 4920     │
//   │                  │ ▓▓▓░░ 24/40 60% qps   │ Last 0.8s        │
//   └──────────────────┴───────────────────────┴──────────────────┘
//
// One card, one set of paddings, one source for every stat the operator
// scans in the first second of looking at the page.

interface Props {
  dash: DashboardSnapshot | null;
  dashRound: number | null;
}

interface QpsState {
  lastQ: number | null;
  lastT: number | null;
  qps: number | null;
}

function parseProgress(raw: string | undefined): { cur: number; tot: number } | null {
  if (!raw) return null;
  const m = String(raw).match(/^(\d+)\/(\d+)$/);
  if (!m) return null;
  return { cur: parseInt(m[1], 10), tot: parseInt(m[2], 10) };
}

// Rolling qps — same EMA logic as the ex-ProgressCard, lifted verbatim
// from vanilla webapp/index.html:1521.
function estimateQps(state: QpsState, dash: DashboardSnapshot | null): number | null {
  const m = String((dash as { query?: string } | null)?.query || "").match(/^(\d+)\/(\d+)$/);
  if (!m) return state.qps;
  const q = parseInt(m[1], 10);
  const now = Date.now();
  if (state.lastQ != null && state.lastT != null && q >= state.lastQ) {
    const dq = q - state.lastQ;
    const dt = (now - state.lastT) / 1000;
    if (dt > 0.05 && dq > 0) {
      const inst = dq / dt;
      state.qps = state.qps == null ? inst : 0.6 * state.qps + 0.4 * inst;
    } else if (dq === 0 && dt > 4) {
      state.qps = 0;
    }
  }
  state.lastQ = q;
  state.lastT = now;
  return state.qps;
}

export function TopStrip({ dash, dashRound }: Props) {
  // Sparkline: running-best composite over rounds. Lifted from HeroSummary.
  const { rounds: docs } = useCycleStream();
  const spark = useMemo(() => {
    const pts: { round: number; composite: number }[] = [];
    for (const d of docs) {
      if (typeof d.round !== "number") continue;
      pts.push({ round: d.round, composite: (d.composite_fitness ?? d.accuracy ?? 0) as number });
    }
    pts.sort((a, b) => a.round - b.round);
    if (pts.length < 2) return null;
    const W = 120;
    const H = 26;
    let runningBest = 0;
    const ys = pts.map((p) => {
      runningBest = Math.max(runningBest, p.composite);
      return runningBest;
    });
    const xs = pts.map((_, i) => (i / (pts.length - 1)) * W);
    const maxY = Math.max(...ys, 0.01);
    const toY = (v: number) => H - 2 - (v / maxY) * (H - 4);
    const path = xs.map((x, i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${toY(ys[i]).toFixed(1)}`).join("");
    const area = `${path} L${xs[xs.length - 1].toFixed(1)},${H} L0,${H} Z`;
    return { path, area, W, H };
  }, [docs]);

  // Rolling qps — ref pattern lifted from ProgressCard. EMA across
  // dashboard ticks; reading qpsRef.current.qps during render is intended.
  const qpsRef = useRef<QpsState>({ lastQ: null, lastT: null, qps: null });
  useEffect(() => {
    estimateQps(qpsRef.current, dash);
  }, [dash]);
  const qps = qpsRef.current.qps;

  const best = typeof dash?.best === "number" ? dash.best : null;
  const queries = dash?.total_queries_scored ?? null;
  const lastQuery = dash?.last_query_elapsed_s ?? null;
  const phase = cycleStatusLabel(
    dash?.state as string | undefined,
    (dash as { stop_reason?: string } | null)?.stop_reason,
  );
  const round = dashRound != null ? `R${dashRound}` : "—";
  const qm = parseProgress((dash as { query?: string } | null)?.query);
  const qPct = qm && qm.tot ? Math.min(100, Math.round((qm.cur / qm.tot) * 100)) : 0;
  const qpsTxt = qps != null ? `${qps.toFixed(qps < 10 ? 2 : 1)} q/s` : null;
  const phaseTip = TERMS[`phase_${phase.toLowerCase()}`] ?? "";

  return (
    <div className="topstrip">
      <div className="topstrip-cell topstrip-best">
        <span className="topstrip-label">Best</span>
        <span className="topstrip-best-val">
          {best != null && Number.isFinite(best) ? `${(best * 100).toFixed(0)}%` : "—"}
        </span>
        {spark && (
          <svg className="topstrip-spark" viewBox={`0 0 ${spark.W} ${spark.H}`} aria-hidden="true">
            <path className="area" d={spark.area} />
            <path className="line" d={spark.path} />
          </svg>
        )}
      </div>
      <div className="topstrip-cell topstrip-progress">
        <div className="topstrip-progress-head">
          <span className="phase-tag" title={phaseTip}>{phase}</span>
          <span className="topstrip-round">{round}</span>
        </div>
        <div className="topstrip-bar-wrap">
          <div
            className={`topstrip-bar-fill${qPct >= 100 ? " complete" : ""}${qm ? "" : " empty"}`}
            style={{ width: `${qPct}%` }}
          />
        </div>
        <div className="topstrip-progress-foot">
          {qm ? `${qm.cur}/${qm.tot} · ${qPct}%` : "queries —"}
          {qpsTxt ? <span className="topstrip-qps"> · {qpsTxt}</span> : null}
        </div>
      </div>
      <div className="topstrip-cell topstrip-counters">
        <div className="topstrip-counter">
          <span className="topstrip-label">Queries</span>
          <span className="topstrip-counter-val">{queries != null ? String(queries) : "—"}</span>
        </div>
        <div className="topstrip-counter">
          <span className="topstrip-label">Last</span>
          <span className="topstrip-counter-val">{fmtSecs(lastQuery)}</span>
        </div>
      </div>
    </div>
  );
}
