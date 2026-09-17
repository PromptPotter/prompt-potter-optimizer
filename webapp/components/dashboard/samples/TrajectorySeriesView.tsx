"use client";
// Series grid for the Sample Trajectory: one row per round, one column per
// sample (union, first-appearance order), each cell coloured by position
// change. Hovering a cell shows the round's order around it; clicking seeds the
// fitness sample-set. No round-file fetch — the order is positional over the
// round's `selection`, which `dashboard.json` already carries.

import { useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { pressable } from "@/components/ui";
import { cx } from "@/lib/cx";
import { useSelection } from "@/lib/SelectionContext";
import {
  classifyCell,
  orderAtStep,
  seedFromOrder,
  type CellKind,
  type SelectMode,
  type StepOrder,
  cumulativeEverSeen,
  unionFirstAppearance,
  type SortedRounds,
} from "@/lib/derivations";
import { sameSampleSet } from "@/lib/sample-set";

// Cell kind → CSS modifier (presentation half; `classifyCell` owns the predicate).
// "gained" shares the "add" recipe — one colour for one meaning, wherever it lands.
const CELL_CLASS: Record<Exclude<CellKind, "absent">, string> = {
  new: "new",
  gained: "add",
  lost: "lost",
  kept: "kept",
};

interface HoverState {
  round: number;
  sampleId: number;
  position: number; // 1-indexed measurement position in the round
  total: number; // round's measured count
  x: number;
  y: number;
}

export function SeriesView({
  sorted,
  selectMode = "measured",
  maxHeight,
}: {
  sorted: SortedRounds;
  selectMode?: SelectMode;
  maxHeight?: number;
}) {
  const columns = useMemo(() => unionFirstAppearance(sorted.rounds), [sorted.rounds]);
  const { sampleSet, setSelectionForSampleSet } = useSelection();
  const [hover, setHover] = useState<HoverState | null>(null);

  // Cumulative ever-seen set per round-index so we can distinguish
  // "newly added" (never seen before) from "re-added after a drop" — both
  // get the NEW colour today, but kept separate so we can split later.
  const everSeen = cumulativeEverSeen(sorted.rounds);

  // No round-file fetch here: the order is positional over the round's `selection`,
  // which `dashboard.json` already carries. This used to lazy-fetch the whole
  // `round_NNNN.json` on every hover purely to read a `sample_order_timeline` that
  // held one step — and that one step only ever matched the round's FIRST cell, so
  // the fetch bought a divergence rather than fixing one. Both are gone.
  const hoveredSelection = hover
    ? (sorted.rounds.find((r) => r.round === hover.round)?.selection ?? [])
    : [];
  const order = hover ? orderAtStep(hoveredSelection, hover.sampleId, hover.position) : null;
  // Clicking a cell loads this state into the fitness sample-set — measured-
  // through-here by default, or the whole round when selectMode is "all".
  const seedSet = order ? seedFromOrder(order, selectMode) : [];

  return (
    <div
      className={cx("st-series", maxHeight != null && "scrollable")}
      style={maxHeight != null ? { maxHeight } : undefined}
      onMouseLeave={() => setHover(null)}
    >
      <div className="st-series-inner">
        {/* column header — sample ids */}
        <div className="st-series-row">
          <span className="st-row-label">id</span>
          <span className="st-series-cells">
            {columns.map((sid) => (
              <span key={sid} className="st-sq st-col-header">{sid}</span>
            ))}
          </span>
        </div>
        {/* one row per round */}
        {sorted.rounds.map((r, i) => {
          // `positions` and `everSeen` are built one-per-round, parallel to `sorted.rounds`.
          const pos = sorted.positions[i]!;
          const prev = i > 0 ? sorted.positions[i - 1]! : null;
          const everPrev = i > 0 ? everSeen[i - 1]! : new Set<number>();
          const total = r.selection.length;
          return (
            <div key={r.round} className="st-series-row">
              <span className="st-row-label">R{r.round}</span>
              <span className="st-series-cells">
                {columns.map((sid) => {
                  const kind = classifyCell(sid, pos, prev, everPrev);
                  if (kind === "absent") {
                    return <span key={sid} className="st-sq absent">·</span>;
                  }
                  const p = pos.get(sid)!;
                  const pp = prev?.get(sid);
                  // "new" covers first-appearance + re-add (split here by everPrev).
                  const titleNote =
                    kind === "new"
                      ? everPrev.has(sid)
                        ? "re-added"
                        : "newly added"
                      : kind === "gained"
                        ? `gained: pos ${pp} → ${p}`
                        : kind === "lost"
                          ? `lost: pos ${pp} → ${p}`
                          : "kept position";
                  const isHovered = hover?.round === r.round && hover.sampleId === sid;
                  const activate = () => {
                    const o = orderAtStep(r.selection, sid, p);
                    setSelectionForSampleSet(seedFromOrder(o, selectMode));
                  };
                  return (
                    <span
                      key={sid}
                      className={cx("st-sq", CELL_CLASS[kind], "st-series-cell", isHovered && "hovered")}
                      aria-label={`Sample ${sid}, round ${r.round}, position ${p} of ${total}. ${titleNote}. Hover for the sample order at this step; click to compare fitness on the samples up to here.`}
                      onMouseEnter={(e) =>
                        setHover({ round: r.round, sampleId: sid, position: p, total, x: e.clientX, y: e.clientY })
                      }
                      onMouseMove={(e) =>
                        setHover((h) =>
                          h && h.round === r.round && h.sampleId === sid
                            ? { ...h, x: e.clientX, y: e.clientY }
                            : { round: r.round, sampleId: sid, position: p, total, x: e.clientX, y: e.clientY },
                        )
                      }
                      onFocus={(e) => {
                        const box = e.currentTarget.getBoundingClientRect();
                        setHover({ round: r.round, sampleId: sid, position: p, total, x: box.right, y: box.bottom });
                      }}
                      onBlur={() => setHover((h) => (h?.round === r.round && h.sampleId === sid ? null : h))}
                      {...pressable(activate)}
                    >
                      {p}
                    </span>
                  );
                })}
              </span>
            </div>
          );
        })}
        <Legend />
      </div>
      {hover && order && (
        <SeriesHoverPopup
          hover={hover}
          order={order}
          loadedSet={sampleSet}
          seedSet={seedSet}
        />
      )}
    </div>
  );
}

// Floating order-at-step table, fixed at the cursor. Computed samples (✓, in
// measurement order), the one being measured (▶), then the picker's planned
// remainder — the frozen plan at this state.
function SeriesHoverPopup({
  hover,
  order,
  loadedSet,
  seedSet,
}: {
  hover: HoverState;
  order: StepOrder;
  loadedSet: number[] | null;
  seedSet: number[];
}) {
  const vw = typeof window !== "undefined" ? window.innerWidth : 1280;
  const vh = typeof window !== "undefined" ? window.innerHeight : 800;
  const left = Math.min(hover.x + 14, vw - 320);
  const top = Math.min(hover.y + 14, vh - 280);
  const isLoaded = sameSampleSet(loadedSet, seedSet);
  return (
    <div role="tooltip" className="st-hover" style={{ left, top } as CSSProperties}>
      <div className="st-hover-head">
        R{hover.round} · order at sample #{hover.sampleId} (step {hover.position}/{hover.total})
      </div>
      <div className="st-hover-chips">
        {order.computed.map((sid) => (
          <span key={`c-${sid}`} className="st-chip computed" title={`#${sid} — already computed`}>
            {sid}
          </span>
        ))}
        <span className="st-chip current" title={`#${order.current} — measuring now`}>
          ▶{order.current}
        </span>
        {order.planned.map((sid) => (
          <span key={`p-${sid}`} className="st-chip planned" title={`#${sid} — planned`}>
            {sid}
          </span>
        ))}
      </div>
      <div className="st-hover-foot">
        {order.computed.length} computed · {order.planned.length} planned —{" "}
        {isLoaded ? "loaded in fitness ✓" : `click to compare fitness on ${seedSet.length} samples`}
      </div>
    </div>
  );
}

function Legend() {
  const swatch = (className: string, label: string) => (
    <span className="st-legend-item">
      <span className={cx("st-legend-swatch", className)} />
      <span className="st-legend-label">{label}</span>
    </span>
  );
  return (
    <div className="st-legend">
      {swatch("st-sq new", "new / re-added")}
      {swatch("st-sq add", "gained position")}
      {swatch("st-sq lost", "lost position")}
      {swatch("st-sq kept", "kept position")}
      {swatch("st-sq absent", "not in bank")}
    </div>
  );
}
