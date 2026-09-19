"use client";
import { memo } from "react";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { fmtSecs } from "@/lib/format";
import { Hearts } from "@/components/ui";

// Single-line, frameless run summary. Everything the operator scans in
// the first second sits on one inline row, separated by hairline dividers:
//
//   ♥♥♥ │ Last 0.8s │ Trace ↗
//
// No card chrome — it reads as a status line, not a panel. The run's state
// (phase, round, the candidate being scored, how it is doing) is NOT duplicated
// here — the masthead's chips wear it, and the sidebar the row's own mark.

export const TopStrip = memo(function TopStrip() {
  // Self-sourced from the cycle stream — no props threaded through the frame.
  const { dash } = useDashboard();
  const lastQuery = dash?.last_query_elapsed_s ?? null;
  // Banked lives ("hearts") — the strip's one run-state reading, and only in
  // improvement-banked-budget mode; `null`/undefined ⇒ the segment renders nothing.
  // The cap rides along as the denominator: `♥♥♥` alone can't tell 3-of-4 from 3-of-7.
  const hearts = dash?.hearts ?? null;
  const livesCap = dash?.run_limits?.lives_cap ?? null;

  return (
    <div className="topstrip">
      {hearts != null && (
        <>
          <Hearts hearts={hearts} cap={livesCap} className="topstrip-hearts" />
          <span className="topstrip-sep" aria-hidden="true" />
        </>
      )}
      <span className="topstrip-last">
        <span className="topstrip-label">Last</span>
        <span className="topstrip-counter-val">{fmtSecs(lastQuery)}</span>
      </span>
      {dash?.langfuse_trace_url && (
        <>
          <span className="topstrip-sep" aria-hidden="true" />
          <a
            className="topstrip-trace"
            href={dash.langfuse_trace_url}
            target="_blank"
            rel="noreferrer"
            title="Open this cycle's full nested trace in Langfuse"
          >
            Trace ↗
          </a>
        </>
      )}
    </div>
  );
});
