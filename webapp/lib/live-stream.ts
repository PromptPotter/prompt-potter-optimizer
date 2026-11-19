import { RECONNECT_POLL_MS } from "@/lib/cadence";
import { reportIncident, type IncidentContext } from "@/lib/diagnostics";

// The one door both live channels (the account's feed, the viewed cycle's tail) open through. A
// dropped stream reconnects by itself, and its first frames say what moved meanwhile; a refused one
// (a 401, a 5xx) is closed for good and reopened on the reconnect beat. Returns its close.
export function openLiveStream<F>(
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
      let frame: F;
      try {
        frame = JSON.parse(ev.data) as F;
      } catch (e) {
        // The server serializes every frame, so one that does not parse is an incident, not noise.
        reportIncident(e, incident);
        return;
      }
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
