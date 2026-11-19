import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";

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

// An open overlay sits at its trigger as it is NOW: placed on `show`, then again each frame its
// trigger's rect or the viewport moved. A frame, not scroll and resize events: a header loading
// above the trigger moves it with neither, and an overlay left where the trigger was covers it.
// `place` maps the trigger's rect to the overlay's style; keep it stable.
export function useAnchored(place: (r: DOMRect) => CSSProperties) {
  const trigger = useRef<HTMLElement | null>(null);
  const setTrigger = useCallback((n: HTMLElement | null) => {
    trigger.current = n;
  }, []);
  const [at, setAt] = useState<CSSProperties | null>(null);
  const open = at !== null;

  const show = useCallback(() => {
    const r = trigger.current?.getBoundingClientRect();
    setAt(r ? place(r) : null);
  }, [place]);
  const close = useCallback(() => setAt(null), []);

  useEffect(() => {
    if (!open) return;
    let seen = "";
    let frame = requestAnimationFrame(function follow() {
      const r = trigger.current?.getBoundingClientRect();
      const now = r ? `${r.left} ${r.top} ${r.right} ${r.bottom} ${window.innerWidth} ${window.innerHeight}` : "";
      if (r && now !== seen) setAt(place(r));
      seen = now;
      frame = requestAnimationFrame(follow);
    });
    return () => cancelAnimationFrame(frame);
  }, [open, place]);

  return { trigger, setTrigger, at, show, close };
}
