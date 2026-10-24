"use client";
import { memo, useMemo } from "react";
import { Chart } from "react-chartjs-2";
import { ensureChartRegistered, getCss, lineLook, lineMotion, useThemeVersion } from "@/lib/theme";
import { useMediaQuery } from "@/lib/hooks/useMediaQuery";
import { liftOf } from "@/lib/derivations";
import { fmtSigned, fmtTheta, unitCount } from "@/lib/format";
import { NOT_SEPARABLE } from "@/lib/fitness";
import type { CandidateBar } from "@/lib/types";
import {
  CANDIDATE_SERIES,
  activeSeries,
  seriesByKey,
  seriesColumn,
  whiskerBands,
  type SeriesCtx,
  type WhiskerBand,
} from "./series";
import type { Chart as ChartJS, ChartData, ChartOptions, ChartType, Plugin } from "chart.js";

ensureChartRegistered();

// Fractions, not pixels: invariant under a width change, so a resize costs no React work.
export interface PlotGeometry {
  left: number;
  rightGutter: number;
  centers: number[];
}

export function geomEqual(a: PlotGeometry | null, b: PlotGeometry): boolean {
  return (
    a != null &&
    a.left === b.left &&
    a.rightGutter === b.rightGutter &&
    a.centers.length === b.centers.length &&
    a.centers.every((v, i) => v === b.centers[i])
  );
}

declare module "chart.js" {
  // Type param arity must match chart.js's own declaration to merge; unused here.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface PluginOptionsByType<TType extends ChartType> {
    barCaps?: { counts: (number | null)[]; parent: number | null; crown: string };
    divergenceLine?: { index: number | null };
    inFlightPulse?: { index: number | null };
    ciWhisker?: { bands: WhiskerBand[]; shaded: boolean };
    verificationMarks?: { marks: MarkRow[]; still: boolean };
    partialPanels?: { short: boolean[]; picked: number | null };
    xBridge?: { onGeometry: (g: PlotGeometry) => void };
  }
}

const CROWN = "♛";
const barCapsPlugin: Plugin<
  "bar",
  { counts: (number | null)[]; parent: number | null; crown: string }
> = {
  id: "barCaps",
  afterDatasetsDraw(chart, _args, opts) {
    const counts = opts?.counts;
    const xScale = chart.scales.x;
    if (!counts || !xScale) return;
    const { ctx, chartArea } = chart;
    // Bar channels only: letting the cache line into the minimum drags the caption onto the dash.
    const topOf = (i: number): number => {
      let topY = Infinity;
      chart.data.datasets.forEach((ds, di) => {
        if (seriesByKey(String(ds.label ?? ""))?.kind !== "bar") return;
        const el = chart.getDatasetMeta(di).data[i] as { y?: number } | undefined;
        if (el && typeof el.y === "number" && el.y < topY) topY = el.y;
      });
      return topY;
    };
    ctx.save();
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    const mono = getCss("--font-mono");
    counts.forEach((n, i) => {
      if (n == null) return;
      const topY = topOf(i);
      if (!Number.isFinite(topY)) return;
      ctx.font = `${getCss("--text-xs")} ${mono}`;
      ctx.fillStyle = getCss("--color-text-secondary");
      ctx.fillText(String(n), xScale.getPixelForValue(i), Math.max(topY - 4, chartArea.top + 10));
    });
    const w = opts?.parent;
    if (w != null && w >= 0) {
      const topY = topOf(w);
      if (Number.isFinite(topY)) {
        ctx.font = `12px ${mono}`;
        ctx.fillStyle = getCss("--color-accent");
        const capY = counts[w] != null ? topY - 15 : topY - 4;
        ctx.fillText(
          `${CROWN}${opts?.crown ?? ""}`,
          xScale.getPixelForValue(w),
          Math.max(capY, chartArea.top + 10),
        );
      }
    }
    ctx.restore();
  },
};

const BAND_ALPHA = 0.16;
type BandOpts = { bands: WhiskerBand[]; shaded: boolean };
type Placed = { getProps?: (p: string[], final: boolean) => Record<string, number> } | undefined;

function drawRibbons(chart: ChartJS, bands: WhiskerBand[]): void {
  const { ctx, chartArea } = chart;
  ctx.save();
  ctx.beginPath();
  ctx.rect(chartArea.left, chartArea.top, chartArea.width, chartArea.height);
  ctx.clip();
  ctx.globalAlpha = BAND_ALPHA;
  for (const band of bands) {
    const yScale = chart.scales[seriesByKey(band.anchor)?.axis ?? "y"];
    const di = chart.data.datasets.findIndex((ds) => ds.label === band.anchor);
    const ink = chart.data.datasets[di]?.borderColor;
    if (!yScale || di < 0 || typeof ink !== "string") continue;
    const points = chart.getDatasetMeta(di).data as Placed[];
    ctx.fillStyle = ink;
    let run: { x: number; lo: number; hi: number }[] = [];
    const close = () => {
      const first = run[0];
      if (first && run.length > 1) {
        ctx.beginPath();
        ctx.moveTo(first.x, first.hi);
        for (const p of run) ctx.lineTo(p.x, p.hi);
        for (let i = run.length - 1; i >= 0; i--) {
          const p = run[i];
          if (p) ctx.lineTo(p.x, p.lo);
        }
        ctx.closePath();
        ctx.fill();
      }
      run = [];
    };
    for (let i = 0; i < band.lo.length; i++) {
      const lo = band.lo[i];
      const hi = band.hi[i];
      const x = points[i]?.getProps?.(["x"], true)?.x;
      if (lo == null || hi == null || typeof x !== "number") {
        close();
        continue;
      }
      run.push({ x, lo: yScale.getPixelForValue(lo), hi: yScale.getPixelForValue(hi) });
    }
    close();
  }
  ctx.restore();
}

const isMark = (label: unknown): boolean => seriesByKey(String(label ?? ""))?.kind === "mark";

// Shaded, a bar channel's bands are a ribbon; a mark's interval is always its own whisker.
const ciWhiskerPlugin: Plugin<"bar", BandOpts> = {
  id: "ciWhisker",
  beforeDatasetsDraw(chart, _args, opts) {
    const ribbons = opts?.bands?.filter((b) => !isMark(b.anchor));
    if (opts?.shaded && ribbons?.length) drawRibbons(chart, ribbons);
  },
  afterDatasetsDraw(chart, _args, opts) {
    const bands = opts?.shaded ? opts.bands?.filter((b) => isMark(b.anchor)) : opts?.bands;
    if (!bands?.length) return;
    const { ctx } = chart;
    ctx.save();
    ctx.strokeStyle = getCss("--color-ci");
    ctx.lineWidth = 1.5;
    const capHalf = 4;
    for (const band of bands) {
      const yScale = chart.scales[seriesByKey(band.anchor)?.axis ?? "y"];
      const meta = chart.data.datasets.findIndex((ds) => ds.label === band.anchor);
      if (!yScale || meta < 0) continue;
      const bars = chart.getDatasetMeta(meta);
      for (let i = 0; i < band.lo.length; i++) {
        const lo = band.lo[i];
        const hi = band.hi[i];
        if (lo == null || hi == null) continue;
        const el = bars.data[i] as
          | { getProps?: (p: string[], final: boolean) => Record<string, number> }
          | undefined;
        const x = el?.getProps?.(["x"], true)?.x;
        if (typeof x !== "number") continue;
        // No clamp: the server clips `mean_fitness_ci` to [0,1] and θ's axis is fitted to its data.
        const yLo = yScale.getPixelForValue(lo);
        const yHi = yScale.getPixelForValue(hi);
        ctx.beginPath();
        ctx.moveTo(x, yLo);
        ctx.lineTo(x, yHi);
        ctx.moveTo(x - capHalf, yLo);
        ctx.lineTo(x + capHalf, yLo);
        ctx.moveTo(x - capHalf, yHi);
        ctx.lineTo(x + capHalf, yHi);
        ctx.stroke();
      }
    }
    ctx.restore();
  },
};

const divergenceLinePlugin: Plugin<"bar", { index: number | null }> = {
  id: "divergenceLine",
  afterDatasetsDraw(chart, _args, opts) {
    const idx = opts?.index;
    if (idx == null || idx < 0) return;
    const xScale = chart.scales.x;
    if (!xScale) return;
    const { ctx, chartArea } = chart;
    const c = xScale.getPixelForValue(idx);
    let x: number;
    if (idx > 0) {
      x = (xScale.getPixelForValue(idx - 1) + c) / 2;
    } else {
      const step = xScale.getPixelForValue(1) - xScale.getPixelForValue(0);
      x = Math.max(chartArea.left, c - (Number.isFinite(step) ? step : 0) / 2);
    }
    const red = getCss("--color-danger");
    ctx.save();
    ctx.strokeStyle = red;
    ctx.lineWidth = 2;
    ctx.shadowColor = red;
    ctx.shadowBlur = 6;
    ctx.beginPath();
    ctx.moveTo(x, chartArea.top);
    ctx.lineTo(x, chartArea.bottom);
    ctx.stroke();
    ctx.restore();
  },
};

const PULSE_PERIOD_MS = 1600;
const POINT_PULSE_WIDTH = 10;
// One pending frame per chart, whichever plugin asks: each asks again from the frame it drew.
const pulseRaf = new WeakMap<object, number>();
function nextPulseFrame(chart: ChartJS): void {
  if (pulseRaf.has(chart)) return;
  pulseRaf.set(
    chart,
    requestAnimationFrame(() => {
      pulseRaf.delete(chart);
      if (chart.ctx) chart.draw();
    }),
  );
}

const inFlightPulsePlugin: Plugin<"bar", { index: number | null }> = {
  id: "inFlightPulse",
  afterDatasetsDraw(chart, _args, opts) {
    const idx = opts?.index;
    if (idx == null || idx < 0) return;
    const { ctx } = chart;
    if (!ctx) return;
    let left = Infinity;
    let right = -Infinity;
    let top = Infinity;
    let base = -Infinity;
    chart.data.datasets.forEach((ds, di) => {
      if (isMark(ds.label)) return;
      const el = chart.getDatasetMeta(di).data[idx] as
        | { getProps?: (p: string[], final: boolean) => Record<string, number> }
        | undefined;
      const p = el?.getProps?.(["x", "y", "base", "width"], true);
      const x = p?.x;
      const y = p?.y;
      if (typeof x !== "number" || typeof y !== "number") return;
      const b = typeof p?.base === "number" ? p.base : chart.chartArea.bottom;
      const width = typeof p?.width === "number" ? p.width : POINT_PULSE_WIDTH;
      left = Math.min(left, x - width / 2);
      right = Math.max(right, x + width / 2);
      top = Math.min(top, y);
      base = Math.max(base, b);
    });
    if ([left, right, top, base].some((v) => !Number.isFinite(v))) return;
    const t = 0.5 + 0.5 * Math.sin((Date.now() / PULSE_PERIOD_MS) * Math.PI * 2);
    const glow = getCss("--color-success");
    ctx.save();
    ctx.strokeStyle = glow;
    ctx.globalAlpha = 0.55 + 0.4 * t;
    ctx.lineWidth = 1.5;
    ctx.shadowColor = glow;
    ctx.shadowBlur = 7 + 9 * t;
    ctx.strokeRect(left - 1.5, top - 1.5, right - left + 3, base - top + 3);
    ctx.restore();
    nextPulseFrame(chart);
  },
  beforeDestroy(chart) {
    const raf = pulseRaf.get(chart);
    if (raf != null) cancelAnimationFrame(raf);
    pulseRaf.delete(chart);
  },
};

const MARK_R = 5;
const MARK_GAP = 7;
const MARK_STEP = 11;
const MARK_PULSE_MS = 1400;
type MarkOpts = { marks: MarkRow[]; still: boolean };
interface MarkRow {
  key: string;
  filled: boolean;
  running: boolean[];
}
type MarkEl = { x: number; y: number; skip?: boolean };

// Beside the bar group, never on it: each mark series keeps its slot, drawn or not.
const verificationMarksPlugin: Plugin<"bar", MarkOpts> = {
  id: "verificationMarks",
  afterDatasetsUpdate(chart, _args, opts) {
    const rows = opts?.marks;
    const xScale = chart.scales.x;
    if (!rows?.length || !xScale) return;
    const bars = chart.data.datasets.flatMap((ds, di) =>
      seriesByKey(String(ds.label ?? ""))?.kind === "bar" ? [chart.getDatasetMeta(di)] : [],
    );
    rows.forEach((row, slot) => {
      const di = chart.data.datasets.findIndex((ds) => ds.label === row.key);
      if (di < 0) return;
      chart.getDatasetMeta(di).data.forEach((el, i) => {
        let right = xScale.getPixelForValue(i);
        for (const meta of bars) {
          const bar = (meta.data[i] as Placed)?.getProps?.(["x", "width"], true);
          const x = bar?.x;
          const w = bar?.width;
          // A bar channel drawn as a line has points, no width.
          if (typeof x === "number") right = Math.max(right, typeof w === "number" ? x + w / 2 : x);
        }
        (el as unknown as MarkEl).x = right + MARK_GAP + MARK_STEP * slot;
      });
    });
  },
  afterDatasetsDraw(chart, _args, opts) {
    const rows = opts?.marks;
    if (!rows?.length) return;
    const { ctx } = chart;
    const t = 0.5 + 0.5 * Math.cos((Date.now() / MARK_PULSE_MS) * Math.PI * 2);
    let pulsing = false;
    ctx.save();
    ctx.lineWidth = 1.4;
    for (const row of rows) {
      const di = chart.data.datasets.findIndex((ds) => ds.label === row.key);
      const ink = chart.data.datasets[di]?.borderColor;
      if (di < 0 || typeof ink !== "string") continue;
      ctx.strokeStyle = ink;
      ctx.fillStyle = ink;
      chart.getDatasetMeta(di).data.forEach((el, i) => {
        const { x, y, skip } = el as unknown as MarkEl;
        if (skip || !Number.isFinite(x) || !Number.isFinite(y)) return;
        const running = row.running[i] === true;
        pulsing ||= running && !opts.still;
        ctx.globalAlpha = !running ? 1 : opts.still ? 0.6 : 0.35 + 0.65 * t;
        ctx.beginPath();
        ctx.moveTo(x, y - MARK_R);
        ctx.lineTo(x + MARK_R, y);
        ctx.lineTo(x, y + MARK_R);
        ctx.lineTo(x - MARK_R, y);
        ctx.closePath();
        if (row.filled) ctx.fill();
        ctx.stroke();
      });
    }
    ctx.restore();
    if (pulsing) nextPulseFrame(chart);
  },
};

const HEX_R = 4;
// A point on fewer rows than its panel holds is an empty hexagon, never a dot.
const partialPanelsPlugin: Plugin<"bar", { short: boolean[]; picked: number | null }> = {
  id: "partialPanels",
  afterDatasetsDraw(chart, _args, opts) {
    const short = opts?.short;
    if (!short?.some(Boolean)) return;
    const { ctx } = chart;
    ctx.save();
    ctx.lineWidth = 1.4;
    ctx.fillStyle = getCss("--color-background-primary");
    chart.data.datasets.forEach((ds, di) => {
      if (seriesByKey(String(ds.label ?? ""))?.kind !== "bar" || typeof ds.borderColor !== "string") return;
      ctx.strokeStyle = ds.borderColor;
      chart.getDatasetMeta(di).data.forEach((el, i) => {
        if (!short[i]) return;
        const { x, y, skip } = el as unknown as MarkEl;
        if (skip || !Number.isFinite(x) || !Number.isFinite(y)) return;
        const r = i === opts.picked ? HEX_R + 2 : HEX_R;
        ctx.beginPath();
        for (let k = 0; k < 6; k++) {
          const a = (Math.PI / 3) * k - Math.PI / 2;
          ctx.lineTo(x + r * Math.cos(a), y + r * Math.sin(a));
        }
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
      });
    });
    ctx.restore();
  },
};

// `afterLayout`, not `afterDraw`: `inFlightPulse` re-enters `chart.draw()` at ~60fps.
const xBridgePlugin: Plugin<"bar", { onGeometry: (g: PlotGeometry) => void }> = {
  id: "xBridge",
  afterLayout(chart, _args, opts) {
    const emit = opts?.onGeometry;
    const x = chart.scales.x;
    if (!emit || !x) return;
    const { left, right, width } = chart.chartArea;
    // A zero-width (hidden) card keeps the last good geometry; re-showing fires a resize.
    if (!(width > 0)) return;
    const n = chart.data.labels?.length ?? 0;
    const centers: number[] = [];
    for (let i = 0; i < n; i++) centers.push((x.getPixelForValue(i) - left) / width);
    emit({ left, rightGutter: chart.width - right, centers });
  },
};

const CHART_PLUGINS = [
  barCapsPlugin,
  divergenceLinePlugin,
  inFlightPulsePlugin,
  ciWhiskerPlugin,
  verificationMarksPlugin,
  partialPanelsPlugin,
  xBridgePlugin,
];

const MARK_SERIES = CANDIDATE_SERIES.filter((s) => s.kind === "mark");

const ROTATE_THRESHOLD = 8;
const LINES_ABOVE = 12;

interface Props {
  // MUST be memoized; the legend reads the same value (`useCandidatesModel::seriesCtx`).
  ctx: SeriesCtx;
  selectedKey: string | null;
  onSelect: (view: CandidateBar | null) => void;
  divergenceBoundary: number | null;
  inFlightIndex: number | null;
  onGeometry: (g: PlotGeometry) => void;
}

export const FitnessChart = memo(function FitnessChart({
  ctx,
  selectedKey,
  onSelect,
  divergenceBoundary,
  inFlightIndex,
  onGeometry,
}: Props) {
  const { views, metrics, unit } = ctx;
  const themeVersion = useThemeVersion();
  const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const labels = useMemo(() => views.map((v) => v.label), [views]);
  const asLines = labels.length > LINES_ABOVE;

  const active = useMemo(() => activeSeries(ctx), [ctx]);
  const bands = useMemo(() => whiskerBands(ctx), [ctx]);
  const showAbility = metrics.has("ability");

  const selectionBorder = useMemo(() => {
    if (selectedKey == null) return null;
    const idx = views.findIndex((v) => v.key === selectedKey);
    if (idx < 0) return null;
    const colour = getCss("--color-selection");
    return { idx, colour };
    // themeVersion gates getCss() — needed in deps; lint flags it unused.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [views, selectedKey, themeVersion]);

  const parentIdx = useMemo(() => {
    const idx = views.findIndex((v) => v.reading !== null && v.node.stands);
    return idx >= 0 ? idx : null;
  }, [views]);

  const crown = useMemo(() => {
    const lift = parentIdx == null ? null : liftOf(views[parentIdx]?.reading?.vs_reference ?? null);
    return lift == null || lift.side === "spans" ? "" : ` ${fmtSigned(lift.value, 2)}`;
  }, [views, parentIdx]);

  const cutCounts = useMemo(
    () => views.map((v) => (v.reading?.panel.cut ? v.reading.panel.scored : null)),
    [views],
  );

  const shortPanels = useMemo(
    () =>
      views.map((v) => {
        const p = v.reading?.panel;
        return p?.scored != null && p.expected != null && p.scored < p.expected;
      }),
    [views],
  );

  const marks = useMemo<MarkRow[]>(
    () =>
      MARK_SERIES.map((spec) => ({
        key: spec.key,
        filled: spec.mark?.filled === true,
        running: views.map((v) => spec.mark?.of(v)?.state === "running"),
      })),
    [views],
  );

  const data = useMemo<ChartData<"bar" | "line">>(() => {
    const bars = active.filter((s) => s.kind === "bar").length;
    // A group carrying marks narrows, leaving them room before the next group.
    const roomy = active.some((s) => s.kind === "mark") ? 0.7 : 1;
    const cat = roomy * (bars <= 1 ? 0.55 : bars === 2 ? 0.75 : bars === 3 ? 0.9 : 0.95);
    const barCount = Math.max(1, labels.length);
    const maxBar = Math.max(6, Math.min(28, Math.round(640 / (barCount * Math.max(1, bars)))));
    const outline = () => {
      if (!selectionBorder) return { borderColor: "transparent", borderWidth: 0 };
      return {
        borderColor: labels.map((_, i) =>
          i === selectionBorder.idx ? selectionBorder.colour : "transparent",
        ),
        borderWidth: labels.map((_, i) => (i === selectionBorder.idx ? 3 : 0)),
      };
    };
    return {
      labels,
      datasets: active.map((spec) => {
        const ink = getCss(spec.ink(ctx));
        const data = seriesColumn(spec, views);
        if (spec.kind === "mark") {
          // Hit targets only: `verificationMarks` places and draws each diamond.
          return {
            type: "line" as const,
            label: spec.key,
            data,
            borderColor: ink,
            showLine: false,
            pointRadius: MARK_R,
            pointHoverRadius: MARK_R,
            pointHitRadius: MARK_R + 3,
            pointBackgroundColor: "transparent",
            pointBorderColor: "transparent",
            yAxisID: spec.axis,
            animation: false as const,
          };
        }
        if (spec.kind === "line") {
          return {
            type: "line" as const,
            label: spec.key,
            data,
            borderColor: ink,
            borderDash: [6, 4],
            borderWidth: 1.5,
            pointRadius: 0,
            fill: false,
            spanGaps: false,
            yAxisID: spec.axis,
            // Lowest `order` paints LAST in chart.js — +1 would hide the line behind the bars.
            order: -1,
          };
        }
        if (asLines) {
          const picked = selectionBorder?.idx;
          return {
            type: "line" as const,
            label: spec.key,
            data,
            ...lineLook(ink),
            // `partialPanels` draws a short panel's hexagon in place of the dot.
            pointRadius: labels.map((_, i) => (shortPanels[i] ? 0 : i === picked ? 5 : 2)),
            pointHoverRadius: labels.map((_, i) => (shortPanels[i] ? 0 : 4)),
            pointBorderColor: labels.map((_, i) =>
              i === picked && selectionBorder ? selectionBorder.colour : ink,
            ),
            pointHitRadius: 10,
            fill: false,
            spanGaps: spec.gap === "sparse",
            yAxisID: spec.axis,
          };
        }
        return {
          label: spec.key,
          data,
          backgroundColor: ink,
          ...outline(),
          yAxisID: spec.axis,
          barPercentage: 0.95,
          categoryPercentage: cat,
          maxBarThickness: maxBar,
          // chart.js applies `minBarLength` as absolute, so a negative stub would cross zero.
          ...(spec.signed ? {} : { minBarLength: 2 }),
        };
      }),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [labels, views, active, ctx, themeVersion, selectionBorder, asLines, shortPanels]);

  const rotate = labels.length > ROTATE_THRESHOLD;
  const options = useMemo<ChartOptions<"bar">>(() => ({
    responsive: true,
    maintainAspectRatio: false,
    animation: asLines ? lineMotion(reducedMotion) : false,
    onClick: (_evt, elements) => {
      const hit = elements?.[0];
      if (!hit) return;
      const view = views[hit.index];
      if (!view) return;
      if (view.key === selectedKey) onSelect(null);
      else onSelect(view);
    },
    onHover: (evt, elements) => {
      const target = (evt.native?.target ?? null) as HTMLElement | null;
      if (!target) return;
      target.style.cursor = elements?.[0] ? "pointer" : "default";
    },
    scales: {
      x: { grid: { display: false }, ticks: { color: (t) => getCss(t.index === parentIdx ? "--color-accent" : "--color-text-secondary"), font: (t) => ({ size: rotate ? 10 : 11, family: getCss("--font-mono"), weight: t.index === parentIdx ? "bold" as const : "normal" as const }), autoSkip: false, maxRotation: rotate ? 60 : 0, minRotation: rotate ? 60 : 0 } },
      y: { min: 0, max: 1, grid: { color: getCss("--color-border") }, ticks: { font: { size: 11 }, stepSize: 0.2 } },
      // Declared only while θ shows: the right gutter shifts every dendrogram fraction.
      ...(showAbility
        ? {
            y1: {
              position: "right" as const,
              grid: { display: false },
              ticks: { font: { size: 11 } },
              title: {
                display: true,
                text: "[θ]",
                align: "end" as const,
                color: getCss("--color-text-secondary"),
                font: { size: 11 },
              },
            },
          }
        : {}),
    },
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          label: (item) => {
            const v = views[item.dataIndex];
            const spec = seriesByKey(String(item.dataset.label ?? ""));
            return v && spec ? spec.tip(v, ctx) : "";
          },
          footer: (items) => {
            const idx = items[0]?.dataIndex;
            if (idx == null) return "";
            const lines: string[] = [];
            const bar = views[idx];
            const reading = bar?.reading;
            const n = reading?.panel.scored;
            if (reading && n != null) {
              const { expected: exp, cached } = reading.panel;
              lines.push(
                exp != null && exp !== n
                  ? `${n} of ${unitCount(exp, unit)} scored`
                  : `${unitCount(n, unit)} scored`,
              );
              if (cached != null && cached > 0) {
                lines.push(`${cached} of ${unitCount(n, unit)} from cache`);
              }
            }
            const theta = reading?.ability?.theta;
            if (typeof theta === "number") {
              const se = reading?.ability?.se;
              // Named "se", not ±: the drawn whisker is the wider 95% band.
              const tail = typeof se === "number" ? `, se ${se.toFixed(2)}` : "";
              lines.push(`ability θ ${fmtTheta(theta)}${tail} (elected on θ, not accuracy)`);
            }
            const ciLo = reading?.own?.accuracy?.ci_lo;
            const ciHi = reading?.own?.accuracy?.ci_hi;
            if (typeof ciLo === "number" && typeof ciHi === "number") {
              lines.push(`accuracy 95% CI [${ciLo.toFixed(3)}, ${ciHi.toFixed(3)}]`);
            }
            const lift = liftOf(reading?.vs_reference ?? null);
            if (lift != null && lift.ci_lo != null && lift.ci_hi != null) {
              const flat = lift.side === "spans" ? ` — ${NOT_SEPARABLE}` : "";
              lines.push(
                `accuracy lift vs parent ${fmtSigned(lift.value)} [${fmtSigned(lift.ci_lo)}, ${fmtSigned(lift.ci_hi)}]${flat}`,
              );
            }
            if (bar?.reading && !bar.reading.election.held) {
              lines.push("no election yet — nothing crowned in this round");
            }
            return lines.join("\n");
          },
        },
      },
      barCaps: { counts: cutCounts, parent: parentIdx, crown },
      divergenceLine: { index: divergenceBoundary },
      inFlightPulse: { index: inFlightIndex },
      ciWhisker: { bands, shaded: asLines },
      verificationMarks: { marks, still: reducedMotion },
      partialPanels: { short: asLines ? shortPanels : [], picked: selectionBorder?.idx ?? null },
      xBridge: { onGeometry },
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [themeVersion, ctx, rotate, asLines, reducedMotion, views, selectedKey, onSelect, divergenceBoundary, inFlightIndex, showAbility, parentIdx, crown, cutCounts, onGeometry, bands, marks, shortPanels, selectionBorder]);

  return (
    <div className="fitness-chart-frame">
      <Chart<"bar" | "line">
        type="bar"
        data={data}
        options={options}
        plugins={CHART_PLUGINS}
        aria-label={`Candidates this round — ${views.length} ${asLines ? "point" : "bar"}${
          views.length === 1 ? "" : "s"
        } per series (${active.map((s) => s.key).join(", ")}); intervals on ${
          bands.map((b) => b.anchor).join(" and ") || "none"
        }.`}
      />
    </div>
  );
});
