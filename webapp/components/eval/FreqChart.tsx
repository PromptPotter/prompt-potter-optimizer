"use client";
import { useEffect, useMemo, useRef } from "react";
import { Bar } from "react-chartjs-2";
import { ensureChartRegistered } from "@/lib/chart-init";
import { getCss } from "@/lib/theme";
import { TERMS } from "@/lib/terms";
import { parseSampleLine } from "@/lib/sample-line";
import { liveL1Candidates, useCycleStream, type DashboardSnapshot } from "@/lib/poll";
import type { ChartOptions } from "chart.js";

ensureChartRegistered();

interface ResultRow {
  score?: number;
  error?: unknown;
  predicted?: string;
  ground_truth?: string;
}

interface Props {
  dash: DashboardSnapshot | null;
  themeKey: string;
}

const LABELS = ["0", "", "", "", "", "", "", "", "", "1"];

function bucketScores(results: ResultRow[]): number[] {
  const buckets = new Array<number>(10).fill(0);
  results.forEach((r) => {
    const score = typeof r.score === "number"
      ? r.score
      : (r.error
        ? 0
        : (r.ground_truth && r.predicted && String(r.predicted).includes(String(r.ground_truth).replace("#### ", "").trim()) ? 1 : 0));
    const idx = Math.min(9, Math.max(0, Math.floor(score * 9.999)));
    buckets[idx] += 1;
  });
  return buckets;
}

// Parse the live sample lines from in-flight candidates into pseudo-results
// so the chart can bucket per-sample HIT/MISS without waiting for round
// completion.
function liveResultsFrom(dash: DashboardSnapshot | null): ResultRow[] {
  const out: ResultRow[] = [];
  for (const c of liveL1Candidates(dash)) {
    for (const raw of c.samples ?? []) {
      if (typeof raw !== "string") continue;
      const p = parseSampleLine(raw);
      if (p.status === "HIT") out.push({ score: 1 });
      else if (p.status === "MISS") out.push({ score: 0 });
    }
  }
  return out;
}

export function FreqChart({ dash, themeKey }: Props) {
  const chartRef = useRef(null);

  // Reuse the shared round-history stream — the latest entry is the freshest
  // round_NNNN.json on disk. No second round-file fetch path: every consumer
  // (TopStrip, TrendChart, LineageTree, FitnessPanel, HardSamples*, here)
  // reads from the same `useCycleStream().rounds` array.
  const { rounds: historyDocs } = useCycleStream();
  const latestResults = useMemo<ResultRow[]>(() => {
    const latest = historyDocs[historyDocs.length - 1];
    return (latest?.results as ResultRow[] | undefined) ?? [];
  }, [historyDocs]);

  const live = liveResultsFrom(dash);
  const useLive = live.length > 0;
  const data = bucketScores(useLive ? live : latestResults);
  const accStrong = getCss("--color-accent-strong");
  const acc = getCss("--color-accent");
  const colors = data.map((_, i) => (i < 5 ? accStrong : acc));

  // re-derive on theme change
  useEffect(() => {}, [themeKey]);

  const chartData = {
    labels: LABELS,
    datasets: [{ data, backgroundColor: colors, borderRadius: 2 }],
  };
  const options: ChartOptions<"bar"> = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: { x: { display: false }, y: { display: false } },
  };

  return (
    <div className="card">
      <div className="card-title">
        <span title={TERMS.stub_score_freq}>Score Frequency</span>
        <span className="badge">{useLive ? "live" : "round"}</span>
      </div>
      <div style={{ position: "relative", height: 140 }}>
        <Bar key={themeKey} ref={chartRef} data={chartData} options={options} />
      </div>
    </div>
  );
}
