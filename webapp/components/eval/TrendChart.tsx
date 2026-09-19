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
import { Badge, CardFrame } from "@/components/ui";
import { metricInkToken } from "@/components/candidates/series";
import { degradedRoundNotices, fitnessTrend } from "@/lib/derivations";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useMediaQuery } from "@/lib/hooks/useMediaQuery";

ensureChartRegistered();

// `compact` is a DENSITY: the same three series, without the legend, the θ ticks or the
// degraded-rounds list.
export const TrendChart = memo(function TrendChart({ compact = false }: { compact?: boolean }) {
  const { dash, isLive } = useDashboard();
  const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
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

  // θ takes its ink by ROLE, from the ONE declaration the candidates card reads, so the operator
  // learns that channel's colour once.
  const elected = dash?.headline_metric ?? "accuracy";
  // Two axes, deliberately. `y` is accuracy, pinned to 0..1 and unlabelled. `theta` is a LOGIT —
  // unbounded, signed, and the series the round is actually won on — so it cannot share that
  // scale and gets its own VISIBLE axis on the right. Drawing it against 0..1 would clip every
  // negative ability to the floor and read as a run that never started.
  //
  // Accuracy takes two accent shades: amber for the best-so-far envelope, red-orange for
  // the live round line.
  //
  // A changed dataset id makes Chart.js build that line anew and raise it from the baseline.
  // The round line's id moves with every scored sample, so it pulses while a run writes; the
  // best line keeps its id, so a closed round's new point flies in on it.
  const data = {
    labels,
    datasets: [
      { id: "best", data: bestData, borderColor: getCss("--color-accent"), backgroundColor: cssRgba("--color-accent-rgb", 0.08), tension: 0.3, pointRadius: 2, fill: true, borderWidth: 1.5, yAxisID: "y", label: "best accuracy" },
      { id: `round:${isLive ? dash?.total_queries_scored : "still"}`, data: curData, borderColor: getCss("--color-accent-strong"), tension: 0.3, pointRadius: 2, borderWidth: 1.5, yAxisID: "y", label: "round accuracy" },
      ...(hasTheta
        ? [{ id: "theta", data: thetaData, borderColor: getCss(metricInkToken("ability", elected)), borderDash: [4, 3], tension: 0.3, pointRadius: 2, borderWidth: 1.5, yAxisID: "theta", label: "ability θ", spanGaps: true }]
        : []),
    ],
  };
  const options = lineChartDefaults({
    animation: reducedMotion ? false : { duration: 1000, easing: "easeOutQuart" },
    plugins: {
      legend: { display: hasTheta && !compact, labels: { boxWidth: 10, font: { size: 10 } } },
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
      theta: { display: hasTheta && !compact, position: "right" as const, grid: { display: false }, ticks: { font: { size: 9 } } },
    },
  });
  const ariaLabel = `Best and round accuracy per round${hasTheta ? ", with ability θ" : ""}`;

  return (
    <CardFrame title={<span>Trend</span>} actions={<Badge>campaign</Badge>}>
      <div style={{ position: "relative", height: compact ? 64 : 140 }}>
        {points.length === 0 ? (
          <div style={{ color: "var(--color-text-tertiary)", fontSize: "var(--text-sm)", padding: "var(--space-16)" }}>
            Trend builds up as rounds finish. Each completed round adds a point.
          </div>
        ) : (
          <Line datasetIdKey="id" data={data} options={options} aria-label={ariaLabel} role="img" />
        )}
      </div>
      {!compact && degraded.length > 0 && (
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
