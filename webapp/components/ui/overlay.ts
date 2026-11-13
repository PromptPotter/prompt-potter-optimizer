import type { CSSProperties } from "react";

// An overlay's margin to the viewport's edge.
export const EDGE = 8;

// The free space toward each side the overlay grows; its CSS takes the smaller of it and the host's
// own cap, so no placement reaches past an edge.
export const room = (w: number, h: number) =>
  ({ "--overlay-room-w": `${w}px`, "--overlay-room-h": `${h}px` }) as CSSProperties;

// Under a trigger in the viewport's upper half, else over it, `gap` clear of it; `level` starts at
// the trigger's near edge instead, growing down or up across it. Trigger geometry only.
export const vertical = (r: DOMRect, gap: number, level = false) => {
  const vh = window.innerHeight;
  if (r.top * 2 < vh) {
    const top = level ? r.top : r.bottom + gap;
    return { at: { top }, h: vh - top - EDGE };
  }
  const bottom = vh - (level ? r.bottom : r.top - gap);
  return { at: { bottom }, h: vh - bottom - EDGE };
};
