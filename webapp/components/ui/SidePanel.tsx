"use client";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useLocalStorage } from "@/lib/hooks/useLocalStorage";
import { Button } from "./Button";
import s from "./SidePanel.module.css";

// THE detail panel — one row of a table opened beside it, over the page. Opik's
// `ResizableSidePanel`: the host passes only whether a previous/next row exists and how to
// step (`onStep(±1)`); it never hands the panel its rows. J/K step, Esc closes, and the width
// the operator drags is kept per `panelId` for the next visit.

const MIN_PX = 360;
const DEFAULT_PX = 560;
const KEY_STEP_PX = 32;

const clampWidth = (px: number) =>
  Math.round(Math.max(MIN_PX, Math.min(window.innerWidth - 48, px)));

function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName);
}

export function SidePanel({
  panelId,
  title,
  onClose,
  hasPrev = false,
  hasNext = false,
  onStep,
  children,
}: {
  panelId: string;
  title: ReactNode;
  onClose: () => void;
  hasPrev?: boolean;
  hasNext?: boolean;
  onStep?: (shift: -1 | 1) => void;
  children: ReactNode;
}) {
  // A display preference, per device: the one localStorage hook, whose server snapshot is the
  // default so the static export's first render matches.
  const [stored, setStored] = useLocalStorage<number>(`side-panel:${panelId}`, DEFAULT_PX);
  // The width mid-drag, written through once on release rather than on every pointer move.
  const [dragging, setDragging] = useState<number | null>(null);
  const width = dragging ?? (Number.isFinite(stored) && stored >= MIN_PX ? stored : DEFAULT_PX);
  const endDrag = useRef<(() => void) | null>(null);
  useEffect(() => () => endDrag.current?.(), []);

  // The handlers read the latest props through a ref, so the listener is bound once.
  const live = useRef({ onClose, onStep, hasPrev, hasNext });
  useEffect(() => {
    live.current = { onClose, onStep, hasPrev, hasNext };
  });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || typing(e.target)) return;
      const p = live.current;
      if (e.key === "Escape") p.onClose();
      else if (e.key === "j" && p.hasNext) p.onStep?.(1);
      else if (e.key === "k" && p.hasPrev) p.onStep?.(-1);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const startDrag = (e: React.PointerEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = width;
    let last = startW;
    const move = (ev: PointerEvent) => {
      last = clampWidth(startW + (startX - ev.clientX));
      setDragging(last);
    };
    const detach = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      endDrag.current = null;
    };
    const up = () => {
      detach();
      setDragging(null);
      setStored(last);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
    endDrag.current = detach;
  };

  const keyResize = (e: React.KeyboardEvent) => {
    const shift = e.key === "ArrowLeft" ? KEY_STEP_PX : e.key === "ArrowRight" ? -KEY_STEP_PX : 0;
    if (shift === 0) return;
    e.preventDefault();
    setStored(clampWidth(width + shift));
  };

  return (
    <aside className={s.panel} style={{ width: `min(${width}px, 100vw)` }} aria-label="Details">
      <div
        className={s.grip}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize panel (arrow keys)"
        aria-valuenow={width}
        aria-valuemin={MIN_PX}
        tabIndex={0}
        onPointerDown={startDrag}
        onKeyDown={keyResize}
      />
      <header className={s.head}>
        <div className={s.title}>{title}</div>
        {onStep && (
          <>
            <Button
              variant="ghost"
              aria-label="Previous row (k)"
              title="Previous (k)"
              disabled={!hasPrev}
              onClick={() => onStep(-1)}
            >
              ↑
            </Button>
            <Button
              variant="ghost"
              aria-label="Next row (j)"
              title="Next (j)"
              disabled={!hasNext}
              onClick={() => onStep(1)}
            >
              ↓
            </Button>
          </>
        )}
        <Button variant="ghost" aria-label="Close (Esc)" title="Close (Esc)" onClick={onClose}>
          ✕
        </Button>
      </header>
      <div className={s.body}>{children}</div>
    </aside>
  );
}
