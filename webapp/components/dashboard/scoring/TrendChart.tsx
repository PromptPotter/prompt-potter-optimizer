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
import { Badge, CardFrame, Said } from "@/components/ui";
import { metricInkToken } from "@/components/candidates/series";
import { HEALTH_GRADE_INFO } from "@/lib/api/types.generated";
import { fitnessTrend } from "@/lib/derivations";
import { useCycleStream } from "@/lib/poll";
import { useMediaQuery } from "@/lib/hooks/useMediaQuery";

ensureChartRegistered();

export const TrendChart = memo(function TrendChart() {
  const { dash, isLive } = useCycleStream();
  const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  useThemeVersion();
  const { points } = useMemo(
    () => fitnessTrend(dash?.rounds),
    [dash?.rounds],
  );
  const curData = points.map((p) => p.composite);
  const benchData = points.map((p) => p.bench);
  const thetaData = points.map((p) => p.theta);
  const hasBench = benchData.some((b) => typeof b === "number");
  const hasTheta = thetaData.some((t) => typeof t === "number");
  const labels = points.map((p) => String(p.round));
  // A critical round is the run's pinned line (`dash.said`); a degraded one sits beside its reading.
  const degraded = (dash?.rounds ?? []).filter((r) => r.health.grade === "degraded");

  // A changed dataset id re-raises its line, which is how the live round line pulses per scored query.
  const series = [
    ...(hasBench
      ? [{ id: "bench", data: benchData, ...lineLook(getCss("--color-accent")), borderDash: [2, 3], tension: 0, pointRadius: 4, yAxisID: "y", label: "bench · held out", spanGaps: true }]
      : []),
    { id: `round:${isLive ? dash?.total_queries_scored : "still"}`, data: curData, ...lineLook(getCss("--color-accent-strong")), yAxisID: "y", label: "round composite · search split" },
    ...(hasTheta && dash
      ? [{ id: "theta", data: thetaData, ...lineLook(getCss(metricInkToken("ability", dash.display_metric)), true), yAxisID: "theta", label: "ability θ · potter's election", spanGaps: true }]
      : []),
  ];
  const data = { labels, datasets: series };
  const options = lineChartDefaults({
    animation: lineMotion(reducedMotion),
    plugins: {
      legend: { display: true, labels: { boxWidth: 10, font: { size: 10 } } },
      tooltip: {
        callbacks: {
          afterBody: (items: { dataIndex: number; datasetIndex: number }[]) => {
            const point = points[items[0]?.dataIndex ?? -1];
            const lines = point ? [`n = ${point.n} on the search split`] : [];
            // The bench series is dataset 0 whenever it draws.
            if (point?.benchN != null && hasBench && items.some((i) => i.datasetIndex === 0)) {
              lines.push(`bench: ${point.benchN} held-out rows`);
            }
            return lines;
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
  const ariaLabel = `Round composite on the search split per round${
    hasBench ? ", with the bench score of each graded selection on held-out rows" : ""
  }${hasTheta ? ", with ability θ" : ""}`;

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
          {degraded.map(({ round, health: { grade, reading } }) =>
            reading ? (
              <li key={round}>
                <Said tone={HEALTH_GRADE_INFO[grade].tone} label={reading.title} detail={reading.advice} />
              </li>
            ) : null,
          )}
        </ul>
      )}
    </CardFrame>
  );
});
