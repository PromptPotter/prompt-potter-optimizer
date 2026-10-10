"use client";

import { useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { pressable } from "@/components/ui";
import { cx } from "@/lib/cx";
import { useSelection } from "@/lib/SelectionContext";
import {
  orderAtStep,
  seedFromOrder,
  type SampleMovement,
  type SelectMode,
  type StepOrder,
  unionFirstAppearance,
  type SortedRounds,
} from "@/lib/derivations";
import { SAMPLE_MOVEMENT_LABELS } from "@/lib/api/types.generated";
import { sameSampleSet } from "@/lib/sample-set";

const CELL_CLASS: Record<SampleMovement, string> = {
  new: "new",
  readded: "new",
  gained: "add",
  lost: "lost",
  kept: "kept",
};

interface HoverState {
  round: number;
  sampleId: number;
  position: number; // 1-indexed
  total: number;
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

  const hoveredSelection = hover
    ? (sorted.rounds.find((r) => r.round === hover.round)?.selection ?? [])
    : [];
  const order = hover ? orderAtStep(hoveredSelection, hover.sampleId, hover.position) : null;
  const seedSet = order ? seedFromOrder(order, selectMode) : [];

  return (
    <div
      className={cx("st-series", maxHeight != null && "scrollable")}
      style={maxHeight != null ? { maxHeight } : undefined}
      onMouseLeave={() => setHover(null)}
    >
      <div className="st-series-inner">
        <div className="st-series-row">
          <span className="st-row-label">id</span>
          <span className="st-series-cells">
            {columns.map((sid) => (
              <span key={sid} className="st-sq st-col-header">{sid}</span>
            ))}
          </span>
        </div>
        {sorted.rounds.map((r, i) => {
          const pos = sorted.positions[i]!;
          const prev = i > 0 ? sorted.positions[i - 1]! : null;
          const movements = sorted.movements[i]!;
          const total = r.selection.length;
          return (
            <div key={r.round} className="st-series-row">
              <span className="st-row-label">R{r.round}</span>
              <span className="st-series-cells">
                {columns.map((sid) => {
                  const kind = movements.get(sid);
                  const p = pos.get(sid);
                  if (kind === undefined || p === undefined) {
                    return <span key={sid} className="st-sq absent">·</span>;
                  }
                  const pp = prev?.get(sid);
                  const titleNote =
                    pp !== undefined && pp !== p
                      ? `${SAMPLE_MOVEMENT_LABELS[kind]}: pos ${pp} → ${p}`
                      : SAMPLE_MOVEMENT_LABELS[kind];
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
      {swatch("st-sq new", `${SAMPLE_MOVEMENT_LABELS.new} / ${SAMPLE_MOVEMENT_LABELS.readded}`)}
      {swatch("st-sq add", SAMPLE_MOVEMENT_LABELS.gained)}
      {swatch("st-sq lost", SAMPLE_MOVEMENT_LABELS.lost)}
      {swatch("st-sq kept", SAMPLE_MOVEMENT_LABELS.kept)}
      {swatch("st-sq absent", "not in bank")}
    </div>
  );
}
