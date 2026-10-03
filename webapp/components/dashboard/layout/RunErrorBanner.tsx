// Both run alerts from `dashboard.json` — fatal `error` and non-fatal `recent_loop_warnings`.

import { useDashboard } from "@/lib/hooks/useDashboard";

export function RunErrorBanner() {
  const { dash } = useDashboard();
  const err = dash?.error;
  const warnings = (dash?.recent_loop_warnings ?? []).slice(-4).reverse();

  if (!err && warnings.length === 0) return null;

  const lines = err ? err.message.split("\n").filter((l) => l.trim().length > 0) : [];

  return (
    <>
      {err ? (
        <div className="run-error-banner" role="alert">
          <div className="run-error-banner-head">
            <span className="run-error-banner-kind">{err.kind}</span>
            <span className="run-error-banner-stop">stop: {err.stop_reason}</span>
          </div>
          <p className="run-error-banner-msg">{lines[0] ?? err.message}</p>
          {lines.length > 1 ? (
            <ul className="run-error-banner-list">
              {lines.slice(1).map((l, i) => (
                <li key={i}>{l}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      {warnings.length > 0 ? (
        <div className="run-error-banner is-warn" role="status">
          <div className="run-error-banner-head">
            <span className="run-error-banner-kind">Optimizer warnings</span>
          </div>
          <ul className="run-error-banner-list">
            {warnings.map((w, i) => (
              <li key={i}>
                {typeof w.round === "number" ? `r${w.round} · ` : ""}
                {w.message}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </>
  );
}
