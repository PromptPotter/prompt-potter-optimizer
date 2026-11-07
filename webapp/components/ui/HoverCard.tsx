import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { createPortal } from "react-dom";
import { HOVER_CLOSE_GRACE_MS } from "@/lib/cadence";
import { cx } from "@/lib/cx";
import s from "./HoverCard.module.css";

const GAP = 8;
// The card's margin to the viewport's edge.
const EDGE = 8;
// Narrower than this beside its trigger on both sides (a phone), the card spans the viewport under
// or over the trigger instead.
const BESIDE_MIN = 240;

// The free space toward each side the card grows; `.card` takes the smaller of it and its host's own
// cap, so no placement reaches past an edge.
const room = (w: number, h: number) =>
  ({ "--hovercard-room-w": `${w}px`, "--hovercard-room-h": `${h}px` }) as CSSProperties;

// Trigger geometry only — nothing measures the card, so late-arriving content can't strand it.
const anchor = (r: DOMRect): CSSProperties => {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const high = r.top * 2 < vh;
  const toRight = vw - r.right - GAP - EDGE;
  const toLeft = r.left - GAP - EDGE;
  if (Math.max(toRight, toLeft) < BESIDE_MIN) {
    return {
      position: "fixed",
      left: EDGE,
      ...(high ? { top: r.bottom + GAP } : { bottom: vh - r.top + GAP }),
      ...room(vw - 2 * EDGE, high ? vh - r.bottom - GAP - EDGE : r.top - GAP - EDGE),
    };
  }
  return {
    position: "fixed",
    ...(high ? { top: r.top } : { bottom: vh - r.bottom }),
    ...(toRight >= toLeft ? { left: r.right + GAP } : { right: vw - r.left + GAP }),
    ...room(Math.max(toRight, toLeft), high ? vh - r.top - EDGE : r.bottom - EDGE),
  };
};

export function HoverCard({
  content,
  className,
  block = false,
  focusable = false,
  children,
}: {
  content: ReactNode;
  className?: string;
  block?: boolean;
  // The trigger takes focus itself, for children that hold no control of their own.
  focusable?: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLElement | null>(null);
  const setRef = useCallback((n: HTMLElement | null) => {
    ref.current = n;
  }, []);
  const cardRef = useRef<HTMLDivElement>(null);
  const timer = useRef(0);
  const [at, setAt] = useState<CSSProperties | null>(null);
  const open = at !== null;

  const within = useCallback(
    (n: Node | null) => !!n && (!!ref.current?.contains(n) || !!cardRef.current?.contains(n)),
    [],
  );

  const show = useCallback(() => {
    window.clearTimeout(timer.current);
    const r = ref.current?.getBoundingClientRect();
    if (r) setAt(anchor(r));
  }, []);

  // Never immediate: a pointer crossing the GAP is over neither half for a moment.
  const hide = useCallback(() => {
    timer.current = window.setTimeout(() => setAt(null), HOVER_CLOSE_GRACE_MS);
  }, []);

  // A held button is a drag-select in flight, so the release below closes it instead of this.
  const leave = useCallback(
    (e: { buttons: number }) => {
      if (e.buttons === 0) hide();
    },
    [hide],
  );

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setAt(null);
    };
    const onUp = (e: PointerEvent) => {
      if (!within(e.target as Node)) hide();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerup", onUp);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerup", onUp);
    };
  }, [open, hide, within]);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const Wrap = block ? "div" : "span";
  return (
    <Wrap
      ref={setRef}
      className={block ? s.block : s.wrap}
      tabIndex={focusable ? 0 : undefined}
      onMouseEnter={show}
      onMouseLeave={leave}
      // A tap opens it itself: iOS focuses a tapped span only sometimes.
      onPointerUp={(e) => {
        if (e.pointerType !== "mouse") show();
      }}
      onFocus={show}
      onBlur={(e) => {
        if (!within(e.relatedTarget)) hide();
      }}
    >
      {children}
      {at &&
        createPortal(
          <div
            ref={cardRef}
            className={cx(s.card, className)}
            role="note"
            style={at}
            onMouseEnter={show}
            onMouseLeave={leave}
          >
            {content}
          </div>,
          document.body,
        )}
    </Wrap>
  );
}
