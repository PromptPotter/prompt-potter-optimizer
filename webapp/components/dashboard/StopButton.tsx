"use client";
import { useState } from "react";
import { postStopCycle } from "@/lib/api";

interface Props {
  cycleId: string;
}

// Writes `.runtime/stop.flag` under the cycle dir. The running optimizer's
// Session.stop_check polls for the flag and exits cleanly at the next
// round boundary. Idempotent — writing twice is a no-op.
export function StopButton({ cycleId }: Props) {
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const onClick = async () => {
    if (
      !window.confirm(
        `Stop ${cycleId}?\n\nWrites .runtime/stop.flag; the running optimizer exits at the next round boundary. Round-in-progress measurements are preserved.`,
      )
    ) {
      return;
    }
    setPending(true);
    setErr(null);
    try {
      await postStopCycle(cycleId);
      setDone(true);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setPending(false);
    }
  };

  return (
    <span className="stop-button-wrap">
      <button
        type="button"
        className="stop-button"
        onClick={() => void onClick()}
        disabled={pending || done}
        title="Stop the running optimizer at next round boundary"
      >
        {done ? "Stop flag written" : pending ? "Stopping…" : "Stop run"}
      </button>
      {err && <span className="stop-err">stop: {err}</span>}
    </span>
  );
}
