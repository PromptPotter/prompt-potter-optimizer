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
import { metricInkToken } from "@/components/candidates/series";
import { degradedRoundNotices, fitnessTrend } from "@/lib/derivations";
import { useDashboard } from "@/lib/hooks/useDashboard";

ensureChartRegistered();

export const TrendChart = memo(function TrendChart() {
  const { dash } = useDashboard();
  // Subscribe to theme so a flip re-runs this component and pulls fresh
  // getCss() values into the chart data/options below.
  useThemeVersion();
  const { points, best: bestData } = useMemo(
    () => fitnessTrend(dash?.rounds, dash?.best),
    [dash?.rounds, dash?.best],
  );
  const curData = points.map((p) => p.composite);
  const thetaData = points.map((p) => p.theta);
  // Silent until the ruler warms — a flat ruler makes θ logit-accuracy on each round's own
  // subset, which is the very thing this axis exists to be independent of.
  const hasTheta = thetaData.some((t) => typeof t === "number");
  const labels = points.map((p) => String(p.round));
  // Quiet amber notices for rounds the backend graded `degraded` — the webapp
  // twin of the CLI's yellow degraded line. `critical` stays on the loud banner.
  const degraded = degradedRoundNotices(dash);

  // Ink by ROLE, from the ONE declaration the candidates card reads — the metric the engine
  // elects on is the loud line here too, so the operator learns each channel's colour once.
  const elected = dash?.headline_metric ?? "accuracy";
  const accuracyInk = getCss(metricInkToken("accuracy", elected));
  // Two axes, deliberately. `y` is accuracy, pinned to 0..1 and unlabelled. `theta` is a LOGIT —
  // unbounded, signed, and the series the round is actually won on — so it cannot share that
  // scale and gets its own VISIBLE axis on the right. Drawing it against 0..1 would clip every
  // negative ability to the floor and read as a run that never started.
  //
  // Best-so-far and this-round are one metric, so they share one ink and differ by FILL — the
  // envelope-vs-line device, not a second hue.
  const data = {
    labels,
    datasets: [
      { data: bestData, borderColor: accuracyInk, backgroundColor: cssRgba("--color-accent-rgb", 0.08), tension: 0.3, pointRadius: 2, fill: true, borderWidth: 1.5, yAxisID: "y", label: "best accuracy" },
      { data: curData, borderColor: accuracyInk, tension: 0.3, pointRadius: 2, borderWidth: 1.5, yAxisID: "y", label: "round accuracy" },
      ...(hasTheta
        ? [{ data: thetaData, borderColor: getCss(metricInkToken("ability", elected)), borderDash: [4, 3], tension: 0.3, pointRadius: 2, borderWidth: 1.5, yAxisID: "theta", label: "ability θ", spanGaps: true }]
        : []),
    ],
  };
  const options = lineChartDefaults({
    plugins: {
      legend: { display: hasTheta, labels: { boxWidth: 10, font: { size: 10 } } },
      tooltip: {
        callbacks: {
          afterBody: (items: { dataIndex: number }[]) => {
            const n = points[items[0]?.dataIndex ?? -1]?.n;
            // The count the accuracy was measured over — the other half of the honest fix,
            // since under `per_round_resubset` two rounds' accuracies sat different exams.
            return typeof n === "number" ? `n = ${n}` : "";
          },
        },
      },
    },
    scales: {
      x: { display: false },
      y: { display: false, min: 0, max: 1 },
      theta: { display: hasTheta, position: "right" as const, grid: { display: false }, ticks: { font: { size: 9 } } },
    },
  });

  return (
    <CardFrame title={<span>Trend</span>} actions={<span className="badge">campaign</span>}>
      <div style={{ position: "relative", height: 140 }}>
        {points.length === 0 ? (
          <div style={{ color: "var(--color-text-tertiary)", fontSize: "var(--text-sm)", padding: 16 }}>
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
