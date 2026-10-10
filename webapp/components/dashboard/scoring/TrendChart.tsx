"use client";
import { memo, useMemo } from "react";
import { Line } from "react-chartjs-2";
import {
  ensureChartRegistered,
  getCss,
  lineChartDefaults,
  lineLook,
  lineMotion,
  useThemeVersion,
} from "@/lib/theme";
import { Badge, CardFrame } from "@/components/ui";
import { metricInkToken } from "@/components/candidates/series";
import { degradedRoundNotices, fitnessTrend } from "@/lib/derivations";
import { useCycleStream } from "@/lib/poll";
import { useMediaQuery } from "@/lib/hooks/useMediaQuery";

ensureChartRegistered();

type Props = { density?: "full" } | { density: "glyph"; onOpen: () => void };

export const TrendChart = memo(function TrendChart(props: Props) {
  const glyph = props.density === "glyph";
  const { dash, isLive } = useCycleStream();
  const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  useThemeVersion();
  const bench = dash?.bench_score;
  const { points } = useMemo(() => fitnessTrend(dash?.rounds), [dash?.rounds]);
  const curData = points.map((p) => p.composite);
  const benchData = points.map((p) => p.bench);
  const thetaData = points.map((p) => p.theta);
  const hasBench = benchData.some((b) => typeof b === "number");
  const hasTheta = thetaData.some((t) => typeof t === "number");
  const labels = points.map((p) => String(p.round));
  const degraded = degradedRoundNotices(dash?.rounds);

  // A changed dataset id re-raises its line, which is how the live round line pulses per scored query.
  const series = [
    ...(hasBench
      ? [{ id: "bench", data: benchData, ...lineLook(getCss("--color-accent")), borderDash: [2, 3], tension: 0, pointRadius: 4, yAxisID: "y", label: "bench · held out", spanGaps: true }]
      : []),
    { id: `round:${isLive ? dash?.total_queries_scored : "still"}`, data: curData, ...lineLook(getCss("--color-accent-strong")), yAxisID: "y", label: "round composite · search pool" },
    ...(hasTheta && dash
      ? [{ id: "theta", data: thetaData, ...lineLook(getCss(metricInkToken("ability", dash.display_metric)), true), yAxisID: "theta", label: "ability θ · potter's election", spanGaps: true }]
      : []),
  ];
  const data = {
    labels,
    datasets: glyph ? series.map((s) => ({ ...s, pointRadius: 0 })) : series,
  };
  const options = lineChartDefaults({
    animation: lineMotion(reducedMotion),
    ...(glyph ? { events: [] } : {}),
    plugins: {
      legend: { display: !glyph, labels: { boxWidth: 10, font: { size: 10 } } },
      tooltip: {
        enabled: !glyph,
        callbacks: {
          afterBody: (items: { dataIndex: number; datasetIndex: number }[]) => {
            const n = points[items[0]?.dataIndex ?? -1]?.n;
            const lines = typeof n === "number" ? [`n = ${n} on the search pool`] : [];
            // The bench series is dataset 0 whenever it draws.
            if (bench && hasBench && items.some((i) => i.datasetIndex === 0)) {
              lines.push(`bench: ${bench.bench_size} held-out rows`);
            }
            return lines;
          },
        },
      },
    },
    scales: {
      x: { display: false },
      y: { display: false, min: 0, max: 1 },
      theta: { display: hasTheta && !glyph, position: "right" as const, grid: { display: false }, ticks: { font: { size: 9 } } },
    },
  });
  const ariaLabel = `Round composite on the search pool per round${
    hasBench ? ", with the bench score of each graded selection on held-out rows" : ""
  }${hasTheta ? ", with ability θ" : ""}`;

  if (props.density === "glyph") {
    // One round is a level, not a trend, and a pointless line draws nothing.
    if (points.length < 2) return null;
    return (
      <button
        type="button"
        className="trend-glyph"
        onClick={props.onOpen}
        aria-label={`Trend — open the dashboard. ${ariaLabel}`}
        title="Trend — open the dashboard"
      >
        <Line datasetIdKey="id" data={data} options={options} aria-hidden="true" />
      </button>
    );
  }

  return (
    <CardFrame title={<span>Trend</span>} actions={<Badge>campaign</Badge>}>
      <div style={{ position: "relative", height: 140 }}>
        {points.length === 0 ? (
          <div style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)", padding: "var(--space-16)" }}>
            Trend builds up as rounds finish. Each completed round adds a point.
          </div>
        ) : (
          <Line datasetIdKey="id" data={data} options={options} aria-label={ariaLabel} role="img" />
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
