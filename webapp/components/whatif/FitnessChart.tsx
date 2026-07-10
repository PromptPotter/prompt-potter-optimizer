"use client";
import { memo, useMemo } from "react";
import { Bar } from "react-chartjs-2";
import { cssRgba, ensureChartRegistered, getCss, useThemeVersion } from "@/lib/theme";
import type { ChartData, ChartOptions, ChartType, Plugin } from "chart.js";

ensureChartRegistered();

// Per-bar sample count painted above each candidate's bar group. The metric
// (accuracy / composite / what-if) is computed over `nSamples` samples — a
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

// Error-bar whisker on the composite bar — a vertical line from `ciLo` to `ciHi`
// (mapped value→pixel via the y scale) with short end-caps, so no composite point
// estimate stands alone. Drawn at the composite dataset's OWN rendered x (which
// shifts within the bar group depending on how many series are showing), not the
// category center — a whisker must sit on the bar it brackets. Bars with a null
// CI (in-flight round, or composite itself null) are skipped.
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
    const dsIndex = chart.data.datasets.findIndex((ds) => ds.label === "composite");
    if (dsIndex < 0) return;
    const meta = chart.getDatasetMeta(dsIndex);
    const { ctx, chartArea } = chart;
    ctx.save();
    ctx.strokeStyle = getCss("--color-ci");
    ctx.lineWidth = 1.5;
    const capHalf = 4;
    // The normal-CLT CI (`mean_ci`) can spill past [0,1] near the boundary — a
    // real case is ci_lo < 0 on a low-composite candidate. The y-axis is fixed
    // [0,1], so an unclamped pixel lands OUTSIDE the plot (a whisker hanging below
    // the axis reads as a stray bar). Clamp both ends to the plot's own pixel span.
    const clampY = (px: number) =>
      Math.min(chartArea.bottom, Math.max(chartArea.top, px));
    for (let i = 0; i < ciLo.length; i++) {
      const lo = ciLo[i];
      const hi = ciHi[i];
      if (lo == null || hi == null) continue;
      const el = meta.data[i] as
        | { getProps?: (p: string[], final: boolean) => Record<string, number> }
        | undefined;
      const x = el?.getProps?.(["x"], true)?.x;
      if (typeof x !== "number") continue;
      const yLo = clampY(yScale.getPixelForValue(lo));
      const yHi = clampY(yScale.getPixelForValue(hi));
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
    const red = getCss("--color-danger") || "#e5484d";
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
      if (!p || [p.x, p.y, p.base, p.width].some((v) => typeof v !== "number")) return;
      left = Math.min(left, p.x - p.width / 2);
      right = Math.max(right, p.x + p.width / 2);
      top = Math.min(top, p.y);
      base = Math.max(base, p.base);
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

// Stable identity — passing a fresh array each render churns the chart.
const CHART_PLUGINS = [
  sampleCountPlugin,
  divergenceLinePlugin,
  inFlightPulsePlugin,
  compositeCiWhiskerPlugin,
];

// Above this real-bar count, x-axis labels rotate 60° so they stop
// overlapping their neighbours. Picked empirically against the 240px
// fitness-card height (rotated labels eat ~30px of plot height).
const ROTATE_THRESHOLD = 8;

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
  nSamples: number | null;   // samples the metric was computed over (null = unknown)
  nExpected: number | null;  // full sample budget; set only when < nSamples is possible
  candidateId?: string;
  round?: number;
  isWinner?: boolean;
  // Difficulty-adjusted Rasch ability (logit scale) the winner is elected on — shown in the
  // tooltip, NOT as a bar (θ is logits, the y-axis is the 0–1 accuracy scale). Null when no
  // election fit (in-flight / eliminated). This is what explains a lower-accuracy winner.
  theta?: number | null;
  thetaSe?: number | null;
  // Normal-CLT CI on the mean of this bar's per-sample composite fitness — drawn as
  // an error-bar whisker around the composite bar. Null when not yet stamped (in-flight
  // round) or when composite itself is null (nothing to bracket).
  compositeCiLo?: number | null;
  compositeCiHi?: number | null;
  // Workspace-verify overlay — `accuracy` is the workspace accuracy
  // (mean hit rate) over every archive measurement for this candidate's
  // config; `workspaceN` is the total sample count behind it (typically
  // >> the campaign n). Same metric as the blue accuracy bar so the two
  // are visually apples-to-apples. Set only on bars that have a matching
  // DiagnosticRunRecord on disk; drives the red `verify` series.
  diag?: { accuracy: number; workspaceN: number; samplesAdded: number };
}

interface Props {
  bars: BarSlot[];
  showComposite: boolean;
  showWhatIf: boolean;
  selectedKey: string | null;
  onSelect: (bar: BarSlot | null) => void;
  // Bar index where the active mask first diverges from the realized record —
  // the red vertical divider is drawn at its left edge. null = no mask / no
  // divergence (no divider).
  divergenceBoundary: number | null;
  // Bar index of the candidate currently accumulating samples — it pulses
  // ("blinking") while live. null = nothing scoring (no pulse).
  inFlightIndex: number | null;
}

export const FitnessChart = memo(function FitnessChart({
  bars,
  showComposite,
  showWhatIf,
  selectedKey,
  onSelect,
  divergenceBoundary,
  inFlightIndex,
}: Props) {
  // Subscribe to theme so a flip re-runs this component and the data/options
  // memos below pick up the new getCss() values.
  const themeVersion = useThemeVersion();
  // One x-axis label per real bar. No empty-slot padding: chart.js spaces
  // categories evenly across the frame, and the `maxBarThickness` cap below
  // keeps individual bars narrow even at very low counts (a 2-bar round
  // shows two 28px bars centered, not two stretched-to-fill bars).
  const labels = useMemo(() => bars.map((b) => b.label), [bars]);

  // Two parallel arrays per series: *Raw drives the tooltip; *Plot pushes
  // null → 0 only for bars whose scoring has begun (so `minBarLength`
  // paints a stub). Unstarted bars stay null so chart.js leaves the slot
  // blank — distinguishing "still computing" from "not yet started".
  // Sized to the padded slot count; padding slots stay null (no bar).
  const { accRaw, compRaw, whatifRaw, verifyRaw, accPlot, compPlot, whatifPlot, verifyPlot } = useMemo(() => {
    const aR: (number | null)[] = [];
    const cR: (number | null)[] = [];
    const wR: (number | null)[] = [];
    const vR: (number | null)[] = [];
    for (let i = 0; i < labels.length; i++) {
      aR.push(bars[i]?.accuracy ?? null);
      cR.push(bars[i]?.composite ?? null);
      wR.push(bars[i]?.whatif ?? null);
      vR.push(bars[i]?.diag?.accuracy ?? null);
    }
    const coerce = (v: number | null, i: number): number | null =>
      v == null ? (bars[i]?.started ? 0 : null) : v;
    // The verify series stays strictly sparse — no started-floor coercion.
    // A bar without a diagnostic-run record must render NO red bar, not a
    // 0-height stub, or every column would carry a misleading red dot.
    return {
      accRaw: aR, compRaw: cR, whatifRaw: wR, verifyRaw: vR,
      accPlot: aR.map(coerce), compPlot: cR.map(coerce), whatifPlot: wR.map(coerce),
      verifyPlot: vR,
    };
  }, [bars, labels]);

  // Per-bar border overlay — picks out the bar matching the shared
  // SelectionContext. Driven by --color-selection (set in app/styles/foundation/themes.css)
  // so it matches the lineage tree's selected stub colour.
  const selectionBorder = useMemo(() => {
    if (selectedKey == null) return null;
    const idx = bars.findIndex((b) => b.key === selectedKey);
    if (idx < 0) return null;
    const colour = getCss("--color-selection");
    return { idx, colour };
    // themeVersion gates getCss() — needed in deps; lint flags it unused.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bars, selectedKey, themeVersion]);

  const hasVerify = useMemo(() => bars.some((b) => b?.diag != null), [bars]);
  const data = useMemo<ChartData<"bar">>(() => {
    const seriesCount =
      1 + (showComposite ? 1 : 0) + (showWhatIf ? 1 : 0) + (hasVerify ? 1 : 0);
    const cat =
      seriesCount === 1 ? 0.55 : seriesCount === 2 ? 0.75 : seriesCount === 3 ? 0.9 : 0.95;
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
    if (hasVerify) {
      // Workspace-verify overlay — populated only on bars with a matching
      // DiagnosticRunRecord. Red is the only series colour outside the
      // accent palette: this is the operator's "did the verdict hold on
      // more samples?" signal, intentionally visually distinct.
      datasets.push({
        label: "verify",
        data: verifyPlot,
        backgroundColor: "#e74c3c",
        borderColor: "#e74c3c",
        borderWidth: 0,
        barPercentage: 0.95,
        categoryPercentage: cat,
        maxBarThickness: maxBar,
        minBarLength: 2,
      });
    }
    return { labels, datasets };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [labels, accPlot, compPlot, whatifPlot, verifyPlot, showComposite, showWhatIf, hasVerify, themeVersion, selectionBorder]);

  const tooltipFor = (label: string, idx: number): string => {
    if (label === "verify") {
      const v = verifyRaw[idx];
      if (v == null) return "verify: —";
      const diag = bars[idx]?.diag;
      const tail = diag ? ` (workspace acc on n=${diag.workspaceN}, +${diag.samplesAdded} fresh)` : "";
      return `verify: ${v.toFixed(3)}${tail}`;
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
      y: { min: 0, max: 1, grid: { color: getCss("--color-border") }, ticks: { font: { size: 11 }, stepSize: 0.25 } },
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
            const n = bars[idx]?.nSamples;
            if (n != null) {
              const exp = bars[idx]?.nExpected;
              lines.push(
                exp != null && exp !== n
                  ? `${n} of ${exp} samples scored`
                  : `${n} sample${n === 1 ? "" : "s"} scored`,
              );
            }
            // Difficulty-adjusted ability — the metric the winner is elected on, so a
            // shorter (lower-accuracy) winner bar reads as "won on harder samples".
            const theta = bars[idx]?.theta;
            if (typeof theta === "number") {
              const se = bars[idx]?.thetaSe;
              const tail = typeof se === "number" ? ` ± ${se.toFixed(2)}` : "";
              lines.push(`ability θ ${theta.toFixed(2)}${tail} (elected on θ, not accuracy)`);
            }
            const ciLo = bars[idx]?.compositeCiLo;
            const ciHi = bars[idx]?.compositeCiHi;
            if (typeof ciLo === "number" && typeof ciHi === "number") {
              lines.push(`composite 95% CI [${ciLo.toFixed(3)}, ${ciHi.toFixed(3)}]`);
            }
            return lines.join("\n");
          },
        },
      },
      sampleCount: { counts: bars.map((b) => b.nSamples) },
      divergenceLine: { index: divergenceBoundary },
      inFlightPulse: { index: inFlightIndex },
      compositeCiWhisker: {
        ciLo: bars.map((b) => b.compositeCiLo ?? null),
        ciHi: bars.map((b) => b.compositeCiHi ?? null),
      },
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [themeVersion, accRaw, compRaw, whatifRaw, verifyRaw, rotate, bars, selectedKey, onSelect, divergenceBoundary, inFlightIndex]);

  return (
    <div className="fitness-chart-frame">
      <Bar data={data} options={options} plugins={CHART_PLUGINS} />
    </div>
  );
});
