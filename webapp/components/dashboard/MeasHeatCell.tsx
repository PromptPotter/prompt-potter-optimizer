"use client";
import { useEffect, useRef, useState, type PointerEvent } from "react";
import {
  bucketRow,
  colX,
  heatLayout,
  mixHitColor,
  parseRgbTriple,
  xToOrdIndex,
  type HeatDot,
  type Rgb,
} from "@/lib/heat-canvas";

// Canvas height in CSS px; the hit/miss bars are CELL_H tall, centred.
const CANVAS_H = 20;
const CELL_H = 8;
// Below this cell width the strip is too crowded for gaps + rounded
// corners — it falls back to flush bars (matches the old squeezed strip).
const NICE_MIN_CELL_PX = 4;
// Hover-grow factor — the old per-cell `transform:scale(1.6)`.
const HOVER_SCALE = 1.6;

interface Props {
  // This row's measurements, keyed by ordinal.
  byOrd: Map<string, HeatDot>;
  // Global ordinal universe (sorted) + its reverse index. Shared refs.
  ordCols: string[];
  ordIndex: Map<string, number>;
  // Meas-column width in CSS px (the table's computed track width).
  widthPx: number;
  // True for the sample being scored right now — draws a pending marker.
  isRunning: boolean;
  // Table-level ord+hit signature. Stable across polls; changes only when
  // measurement data actually changes — the sole redraw gate (a fresh
  // `byOrd`/`ordCols` Map every poll must NOT trigger a repaint).
  drawSig: string;
  // Active theme key — a flip swaps the CSS palette, so the canvas (which
  // resolves colours imperatively) must repaint.
  themeKey: string;
  onSelectOrd: (ord: string) => void;
  onHover: (ord: string, hit: boolean | null, clientX: number, clientY: number) => void;
  onHoverEnd: () => void;
}

function readRgb(prop: string, fallback: Rgb): Rgb {
  return parseRgbTriple(
    getComputedStyle(document.documentElement).getPropertyValue(prop),
    fallback,
  );
}

function fillRoundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
): void {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, Math.max(0, Math.min(r, w / 2, h / 2)));
  ctx.fill();
}

// One row of the measurement heat-map, painted to a <canvas>. One DOM node
// per row instead of one <span> per ordinal — the strip can hold tens of
// thousands of measurements without melting the page. When the ordinals fit
// the column (campaign scope) the cells get gaps, rounded corners and a
// hover-grow, matching the old span design; when they vastly outnumber the
// pixels (all-campaigns scope) they collapse to a flush texture.
export function MeasHeatCell({
  byOrd,
  ordCols,
  ordIndex,
  widthPx,
  isRunning,
  drawSig,
  themeKey,
  onSelectOrd,
  onHover,
  onHoverEnd,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const rafRef = useRef<number | null>(null);
  // Hovered ordinal index — drives the hover-grow. Only set in discrete
  // ("nice") mode; null in bucket mode so a mousemove never churns redraws.
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const paint = () => {
      rafRef.current = null;
      const w = Math.max(0, Math.round(widthPx));
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(CANVAS_H * dpr);
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, CANVAS_H);
      const success = readRgb("--color-success-rgb", [16, 185, 129]);
      const danger = readRgb("--color-danger-rgb", [248, 113, 113]);
      const layout = heatLayout(ordCols.length, w);
      const buckets = bucketRow(layout, byOrd, ordIndex);
      // Neat-square mode: cells wide enough for a 1 px gap + rounded
      // corners. Below that, flush bars (the squeezed-strip look).
      const nice = layout.mode === "discrete" && layout.cellW >= NICE_MIN_CELL_PX;
      const gap = nice ? 1 : 0;
      const y = Math.round((CANVAS_H - CELL_H) / 2);

      for (let col = 0; col < buckets.length; col++) {
        const b = buckets[col];
        if (!b || b.total === 0) continue;
        if (nice && col === hoverIdx) continue; // drawn enlarged, on top
        const span = colX(layout, col);
        const cw = Math.max(1, span.w - gap);
        ctx.fillStyle = mixHitColor(b.hits / b.total, success, danger);
        if (nice) fillRoundRect(ctx, span.x, y, cw, CELL_H, cw * 0.28);
        else ctx.fillRect(span.x, y, cw, CELL_H);
      }

      // Hover-grow the focused cell — the old `transform:scale(1.6)`.
      if (nice && hoverIdx != null) {
        const b = buckets[hoverIdx];
        if (b && b.total > 0) {
          const span = colX(layout, hoverIdx);
          const cw = Math.max(1, span.w - gap);
          const sw = cw * HOVER_SCALE;
          const sh = CELL_H * HOVER_SCALE;
          ctx.fillStyle = mixHitColor(b.hits / b.total, success, danger);
          fillRoundRect(
            ctx,
            span.x + cw / 2 - sw / 2,
            CANVAS_H / 2 - sh / 2,
            sw,
            sh,
            sw * 0.28,
          );
        }
      }

      // Pending marker for the sample being scored — dashed accent box
      // with a soft glow, mirroring the old `.hs-heat-cell.pending`.
      if (isRunning) {
        const a = readRgb("--color-accent-rgb", [245, 158, 11]);
        const pw = nice ? Math.max(4, layout.cellW - gap) : 4;
        ctx.save();
        ctx.strokeStyle = `rgb(${a[0]},${a[1]},${a[2]})`;
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 1.5]);
        ctx.shadowColor = `rgba(${a[0]},${a[1]},${a[2]},0.55)`;
        ctx.shadowBlur = 4;
        ctx.strokeRect(w - pw - 0.5, y + 0.5, pw, CELL_H - 1);
        ctx.restore();
      }
    };
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(paint);
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
    // `byOrd`/`ordCols`/`ordIndex` are fresh objects every poll — read at
    // paint time but kept out of the deps on purpose; `drawSig` is the
    // content signature that says when a repaint is actually warranted.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drawSig, widthPx, isRunning, themeKey, hoverIdx]);

  // Pointer x → ordinal index + whether the strip is in discrete mode.
  const locate = (
    e: PointerEvent<HTMLCanvasElement>,
  ): { idx: number; discrete: boolean } | null => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const layout = heatLayout(ordCols.length, rect.width);
    const idx = xToOrdIndex(layout, e.clientX - rect.left);
    if (idx == null) return null;
    return { idx, discrete: layout.mode === "discrete" };
  };

  return (
    <canvas
      ref={canvasRef}
      className="hs-heat-canvas"
      onPointerDown={(e) => {
        const hit = locate(e);
        if (hit) onSelectOrd(ordCols[hit.idx]);
      }}
      onPointerMove={(e) => {
        const hit = locate(e);
        if (!hit) {
          setHoverIdx(null);
          onHoverEnd();
          return;
        }
        setHoverIdx(hit.discrete ? hit.idx : null);
        const ord = ordCols[hit.idx];
        const m = byOrd.get(ord);
        onHover(ord, m ? m.hit : null, e.clientX, e.clientY);
      }}
      onPointerLeave={() => {
        setHoverIdx(null);
        onHoverEnd();
      }}
    />
  );
}
