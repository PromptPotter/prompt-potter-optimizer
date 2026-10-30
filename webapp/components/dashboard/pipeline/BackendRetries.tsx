"use client";
import { useCycleStream } from "@/lib/poll";
import { fmtClock, fmtUnit } from "@/lib/format";

// The backend's recent retries, under the pipeline that calls it; nothing while it answers cleanly.
export function BackendRetries() {
  const { dash } = useCycleStream();
  const warnings = dash?.recent_backend_warnings;
  if (!warnings || warnings.length === 0) return null;
  return (
    <section className="dash-log" aria-label="Recent backend retries">
      <h3 className="dash-log-title">Backend retries · last {warnings.length}</h3>
      <div className="dash-log-body">
        {warnings.slice().reverse().map((w, i) => {
          const code = w.status_code != null ? ` HTTP ${w.status_code}` : "";
          const err = w.error_class ? ` ${w.error_class}` : "";
          const wait = w.wait_s != null ? ` · wait ${fmtUnit("seconds", w.wait_s)}` : "";
          const final = w.final ? " · FINAL" : "";
          const attempts =
            w.attempt != null ? ` (attempt ${w.attempt}/${w.max_attempts ?? "?"})` : "";
          return (
            <div key={i} className="dash-log-row">
              <span className="dash-log-dim">{fmtClock(w.ts)}</span>{" "}
              <span className="dash-log-warn">{w.kind}</span>
              {code}
              {err}
              {attempts}
              {wait}
              {final}
            </div>
          );
        })}
      </div>
    </section>
  );
}
