"use client";
import { useCallback, useRef, type PointerEvent, type KeyboardEvent } from "react";

// Drag handle straddling the sidebar's right edge. Pointer-drag (or ←/→ when
// focused) rewrites the persisted --sidebar-width; AppShell clamps + stores it and
// mounts this only while the sidebar is expanded, so it never fights the rail.

interface Props {
  width: number;
  setWidth: (w: number) => void;
  min: number;
  max: number;
}

const STEP = 16;

export function SidebarResizer({ width, setWidth, min, max }: Props) {
  const startX = useRef(0);
  const startW = useRef(0);
  const clamp = (w: number) => Math.min(max, Math.max(min, w));

  const onPointerDown = useCallback(
    (e: PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      startX.current = e.clientX;
      startW.current = width;
      e.currentTarget.setPointerCapture(e.pointerId);
    },
    [width],
  );

  const onPointerMove = useCallback(
    (e: PointerEvent<HTMLDivElement>) => {
      if (!e.currentTarget.hasPointerCapture(e.pointerId)) return;
      setWidth(clamp(startW.current + (e.clientX - startX.current)));
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [min, max, setWidth],
  );

  const onPointerUp = useCallback((e: PointerEvent<HTMLDivElement>) => {
    if (e.currentTarget.hasPointerCapture(e.pointerId))
      e.currentTarget.releasePointerCapture(e.pointerId);
  }, []);

  const onKeyDown = useCallback(
    (e: KeyboardEvent<HTMLDivElement>) => {
      if (e.key === "ArrowLeft") setWidth(clamp(width - STEP));
      else if (e.key === "ArrowRight") setWidth(clamp(width + STEP));
      else return;
      e.preventDefault();
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [width, min, max, setWidth],
  );

  return (
    <div
      className="sidebar-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize sidebar"
      aria-valuenow={width}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onKeyDown={onKeyDown}
    />
  );
}
