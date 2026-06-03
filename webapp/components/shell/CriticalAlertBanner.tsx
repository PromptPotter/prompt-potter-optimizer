"use client";
import { cx } from "@/lib/cx";
import { connectorReachability } from "@/lib/derivations/connector-state";
import { criticalAlert } from "@/lib/derivations/critical-alert";
import { useConnector } from "@/lib/hooks/useConnector";
import type { DashboardSnapshot, StatusKind } from "@/lib/poll";

// The loud, can't-miss failure surface — a full-width sticky bar pinned under
// the Topbar and rendered on EVERY tab (Chat / Dashboard / Verify / Files), so
// a server-unreachable, crashed, or gone-silent run reaches an operator who
// has alt-tabbed away from the dashboard. The verdict (and its precedence) is
// the pure `criticalAlert` derivation; this component is the presentation. It
// returns null on a healthy run, so the bar simply isn't in the DOM then.

interface Props {
  bannerStatus: StatusKind;
  bannerText: string;
  bannerHint?: string;
  dash: DashboardSnapshot | null;
  runPhaseResolved: string | null;
  onOpenFiles: () => void;
}

export function CriticalAlertBanner({
  bannerStatus,
  bannerText,
  bannerHint,
  dash,
  runPhaseResolved,
  onOpenFiles,
}: Props) {
  // Same connector reachability the ConnectorInspector LED reads — one shared
  // `useConnector()` probe, one shared `down` verdict (connector-state.ts).
  const { health, connector } = useConnector();
  const { down: connectorDown } = connectorReachability(health);
  const alert = criticalAlert({
    bannerStatus,
    bannerText,
    bannerHint,
    dash,
    runPhaseResolved,
    connectorDown,
    connectorName: connector,
    connectorDetail: health?.detail ?? null,
  });
  if (!alert) return null;

  const critical = alert.severity === "critical";
  return (
    <div
      className={cx("critical-alert", alert.severity)}
      role={critical ? "alert" : "status"}
      aria-live={critical ? "assertive" : "polite"}
      aria-atomic="true"
    >
      <span className="critical-alert-icon" aria-hidden="true">
        {critical ? "⛔" : "⚠"}
      </span>
      <span className="critical-alert-body">
        <strong className="critical-alert-title">{alert.title}</strong>
        {alert.detail ? <span className="critical-alert-detail">{alert.detail}</span> : null}
      </span>
      <button
        type="button"
        className="critical-alert-jump"
        onClick={onOpenFiles}
        aria-label="Open files pane"
      >
        Files →
      </button>
    </div>
  );
}
