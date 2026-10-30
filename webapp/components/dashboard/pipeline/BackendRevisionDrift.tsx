"use client";
import { useCycleStream } from "@/lib/poll";

// Served, never refused: this launch measures on the backend as it is.
export function BackendRevisionDrift() {
  const { dash } = useCycleStream();
  const revision = dash?.backend_revision;
  if (!revision || revision.reported === revision.expected) return null;
  const { expected, reported, unread } = revision;
  const read = unread === null ? `reports ${reported ?? "none"}` : `unread (${unread})`;
  return (
    <section className="dash-log" aria-label="Backend revision">
      <h3 className="dash-log-title">Backend revision</h3>
      <div className="dash-log-body">
        <div className="dash-log-row">
          <span className="dash-log-warn">{read}</span>
          <span className="dash-log-dim"> · its connector expects {expected}</span>
        </div>
      </div>
    </section>
  );
}
