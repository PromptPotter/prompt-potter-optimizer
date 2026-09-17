"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { createPortal } from "react-dom";
import { cx } from "@/lib/cx";
import s from "./HoverCard.module.css";

// Anchored hover/focus card. Wraps a trigger; while pointed at (or keyboard-
// focused) it renders `content` in a `position:fixed` card portaled to <body>,
// off the trigger's edge, so no ancestor's `overflow` can clip it.
//
// The card is REACHABLE — the pointer crosses into it and its text selects, so
// it is `role="note"` (a tooltip may hold no control) and WCAG 1.4.13 applies:
// hoverable, dismissible, persistent. Callers own what goes inside; lazy content
// is fine, since `content` mounts on hover and unmounts on leave.
const CLOSE_GRACE_MS = 160;
const GAP = 8;

// Hang off the trigger, growing toward the roomier half on each axis, so a row
// near an edge opens inward instead of off-screen. Trigger geometry only —
// nothing measures the card, so content arriving late can't strand it.
const anchor = (r: DOMRect): CSSProperties => ({
  position: "fixed",
  ...(r.top * 2 < window.innerHeight ? { top: r.top } : { bottom: window.innerHeight - r.bottom }),
  ...(r.right * 2 < window.innerWidth
    ? { left: r.right + GAP }
    : { right: window.innerWidth - r.left + GAP }),
});

export function HoverCard({
  content,
  className,
  block = false,
  children,
}: {
  content: ReactNode;
  className?: string;
  /** The trigger is block content (a whole row), so the wrapper is a full-width `<div>`
   *  rather than the inline `<span>` a term inside running text needs. */
  block?: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLElement | null>(null);
  const setRef = useCallback((n: HTMLElement | null) => {
    ref.current = n;
  }, []);
  const cardRef = useRef<HTMLDivElement>(null);
  const timer = useRef(0);
  // Where the card is — `null` IS closed, so this is one state, not two.
  const [at, setAt] = useState<CSSProperties | null>(null);
  const open = at !== null;

  // The card is no DOM descendant of the wrapper, so "within" asks both halves.
  const within = useCallback(
    (n: Node | null) => !!n && !!(ref.current?.contains(n) || cardRef.current?.contains(n)),
    [],
  );

  const show = useCallback(() => {
    window.clearTimeout(timer.current);
    const r = ref.current?.getBoundingClientRect();
    if (r) setAt(anchor(r));
  }, []);

  // Never immediate: the card sits GAP off the trigger, so a pointer on its way
  // in spends a moment over neither half.
  const hide = useCallback(() => {
    timer.current = window.setTimeout(() => setAt(null), CLOSE_GRACE_MS);
  }, []);

  // The one leave rule, for both halves: a held button is a drag-select in
  // flight and the selection outlives the card's edge, so the release below
  // closes it instead of this.
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
    const onUp = (e: MouseEvent) => {
      if (!within(e.target as Node)) hide();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mouseup", onUp);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mouseup", onUp);
    };
  }, [open, hide, within]);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const Wrap = block ? "div" : "span";
  // Both halves are their own hover target, and `within` spans them, so focus
  // moving into the card can't close the card under the focus that entered it.
  return (
    <Wrap
      ref={setRef}
      className={block ? s.block : s.wrap}
      onMouseEnter={show}
      onMouseLeave={leave}
      onFocus={show}
      onBlur={(e) => {
        if (!within(e.relatedTarget as Node | null)) hide();
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
