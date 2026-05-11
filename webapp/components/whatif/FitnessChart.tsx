"use client";
import { useMemo } from "react";
import { Bar } from "react-chartjs-2";
import { ensureChartRegistered } from "@/lib/chart-init";
import { cssRgba, getCss } from "@/lib/theme";
import type { ChartData, ChartOptions } from "chart.js";

ensureChartRegistered();

// Pre-projected bar slot. Origin, historical rounds, and the in-flight
// current round all collapse to the same shape — FitnessPanel handles the
// merge so this component stays a pure plotter.
export interface BarSlot {
  key: string;          // React + dedup key
  label: string;        // x-axis label (e.g. "C0", "C1.1", "C2.3")
  accuracy: number | null;
  composite: number | null;
  whatif: number | null;
  started: boolean;     // false = blank slot, true = scored or scoring
}

interface Props {
  bars: BarSlot[];
  showComposite: boolean;
  showWhatIf: boolean;
  themeKey: string;
}

export function FitnessChart({
  bars,
  showComposite,
  showWhatIf,
  themeKey,
}: Props) {
  const labels = useMemo(() => bars.map((b) => b.label), [bars]);

  // Two parallel arrays per series: *Raw drives the tooltip; *Plot pushes
  // null → 0 only for bars whose scoring has begun (so `minBarLength`
  // paints a stub). Unstarted bars stay null so chart.js leaves the slot
  // blank — distinguishing "still computing" from "not yet started".
  const { accRaw, compRaw, whatifRaw, accPlot, compPlot, whatifPlot } = useMemo(() => {
    const aR = bars.map((b) => b.accuracy);
    const cR = bars.map((b) => b.composite);
    const wR = bars.map((b) => b.whatif);
    const coerce = (v: number | null, i: number): number | null =>
      v == null ? (bars[i].started ? 0 : null) : v;
    return {
      accRaw: aR, compRaw: cR, whatifRaw: wR,
      accPlot: aR.map(coerce), compPlot: cR.map(coerce), whatifPlot: wR.map(coerce),
    };
  }, [bars]);

  const data = useMemo<ChartData<"bar">>(() => {
    const seriesCount = 1 + (showComposite ? 1 : 0) + (showWhatIf ? 1 : 0);
    const cat = seriesCount === 1 ? 0.55 : seriesCount === 2 ? 0.75 : 0.9;
    const datasets: ChartData<"bar">["datasets"] = [
      {
        label: "accuracy",
        data: accPlot,
        backgroundColor: cssRgba("--color-accent-rgb", 0.95),
        borderColor: cssRgba("--color-accent-rgb", 0.95),
        borderWidth: 0,
        barPercentage: 0.95,
        categoryPercentage: cat,
        maxBarThickness: 28,
        minBarLength: 2,
      },
    ];
    if (showComposite) {
      datasets.push({
        label: "composite",
        data: compPlot,
        backgroundColor: cssRgba("--color-accent-rgb", 0.55),
        borderColor: cssRgba("--color-accent-rgb", 0.55),
        borderWidth: 0,
        barPercentage: 0.95,
        categoryPercentage: cat,
        maxBarThickness: 24,
        minBarLength: 2,
      });
    }
    if (showWhatIf) {
      const accentStrong = getCss("--color-accent-strong");
      datasets.push({
        label: "what-if",
        data: whatifPlot,
        backgroundColor: accentStrong,
        borderColor: accentStrong,
        borderWidth: 0,
        barPercentage: 0.95,
        categoryPercentage: cat,
        maxBarThickness: 24,
        minBarLength: 2,
      });
    }
    return { labels, datasets };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [labels, accPlot, compPlot, whatifPlot, showComposite, showWhatIf, themeKey]);

  const tooltipFor = (label: string, idx: number): string => {
    const src = label === "accuracy" ? accRaw : label === "composite" ? compRaw : whatifRaw;
    const v = src[idx];
    return `${label}: ${v == null ? "—" : v.toFixed(3)}`;
  };

  const options = useMemo<ChartOptions<"bar">>(() => ({
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 200 },
    scales: {
      x: { grid: { display: false }, ticks: { font: { size: 11, family: "Cascadia Mono, SF Mono, Menlo, Consolas, monospace" }, autoSkip: false, maxRotation: 0 } },
      y: { min: 0, max: 1, grid: { color: getCss("--color-border-tertiary") }, ticks: { font: { size: 11 }, stepSize: 0.25 } },
    },
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          label: (ctx) => tooltipFor(String(ctx.dataset.label ?? ""), ctx.dataIndex),
        },
      },
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [themeKey, accRaw, compRaw, whatifRaw]);

  return (
    <div className="fitness-chart-frame">
      <Bar key={themeKey} data={data} options={options} />
    </div>
  );
}
