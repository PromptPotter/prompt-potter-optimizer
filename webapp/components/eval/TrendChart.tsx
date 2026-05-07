"use client";
import { useEffect, useState } from "react";
import { Line } from "react-chartjs-2";
import { ensureChartRegistered } from "@/lib/chart-init";
import { fetchCycleFile, fetchFiles } from "@/lib/api";
import { cssRgba, getCss } from "@/lib/theme";
import type { ChartOptions } from "chart.js";

ensureChartRegistered();

interface Point {
  round: number;
  composite: number;
}

interface Props {
  cycleId: string | null;
  refreshKey: number; // bumped each new round
  themeKey: string;
}

export function TrendChart({ cycleId, refreshKey, themeKey }: Props) {
  const [points, setPoints] = useState<Point[]>([]);

  useEffect(() => {
    if (!cycleId) return;
    let cancelled = false;
    (async () => {
      try {
        const listing = await fetchFiles(cycleId);
        const rounds = listing.entries
          .filter((e) => e.scope === "cycle" && /^rounds\/round_\d+\.json$/.test(e.path))
          .sort((a, b) => a.path.localeCompare(b.path));
        if (!rounds.length) {
          if (!cancelled) setPoints([]);
          return;
        }
        const fetched = await Promise.all(
          rounds.map(async (f) => {
            try {
              const r = await fetchCycleFile(cycleId, "cycle", f.path);
              const j = JSON.parse(r.content);
              return { round: j.round as number, composite: (j.composite_fitness ?? j.accuracy ?? 0) as number };
            } catch {
              return null;
            }
          }),
        );
        const valid = fetched.filter((p): p is Point => p !== null).sort((a, b) => a.round - b.round);
        if (!cancelled) setPoints(valid);
      } catch {
        /* ignore */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cycleId, refreshKey]);

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
