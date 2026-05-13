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
//
// `candidateId` + `round` (optional) lift the bar into the shared
// SelectionContext: bars carrying them are clickable + highlight when the
// matching candidate is selected anywhere in the dashboard. Origin and
// pre-id'd live bars stay non-selectable (no row in the lineage to
// cross-reference yet).
export interface BarSlot {
  key: string;          // React + dedup key
  label: string;        // x-axis label (e.g. "C0", "C1.1", "C2.3")
  accuracy: number | null;
  composite: number | null;
  whatif: number | null;
  started: boolean;     // false = blank slot, true = scored or scoring
  candidateId?: string;
  round?: number;
  isWinner?: boolean;
}

interface Props {
  bars: BarSlot[];
  showComposite: boolean;
  showWhatIf: boolean;
  themeKey: string;
  selectedKey: string | null;
  onSelect: (bar: BarSlot | null) => void;
}

export function FitnessChart({
  bars,
  showComposite,
  showWhatIf,
  themeKey,
  selectedKey,
  onSelect,
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

  // Per-bar border overlay — picks out the bar matching the shared
  // SelectionContext. Driven by --color-selection (set in globals.css)
  // so it matches the lineage tree's selected stub colour.
  const selectionBorder = useMemo(() => {
    if (selectedKey == null) return null;
    const idx = bars.findIndex((b) => b.key === selectedKey);
    if (idx < 0) return null;
    const colour = getCss("--color-selection");
    return { idx, colour };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bars, selectedKey, themeKey]);

  const data = useMemo<ChartData<"bar">>(() => {
    const seriesCount = 1 + (showComposite ? 1 : 0) + (showWhatIf ? 1 : 0);
    const cat = seriesCount === 1 ? 0.55 : seriesCount === 2 ? 0.75 : 0.9;
    // Dynamic bar thickness ceiling: chart frame fans out to ~720px on
    // wide layouts, so 25 bars × 3 series should leave room without
    // clipping. Scale max thickness down as the bar count grows; min 6.
    const barCount = Math.max(1, labels.length);
    const maxBar = Math.max(6, Math.min(28, Math.round(640 / (barCount * seriesCount))));
    const accent = cssRgba("--color-accent-rgb", 0.95);
    const accentDim = cssRgba("--color-accent-rgb", 0.55);
    const accentStrong = getCss("--color-accent-strong");
    const borderArr = (base: string): string[] | string => {
      if (!selectionBorder) return base;
      return labels.map((_, i) => (i === selectionBorder.idx ? selectionBorder.colour : base));
    };
    const widthArr = (): number[] | number => {
      if (!selectionBorder) return 0;
      return labels.map((_, i) => (i === selectionBorder.idx ? 3 : 0));
    };
    const datasets: ChartData<"bar">["datasets"] = [
      {
        label: "accuracy",
        data: accPlot,
        backgroundColor: accent,
        borderColor: borderArr(accent),
        borderWidth: widthArr(),
        barPercentage: 0.95,
        categoryPercentage: cat,
        maxBarThickness: maxBar,
        minBarLength: 2,
      },
    ];
    if (showComposite) {
      datasets.push({
        label: "composite",
        data: compPlot,
        backgroundColor: accentDim,
        borderColor: borderArr(accentDim),
        borderWidth: widthArr(),
        barPercentage: 0.95,
        categoryPercentage: cat,
        maxBarThickness: maxBar,
        minBarLength: 2,
      });
    }
    if (showWhatIf) {
      datasets.push({
        label: "what-if",
        data: whatifPlot,
        backgroundColor: accentStrong,
        borderColor: borderArr(accentStrong),
        borderWidth: widthArr(),
        barPercentage: 0.95,
        categoryPercentage: cat,
        maxBarThickness: maxBar,
        minBarLength: 2,
      });
    }
    return { labels, datasets };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [labels, accPlot, compPlot, whatifPlot, showComposite, showWhatIf, themeKey, selectionBorder]);

  const tooltipFor = (label: string, idx: number): string => {
    const src = label === "accuracy" ? accRaw : label === "composite" ? compRaw : whatifRaw;
    const v = src[idx];
    return `${label}: ${v == null ? "—" : v.toFixed(3)}`;
  };

  const rotate = labels.length > 14;
  const options = useMemo<ChartOptions<"bar">>(() => ({
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 200 },
    onClick: (_evt, elements) => {
      if (!elements || elements.length === 0) return;
      const idx = elements[0].index;
      const bar = bars[idx];
      if (!bar || !bar.candidateId || bar.round == null) return;     // origin / pre-id'd live bar
      if (bar.key === selectedKey) onSelect(null);
      else onSelect(bar);
    },
    onHover: (evt, elements) => {
      const target = (evt.native?.target ?? null) as HTMLElement | null;
      if (!target) return;
      const hit = elements && elements.length > 0;
      const bar = hit ? bars[elements[0].index] : null;
      target.style.cursor = bar && bar.candidateId && bar.round != null ? "pointer" : "default";
    },
    scales: {
      x: { grid: { display: false }, ticks: { font: { size: rotate ? 10 : 11, family: "Cascadia Mono, SF Mono, Menlo, Consolas, monospace" }, autoSkip: false, maxRotation: rotate ? 60 : 0, minRotation: rotate ? 60 : 0 } },
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
  }), [themeKey, accRaw, compRaw, whatifRaw, rotate, bars, selectedKey, onSelect]);

  return (
    <div className="fitness-chart-frame">
      <Bar key={themeKey} data={data} options={options} />
    </div>
  );
}
