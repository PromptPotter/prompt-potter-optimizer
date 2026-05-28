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

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { fetchCycleFile } from "@/lib/api";
import { useLocalStorage } from "@/lib/useLocalStorage";
import { usePoll } from "@/lib/usePoll";
import { RotatePrompt } from "@/components/shell/RotatePrompt";

const KEY = "promptpotter.console.open";
const MAX_LINES = 200;
const POLL_MS = 1000;

interface Props {
  campaignId: string | null;
  cycleId: string | null;
}

export function ConsolePane({ campaignId, cycleId }: Props) {
  const [open, setOpen] = useLocalStorage<boolean>(KEY, false, {
    serialize: (v) => (v ? "1" : "0"),
    deserialize: (raw) => raw === "1",
  });
  const toggle = () => setOpen((o) => !o);
  return (
    <section className={`console-pane${open ? " open" : " closed"}`} aria-label="Optimizer console">
      <button
        type="button"
        className="console-head"
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
  const [lines, setLines] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  // Sticky-to-bottom flag. True ⇒ new lines auto-scroll the view; flips
  // false the moment the operator scrolls up (browser console convention).
  const stickyBottomRef = useRef(true);

  // Cycle change ⇒ wipe immediately so the prior cycle's tail can't linger
  // visible during the first new fetch. The prev-prop pattern resets the
  // React state (lines / error); the unit effect below resets the
  // stickyBottom ref (refs MUST NOT be written during render).
  const [prevCycleId, setPrevCycleId] = useState<string | null>(cycleId);
  if (cycleId !== prevCycleId) {
    setPrevCycleId(cycleId);
    setLines([]);
    setError(null);
  }

  // Latest viewed unit — lets a tick already in flight when the operator
  // switches cycles discard its now-stale result.
  const unit = campaignId && cycleId ? `${campaignId}::${cycleId}` : "";
  const unitRef = useRef(unit);
  useEffect(() => {
    unitRef.current = unit;
    // Fresh cycle ⇒ re-grab stick-to-bottom; the operator hasn't scrolled
    // the new cycle's tail yet.
    stickyBottomRef.current = true;
  }, [unit]);

  const tick = useCallback(
    async (signal: AbortSignal) => {
      if (!campaignId || !cycleId) return;
      const launchedFor = unit;
      try {
        const r = await fetchCycleFile(
          campaignId,
          cycleId,
          "cycle",
          "output.log",
          signal,
        );
        if (signal.aborted || unitRef.current !== launchedFor) return;
        // Split, trim the trailing empty (the log ends with a newline),
        // keep the last MAX_LINES so the DOM can't grow unbounded.
        const split = (r.content ?? "").split("\n");
        if (split.length > 0 && split[split.length - 1] === "") split.pop();
        setLines(split.length > MAX_LINES ? split.slice(-MAX_LINES) : split);
        setError(null);
      } catch (e) {
        if ((e as Error).name === "AbortError" || signal.aborted) return;
        if (unitRef.current !== launchedFor) return;
        setError((e as Error).message);
      }
    },
    [campaignId, cycleId, unit],
  );
  usePoll(tick, { intervalMs: POLL_MS, enabled: !!campaignId && !!cycleId });

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
