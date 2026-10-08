"use client";
import { memo } from "react";
import { useCycleStream } from "@/lib/poll";
import { fmtSecs } from "@/lib/format";
import { Hearts } from "@/components/ui";

// Frameless one-line run summary. Run state is NOT repeated here — the masthead owns it.

export const TopStrip = memo(function TopStrip() {
  const { dash } = useCycleStream();
  const lastQuery = dash?.last_query_elapsed_s ?? null;
  // Only where the optimizer banks stalls; the cap is the denominator (3-of-4 vs 3-of-7).
  const hearts = dash?.run_standing?.stalls_left ?? null;
  const livesCap = dash?.run_standing?.stalls_left_cap ?? null;

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
