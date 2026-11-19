import { useCallback, useEffect, useRef, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cx } from "@/lib/cx";
import { EDGE, room, useAnchored, vertical } from "./overlay";
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
  const { setTrigger, at, show, close } = useAnchored(
    useCallback((r: DOMRect) => place(r, align, over), [align, over]),
  );
  const open = at !== null;
  const toggle = open ? close : show;

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
      if (!pressed.current) close();
      pressed.current = false;
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);

  return (
    <div className={cx(s.wrap, className)} ref={setTrigger} onMouseDownCapture={press}>
      {renderTrigger({ open, toggle })}
      {at &&
        createPortal(
          <div className={s.panel} style={at} onMouseDownCapture={press}>
            {children({ close })}
          </div>,
          document.body,
        )}
    </div>
  );
}
