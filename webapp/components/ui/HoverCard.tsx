import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { createPortal } from "react-dom";
import { HOVER_CLOSE_GRACE_MS } from "@/lib/cadence";
import { cx } from "@/lib/cx";
import { EDGE, room, vertical } from "./overlay";
import s from "./HoverCard.module.css";

const GAP = 8;
// Narrower than this beside its trigger on both sides (a phone), the card spans the viewport under
// or over the trigger instead.
const BESIDE_MIN = 240;

const CONTROL = "button, a[href], input, select, textarea, summary, [tabindex]";

// A tap on a live control inside the trigger is that control's; a disabled one taps through to the card.
const ownControl = (wrap: HTMLElement | null, t: EventTarget) => {
  const c = t instanceof Element ? t.closest(CONTROL) : null;
  return !!c && c !== wrap && !!wrap?.contains(c) && !c.matches(":disabled");
};

// Trigger geometry only — nothing measures the card, so late-arriving content can't strand it.
const anchor = (r: DOMRect): CSSProperties => {
  const vw = window.innerWidth;
  const toRight = vw - r.right - GAP - EDGE;
  const toLeft = r.left - GAP - EDGE;
  if (Math.max(toRight, toLeft) < BESIDE_MIN) {
    const v = vertical(r, GAP);
    return { position: "fixed", left: EDGE, ...v.at, ...room(vw - 2 * EDGE, v.h) };
  }
  const v = vertical(r, 0, true);
  return {
    position: "fixed",
    ...v.at,
    ...(toRight >= toLeft ? { left: r.right + GAP } : { right: vw - r.left + GAP }),
    ...room(Math.max(toRight, toLeft), v.h),
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
  const pressed = useRef(false);
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
      // Hover is a mouse's: a touch browser's compat mouseenter after a tap opens nothing.
      onPointerEnter={(e) => {
        if (e.pointerType === "mouse") show();
      }}
      onPointerLeave={(e) => {
        if (e.pointerType === "mouse") leave(e);
      }}
      // Focus a press gave is the press's: the release below decides, as iOS focuses a tapped span
      // only sometimes. Any other focus is the keyboard's, and opens it.
      onPointerDown={() => {
        pressed.current = true;
      }}
      onPointerUp={(e) => {
        pressed.current = false;
        if (e.pointerType !== "mouse" && !ownControl(ref.current, e.target)) show();
      }}
      onFocus={() => {
        if (!pressed.current) show();
      }}
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
            onPointerEnter={(e) => {
              if (e.pointerType === "mouse") show();
            }}
            onPointerLeave={(e) => {
              if (e.pointerType === "mouse") leave(e);
            }}
          >
            {content}
          </div>,
          document.body,
        )}
    </Wrap>
  );
}
