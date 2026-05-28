// RunErrorBanner — surfaces the structured crash summary from
// ``dashboard.json::error`` when the runner exits via CRASHED / RENDER_ERROR.
//
// Projected from the canonical ``ErrorRecord`` on the ledger by
// ``LiveDashboardView._handle_error`` (see
// ``promptpotter/infrastructure/projections/live_dashboard/view.py``). Absent
// on normal stops (interrupted / completed) — the banner renders nothing in
// that case, so this component is always safe to mount.

import type { DashboardSnapshot } from "@/lib/poll";

interface Props {
  dash: DashboardSnapshot | null;
}

export function RunErrorBanner({ dash }: Props) {
  const err = dash?.error;
  if (!err) return null;

  // Multi-line messages from the backend (e.g. ``RequestTooLargeError`` lists
  // remediation steps after the summary). Render as a wrapped block so the
  // operator can read the action hint, not just the headline.
  const lines = err.message.split("\n").filter((l) => l.trim().length > 0);
  const headline = lines[0] ?? err.message;
  const rest = lines.slice(1);

  return (
    <div className="run-error-banner" role="alert">
      <div className="run-error-banner-head">
        <span className="run-error-banner-kind">{err.kind}</span>
        <span className="run-error-banner-stop">stop: {err.stop_reason}</span>
      </div>
      <p className="run-error-banner-msg">{headline}</p>
      {rest.length > 0 ? (
        <ul className="run-error-banner-list">
          {rest.map((l, i) => (
            <li key={i}>{l}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
