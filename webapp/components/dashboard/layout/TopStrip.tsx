"use client";
import { memo, useMemo } from "react";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { headlineStats, fitnessTrend } from "@/lib/derivations";
import { fmtSecs, fmtPct0 } from "@/lib/format";
import { Hearts } from "@/components/ui";

// Single-line, frameless run summary. Everything the operator scans in
// the first second sits on one inline row, separated by hairline dividers:
//
//   Best 42% ╱╲╱╲ │ ♥♥♥ │ Last 0.8s
//
// No card chrome — it reads as a status line, not a panel. The run's state
// (phase, round, the candidate being scored) is NOT duplicated here — the
// remote control and the sidebar wear it.

export const TopStrip = memo(function TopStrip() {
  // Self-sourced from the cycle stream — no props threaded through the frame.
  const { dash } = useDashboard();
  // Sparkline: running-best composite over rounds, read from the
  // dashboard's per-round summary block.
  const spark = useMemo(() => {
    const { best: ys } = fitnessTrend(dash?.rounds, dash?.best);
    if (ys.length < 2) return null;
    const W = 120;
    const H = 26;
    const maxY = Math.max(...ys, 0.01);
    const toX = (i: number) => (i / (ys.length - 1)) * W;
    const toY = (v: number) => H - 2 - (v / maxY) * (H - 4);
    const path = ys
      .map((y, i) => `${i === 0 ? "M" : "L"}${toX(i).toFixed(1)},${toY(y).toFixed(1)}`)
      .join("");
    const area = `${path} L${W},${H} L0,${H} Z`;
    return { path, area, W, H };
  }, [dash?.rounds, dash?.best]);

  const { best } = headlineStats(dash);
  const lastQuery = dash?.last_query_elapsed_s ?? null;
  // Banked lives ("hearts") — the strip's one run-state reading, and only in
  // improvement-banked-budget mode; `null`/undefined ⇒ the segment renders nothing.
  // The cap rides along as the denominator: `♥♥♥` alone can't tell 3-of-4 from 3-of-7.
  const hearts = dash?.hearts ?? null;
  const livesCap = dash?.run_limits?.lives_cap ?? null;

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
      {hearts != null && (
        <>
          <span className="topstrip-sep" aria-hidden="true" />
          <Hearts hearts={hearts} cap={livesCap} className="topstrip-hearts" />
        </>
      )}
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
