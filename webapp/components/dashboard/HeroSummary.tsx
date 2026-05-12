"use client";
import { useMemo } from "react";
import type { DashboardSnapshot } from "@/lib/poll";
import { useRoundHistory } from "@/lib/use-round-history";

interface Props {
  cycleId: string | null;
  dash: DashboardSnapshot | null;
  refreshKey: number;
}

interface SparkPoint {
  round: number;
  composite: number;
}

function fmtSecs(s: number | null | undefined): string {
  if (s == null) return "—";
  if (s < 1) return `${(s * 1000).toFixed(0)}ms`;
  if (s < 60) return `${s.toFixed(2)}s`;
  return `${(s / 60).toFixed(1)}m`;
}

// One-primary-stat-with-sparkline replaces the canonical 3-stat hero trio.
// BEST gets the sparkline (page anchor); QUERIES + LAST QUERY sit inline.
export function HeroSummary({ cycleId, dash, refreshKey }: Props) {
  const docs = useRoundHistory(cycleId, refreshKey);
  const points: SparkPoint[] = useMemo(() => {
    const out: SparkPoint[] = [];
    for (const d of docs) {
      if (typeof d.round !== "number") continue;
      out.push({ round: d.round, composite: (d.composite_fitness ?? d.accuracy ?? 0) as number });
    }
    out.sort((a, b) => a.round - b.round);
    return out;
  }, [docs]);

  const best = typeof dash?.best === "number" ? dash.best : null;
  const queries = dash?.total_queries_scored ?? null;
  const last = dash?.last_query_elapsed_s ?? null;

  // Build sparkline path
  let path = "";
  let area = "";
  if (points.length >= 2) {
    const W = 160;
    const H = 34;
    let runningBest = 0;
    const ys = points.map((p) => {
      runningBest = Math.max(runningBest, p.composite);
      return runningBest;
    });
    const xs = points.map((_, i) => (points.length === 1 ? 0 : (i / (points.length - 1)) * W));
    const minY = 0;
    const maxY = Math.max(...ys, 0.01);
    const toY = (v: number) => H - 2 - ((v - minY) / (maxY - minY)) * (H - 4);
    path = xs.map((x, i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${toY(ys[i]).toFixed(1)}`).join("");
    area = `${path} L${xs[xs.length - 1].toFixed(1)},${H} L0,${H} Z`;
  }

  return (
    <div className="hero-summary">
      <div className="hero-primary">
        <span className="hero-primary-label">Best</span>
        <span className="hero-primary-val">{best != null && Number.isFinite(best) ? `${(best * 100).toFixed(0)}%` : "—"}</span>
        {points.length >= 2 && (
          <svg className="hero-spark" viewBox="0 0 160 34" aria-hidden="true">
            <path className="area" d={area} />
            <path className="line" d={path} />
          </svg>
        )}
      </div>
      <div className="hero-secondary">
        <span className="hero-secondary-label">Queries</span>
        <span className="hero-secondary-val">{queries != null ? String(queries) : "—"}</span>
      </div>
      <div className="hero-secondary">
        <span className="hero-secondary-label">Last query</span>
        <span className="hero-secondary-val">{fmtSecs(last)}</span>
      </div>
    </div>
  );
}
