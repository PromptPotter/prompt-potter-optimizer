"use client";
import { useRef } from "react";
import { Bar } from "react-chartjs-2";
import { barChartDefaults, ensureChartRegistered, getCss, useThemeVersion } from "@/lib/theme";
import { TERMS } from "@/lib/terms";
import { useCycleStream } from "@/lib/poll";
import { useObserveSubject } from "@/lib/hooks/useObserveSubject";
import { useRound } from "@/lib/hooks/useRound";
import { useWorkspace } from "@/lib/workspace";
import { Badge, CardFrame, Term } from "@/components/ui";

ensureChartRegistered();

const NO_BANDS: number[] = [];

export function FreqChart() {
  useThemeVersion();
  const chartRef = useRef(null);
  const { dash } = useCycleStream();
  const { round: effectiveRound, live: isLiveView } = useObserveSubject();

  const { viewedPath } = useWorkspace();
  const { unfiled, doc: roundDoc } = useRound(viewedPath, effectiveRound);

  const data = (unfiled ? dash?.current_round.fitness_bands : roundDoc?.fitness_bands) ?? NO_BANDS;
  const accStrong = getCss("--color-accent-strong");
  const acc = getCss("--color-accent");
  const colors = data.map((_, i) => (i < data.length / 2 ? accStrong : acc));

  const chartData = {
    labels: data.map((_, i) => (i === 0 ? "0" : i === data.length - 1 ? "1" : "")),
    datasets: [{ data, backgroundColor: colors, borderRadius: 2 }],
  };
  const options = barChartDefaults({
    plugins: { legend: { display: false } },
    scales: { x: { display: false }, y: { display: false } },
  });

  return (
    <CardFrame
      title={<Term content={TERMS.stub_score_freq}>Score Frequency</Term>}
      actions={<Badge>{isLiveView ? "live" : `R${effectiveRound}`}</Badge>}
    >
      <div style={{ position: "relative", height: 140 }}>
        <Bar
          ref={chartRef}
          data={chartData}
          options={options}
          aria-label={`Per-cell fitness for ${
            isLiveView ? "the live round" : `round ${effectiveRound}`
          }, in ten bands from 0 to 1 — how many cells landed in each.`}
        />
      </div>
    </CardFrame>
  );
}
