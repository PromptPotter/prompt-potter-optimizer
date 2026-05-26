"use client";
import { memo, useMemo } from "react";
import { Line } from "react-chartjs-2";
import { ensureChartRegistered, lineChartDefaults } from "@/lib/chart-config";
import { cssRgba, getCss } from "@/lib/theme";
import type { DashboardSnapshot } from "@/lib/poll";
import { CardFrame } from "@/components/ui/card";

ensureChartRegistered();

interface Point {
  round: number;
  composite: number;
}

interface Props {
  dash: DashboardSnapshot | null;
  themeKey: string;
}

export const TrendChart = memo(function TrendChart({ dash, themeKey }: Props) {
  // themeKey isn't read inside the body — it's a memo-bust prop. When the
  // operator flips the theme, DashboardPane bumps themeKey, which trips
  // memo gates so memo'd chart components re-render and pick up the new
  // CSS-var colours. Without this reference the unused-vars rule would
  // flag it; the prop must stay or the memo gate eats theme changes.
  void themeKey;
  const points: Point[] = useMemo(() => {
    const out: Point[] = [];
    for (const r of dash?.rounds ?? []) {
      out.push({ round: r.round, composite: r.composite_fitness || r.accuracy });
    }
    out.sort((a, b) => a.round - b.round);
    return out;
  }, [dash?.rounds]);

  let runningBest = 0;
  const bestData = points.map((p) => {
    runningBest = Math.max(runningBest, p.composite);
    return runningBest;
  });
  const curData = points.map((p) => p.composite);
  const labels = points.map((p) => String(p.round));

  const data = {
    labels,
    datasets: [
      { data: bestData, borderColor: getCss("--color-accent"), backgroundColor: cssRgba("--color-accent-rgb", 0.08), tension: 0.3, pointRadius: 2, fill: true, borderWidth: 1.5 },
      { data: curData, borderColor: getCss("--color-accent-strong"), tension: 0.3, pointRadius: 2, borderWidth: 1.5 },
    ],
  };
  const options = lineChartDefaults({
    plugins: { legend: { display: false } },
    scales: { x: { display: false }, y: { display: false, min: 0, max: 1 } },
  });

  // No `key={themeKey}` remount: data/options are rebuilt inline each
  // render (no useMemo), so they pull fresh CSS-var colours via getCss()
  // every time. Chart.js then diffs them into the live canvas instead of
  // the previous teardown-and-recreate cost on every theme swap.
  return (
    <CardFrame title={<span>Trend</span>} actions={<span className="badge">campaign</span>}>
      <div style={{ position: "relative", height: 140 }}>
        {points.length === 0 ? (
          <div style={{ color: "var(--color-text-tertiary)", fontSize: 12, padding: 16 }}>
            Trend builds up as rounds finish. Each completed round adds a point.
          </div>
        ) : (
          <Line data={data} options={options} />
        )}
      </div>
    </CardFrame>
  );
});
