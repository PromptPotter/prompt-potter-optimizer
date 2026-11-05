import { memo } from "react";
import { useViewedDashboard } from "@/lib/poll";
import { useViewedCourse } from "@/lib/lineage";
import { figureOf } from "@/lib/derivations";
import { Hearts } from "@/components/ui";

// A grid box of its own: with neither hearts nor a trace it renders nothing, and leaves no gap.
export const TopStrip = memo(function TopStrip() {
  const { dash } = useViewedDashboard();
  const lives = figureOf(useViewedCourse(), "lives");
  const trace = dash?.langfuse_trace_url ?? null;
  if (!lives && !trace) return null;

  return (
    <div className="topstrip box-full">
      {lives && <Hearts row={lives} className="topstrip-hearts" />}
      {lives && trace && <span className="topstrip-sep" aria-hidden="true" />}
      {trace && (
        <a
          className="topstrip-trace"
          href={trace}
          target="_blank"
          rel="noreferrer"
          title="Open this cycle's full nested trace in Langfuse"
        >
          Trace ↗
        </a>
      )}
    </div>
  );
});
