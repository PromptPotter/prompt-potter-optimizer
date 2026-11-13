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
import { EDGE, room, vertical } from "./overlay";
import s from "./Popover.module.css";

type Align = "left" | "right";

interface Props {
  renderTrigger: (args: { open: boolean; toggle: () => void }) => ReactNode;
  children: (args: { close: () => void }) => ReactNode;
  align?: Align;
  // The panel opens across its trigger, as a list over its field.
  over?: boolean;
  className?: string | undefined;
}

const GAP = 2;

// Flips above a low trigger and clamps the aligned edge inside the viewport, as HoverCard does.
const place = (r: DOMRect, align: Align, over: boolean): CSSProperties => {
  const vw = window.innerWidth;
  const v = vertical(r, GAP, over);
  const inset = Math.max(EDGE, align === "right" ? vw - r.right : r.left);
  return {
    ...(align === "right" ? { right: inset } : { left: inset }),
    ...v.at,
    ...(over ? { minWidth: r.width } : {}),
    ...room(vw - inset - EDGE, v.h),
  };
};

export function Popover({
  renderTrigger,
  children,
  align = "left",
  over = false,
  className,
}: Props) {
  const [anchor, setAnchor] = useState<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [at, setAt] = useState<CSSProperties | null>(null);
  const open = at !== null;
  const close = useCallback(() => setAt(null), []);
  const toggle = useCallback(() => {
    const r = anchor?.getBoundingClientRect();
    setAt((prev) => (prev || !r ? null : place(r, align, over)));
  }, [anchor, align, over]);

  // Inside is the React tree, not the DOM: a popover opened in this panel portals out of it, and its
  // press still bubbles here first (React's root listens below `document`).
  const pressed = useRef(false);
  const press = useCallback(() => {
    pressed.current = true;
  }, []);

  useEffect(() => {
    if (!open) return;
    pressed.current = false;
    const onDoc = () => {
      if (!pressed.current) setAt(null);
      pressed.current = false;
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setAt(null);
    };
    const follow = (e: Event) => {
      if (e.target instanceof Node && panelRef.current?.contains(e.target)) return;
      const r = anchor?.getBoundingClientRect();
      if (r) setAt(place(r, align, over));
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
  }, [open, anchor, align, over]);

  return (
    <div className={cx(s.wrap, className)} ref={setAnchor} onMouseDownCapture={press}>
      {renderTrigger({ open, toggle })}
      {at &&
        createPortal(
          <div ref={panelRef} className={s.panel} style={at} onMouseDownCapture={press}>
            {children({ close })}
          </div>,
          document.body,
        )}
    </div>
  );
}
