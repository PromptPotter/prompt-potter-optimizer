"use client";
import { COLUMNS, type ColId } from "./columns";

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
// shows HIT/MISS plus the measurement ordinal.
export function HardSamplesHeatTip({
  tip,
}: {
  tip: { ord: string; hit: boolean | null; x: number; y: number };
}) {
  return (
    <div
      className="hs-heat-tip"
      style={{ left: `${tip.x + 14}px`, top: `${tip.y + 14}px` }}
    >
      {tip.hit == null ? "—" : tip.hit ? "HIT" : "MISS"} · {tip.ord}
    </div>
  );
}
