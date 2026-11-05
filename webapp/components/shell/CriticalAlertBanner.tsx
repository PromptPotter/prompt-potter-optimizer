import { FailureLine, Said } from "@/components/ui";
import { BACKEND_REACHABILITY_INFO, TONES, type Said as SaidRow } from "@/lib/api/types";
import { cycleAddress, postCommand } from "@/lib/api/commands";
import { cx } from "@/lib/cx";
import { useCommand } from "@/lib/hooks/useCommand";
import { useConnector } from "@/lib/hooks/useConnector";
import { failureOf } from "@/lib/hooks/useRead";
import { connectionSaid, useViewedDashboard } from "@/lib/poll";
import { useActivePointer, useRegistry } from "@/lib/hooks/useRegistry";
import { useWorkspace } from "@/lib/workspace";

// The pinned line: the connection's own row, else the louder of the run's served `said` and its
// backend probe's. A connection row is the one said row the client makes.
export function CriticalAlertBanner() {
  const { read, dash } = useViewedDashboard();
  const { viewedPath, cycleId, pinned, goneAddress } = useWorkspace();
  const registryFailure = failureOf(useRegistry().read);
  const pointerFailure = failureOf(useActivePointer().read);
  const pause = useCommand<"pause-cycle">("critical-alert", { scope: cycleId });
  const { health } = useConnector();

  // The workspace's verdict wins: the dashboard has already reset onto another address. With no
  // unit in view, the reads that list and point at runs are the connection.
  const connection = connectionSaid(
    cycleId ? failureOf(read) : (pointerFailure ?? registryFailure),
    goneAddress ? "left" : !cycleId ? "listing" : pinned ? "pinned" : "followed",
  );
  const backend: SaidRow | null =
    health === null || health.status === "live"
      ? null
      : { key: "backend", ...BACKEND_REACHABILITY_INFO[health.status], value: null, sub: null, detail: null };
  const said: SaidRow | null =
    connection ??
    ([dash?.said ?? null, backend]
      .filter((row) => row !== null)
      .sort((a, b) => TONES.indexOf(b.tone) - TONES.indexOf(a.tone))[0] ?? null);
  if (!said) return null;

  const danger = said.tone === "danger";
  return (
    <div
      className={cx("critical-alert", `tone-${said.tone}`)}
      role={danger ? "alert" : "status"}
      aria-live={danger ? "assertive" : "polite"}
      aria-atomic="true"
    >
      <Said tone={said.tone} label={said.label} value={said.value} sub={said.sub} detail={said.detail} />
      {said.key === "round_critical" && dash?.admission.offers === "pause" && viewedPath ? (
        <>
          <button
            type="button"
            className="critical-alert-stop"
            disabled={pause.pending !== null}
            onClick={() =>
              void pause.run("pause-cycle", () =>
                postCommand("pause-cycle", cycleAddress(viewedPath)),
              )
            }
            aria-label="Pause the campaign"
          >
            Pause
          </button>
          <FailureLine failure={pause.failure} className="critical-alert-failure" />
        </>
      ) : null}
    </div>
  );
}
