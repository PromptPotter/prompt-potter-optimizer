"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { createPortal } from "react-dom";
import { cx } from "@/lib/cx";
import s from "./HoverCard.module.css";

// Teaching prose for a control, portaled to <body> so no ancestor's `overflow` clips it. REACHABLE,
// hence `role="note"` rather than tooltip, and WCAG 1.4.13 applies.
const CLOSE_GRACE_MS = 160;
const GAP = 8;

// Trigger geometry only — nothing measures the card, so late-arriving content can't strand it.
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
  block?: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLElement | null>(null);
  const setRef = useCallback((n: HTMLElement | null) => {
    ref.current = n;
  }, []);
  const cardRef = useRef<HTMLDivElement>(null);
  const timer = useRef(0);
  // `null` IS closed.
  const [at, setAt] = useState<CSSProperties | null>(null);
  const open = at !== null;

  const within = useCallback(
    (n: Node | null) => !!n && !!(ref.current?.contains(n) || cardRef.current?.contains(n)),
    [],
  );

  const show = useCallback(() => {
    window.clearTimeout(timer.current);
    const r = ref.current?.getBoundingClientRect();
    if (r) setAt(anchor(r));
  }, []);

  // Never immediate: a pointer crossing the GAP is over neither half for a moment.
  const hide = useCallback(() => {
    timer.current = window.setTimeout(() => setAt(null), CLOSE_GRACE_MS);
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
