"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { cx } from "@/lib/cx";
import s from "./Popover.module.css";

interface Props {
  /** The clickable anchor. `toggle` opens/closes; `open` drives aria state. */
  renderTrigger: (args: { open: boolean; toggle: () => void }) => ReactNode;
  /** Floating panel content. Call `close` after an action to dismiss. */
  children: (args: { close: () => void }) => ReactNode;
  align?: "left" | "right";
  className?: string;
}

// Anchored popover that owns the open state and the dismiss behaviour every
// hand-rolled menu re-implements: click-outside and Escape both close, and the
// listeners are mounted only while open. Markup is the caller's (render props),
// so triggers and panels keep their own classes/aria.
export function Popover({ renderTrigger, children, align = "left", className }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  const toggle = useCallback(() => setOpen((v) => !v), []);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className={cx(s.wrap, className)} ref={ref}>
      {renderTrigger({ open, toggle })}
      {open && (
        <div className={cx(s.panel, align === "right" && s.alignRight)}>
          {children({ close })}
        </div>
      )}
    </div>
  );
}
