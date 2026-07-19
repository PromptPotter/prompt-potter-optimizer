"use client";
import { useRef, useState, type ReactNode } from "react";
import s from "./HoverCard.module.css";

// Anchored hover/focus card. Wraps a trigger; while pointed at (or keyboard-
// focused) it renders `content` in a `position:fixed` card off the trigger's
// right edge, so an ancestor's `overflow` can't clip it. Read-only tooltip —
// `pointer-events:none`, `role="tooltip"`.
//
// The generic anchored-popover behaviour lives here; callers own what goes
// inside. Lazy content (a fetch that should fire only while open) is fine — the
// card mounts `content` only when open, so a component rendered there mounts on
// hover and unmounts on leave.
export function HoverCard({
  content,
  className,
  children,
}: {
  content: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  const show = () => {
    const r = ref.current?.getBoundingClientRect();
    if (r) setPos({ top: r.top, left: r.right + 8 });
    setOpen(true);
  };
  const hide = () => setOpen(false);

  return (
    <div
      ref={ref}
      className={s.wrap}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
    >
      {children}
      {open && pos && (
        <div
          className={className ? `${s.card} ${className}` : s.card}
          role="tooltip"
          style={{ position: "fixed", top: pos.top, left: pos.left }}
        >
          {content}
        </div>
      )}
    </div>
  );
}
