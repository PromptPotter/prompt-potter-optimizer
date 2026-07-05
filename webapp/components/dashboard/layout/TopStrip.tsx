"use client";
import { memo, useEffect, useMemo, useRef } from "react";
import { TERMS } from "@/lib/terms";
import { cx } from "@/lib/cx";
import { type DashboardSnapshot } from "@/lib/poll";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { runPhaseLabel } from "@/lib/run-phase";
import { activeNodeId } from "@/components/workflow";
import { headlineStats, fitnessTrend } from "@/lib/derivations";
import { fmtSecs, fmtPct0 } from "@/lib/format";
import { HeartIcon } from "./HeartIcon";

// Single-line, frameless run summary. Everything the operator scans in
// the first second sits on one inline row, separated by hairline dividers:
//
//   Best 42% ╱╲╱╲ │ scoring R3 │ 24/40 · 60% · 1.2 q/s │ Last 0.8s
//
// No card chrome — it reads as a status line, not a panel.

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

// Rolling qps — EMA over the n/total progress string.
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

export const TopStrip = memo(function TopStrip() {
  // Self-sourced from the cycle stream — no props threaded through the frame.
  const { dash, dashRound, isLive } = useDashboard();
  // Sparkline: running-best composite over rounds, read from the
  // dashboard's per-round summary block.
  const spark = useMemo(() => {
    const { points, best: ys } = fitnessTrend(dash?.rounds);
    if (points.length < 2) return null;
    const W = 120;
    const H = 26;
    const xs = points.map((_, i) => (i / (points.length - 1)) * W);
    const maxY = Math.max(...ys, 0.01);
    const toY = (v: number) => H - 2 - (v / maxY) * (H - 4);
    const path = xs.map((x, i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${toY(ys[i]).toFixed(1)}`).join("");
    const area = `${path} L${xs[xs.length - 1].toFixed(1)},${H} L0,${H} Z`;
    return { path, area, W, H };
  }, [dash?.rounds]);

  // Rolling qps — ref pattern lifted from ProgressCard. EMA across
  // dashboard ticks; reading qpsRef.current.qps during render is intended.
  const qpsRef = useRef<QpsState>({ lastQ: null, lastT: null, qps: null });
  useEffect(() => {
    estimateQps(qpsRef.current, dash);
  }, [dash]);
  const qps = qpsRef.current.qps;

  const { best } = headlineStats(dash);
  const lastQuery = dash?.last_query_elapsed_s ?? null;
  const phase = runPhaseLabel(dash?.run_phase, dash?.stop_reason);
  const round = dashRound != null ? `R${dashRound}` : "—";
  // Banked lives ("hearts") — shown as pips beside the round when the run is in
  // improvement-banked-budget mode; `null`/undefined ⇒ the round counter alone.
  const hearts = dash?.hearts ?? null;
  // Current candidate being scored ("C3.2"), shown beside the round while the
  // active node is the scorer — the finer position than the round alone.
  // `dash.candidate` is "C3.2/4" and stale between rounds; gate on scoring.
  const scoringCand =
    activeNodeId(dash?.in_flight?.node ?? null, dash?.state) === "l1_score"
      ? String(dash?.candidate || "").split("/")[0]
      : "";
  const qm = parseProgress((dash as { query?: string } | null)?.query);
  const qPct = qm && qm.tot ? Math.min(100, Math.round((qm.cur / qm.tot) * 100)) : 0;
  const qpsTxt = qps != null ? `${qps.toFixed(qps < 10 ? 2 : 1)} q/s` : null;
  const phaseTip = TERMS[`phase_${phase.toLowerCase()}`] ?? "";
  // The tag is success-green by default; recolour it when the phase contradicts
  // that — a crash terminal reads danger, a gone-silent run reads warn. Composed
  // from the two orthogonal signals (server `run_phase` + this connection's
  // `isLive`), never a client-synthesized "detached" phase value (I6).
  // A clean terminal (target hit / max rounds, no error record) stays green.
  const phaseErr = dash?.run_phase === "terminal" && !!dash?.error;
  const phaseWarn = dash?.run_phase === "running" && !isLive;

  return (
    <div className="topstrip">
      <span className="topstrip-best">
        <span className="topstrip-label">Best</span>
        <span className="topstrip-best-val">
          {fmtPct0(best)}
        </span>
        {spark && (
          <svg className="topstrip-spark" viewBox={`0 0 ${spark.W} ${spark.H}`} aria-hidden="true">
            <path className="area" d={spark.area} />
            <path className="line" d={spark.path} />
          </svg>
        )}
      </span>
      <span className="topstrip-sep" aria-hidden="true" />
      <span className="topstrip-phase">
        <span className={cx("phase-tag", phaseErr && "is-error", phaseWarn && "is-warn")} title={phaseTip}>{phase}</span>
        {/* Candidate ("C2.3") already encodes the round, so show it instead of
            "R{n}" while scoring; round otherwise. */}
        {scoringCand ? (
          <span className="topstrip-cand">{scoringCand}</span>
        ) : (
          <span className="topstrip-round">{round}</span>
        )}
        {hearts != null && (
          <span
            className="topstrip-hearts"
            title={`${hearts} ${hearts === 1 ? "life" : "lives"} left`}
            aria-label={`${hearts} lives left`}
          >
            {hearts > 0 ? (
              Array.from({ length: hearts }, (_, i) => <HeartIcon key={i} filled />)
            ) : (
              <HeartIcon filled={false} />
            )}
          </span>
        )}
      </span>
      <span className="topstrip-sep" aria-hidden="true" />
      <span className="topstrip-prog">
        {qm ? `${qm.cur}/${qm.tot} · ${qPct}%` : "—"}
        {qpsTxt ? <span className="topstrip-qps"> · {qpsTxt}</span> : null}
      </span>
      <span className="topstrip-sep" aria-hidden="true" />
      <span className="topstrip-last">
        <span className="topstrip-label">Last</span>
        <span className="topstrip-counter-val">{fmtSecs(lastQuery)}</span>
      </span>
      {dash?.langfuse_trace_url && (
        <>
          <span className="topstrip-sep" aria-hidden="true" />
          <a
            className="topstrip-trace"
            href={dash.langfuse_trace_url}
            target="_blank"
            rel="noreferrer"
            title="Open this cycle's full nested trace in Langfuse"
          >
            Trace ↗
          </a>
        </>
      )}
    </div>
  );
});
