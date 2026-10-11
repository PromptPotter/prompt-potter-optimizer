"use client";
import { postPauseCycle } from "@/lib/api/commands";
import { cx } from "@/lib/cx";
import { useCommand } from "@/lib/hooks/useCommand";
import { pathRoot } from "@/lib/ids";
import { connectorReachability, criticalAlert } from "@/lib/derivations";
import { useConnector } from "@/lib/hooks/useConnector";
import { useMachineStatus } from "@/lib/hooks/useMachineStatus";
import { readyData } from "@/lib/hooks/useRead";
import { useCycleStream, type StatusKind } from "@/lib/poll";
import { useActivePointer, useRegistry } from "@/lib/registry";
import { useWorkspace } from "@/lib/workspace";

export function CriticalAlertBanner() {
  const { dash, status, statusText, statusHint } = useCycleStream();
  const { viewedPath, cycleId, goneAddress, openView } = useWorkspace();
  const { cycles, cyclesLoaded, failure: registryFailure } = useRegistry();
  const { failure: pointerFailure } = useActivePointer();
  const pause = useCommand<"pause-cycle">("critical-alert");

  const noUnit = !cycleId;
  const netFailure = pointerFailure ?? registryFailure;
  const netDown = netFailure !== null;
  // `!netDown`: a down server also reports zero cycles; a fresh account must not read as an outage.
  const emptyWorkspace = noUnit && cyclesLoaded && !netDown && cycles.length === 0;
  let bannerStatus: StatusKind = status;
  let bannerText = statusText;
  let bannerHint = statusHint;
  if (goneAddress) {
    // The workspace's verdict wins: the dashboard has already reset onto another address.
    bannerStatus = "gone";
    bannerText = "This campaign no longer exists";
    bannerHint = "It was deleted, or its store was reset — returning to the active run.";
  } else if (noUnit && netDown) {
    bannerStatus = "offline";
    bannerText = "Server unreachable — retrying";
    bannerHint = netFailure?.message ?? "";
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
    machineNotice: machine?.notice ?? null,
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
      {alert.action === "pause" && viewedPath ? (
        <button
          type="button"
          className="critical-alert-jump critical-alert-stop"
          // The CAMPAIGN is the root hop: an inner run stops with the outer run that spawned it.
          onClick={() =>
            void pause.run("pause-cycle", () => postPauseCycle([pathRoot(viewedPath)]))
          }
          aria-label="Pause the campaign"
        >
          Pause campaign
        </button>
      ) : null}
      {/* No jump on `info`: the address is gone, so there are no files to open. */}
      {info ? null : (
        <button
          type="button"
          className="critical-alert-jump"
          onClick={() => openView("files")}
          aria-label="Open files pane"
        >
          Files →
        </button>
      )}
    </div>
  );
}
