"use client";
import { memo, useMemo } from "react";
import { Line } from "react-chartjs-2";
import { ensureChartRegistered, lineChartDefaults } from "@/lib/chart-config";
import { cssRgba, getCss, useThemeVersion } from "@/lib/theme";
import type { DashboardSnapshot } from "@/lib/poll";
import { CardFrame } from "@/components/ui/Card";

ensureChartRegistered();

interface Point {
  round: number;
  composite: number;
}

interface Props {
  dash: DashboardSnapshot | null;
}

export const TrendChart = memo(function TrendChart({ dash }: Props) {
  // Subscribe to theme so a flip re-runs this component and pulls fresh
  // getCss() values into the chart data/options below.
  useThemeVersion();
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
