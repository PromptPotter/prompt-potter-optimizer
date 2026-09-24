"use client";
import { useMemo, useRef } from "react";
import { Bar } from "react-chartjs-2";
import { barChartDefaults, ensureChartRegistered, getCss, useThemeVersion } from "@/lib/theme";
import { TERMS } from "@/lib/terms";
import {
  liveL1Candidates,
  type DashboardSnapshot,
} from "@/lib/poll";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useEffectiveRound } from "@/lib/hooks/useEffectiveRound";
import { useRoundRows } from "@/lib/hooks/useRoundRows";
import { Badge, CardFrame, Term } from "@/components/ui";
import type { RawResultRow } from "@/lib/types";

ensureChartRegistered();

// `rescore_results` floors error rows to 0.0, so `fitness` is always served.
type ResultRow = Pick<RawResultRow, "fitness">;

const LABELS = ["0", "", "", "", "", "", "", "", "", "1"];

function bucketScores(results: ResultRow[]): number[] {
  const buckets = new Array<number>(10).fill(0);
  results.forEach((r) => {
    const score = typeof r.fitness === "number" ? r.fitness : 0;
    const idx = Math.min(9, Math.max(0, Math.floor(score * 9.999)));
    buckets[idx] = (buckets[idx] ?? 0) + 1;
  });
  return buckets;
}

// The live row carries a verdict, not a fitness, so only the two graded marks bucket.
function liveResultsFrom(dash: DashboardSnapshot | null): ResultRow[] {
  const out: ResultRow[] = [];
  for (const c of liveL1Candidates(dash)) {
    for (const s of c.samples ?? []) {
      if (s.status === "HIT") out.push({ fitness: 1 });
      else if (s.status === "MISS") out.push({ fitness: 0 });
    }
  }
  return out;
}

export function FreqChart() {
  useThemeVersion();
  const chartRef = useRef(null);
  const { dash } = useDashboard();
  const { round: effectiveRound, isLiveView } = useEffectiveRound();

  // No stitch: `useRoundRows` idles the round-file fetch on the live round.
  const { live, doc: roundDoc } = useRoundRows(effectiveRound);

  const results: ResultRow[] = useMemo(() => {
    if (live) return liveResultsFrom(dash);
    return (roundDoc?.results as ResultRow[] | undefined) ?? [];
  }, [live, dash, roundDoc]);

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
        {/* A canvas has no text, so the name IS the whole reading for anyone not looking at it. */}
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
