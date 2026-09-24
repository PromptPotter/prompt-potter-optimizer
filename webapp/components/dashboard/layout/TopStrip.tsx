"use client";
import { memo } from "react";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { fmtSecs } from "@/lib/format";
import { Hearts } from "@/components/ui";

// Frameless one-line run summary. Run state is NOT repeated here — the masthead owns it.

export const TopStrip = memo(function TopStrip() {
  const { dash } = useDashboard();
  const lastQuery = dash?.last_query_elapsed_s ?? null;
  // Only in improvement-banked-budget mode; the cap is the denominator (3-of-4 vs 3-of-7).
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
