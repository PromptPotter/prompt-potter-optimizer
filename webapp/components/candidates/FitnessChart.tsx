"use client";
import { memo, useMemo } from "react";
import { Bar } from "react-chartjs-2";
import { cssRgba, ensureChartRegistered, getCss, useThemeVersion } from "@/lib/theme";
import type { HeadlineMetric } from "@/lib/derivations";
import type { CandidateView } from "@/lib/types";
import type { ChartData, ChartOptions, ChartType, Plugin } from "chart.js";

ensureChartRegistered();

// Published plot geometry — the bridge that lets the dendrogram strip, an SVG
// sibling rendered UNDER this canvas, land its nodes exactly on the bar category
// centers.
//
// FRACTION space, deliberately. chart.js's category scale is linear in the plot
// width (the bar controller sets `offset:true` on the index scale), so a centre's
// fraction is INVARIANT under a pure width change — only the two px gutters and
// the bar COUNT can move it. A window resize, What-If opening, or the sidebar
// collapsing therefore costs no React work at all: the strip's percentage
// coordinates track the canvas through the browser's own layout pass, in the same
// frame. Publishing raw pixels would re-render on every resize tick and still
// leave the genealogy one frame behind the bars mid-drag.
export interface PlotGeometry {
  // px inset, canvas left edge → plot area.
  left: number;
  // px inset, plot area's right edge → canvas right edge.
  rightGutter: number;
  // Category centre i, as a fraction of the plot width.
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

// Per-bar sample count painted above each candidate's bar group. The metric
// (accuracy / composite / what-if) is computed over `n_samples` samples — a
// 0.6 over 5 samples is far noisier than a 0.6 over 20, and PoBB leader-lock
// routinely stops a candidate on a partial set. The count makes that visible.
declare module "chart.js" {
  // Type param arity must match chart.js's own declaration to merge; unused here.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface PluginOptionsByType<TType extends ChartType> {
    sampleCount?: { counts: (number | null)[] };
    divergenceLine?: { index: number | null };
    inFlightPulse?: { index: number | null };
    compositeCiWhisker?: { ciLo: (number | null)[]; ciHi: (number | null)[] };
    xBridge?: { onGeometry: (g: PlotGeometry) => void };
  }
}

const sampleCountPlugin: Plugin<"bar", { counts: (number | null)[] }> = {
  id: "sampleCount",
  afterDatasetsDraw(chart, _args, opts) {
    const counts = opts?.counts;
    const xScale = chart.scales.x;
    if (!counts || !xScale) return;
    const { ctx, chartArea } = chart;
    ctx.save();
    ctx.font = "10px Cascadia Mono, SF Mono, Menlo, Consolas, monospace";
    ctx.fillStyle = getCss("--color-text-secondary");
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    counts.forEach((n, i) => {
      if (n == null) return;
      // Highest (smallest y) bar top across every series for this candidate.
      let topY = Infinity;
      chart.data.datasets.forEach((_ds, di) => {
        const el = chart.getDatasetMeta(di).data[i] as { y?: number } | undefined;
        if (el && typeof el.y === "number" && el.y < topY) topY = el.y;
      });
      if (!Number.isFinite(topY)) return;
      const labelY = Math.max(topY - 4, chartArea.top + 10);
      ctx.fillText(String(n), xScale.getPixelForValue(i), labelY);
    });
    ctx.restore();
  },
};

// Error-bar whisker on the primary metric bar — a vertical line from `ciLo` to
// `ciHi` (mapped value→pixel via the left [0,1] y scale) with short end-caps, so
// no point estimate stands alone. It rides whichever 0–1-scale bar is shown —
// accuracy (the default-on metric) if present, else composite — so the CI is
// visible by default without toggling the composite metric on. Drawn at that
// dataset's OWN rendered x (which shifts within the bar group depending on how
// many series show), not the category center — a whisker must sit on the bar it
// brackets. Bars with a null CI (in-flight round) are skipped.
const compositeCiWhiskerPlugin: Plugin<
  "bar",
  { ciLo: (number | null)[]; ciHi: (number | null)[] }
> = {
  id: "compositeCiWhisker",
  afterDatasetsDraw(chart, _args, opts) {
    const ciLo = opts?.ciLo;
    const ciHi = opts?.ciHi;
    const yScale = chart.scales.y;
    if (!ciLo || !ciHi || !yScale) return;
    // The whisker data IS composite_ci_lo/hi — composite-scale by default, and the backend only
    // rewrites it to the θ-accuracy band where composite == accuracy (`winner.py::l1_score`), in
    // which case the accuracy bar carries the identical value. So sit on the COMPOSITE bar when
    // it is drawn (a composite-formula run: bracketing the accuracy bar with a composite CI is the
    // bug), and fall back to accuracy only when composite is hidden — exactly the θ-band case.
    let dsIndex = chart.data.datasets.findIndex((ds) => ds.label === "composite");
    if (dsIndex < 0) dsIndex = chart.data.datasets.findIndex((ds) => ds.label === "accuracy");
    if (dsIndex < 0) return;
    const meta = chart.getDatasetMeta(dsIndex);
    const { ctx } = chart;
    ctx.save();
    ctx.strokeStyle = getCss("--color-ci");
    ctx.lineWidth = 1.5;
    const capHalf = 4;
    for (let i = 0; i < ciLo.length; i++) {
      const lo = ciLo[i];
      const hi = ciHi[i];
      if (lo == null || hi == null) continue;
      const el = meta.data[i] as
        | { getProps?: (p: string[], final: boolean) => Record<string, number> }
        | undefined;
      const x = el?.getProps?.(["x"], true)?.x;
      if (typeof x !== "number") continue;
      // Both band sources are bounded to [0,1] by the server — the composite CI is clipped
      // to its support in `scoring/metrics.py::composite_ci`, and the θ band is a mean of
      // sigmoids — so the pixel always lands inside the fixed axis. This used to clamp to
      // the plot area, compensating here for an interval that claimed negative accuracy.
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
    ctx.restore();
  },
};

// Red vertical divider at the mask divergence boundary: bars left of it are the
// invariant prefix (the masked criterion would have elected the SAME winners up
// to here), bars at/right of it are counterfactual. Drawn at the LEFT edge of the
// first divergent bar — "before the divergent values, after what is truly the
// same". Index null ⇒ no mask active / no divergence ⇒ nothing drawn.
const divergenceLinePlugin: Plugin<"bar", { index: number | null }> = {
  id: "divergenceLine",
  afterDatasetsDraw(chart, _args, opts) {
    const idx = opts?.index;
    if (idx == null || idx < 0) return;
    const xScale = chart.scales.x;
    if (!xScale) return;
    const { ctx, chartArea } = chart;
    // Left edge of category `idx` = midpoint to its left neighbour; for idx 0,
    // step half a category left of the first centre (clamped to the plot edge).
    const c = xScale.getPixelForValue(idx);
    let x: number;
    if (idx > 0) {
      x = (xScale.getPixelForValue(idx - 1) + c) / 2;
    } else {
      const step = xScale.getPixelForValue(1) - xScale.getPixelForValue(0);
      x = Math.max(chartArea.left, c - (Number.isFinite(step) ? step : 0) / 2);
    }
    const red = getCss("--color-danger") || "#C53030";
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

// The in-flight candidate's bar pulses a green glow around its outline while it
// is still accumulating samples — the same success-green glow the optimizer
// canvas casts around the active node, here hugging the bar's box (no
// background fill, just the glow around the box). Bars are canvas-drawn (not
// DOM), so a CSS animation can't reach them: this plugin strokes the glow in
// `afterDatasetsDraw` (on top of the bars) and drives its own redraw via
// requestAnimationFrame. The loop runs ONLY while `index` is a real bar (a
// candidate is scoring) and cancels itself the moment scoring ends or the chart
// is destroyed — no standing animation against `animation:false`.
const PULSE_PERIOD_MS = 1600;
const pulseRaf = new WeakMap<object, number>();
const inFlightPulsePlugin: Plugin<"bar", { index: number | null }> = {
  id: "inFlightPulse",
  afterDatasetsDraw(chart, _args, opts) {
    const idx = opts?.index;
    const cancel = () => {
      const raf = pulseRaf.get(chart);
      if (raf != null) {
        cancelAnimationFrame(raf);
        pulseRaf.delete(chart);
      }
    };
    if (idx == null || idx < 0) {
      cancel();
      return;
    }
    const { ctx } = chart;
    if (!ctx) return;
    // Bounding box of the candidate's bar group (all visible series at idx).
    let left = Infinity;
    let right = -Infinity;
    let top = Infinity;
    let base = -Infinity;
    chart.data.datasets.forEach((_ds, di) => {
      const el = chart.getDatasetMeta(di).data[idx] as
        | { getProps?: (p: string[], final: boolean) => Record<string, number> }
        | undefined;
      const p = el?.getProps?.(["x", "y", "base", "width"], true);
      const x = p?.x;
      const y = p?.y;
      const b = p?.base;
      const width = p?.width;
      if (
        typeof x !== "number" ||
        typeof y !== "number" ||
        typeof b !== "number" ||
        typeof width !== "number"
      ) {
        return;
      }
      left = Math.min(left, x - width / 2);
      right = Math.max(right, x + width / 2);
      top = Math.min(top, y);
      base = Math.max(base, b);
    });
    if ([left, right, top, base].some((v) => !Number.isFinite(v))) {
      cancel();
      return;
    }
    const t = 0.5 + 0.5 * Math.sin((Date.now() / PULSE_PERIOD_MS) * Math.PI * 2);
    const glow = getCss("--color-success") || "#1a8265";
    ctx.save();
    ctx.strokeStyle = glow;
    ctx.globalAlpha = 0.55 + 0.4 * t;
    ctx.lineWidth = 1.5;
    ctx.shadowColor = glow;
    ctx.shadowBlur = 7 + 9 * t;
    ctx.strokeRect(left - 1.5, top - 1.5, right - left + 3, base - top + 3);
    ctx.restore();
    // Drive the next frame — chart.draw() re-enters this hook, so the loop
    // self-sustains while a bar is in flight (rAF caps it to the refresh rate).
    pulseRaf.set(
      chart,
      requestAnimationFrame(() => {
        if (chart.ctx) chart.draw();
      }),
    );
  },
  beforeDestroy(chart) {
    const raf = pulseRaf.get(chart);
    if (raf != null) cancelAnimationFrame(raf);
    pulseRaf.delete(chart);
  },
};

// `afterLayout`, NOT `afterDraw`: (1) `chartArea` and `scales` are final here;
// (2) it fires on every update AND on every resize (chart.js's resize path runs
// an update, which re-lays-out); (3) `inFlightPulse` re-enters `chart.draw()` at
// ~60fps via rAF while a candidate scores, so an afterDraw publisher would
// rebuild this array 60×/s for nothing.
const xBridgePlugin: Plugin<"bar", { onGeometry: (g: PlotGeometry) => void }> = {
  id: "xBridge",
  afterLayout(chart, _args, opts) {
    const emit = opts?.onGeometry;
    const x = chart.scales.x;
    if (!emit || !x) return;
    const { left, right, width } = chart.chartArea;
    // Hidden / zero-width card (a collapsed chat dropdown) makes every fraction
    // garbage. Keep the last good geometry; re-showing fires a resize, which
    // republishes.
    if (!(width > 0)) return;
    const n = chart.data.labels?.length ?? 0;
    const centers: number[] = [];
    for (let i = 0; i < n; i++) centers.push((x.getPixelForValue(i) - left) / width);
    emit({ left, rightGutter: chart.width - right, centers });
  },
};

// Stable identity — passing a fresh array each render churns the chart.
const CHART_PLUGINS = [
  sampleCountPlugin,
  divergenceLinePlugin,
  inFlightPulsePlugin,
  compositeCiWhiskerPlugin,
  xBridgePlugin,
];

// Above this real-bar count, x-axis labels rotate 60° so they stop
// overlapping their neighbours. Picked empirically against the card
// height (rotated labels eat ~30px of plot height).
const ROTATE_THRESHOLD = 8;

interface Props {
  views: CandidateView[];
  // The card's metric axis — one bar series per selected metric. Never empty.
  metrics: ReadonlySet<HeadlineMetric>;
  showWhatIf: boolean;
  selectedKey: string | null;
  onSelect: (view: CandidateView | null) => void;
  // Bar index where the active mask first diverges from the realized record —
  // the red vertical divider is drawn at its left edge. null = no mask / no
  // divergence (no divider).
  divergenceBoundary: number | null;
  // Bar index of the candidate currently accumulating samples — it pulses
  // ("blinking") while live. null = nothing scoring (no pulse).
  inFlightIndex: number | null;
  // Publishes the plot geometry the dendrogram strip aligns to. MUST be a stable
  // callback: it rides the `options` memo, so a fresh identity per render would
  // force a chart.update() on every poll tick.
  onGeometry: (g: PlotGeometry) => void;
}

export const FitnessChart = memo(function FitnessChart({
  views,
  metrics,
  showWhatIf,
  selectedKey,
  onSelect,
  divergenceBoundary,
  inFlightIndex,
  onGeometry,
}: Props) {
  // Subscribe to theme so a flip re-runs this component and the data/options
  // memos below pick up the new getCss() values.
  const themeVersion = useThemeVersion();
  // One x-axis label per real bar. No empty-slot padding: chart.js spaces
  // categories evenly across the frame, and the `maxBarThickness` cap below
  // keeps individual bars narrow even at very low counts (a 2-bar round
  // shows two 28px bars centered, not two stretched-to-fill bars).
  const labels = useMemo(() => views.map((v) => v.label), [views]);

  const showAccuracy = metrics.has("accuracy");
  const showComposite = metrics.has("composite");
  const showAbility = metrics.has("ability");

  // Two parallel arrays per series: *Raw drives the tooltip; *Plot pushes
  // null → 0 only for bars whose scoring has begun (so `minBarLength`
  // paints a stub). Unstarted bars stay null so chart.js leaves the slot
  // blank — distinguishing "still computing" from "not yet started".
  const {
    accRaw, compRaw, whatifRaw, verifyRaw, thetaRaw, accPlot, compPlot, whatifPlot,
  } = useMemo(() => {
    const aR: (number | null)[] = [];
    const cR: (number | null)[] = [];
    const wR: (number | null)[] = [];
    const vR: (number | null)[] = [];
    const tR: (number | null)[] = [];
    for (let i = 0; i < labels.length; i++) {
      aR.push(views[i]?.accuracy ?? null);
      cR.push(views[i]?.composite ?? null);
      wR.push(views[i]?.whatif ?? null);
      vR.push(views[i]?.diag?.accuracy ?? null);
      tR.push(views[i]?.theta ?? null);
    }
    const coerce = (v: number | null, i: number): number | null =>
      v == null ? (views[i]?.started ? 0 : null) : v;
    // verify and θ stay strictly sparse — NO started-floor coercion. For verify a
    // 0-height stub on every column would read as a red dot everywhere. For θ it
    // is worse: on the 0–1 percent axis a coerced 0 reads as "nothing yet", but θ
    // is a logit where 0 is a REAL, middling ability — coercing a missing θ to 0
    // renders a fabricated measurement.
    return {
      accRaw: aR, compRaw: cR, whatifRaw: wR, verifyRaw: vR, thetaRaw: tR,
      accPlot: aR.map(coerce), compPlot: cR.map(coerce), whatifPlot: wR.map(coerce),
    };
  }, [views, labels]);

  // Per-bar border overlay — picks out the bar matching the shared
  // SelectionContext. Driven by --color-selection (app/styles/foundation/themes.css)
  // so it matches the dendrogram node's selected colour directly beneath it.
  const selectionBorder = useMemo(() => {
    if (selectedKey == null) return null;
    const idx = views.findIndex((v) => v.key === selectedKey);
    if (idx < 0) return null;
    const colour = getCss("--color-selection");
    return { idx, colour };
    // themeVersion gates getCss() — needed in deps; lint flags it unused.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [views, selectedKey, themeVersion]);

  const hasVerify = useMemo(() => views.some((v) => v.diag != null), [views]);
  const data = useMemo<ChartData<"bar">>(() => {
    const seriesCount = metrics.size + (showWhatIf ? 1 : 0) + (hasVerify ? 1 : 0);
    const cat =
      seriesCount <= 1 ? 0.55 : seriesCount === 2 ? 0.75 : seriesCount === 3 ? 0.9 : 0.95;
    // Dynamic bar thickness ceiling: chart frame fans out to ~720px on
    // wide layouts, so 25 bars × 3 series should leave room without
    // clipping. Scale max thickness down as the bar count grows; min 6.
    const barCount = Math.max(1, labels.length);
    const maxBar = Math.max(
      6,
      Math.min(28, Math.round(640 / (barCount * Math.max(1, seriesCount)))),
    );
    const accent = cssRgba("--color-accent-rgb", 0.95);
    const accentDim = cssRgba("--color-accent-rgb", 0.55);
    const accentStrong = getCss("--color-accent-strong");
    const muted = getCss("--color-text-tertiary");
    const borderArr = (base: string): string[] | string => {
      if (!selectionBorder) return base;
      return labels.map((_, i) => (i === selectionBorder.idx ? selectionBorder.colour : base));
    };
    const widthArr = (): number[] | number => {
      if (!selectionBorder) return 0;
      return labels.map((_, i) => (i === selectionBorder.idx ? 3 : 0));
    };
    const shared = {
      barPercentage: 0.95,
      categoryPercentage: cat,
      maxBarThickness: maxBar,
      minBarLength: 2,
    };
    const datasets: ChartData<"bar">["datasets"] = [];
    if (showAccuracy) {
      datasets.push({
        label: "accuracy",
        data: accPlot,
        backgroundColor: accent,
        borderColor: borderArr(accent),
        borderWidth: widthArr(),
        ...shared,
      });
    }
    if (showAbility) {
      // Second bar by default (the metric the winner is elected on). θ is a LOGIT,
      // not a percent — it rides its own right-hand axis, which auto-ranges and may go
      // negative. No `minBarLength`: on a negative θ that would paint a stub on the
      // wrong side of zero.
      datasets.push({
        label: "ability",
        data: thetaRaw,
        yAxisID: "y1",
        backgroundColor: muted,
        borderColor: borderArr(muted),
        borderWidth: widthArr(),
        barPercentage: 0.95,
        categoryPercentage: cat,
        maxBarThickness: maxBar,
      });
    }
    if (showComposite) {
      datasets.push({
        label: "composite",
        data: compPlot,
        backgroundColor: accentDim,
        borderColor: borderArr(accentDim),
        borderWidth: widthArr(),
        ...shared,
      });
    }
    if (showWhatIf) {
      datasets.push({
        label: "what-if",
        data: whatifPlot,
        backgroundColor: accentStrong,
        borderColor: borderArr(accentStrong),
        borderWidth: widthArr(),
        ...shared,
      });
    }
    if (hasVerify) {
      // Workspace-verify overlay — populated only on bars with a matching
      // DiagnosticRunRecord. Red is the only series colour outside the
      // accent palette: this is the operator's "did the verdict hold on
      // more samples?" signal, intentionally visually distinct.
      datasets.push({
        label: "verify",
        data: verifyRaw,
        backgroundColor: "#e74c3c",
        borderColor: "#e74c3c",
        borderWidth: 0,
        ...shared,
      });
    }
    return { labels, datasets };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [labels, accPlot, compPlot, whatifPlot, verifyRaw, thetaRaw, metrics, showAccuracy, showComposite, showAbility, showWhatIf, hasVerify, themeVersion, selectionBorder]);

  const tooltipFor = (label: string, idx: number): string => {
    if (label === "verify") {
      const v = verifyRaw[idx];
      if (v == null) return "verify: —";
      const diag = views[idx]?.diag;
      const tail = diag
        ? ` (workspace acc on n=${diag.workspaceN}, +${diag.samplesAdded} fresh)`
        : "";
      return `verify: ${v.toFixed(3)}${tail}`;
    }
    if (label === "ability") {
      const v = thetaRaw[idx];
      return `ability θ: ${v == null ? "—" : v.toFixed(2)}`;
    }
    const src = label === "accuracy" ? accRaw : label === "composite" ? compRaw : whatifRaw;
    const v = src[idx];
    return `${label}: ${v == null ? "—" : v.toFixed(3)}`;
  };

  const rotate = labels.length > ROTATE_THRESHOLD;
  const options = useMemo<ChartOptions<"bar">>(() => ({
    responsive: true,
    maintainAspectRatio: false,
    // Animation off — see chart-config.ts for the rationale. Chart.js's
    // internal data-diff is fast enough that a poll-driven update lands in
    // a single frame; the prior 200 ms tween was visible jank, not polish.
    animation: false,
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
      x: { grid: { display: false }, ticks: { font: { size: rotate ? 10 : 11, family: "Cascadia Mono, SF Mono, Menlo, Consolas, monospace" }, autoSkip: false, maxRotation: rotate ? 60 : 0, minRotation: rotate ? 60 : 0 } },
      y: { min: 0, max: 1, grid: { color: getCss("--color-border") }, ticks: { font: { size: 11 }, stepSize: 0.2 } },
      // θ's own axis, declared only while the ability series shows — otherwise the
      // right-hand gutter (which every dendrogram fraction is measured against)
      // would reserve space for an axis with no data.
      ...(showAbility
        ? {
            y1: {
              position: "right" as const,
              grid: { display: false },
              ticks: { font: { size: 11 } },
              // Labels the right axis as θ, sitting at the top next to the left axis's
              // "1" — replaces the "(right axis)" caption the legend used to carry.
              title: {
                display: true,
                text: "[θ]",
                align: "end" as const,
                color: getCss("--color-text-tertiary"),
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
          label: (ctx) => tooltipFor(String(ctx.dataset.label ?? ""), ctx.dataIndex),
          footer: (items) => {
            const idx = items[0]?.dataIndex;
            if (idx == null) return "";
            const lines: string[] = [];
            const n = views[idx]?.n_samples;
            if (n != null) {
              const exp = views[idx]?.n_expected;
              lines.push(
                exp != null && exp !== n
                  ? `${n} of ${exp} samples scored`
                  : `${n} sample${n === 1 ? "" : "s"} scored`,
              );
            }
            // Difficulty-adjusted ability — the metric the winner is elected on, so a
            // shorter (lower-accuracy) winner bar reads as "won on harder samples".
            const theta = views[idx]?.theta;
            if (typeof theta === "number") {
              const se = views[idx]?.theta_se;
              const tail = typeof se === "number" ? ` ± ${se.toFixed(2)}` : "";
              lines.push(`ability θ ${theta.toFixed(2)}${tail} (elected on θ, not accuracy)`);
            }
            const ciLo = views[idx]?.compositeCiLo;
            const ciHi = views[idx]?.compositeCiHi;
            if (typeof ciLo === "number" && typeof ciHi === "number") {
              lines.push(`composite 95% CI [${ciLo.toFixed(3)}, ${ciHi.toFixed(3)}]`);
            }
            // Say why there is no crown and no θ, rather than leaving the absence to be
            // read as a loss. The election is a round-scoped fit over every arm, so it
            // cannot exist until the round closes — an uncrowned bar in an open round has
            // nothing yet to have lost to.
            if (views[idx]?.roundOpen) {
              lines.push("round still open — no election yet");
            }
            return lines.join("\n");
          },
        },
      },
      sampleCount: { counts: views.map((v) => v.n_samples) },
      divergenceLine: { index: divergenceBoundary },
      inFlightPulse: { index: inFlightIndex },
      compositeCiWhisker: {
        ciLo: views.map((v) => v.compositeCiLo),
        ciHi: views.map((v) => v.compositeCiHi),
      },
      xBridge: { onGeometry },
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [themeVersion, accRaw, compRaw, whatifRaw, verifyRaw, thetaRaw, rotate, views, selectedKey, onSelect, divergenceBoundary, inFlightIndex, showAbility, onGeometry]);

  return (
    <div className="fitness-chart-frame">
      <Bar data={data} options={options} plugins={CHART_PLUGINS} />
    </div>
  );
});
