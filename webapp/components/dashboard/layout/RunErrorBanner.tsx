// RunErrorBanner — surfaces both run alerts from ``dashboard.json``, reusing
// one banner shape:
//   * ``error`` — the fatal crash summary (CRASHED / RENDER_ERROR / DIVERGED),
//     projected from the canonical ``ErrorRecord`` by ``_handle_error``.
//   * ``recent_loop_warnings`` — non-fatal optimizer-loop degradations the
//     self-healing rails recovered from (zero-candidate round, L2 soft-reject,
//     injection truncation), projected from ``RoundWarningRecord`` by
//     ``_handle_round_warning``. Shown in a warn-toned variant of the same box.
// Both absent on a clean run, so the component is always safe to mount.

import { useDashboard } from "@/lib/hooks/useDashboard";

export function RunErrorBanner() {
  const { dash } = useDashboard();
  const err = dash?.error;
  const warnings = (dash?.recent_loop_warnings ?? []).slice(-4).reverse();

  if (!err && warnings.length === 0) return null;

  // Multi-line crash messages (e.g. ``RequestTooLargeError`` lists remediation
  // steps after the summary) render as a wrapped block, not just the headline.
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
