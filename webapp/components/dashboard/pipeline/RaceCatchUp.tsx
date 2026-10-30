"use client";
import { useCycleStream } from "@/lib/poll";

// Members that caught up on a race, under the optimizer that ran it; nothing until one has.
export function RaceCatchUp() {
  const { dash } = useCycleStream();
  const log = dash?.catch_up_log;
  if (!log || log.length === 0) return null;
  return (
    <section className="dash-log" aria-label="Race catch-up">
      <h3 className="dash-log-title">Race catch-up · last {log.length}</h3>
      <div className="dash-log-body">
        {log.slice().reverse().map((e, i) => (
          <div key={i} className="dash-log-row">
            <span className="dash-log-dim">
              R{e.round} {e.label} · {e.member}
            </span>{" "}
            <span className="dash-log-mark">↻</span> #{e.position}
            {e.prior_keys.length > 0 && (
              <span className="dash-log-dim"> — {e.prior_keys.join(", ")}</span>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
