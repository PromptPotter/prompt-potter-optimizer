"use client";
import { useMemo, useRef } from "react";
import { Bar } from "react-chartjs-2";
import { barChartDefaults, ensureChartRegistered, getCss, useThemeVersion } from "@/lib/theme";
import { TERMS } from "@/lib/terms";
import { liveCandidates, useCycleStream } from "@/lib/poll";
import { useObserveSubject } from "@/lib/hooks/useObserveSubject";
import { useRound } from "@/lib/hooks/useRound";
import { useWorkspace } from "@/lib/workspace";
import { Badge, CardFrame, Term } from "@/components/ui";
import type { DashboardSample, SheetRow } from "@/lib/api/types";

ensureChartRegistered();

type ResultRow = Pick<DashboardSample | SheetRow, "status" | "fitness">;

const LABELS = ["0", "", "", "", "", "", "", "", "", "1"];

// Only graded marks bucket: `Scorer.grade` floors an errored row to 0.0, which is not a low score.
function bucketScores(results: ResultRow[]): number[] {
  const buckets = new Array<number>(10).fill(0);
  results.forEach((r) => {
    if ((r.status !== "HIT" && r.status !== "MISS") || typeof r.fitness !== "number") return;
    const idx = Math.min(9, Math.max(0, Math.floor(r.fitness * 9.999)));
    buckets[idx] = (buckets[idx] ?? 0) + 1;
  });
  return buckets;
}

export function FreqChart() {
  useThemeVersion();
  const chartRef = useRef(null);
  const { dash } = useCycleStream();
  const { round: effectiveRound, live: isLiveView } = useObserveSubject();

  const { viewedPath } = useWorkspace();
  const { unfiled, doc: roundDoc } = useRound(viewedPath, effectiveRound);

  const results: ResultRow[] = useMemo(() => {
    if (unfiled) return liveCandidates(dash).flatMap((c) => c.samples);
    return roundDoc?.results ?? [];
  }, [unfiled, dash, roundDoc]);

  const data = bucketScores(results);
  const accStrong = getCss("--color-accent-strong");
  const acc = getCss("--color-accent");
  const colors = data.map((_, i) => (i < 5 ? accStrong : acc));

  const chartData = {
    labels: LABELS,
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
