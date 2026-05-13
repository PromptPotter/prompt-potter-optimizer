"use client";
import { useEffect, useRef } from "react";
import { TERMS } from "@/lib/terms";
import type { DashboardSnapshot } from "@/lib/poll";

interface Props {
  dash: DashboardSnapshot | null;
  dashRound: number | null;
}

interface QpsState {
  lastQ: number | null;
  lastT: number | null;
  qps: number | null;
}

// Rolling QPS estimator — compares the last two dashboard ticks' query
// counters. Lifted from vanilla webapp/index.html:1521.
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

function parseProgress(raw: string | undefined, anchor: "q" | "c"): { cur: number; tot: number } | null {
  if (!raw) return null;
  const re = anchor === "q" ? /^(\d+)\/(\d+)$/ : /^[Cc]?(\d+)\/(\d+)$/;
  const m = String(raw).match(re);
  if (!m) return null;
  return { cur: parseInt(m[1], 10), tot: parseInt(m[2], 10) };
}

export function ProgressCard({ dash, dashRound }: Props) {
  // Persist the QPS state across renders so the moving average actually
  // accumulates — without the ref, every parent re-render resets it.
  const qpsRef = useRef<QpsState>({ lastQ: null, lastT: null, qps: null });
  useEffect(() => {
    estimateQps(qpsRef.current, dash);
  }, [dash]);

  const phase = (dash?.state as string | undefined) ?? "—";
  const round = dashRound != null ? String(dashRound) : "—";
  const qm = parseProgress((dash as { query?: string } | null)?.query, "q");
  const cm = parseProgress((dash as { candidate?: string } | null)?.candidate, "c");

  const qPct = qm && qm.tot ? Math.min(100, Math.round((qm.cur / qm.tot) * 100)) : 0;
  const cPct = cm && cm.tot ? Math.min(100, Math.round((cm.cur / cm.tot) * 100)) : 0;
  const pobb = dash?.current_round?.pobb;
  const leader = pobb?.leader_prob ?? null;
  const width = pobb?.posterior_width ?? null;
  // Confidence bar tracks leader probability (0..1) — fills as posterior tightens.
  const confPct = leader != null ? Math.min(100, Math.round(leader * 100)) : 0;
  const widthTxt = width != null ? width.toFixed(3) : "—";
  const nSamp = pobb?.n_samples ?? 0;
  const qps = qpsRef.current.qps;
  const qpsTxt = qps != null ? ` · ${qps.toFixed(qps < 10 ? 2 : 1)} q/s` : "";
  const inflight = phase === "scoring" ? " ⟳" : "";
  const phaseTip = TERMS[`phase_${phase.toLowerCase()}`] ?? "";

  return (
    <div className="card progress-card">
      <div className="progress-row">
        <div className="progress-label">
          <span className="phase-tag" title={phaseTip}>{phase}</span>
          <span> round {round}</span>
        </div>
        <div className="progress-bar-wrap">
          <div
            className={`progress-bar-fill${qPct >= 100 ? " complete" : ""}${qm ? "" : " empty"}`}
            style={{ width: `${qPct}%` }}
          />
        </div>
        <div className="progress-stats">
          {qm ? `${qm.cur}/${qm.tot} · ${qPct}%${qpsTxt}${inflight}` : "queries —"}
        </div>
      </div>
      <div className="progress-row">
        <div className="progress-label">candidate</div>
        <div className="progress-bar-wrap">
          <div
            className={`progress-bar-fill${cPct >= 100 ? " complete" : ""}${cm ? "" : " empty"}`}
            style={{ width: `${cPct}%` }}
          />
        </div>
        <div className="progress-stats">
          {cm ? `${cm.cur}/${cm.tot} candidates · ${cPct}%` : "cand —"}
        </div>
      </div>
      <div className="progress-row" title="P(best) leader probability — bar fills as the round's PoBB posterior tightens around one candidate">
        <div className="progress-label">P(best)</div>
        <div className="progress-bar-wrap">
          <div
            className={`progress-bar-fill${confPct >= 95 ? " complete" : ""}${pobb ? "" : " empty"}`}
            style={{ width: `${confPct}%` }}
          />
        </div>
        <div className="progress-stats">
          {pobb ? `width ${widthTxt} @ q${nSamp}` : "P(best) —"}
        </div>
      </div>
    </div>
  );
}
