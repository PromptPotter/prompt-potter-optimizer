"use client";
import { useMemo } from "react";
import { Line } from "react-chartjs-2";
import { ensureChartRegistered } from "@/lib/chart-init";
import { cssRgba, getCss } from "@/lib/theme";
import { useCycleStream } from "@/lib/poll";
import type { ChartOptions } from "chart.js";

ensureChartRegistered();

interface Point {
  round: number;
  composite: number;
}

interface Props {
  themeKey: string;
}

export function TrendChart({ themeKey }: Props) {
  const { rounds: docs } = useCycleStream();
  const points: Point[] = useMemo(() => {
    const out: Point[] = [];
    for (const d of docs) {
      if (typeof d.round !== "number") continue;
      out.push({ round: d.round, composite: (d.composite_fitness ?? d.accuracy ?? 0) as number });
    }
    out.sort((a, b) => a.round - b.round);
    return out;
  }, [docs]);

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
  const options: ChartOptions<"line"> = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: { x: { display: false }, y: { display: false, min: 0, max: 1 } },
  };

  return (
    <div className="card">
      <div className="card-title">
        <span>Trend</span>
        <span className="badge">cycle</span>
      </div>
      <div style={{ position: "relative", height: 140 }}>
        {points.length === 0 ? (
          <div style={{ color: "var(--color-text-tertiary)", fontSize: 12, padding: 16 }}>
            Trend builds up as rounds finish. Each completed round adds a point.
          </div>
        ) : (
          <Line key={themeKey} data={data} options={options} />
        )}
      </div>
    </div>
  );
}
