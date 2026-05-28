"use client";
import { useEffect, useState } from "react";
import { TERMS } from "@/lib/terms";
import type { StatusKind } from "@/lib/poll";

// Floating status assistant — Clippy-style chip pinned to the bottom-right
// corner of the viewport, just above the Console pane. Initial state is
// expanded (dot + status text + hint + Files → + ×). After
// COLLAPSE_AFTER_MS of idle time it shrinks to a circular button showing
// only the colour-coded status dot; clicking it re-expands. The timer
// pauses while the operator hovers or focuses the chip so it never
// vanishes mid-read, and re-expands automatically whenever the status
// changes (live → stale → offline) so important transitions catch the
// eye. Run-state telemetry (round / best / spend / last / updated) and
// the Stop affordance live in the Console head — see RunTelemetry.

const COLLAPSE_AFTER_MS = 10_000;

interface Props {
  status: StatusKind;
  statusText: string;
  statusHint?: string;
  termKey?: string;
  onOpenFiles: () => void;
}

export function StatusAssistant({
  status,
  statusText,
  statusHint,
  termKey,
  onOpenFiles,
}: Props) {
  const tip = termKey ? TERMS[termKey] : "";
  const [collapsed, setCollapsed] = useState(false);
  const [paused, setPaused] = useState(false);

  // Reset the collapse state whenever the displayed status changes — a
  // live → offline flip should re-pop the chip even if it had auto-hidden
  // a moment ago. statusHint changes alone don't re-pop (the dot already
  // carries the salient signal).
  useEffect(() => {
    setCollapsed(false);
  }, [status, statusText]);

  // Auto-collapse timer. Skipped while already collapsed or while the
  // operator is hovering / has focus inside the chip — toast-pattern UX.
  useEffect(() => {
    if (collapsed || paused) return;
    const t = setTimeout(() => setCollapsed(true), COLLAPSE_AFTER_MS);
    return () => clearTimeout(t);
  }, [collapsed, paused, status, statusText]);

  if (collapsed) {
    return (
      <button
        type="button"
        className={`status-assistant collapsed ${status}`}
        onClick={() => setCollapsed(false)}
        aria-label={`Status: ${statusText} — expand`}
        title={statusText}
      >
        <span className="status-dot" aria-hidden="true" />
      </button>
    );
  }

  return (
    <div
      className={`status-assistant expanded ${status}`}
      role="status"
      aria-live="polite"
      aria-atomic="true"
      title={tip || undefined}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      <span className="status-dot" aria-hidden="true" />
      <span className="status-assistant-text">
        <strong>{statusText}</strong>
        {statusHint ? <span className="status-assistant-hint">{statusHint}</span> : null}
      </span>
      <button
        type="button"
        className="status-assistant-jump"
        onClick={onOpenFiles}
        aria-label="Open files pane"
      >
        Files →
      </button>
      <button
        type="button"
        className="status-assistant-close"
        onClick={() => setCollapsed(true)}
        aria-label="Collapse status"
      >
        ×
      </button>
    </div>
  );
}
