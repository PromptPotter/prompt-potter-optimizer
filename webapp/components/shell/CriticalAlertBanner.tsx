"use client";
import { cx } from "@/lib/cx";
import { connectorReachability, criticalAlert } from "@/lib/derivations";
import { useConnector } from "@/lib/hooks/useConnector";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useMachineStatus } from "@/lib/hooks/useMachineStatus";
import { readyData } from "@/lib/hooks/useRead";
import type { StatusKind } from "@/lib/poll";

// The sticky failure bar on every tab; the verdict and its precedence are the pure
// `criticalAlert` derivation, and this is only its presentation.

interface Props {
  bannerStatus: StatusKind;
  bannerText: string;
  bannerHint?: string;
  // Silences the bar: an empty workspace on a reachable server has nothing wrong with it.
  emptyWorkspace?: boolean;
  onOpenFiles: () => void;
  // The run never auto-pauses — this is the operator pulling the trigger.
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
  const { dash } = useDashboard();
  const { health, connector } = useConnector();
  const { down: connectorDown } = connectorReachability(health);
  const machine = readyData(useMachineStatus());
  const alert = criticalAlert({
    bannerStatus,
    bannerText,
    bannerHint,
    emptyWorkspace,
    dash,
    connectorDown,
    connectorName: connector,
    connectorDetail: health?.detail ?? null,
    // No tick landed yet: say nothing about the machine rather than call it free or full.
    machineBusy: machine !== null && machine.busy,
    machineBusyHolder: machine?.holder?.user ?? null,
    machineBusySince: machine?.holder?.started_at ?? null,
    // The served queue is ordered and scoped to this caller: the first entry IS their place.
    queuePosition: machine?.queue[0]?.position ?? null,
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
      {/* No jump on `info`: the address stopped existing, so there are no files to open (I3). */}
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
