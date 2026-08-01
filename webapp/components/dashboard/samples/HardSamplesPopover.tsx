"use client";
import { COLUMNS, type ColId } from "./columns";
import { isHit } from "@/lib/fitness";

// Full-text popover for a clipped left-aligned cell — opened by clicking an
// expandable cell, dismissed by backdrop click or Escape (the Escape handler
// lives on the owner so it can coexist with the table's other key handlers).
export function HardSamplesPopover({
  popover,
  onClose,
}: {
  popover: { col: ColId; sampleId: number; text: string };
  onClose: () => void;
}) {
  return (
    <div className="hs-popover-backdrop" onClick={onClose}>
      <div className="hs-popover" onClick={(e) => e.stopPropagation()}>
        <div className="hs-popover-header">
          <span>
            Sample {popover.sampleId} ·{" "}
            {COLUMNS.find((c) => c.id === popover.col)?.label}
          </span>
          <button type="button" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <pre className="hs-popover-body">{popover.text}</pre>
      </div>
    </div>
  );
}

// One shared hover read-out for the History heat-map — follows the cursor,
// shows the graded score plus the measurement ordinal. A binary scorer reads
// HIT/MISS; a graded one reads its actual value rather than a flat "MISS".
export function HardSamplesHeatTip({
  tip,
}: {
  tip: { ord: string; fitness: number | null; x: number; y: number };
}) {
  return (
    <div
      className="hs-heat-tip"
      style={{ left: `${tip.x + 14}px`, top: `${tip.y + 14}px` }}
    >
      {tip.fitness == null
        ? "—"
        : isHit(tip.fitness)
          ? "HIT"
          : `${tip.fitness.toFixed(2)}`}{" "}
      · {tip.ord}
    </div>
  );
}
