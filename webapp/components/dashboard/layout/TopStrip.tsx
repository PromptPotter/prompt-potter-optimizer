"use client";
import { memo } from "react";
import { useCycleStream } from "@/lib/poll";
import { fmtSecs } from "@/lib/format";
import { Hearts } from "@/components/ui";

export const TopStrip = memo(function TopStrip() {
  const { dash } = useCycleStream();
  const lastQuery = dash?.last_query_elapsed_s ?? null;
  const lives = dash?.run_standing?.lives ?? null;

  return (
    <div className="topstrip">
      {lives && (
        <>
          <Hearts lives={lives} className="topstrip-hearts" />
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
