"use client";
import { memo, useMemo } from "react";
import { Line } from "react-chartjs-2";
import {
  cssRgba,
  ensureChartRegistered,
  getCss,
  lineChartDefaults,
  useThemeVersion,
} from "@/lib/theme";
import { CardFrame } from "@/components/ui";
import { degradedRoundNotices } from "@/lib/derivations";
import { useDashboard } from "@/lib/hooks/useDashboard";

ensureChartRegistered();

interface Point {
  round: number;
  composite: number;
}

export const TrendChart = memo(function TrendChart() {
  const { dash } = useDashboard();
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
  // Quiet amber notices for rounds the backend graded `degraded` — the webapp
  // twin of the CLI's yellow degraded line. `critical` stays on the loud banner.
  const degraded = degradedRoundNotices(dash);

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
      {degraded.length > 0 && (
        <ul className="trend-degraded" aria-label="Degraded rounds">
          {degraded.map((d) => (
            <li key={d.round} className="trend-degraded-row" title={d.detail}>
              <span className="trend-degraded-dot" aria-hidden="true">
                ●
              </span>
              <span className="trend-degraded-label">
                {d.round === 0 ? "Origin" : `R${d.round}`} degraded
              </span>
              <span className="trend-degraded-detail">{d.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </CardFrame>
  );
});
