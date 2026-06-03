"use client";
// Replit-style console pane. Pinned to the bottom of the main pane on every
// tab, collapsible (chevron toggle, default-collapsed, localStorage-persisted).
// Tails the active cycle's output.log at 1 s polling — the LiveDashboardView
// projection writes this file as ANSI-colored line stream, so the console
// reads as a terminal view of the optimizer's narration.
//
// Auto-scrolls to the bottom as new lines land; suspends when the operator
// scrolls up (Replit/console convention), resumes once they scroll back to
// the bottom. New lines append in-place — no flash, no jank.

import { useCallback, useEffect, useLayoutEffect, useRef, type ReactNode } from "react";
import { useLocalStorage } from "@/lib/hooks/useLocalStorage";
import { useConsoleTail } from "@/lib/hooks/useConsoleTail";
import { RotatePrompt } from "@/components/shell/RotatePrompt";

const KEY = "promptpotter.console.open";

interface Props {
  campaignId: string | null;
  cycleId: string | null;
  // Right-aligned slot inside the head row. The dashboard mounts the
  // RunTelemetry strip here so persistent run state sits at the chrome edge
  // alongside the live tail, IDE-status-bar style. The slot is layout-only
  // chrome — it must not capture the head's expand/collapse click. The
  // toggle is its own button; this slot renders as a sibling element.
  headSlot?: ReactNode;
}

export function ConsolePane({ campaignId, cycleId, headSlot }: Props) {
  const [open, setOpen] = useLocalStorage<boolean>(KEY, false, {
    serialize: (v) => (v ? "1" : "0"),
    deserialize: (raw) => raw === "1",
  });
  const toggle = () => setOpen((o) => !o);
  return (
    <section className={`console-pane${open ? " open" : " closed"}`} aria-label="Optimizer console">
      <div className="console-head">
        <button
          type="button"
          className="console-toggle"
          onClick={toggle}
          aria-expanded={open}
          aria-controls="console-body"
        >
          <span className="console-caret" aria-hidden="true">
            {open ? "▾" : "▸"}
          </span>
          <span className="console-title">Console</span>
          <span className="console-subtitle">output.log — live tail</span>
        </button>
        {headSlot}
      </div>
      {open ? (
        <RotatePrompt surfaceName="The console" skipRender>
          <ConsoleBody campaignId={campaignId} cycleId={cycleId} />
        </RotatePrompt>
      ) : null}
    </section>
  );
}

function ConsoleBody({
  campaignId,
  cycleId,
}: {
  campaignId: string | null;
  cycleId: string | null;
}) {
  // Data half lives in the hook (fetch-in-a-hook anatomy); this component
  // owns only the scroll / sticky-bottom DOM concerns.
  const { lines, error } = useConsoleTail(campaignId, cycleId);
  const containerRef = useRef<HTMLDivElement | null>(null);
  // Sticky-to-bottom flag. True ⇒ new lines auto-scroll the view; flips
  // false the moment the operator scrolls up (browser console convention).
  const stickyBottomRef = useRef(true);

  // Fresh cycle ⇒ re-grab stick-to-bottom; the operator hasn't scrolled the
  // new cycle's tail yet. Refs MUST NOT be written during render, so this
  // rides an effect rather than the render-phase reset inside the hook.
  useEffect(() => {
    stickyBottomRef.current = true;
  }, [campaignId, cycleId]);

  const onScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    // 4px slop so micro-scroll wobble doesn't break stick-to-bottom.
    stickyBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 4;
  }, []);

  // useLayoutEffect — scroll synchronously after DOM update so the new line
  // isn't briefly visible above the fold before the auto-scroll catches up.
  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el || !stickyBottomRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [lines]);

  if (!cycleId) {
    return (
      <div id="console-body" className="console-body console-empty">
        Select a unit to tail its output.log.
      </div>
    );
  }

  if (error && lines.length === 0) {
    return (
      <div id="console-body" className="console-body console-empty">
        output.log unreachable — {error}
      </div>
    );
  }

  if (lines.length === 0) {
    return (
      <div id="console-body" className="console-body console-empty">
        Waiting for first log line…
      </div>
    );
  }

  return (
    <div
      id="console-body"
      ref={containerRef}
      className="console-body"
      onScroll={onScroll}
    >
      {lines.map((line, i) => (
        <div key={i} className="console-line">
          {renderAnsiLine(line)}
        </div>
      ))}
    </div>
  );
}

// ── ANSI-SGR parser ────────────────────────────────────────────────────────
// Just enough to colorize the optimizer's narration: foreground colors,
// bold, dim, reset. Anything else (256-color, RGB) renders as plain text.

interface AnsiState {
  fg: number | null;
  bold: boolean;
  dim: boolean;
}

function classesFor(state: AnsiState): string | undefined {
  const cls: string[] = [];
  if (state.fg != null) cls.push(`ansi-fg-${state.fg}`);
  if (state.bold) cls.push("ansi-bold");
  if (state.dim) cls.push("ansi-dim");
  return cls.length > 0 ? cls.join(" ") : undefined;
}

function applyCodes(state: AnsiState, codes: number[]): void {
  for (const c of codes) {
    if (c === 0) {
      state.fg = null;
      state.bold = false;
      state.dim = false;
    } else if (c === 1) state.bold = true;
    else if (c === 2) state.dim = true;
    else if (c === 22) {
      state.bold = false;
      state.dim = false;
    } else if ((c >= 30 && c <= 37) || (c >= 90 && c <= 97)) state.fg = c;
    else if (c === 39) state.fg = null;
  }
}

const ANSI_RE = /\x1b\[([\d;]*)m/g;

function renderAnsiLine(line: string): ReactNode {
  const state: AnsiState = { fg: null, bold: false, dim: false };
  const segs: { text: string; cls?: string }[] = [];
  let lastIdx = 0;
  let m: RegExpExecArray | null;
  ANSI_RE.lastIndex = 0;
  while ((m = ANSI_RE.exec(line)) !== null) {
    if (m.index > lastIdx) {
      segs.push({ text: line.slice(lastIdx, m.index), cls: classesFor(state) });
    }
    const codes = m[1].split(";").map((s) => parseInt(s || "0", 10));
    applyCodes(state, codes);
    lastIdx = ANSI_RE.lastIndex;
  }
  if (lastIdx < line.length) {
    segs.push({ text: line.slice(lastIdx), cls: classesFor(state) });
  }
  if (segs.length === 0) return line;
  return segs.map((s, i) =>
    s.cls ? (
      <span key={i} className={s.cls}>
        {s.text}
      </span>
    ) : (
      <span key={i}>{s.text}</span>
    ),
  );
}
