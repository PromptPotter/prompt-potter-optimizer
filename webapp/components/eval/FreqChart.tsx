"use client";
import { useMemo, useRef } from "react";
import { Bar } from "react-chartjs-2";
import { barChartDefaults, ensureChartRegistered } from "@/lib/chart-config";
import { getCss, useThemeVersion } from "@/lib/theme";
import { TERMS } from "@/lib/terms";
import { parseSampleLine } from "@/lib/sample-line";
import {
  liveL1Candidates,
  roundOf,
  type DashboardSnapshot,
} from "@/lib/poll";
import { useWorkspace } from "@/lib/workspace";
import { useRoundFile } from "@/lib/hooks/useRoundFile";
import { CardFrame } from "@/components/ui/Card";
import { useSelection } from "@/lib/SelectionContext";
import type { RawResultRow } from "@/lib/types/round";

ensureChartRegistered();

// Local alias kept to the fields FreqChart actually buckets. Same
// shape, narrower view — the chart never touches sample_id / hit / fitness.
type ResultRow = Pick<RawResultRow, "score" | "error" | "predicted" | "ground_truth">;

interface Props {
  dash: DashboardSnapshot | null;
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

export function FreqChart({ dash }: Props) {
  useThemeVersion();
  const chartRef = useRef(null);
  const { campaignId, cycleId } = useWorkspace();
  const { round: selectedRound } = useSelection();

  // Source-of-truth split (AGENTS.md no-stitch rule):
  //   - live mode (selectedRound == null or matches the in-flight round)
  //     reads only `dashboard.json`'s in-flight sample lines.
  //   - historical mode (any other round) reads only `round_NNNN.json`'s
  //     `results[]` via `useRoundFile`.
  // No fallback chain between the two — a click on a historical round
  // tab shows that round's bucket, even if it temporarily looks empty
  // while the file loads. The chart never silently merges sources.
  const liveRound = roundOf(dash);
  const isLiveView =
    selectedRound == null ||
    (liveRound != null && selectedRound === liveRound);
  // Only fetch a round file when we're actually in historical mode —
  // null tells `useRoundFile` to stay idle.
  const historicalRoundArg = isLiveView ? null : selectedRound;
  const { doc: roundDoc } = useRoundFile(campaignId, cycleId, historicalRoundArg);

  const results: ResultRow[] = useMemo(() => {
    if (isLiveView) return liveResultsFrom(dash);
    return (roundDoc?.results as ResultRow[] | undefined) ?? [];
  }, [isLiveView, dash, roundDoc]);

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
          {isLiveView ? "live" : `R${selectedRound}`}
        </span>
      }
    >
      <div style={{ position: "relative", height: 140 }}>
        <Bar ref={chartRef} data={chartData} options={options} />
      </div>
    </CardFrame>
  );
}
