"use client";
import { useMemo, useRef } from "react";
import { Bar } from "react-chartjs-2";
import { barChartDefaults, ensureChartRegistered, getCss, useThemeVersion } from "@/lib/theme";
import { TERMS } from "@/lib/terms";
import { parseSampleLine } from "@/lib/sample-line";
import {
  liveL1Candidates,
  type DashboardSnapshot,
} from "@/lib/poll";
import { useWorkspace } from "@/lib/workspace";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useEffectiveRound } from "@/lib/hooks/useEffectiveRound";
import { useRoundSource } from "@/lib/hooks/useRoundSource";
import { CardFrame } from "@/components/ui";
import type { RawResultRow } from "@/lib/types";

ensureChartRegistered();

// Local alias kept to the one field FreqChart buckets: the per-sample
// `fitness` the backend always serves (error rows floored to 0.0 by
// `rescore_results`). Narrower view of the served row.
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

// Parse the live sample lines from in-flight candidates into pseudo-results
// so the chart can bucket per-sample HIT/MISS without waiting for round
// completion. Live lines carry only HIT/MISS → 1.0 / 0.0 fitness.
function liveResultsFrom(dash: DashboardSnapshot | null): ResultRow[] {
  const out: ResultRow[] = [];
  for (const c of liveL1Candidates(dash)) {
    for (const raw of c.samples ?? []) {
      const p = parseSampleLine(raw);
      if (p.status === "HIT") out.push({ fitness: 1 });
      else if (p.status === "MISS") out.push({ fitness: 0 });
    }
  }
  return out;
}

export function FreqChart() {
  useThemeVersion();
  const chartRef = useRef(null);
  const { dash } = useDashboard();
  // Round files follow the VIEWED leaf hop (inner loop → inner cycle's dir).
  const { viewedPath } = useWorkspace();
  // The active round, from the single resolver every round-scoped surface shares.
  const { round: effectiveRound, isLiveView } = useEffectiveRound();

  // Source-of-truth split (no-stitch rule): live mode reads only
  // `dashboard.json`'s in-flight sample lines; historical mode reads only
  // `round_NNNN.json`'s `results[]`. `useRoundSource` owns the guard —
  // it idles the fetch on the live round, so there's no fallback chain.
  const { isLive, doc: roundDoc } = useRoundSource(viewedPath, effectiveRound, dash);

  const results: ResultRow[] = useMemo(() => {
    if (isLive) return liveResultsFrom(dash);
    return (roundDoc?.results as ResultRow[] | undefined) ?? [];
  }, [isLive, dash, roundDoc]);

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
      title={<span title={TERMS.stub_score_freq}>Score Frequency</span>}
      actions={
        <span className="badge">
          {isLiveView ? "live" : `R${effectiveRound}`}
        </span>
      }
    >
      <div style={{ position: "relative", height: 140 }}>
        <Bar ref={chartRef} data={chartData} options={options} />
      </div>
    </CardFrame>
  );
}
