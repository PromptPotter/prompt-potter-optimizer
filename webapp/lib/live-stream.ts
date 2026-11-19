import { noteServedContract } from "@/lib/api/client";
import type { StampedFeedFrame } from "@/lib/api/types";
import { RECONNECT_POLL_MS } from "@/lib/cadence";
import { reportIncident, type IncidentContext } from "@/lib/diagnostics";

type Stamp = Pick<StampedFeedFrame, "contract">;

// A frame under this tab's contract IS its generated type: the stamp is what the shape is checked by.
function stamped<F extends Stamp>(raw: unknown): raw is F {
  return typeof raw === "object" && raw !== null && "contract" in raw && typeof raw.contract === "string";
}

// The one door both live channels (the account's feed, the viewed cycle's tail) open through. A
// dropped stream reconnects by itself, and its first frames say what moved meanwhile; a refused one
// (a 401, a 5xx) is closed for good and reopened on the reconnect beat. Returns its close.
export function openLiveStream<F extends Stamp>(
  url: string,
  incident: IncidentContext,
  on: { frame(f: F): void; connected?(up: boolean): void },
): () => void {
  let es!: EventSource;
  let beat: ReturnType<typeof setTimeout> | undefined;
  const open = () => {
    const self = (es = new EventSource(url, { withCredentials: true }));
    self.onopen = () => on.connected?.(true);
    self.onmessage = (ev: MessageEvent<string>) => {
      let frame: unknown;
      try {
        frame = JSON.parse(ev.data);
      } catch (e) {
        // The server serializes and stamps every frame, so one that does not parse is an incident.
        reportIncident(e, incident);
        return;
      }
      if (!stamped<F>(frame)) {
        reportIncident(new Error("a stream frame carries no served contract"), incident);
        return;
      }
      noteServedContract(frame.contract);
      on.frame(frame);
    };
    self.onerror = () => {
      on.connected?.(false);
      if (self.readyState === EventSource.CLOSED) beat = setTimeout(open, RECONNECT_POLL_MS);
    };
  };
  open();
  return () => {
    clearTimeout(beat);
    es.close();
  };
}
