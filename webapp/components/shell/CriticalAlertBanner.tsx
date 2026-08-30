"use client";
import { cx } from "@/lib/cx";
import { connectorReachability, criticalAlert } from "@/lib/derivations";
import { useConnector } from "@/lib/hooks/useConnector";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useMachineStatus } from "@/lib/hooks/useMachineStatus";
import type { StatusKind } from "@/lib/poll";

// The loud, can't-miss failure surface — a full-width sticky bar at the top of
// the main column, rendered on EVERY tab (Chat / Dashboard / Verify / Files), so
// a server-unreachable, crashed, or gone-silent run reaches an operator who
// has alt-tabbed away from the dashboard. The verdict (and its precedence) is
// the pure `criticalAlert` derivation; this component is the presentation. It
// returns null on a healthy run, so the bar simply isn't in the DOM then.

interface Props {
  bannerStatus: StatusKind;
  bannerText: string;
  bannerHint?: string;
  // Reconciled by AppShell — the cycle list loaded and came back empty, with the
  // server reachable. Silences the bar: a first run has nothing wrong with it.
  emptyWorkspace?: boolean;
  onOpenFiles: () => void;
  // Invoked by the one-click "Pause campaign" button the banner shows when the
  // verdict is structurally-degraded (`alert.action === "pause"`). The run never
  // auto-pauses — this is the operator pulling the trigger.
  onPauseCampaign?: () => void;
}

export function CriticalAlertBanner({
  bannerStatus,
  bannerText,
  bannerHint,
  emptyWorkspace,
  onOpenFiles,
  onPauseCampaign,
}: Props) {
  // Live snapshot, self-sourced from the cycle stream (the banner shows on
  // every tab, so it owns its own read).
  const { dash } = useDashboard();
  // Same connector reachability the ConnectorInspector LED reads — one shared
  // `useConnector()` probe, one shared `down` verdict (connector-state.ts).
  const { health, connector } = useConnector();
  const { down: connectorDown } = connectorReachability(health);
  // Cross-user busy state — its own 5 s poll (useMachineStatus), surfaced in the
  // same bar so "someone else is running" reaches an alt-tabbed operator.
  const machine = useMachineStatus();
  const alert = criticalAlert({
    bannerStatus,
    bannerText,
    bannerHint,
    emptyWorkspace,
    dash,
    connectorDown,
    connectorName: connector,
    connectorDetail: health?.detail ?? null,
    machineBusy: machine.busy,
    machineBusyHolder: machine.holder?.user ?? null,
    machineBusySince: machine.holder?.started_at ?? null,
  });
  if (!alert) return null;

  const critical = alert.severity === "critical";
  const info = alert.severity === "info";
  return (
    <div
      className={cx("critical-alert", alert.severity)}
      role={critical ? "alert" : "status"}
      aria-live={critical ? "assertive" : "polite"}
      aria-atomic="true"
    >
      <span className="critical-alert-icon" aria-hidden="true">
        {critical ? "⛔" : info ? "ⓘ" : "⚠"}
      </span>
      <span className="critical-alert-body">
        <strong className="critical-alert-title">{alert.title}</strong>
        {alert.detail ? <span className="critical-alert-detail">{alert.detail}</span> : null}
      </span>
      {alert.action === "pause" && onPauseCampaign ? (
        <button
          type="button"
          className="critical-alert-jump critical-alert-stop"
          onClick={onPauseCampaign}
          aria-label="Pause the campaign"
        >
          Pause campaign
        </button>
      ) : null}
      {/* No jump on an `info` verdict: it fires when the address stopped existing,
          so there are no files to open — an operable-looking control that lands on
          nothing is exactly what I3_affordance_honest forbids. */}
      {info ? null : (
        <button
          type="button"
          className="critical-alert-jump"
          onClick={onOpenFiles}
          aria-label="Open files pane"
        >
          Files →
        </button>
      )}
    </div>
  );
}
