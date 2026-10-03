"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { cx } from "@/lib/cx";
import s from "./Popover.module.css";

type Align = "left" | "right";
type Side = "bottom" | "top" | "over";

interface Props {
  renderTrigger: (args: { open: boolean; toggle: () => void }) => ReactNode;
  children: (args: { close: () => void }) => ReactNode;
  align?: Align;
  /** `top` for a trigger at the bottom of its scrollport; `over` covers the trigger (dropdown list). */
  side?: Side;
  className?: string;
}

const GAP = 2;

const place = (r: DOMRect, align: Align, side: Side): CSSProperties => {
  const x = align === "right" ? { right: window.innerWidth - r.right } : { left: r.left };
  if (side === "top") return { ...x, bottom: window.innerHeight - r.top + GAP };
  if (side === "over") return { ...x, top: r.top, minWidth: r.width };
  return { ...x, top: r.bottom + GAP };
};

// The one popover: owns open state, click-outside and Escape. Portaled to <body> so no ancestor's
// `overflow` clips it.
export function Popover({
  renderTrigger,
  children,
  align = "left",
  side = "bottom",
  className,
}: Props) {
  const [anchor, setAnchor] = useState<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  // `null` IS closed.
  const [at, setAt] = useState<CSSProperties | null>(null);
  const open = at !== null;
  const close = useCallback(() => setAt(null), []);
  const toggle = useCallback(() => {
    const r = anchor?.getBoundingClientRect();
    setAt((prev) => (prev || !r ? null : place(r, align, side)));
  }, [anchor, align, side]);

  useEffect(() => {
    if (!open) return;
    const inside = (t: EventTarget | null) =>
      t instanceof Node && !!(anchor?.contains(t) || panelRef.current?.contains(t));
    const onDoc = (e: MouseEvent) => {
      if (!inside(e.target)) setAt(null);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setAt(null);
    };
    const follow = (e: Event) => {
      if (e.target instanceof Node && panelRef.current?.contains(e.target)) return;
      const r = anchor?.getBoundingClientRect();
      if (r) setAt(place(r, align, side));
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", follow, true);
    window.addEventListener("resize", follow);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", follow, true);
      window.removeEventListener("resize", follow);
    };
  }, [open, anchor, align, side]);

  return (
    <div className={cx(s.wrap, className)} ref={setAnchor}>
      {renderTrigger({ open, toggle })}
      {at &&
        createPortal(
          <div ref={panelRef} className={s.panel} style={at}>
            {children({ close })}
          </div>,
          document.body,
        )}
    </div>
  );
}
