import { FailureLine, Said } from "@/components/ui";
import { BACKEND_REACHABILITY_INFO, TONES, type Said as SaidRow } from "@/lib/api/types.generated";
import { cycleAddress, postCommand } from "@/lib/api/commands";
import { cx } from "@/lib/cx";
import { useCommand } from "@/lib/hooks/useCommand";
import { useBackend } from "@/lib/hooks/useConnector";
import { connectionSaid, useCycleStream, type Connection } from "@/lib/poll";
import { useActivePointer, useRegistry } from "@/lib/state/registry";
import { useWorkspace } from "@/lib/workspace";

// The pinned line: the connection's own row, else the louder of the run's served `said` and its
// backend probe's. A connection row is the one said row the client makes.
export function CriticalAlertBanner() {
  const stream = useCycleStream();
  const { viewedPath, cycleId, goneAddress } = useWorkspace();
  const { failure: registryFailure } = useRegistry();
  const { failure: pointerFailure } = useActivePointer();
  const pause = useCommand<"pause-cycle">("critical-alert", { scope: cycleId });
  const { health } = useBackend();

  const netFailure = pointerFailure ?? registryFailure;
  // The workspace's verdict wins: the dashboard has already reset onto another address. With no
  // unit in view, the reads that list and point at runs are the connection.
  const connection: Connection = goneAddress
    ? { kind: "gone" }
    : !cycleId && netFailure
      ? { kind: "lost", failure: netFailure }
      : stream.connection;
  const { dash } = stream;
  const backend: SaidRow | null =
    health === null || health.status === "live"
      ? null
      : { key: "backend", ...BACKEND_REACHABILITY_INFO[health.status], value: null, sub: null, detail: null };
  const said: SaidRow | null =
    connectionSaid(connection) ??
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
