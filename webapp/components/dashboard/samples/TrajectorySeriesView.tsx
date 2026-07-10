"use client";
// Series grid for the Sample Trajectory: one row per round, one column per
// sample (union, first-appearance order), each cell coloured by position
// change. Hovering a cell lazy-fetches the round's picker order; clicking seeds
// the fitness sample-set.

import { useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { useRoundFile } from "@/lib/hooks/useRoundFile";
import { useSelection } from "@/lib/SelectionContext";
import { useWorkspace } from "@/lib/workspace";
import {
  classifyCell,
  historicalTimeline,
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
import {
  COL_HEADER,
  POP_COMPUTED,
  POP_CURRENT,
  POP_PLANNED,
  ROW_LABEL,
  SQ_ABSENT,
  SQ_GAINED,
  SQ_KEPT,
  SQ_LOST,
  SQ_NEW,
} from "./trajectoryStyles";

// Cell kind → swatch (presentation half; `classifyCell` owns the predicate).
const CELL_STYLE: Record<Exclude<CellKind, "absent">, CSSProperties> = {
  new: SQ_NEW,
  gained: SQ_GAINED,
  lost: SQ_LOST,
  kept: SQ_KEPT,
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
  // Viewed leaf path self-sourced — the hover popup's lazy round-file fetch
  // follows the viewed cycle (an L4 inner loop reads the inner cycle's dir),
  // always under the workspace context, so no threading is needed.
  const { viewedPath } = useWorkspace();
  const columns = useMemo(() => unionFirstAppearance(sorted.rounds), [sorted.rounds]);
  const { sampleSet, setSelectionForSampleSet } = useSelection();
  const [hover, setHover] = useState<HoverState | null>(null);

  // Cumulative ever-seen set per round-index so we can distinguish
  // "newly added" (never seen before) from "re-added after a drop" — both
  // get the NEW colour today, but kept separate so we can split later.
  const everSeen = cumulativeEverSeen(sorted.rounds);

  // Lazy-fetch the hovered round's durable timeline (picker's intended order).
  // Keyed on the round, so moving between cells in the same row never refetches.
  const { doc } = useRoundFile(viewedPath, hover ? hover.round : null);
  const timeline = useMemo(() => historicalTimeline(doc), [doc]);

  const hoveredSelection = hover
    ? (sorted.rounds.find((r) => r.round === hover.round)?.selection ?? [])
    : [];
  const order = hover
    ? orderAtStep(timeline, hoveredSelection, hover.sampleId, hover.position)
    : null;
  // Clicking a cell loads this state into the fitness sample-set — measured-
  // through-here by default, or the whole round when selectMode is "all".
  const seedSet = order ? seedFromOrder(order, selectMode) : [];

  return (
    <div
      style={{
        overflowX: "auto",
        overflowY: maxHeight != null ? "auto" : "visible",
        maxHeight,
        paddingBottom: 4,
      }}
      onMouseLeave={() => setHover(null)}
    >
      <div style={{ display: "inline-flex", flexDirection: "column", gap: 3, minWidth: "fit-content" }}>
        {/* column header — sample ids */}
        <div style={{ display: "flex", gap: 3 }}>
          <span style={ROW_LABEL}>id</span>
          <span style={{ display: "flex", gap: 3 }}>
            {columns.map((sid) => (
              <span key={sid} style={COL_HEADER}>{sid}</span>
            ))}
          </span>
        </div>
        {/* one row per round */}
        {sorted.rounds.map((r, i) => {
          const pos = sorted.positions[i];
          const prev = i > 0 ? sorted.positions[i - 1] : null;
          const everPrev = i > 0 ? everSeen[i - 1] : new Set<number>();
          const total = (r.selection ?? []).length;
          return (
            <div key={r.round} style={{ display: "flex", gap: 3 }}>
              <span style={ROW_LABEL}>R{r.round}</span>
              <span style={{ display: "flex", gap: 3 }}>
                {columns.map((sid) => {
                  const kind = classifyCell(sid, pos, prev, everPrev);
                  if (kind === "absent") {
                    return <span key={sid} style={SQ_ABSENT}>·</span>;
                  }
                  const p = pos.get(sid)!;
                  const pp = prev?.get(sid);
                  const style = CELL_STYLE[kind];
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
                  const isHovered = hover?.round === r.round && hover?.sampleId === sid;
                  return (
                    <span
                      key={sid}
                      role="button"
                      tabIndex={0}
                      aria-label={`Sample ${sid}, round ${r.round}, position ${p} of ${total}. ${titleNote}. Hover for the sample order at this step; click to compare fitness on the samples up to here.`}
                      style={{
                        ...style,
                        cursor: "pointer",
                        outline: isHovered ? "1px solid var(--color-accent)" : undefined,
                        outlineOffset: isHovered ? -1 : undefined,
                      }}
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
                      onBlur={() => setHover((h) => (h?.round === r.round && h?.sampleId === sid ? null : h))}
                      onClick={() => {
                        const o = orderAtStep(
                          historicalTimeline(doc),
                          r.selection ?? [],
                          sid,
                          p,
                        );
                        setSelectionForSampleSet(seedFromOrder(o, selectMode));
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          const o = orderAtStep(historicalTimeline(doc), r.selection ?? [], sid, p);
                          setSelectionForSampleSet(seedFromOrder(o, selectMode));
                        }
                      }}
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
    <div
      role="tooltip"
      style={{
        position: "fixed",
        left,
        top,
        zIndex: 1000,
        width: 300,
        maxHeight: 260,
        overflowY: "auto",
        padding: 8,
        border: "0.5px solid var(--color-border)",
        borderRadius: 4,
        background: "var(--color-background-primary)",
        boxShadow: "0 6px 24px rgba(0,0,0,0.35)",
        fontFamily: "var(--font-mono)",
        pointerEvents: "none",
      }}
    >
      <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 6 }}>
        R{hover.round} · order at sample #{hover.sampleId} (step {hover.position}/{hover.total})
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 3 }}>
        {order.computed.map((sid) => (
          <span key={`c-${sid}`} style={POP_COMPUTED} title={`#${sid} — already computed`}>
            {sid}
          </span>
        ))}
        <span style={POP_CURRENT} title={`#${order.current} — measuring now`}>
          ▶{order.current}
        </span>
        {order.planned.map((sid) => (
          <span key={`p-${sid}`} style={POP_PLANNED} title={`#${sid} — planned`}>
            {sid}
          </span>
        ))}
      </div>
      <div style={{ fontSize: 10, color: "var(--color-text-tertiary)", marginTop: 6 }}>
        {order.computed.length} computed · {order.planned.length} planned —{" "}
        {isLoaded ? "loaded in fitness ✓" : `click to compare fitness on ${seedSet.length} samples`}
      </div>
    </div>
  );
}

function Legend() {
  const swatch = (style: CSSProperties, label: string) => (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      <span style={{ ...style, width: 12, height: 12, fontSize: 0 }} />
      <span style={{ fontSize: 10, color: "var(--color-text-tertiary)", fontFamily: "var(--font-mono)" }}>{label}</span>
    </span>
  );
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: 6, paddingLeft: 42 }}>
      {swatch(SQ_NEW, "new / re-added")}
      {swatch(SQ_GAINED, "gained position")}
      {swatch(SQ_LOST, "lost position")}
      {swatch(SQ_KEPT, "kept position")}
      {swatch(SQ_ABSENT, "not in bank")}
    </div>
  );
}
