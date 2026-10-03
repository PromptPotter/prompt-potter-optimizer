"use client";
import { cx } from "@/lib/cx";
import { connectorReachability, criticalAlert } from "@/lib/derivations";
import { useConnector } from "@/lib/hooks/useConnector";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useMachineStatus } from "@/lib/hooks/useMachineStatus";
import { readyData } from "@/lib/hooks/useRead";
import type { StatusKind } from "@/lib/poll";
import { useWorkspace } from "@/lib/workspace";

// The sticky failure bar on every tab, presenting the pure `criticalAlert` verdict. It reads the
// dashboard itself, so a tick re-renders the bar and not the shell around it.

interface Props {
  onOpenFiles: () => void;
  // The run never auto-pauses — this is the operator pulling the trigger.
  onPauseCampaign?: () => void;
}

export function CriticalAlertBanner({ onOpenFiles, onPauseCampaign }: Props) {
  const { dash, status, statusText, statusHint } = useDashboard();
  const { cycleId, cycles, cyclesLoaded, activeError, cyclesError, goneAddress } = useWorkspace();

  const noUnit = !cycleId;
  const netDown = Boolean(activeError || cyclesError);
  // Its own fact, not a status, and it silences the bar: the poll rests at `offline`, which would
  // paint a fresh account as an outage. A down server also reports zero cycles, hence `!netDown`.
  const emptyWorkspace = noUnit && cyclesLoaded && !netDown && cycles.length === 0;
  let bannerStatus: StatusKind = status;
  let bannerText = statusText;
  let bannerHint = statusHint;
  if (goneAddress) {
    // The WORKSPACE's verdict wins outright: the dashboard has already reset onto another
    // address and would replace this notice within a frame.
    bannerStatus = "gone";
    bannerText = "This campaign no longer exists";
    bannerHint = "It was deleted, or its store was reset — returning to the active run.";
  } else if (noUnit && netDown) {
    bannerStatus = "offline";
    bannerText = "Server unreachable — retrying";
    bannerHint = activeError ?? cyclesError ?? "";
  }

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
