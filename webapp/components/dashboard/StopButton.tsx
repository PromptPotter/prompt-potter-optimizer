"use client";
import { useState } from "react";
import { postStopCycle } from "@/lib/api";
import { Modal } from "@/components/shell/Modal";

interface Props {
  cycleId: string;
  isLive: boolean;
}

// Writes `.runtime/stop.flag` under the cycle dir. The running optimizer's
// Session.stop_check polls for the flag and exits cleanly at the next
// round boundary. Idempotent — writing twice is a no-op. The Modal copy
// shifts when `isLive` is false (frozen cycle): operator sees the flag is
// being written for a cycle that isn't running, so the action is more
// "leave a flag for the next run" than "halt now."
export function StopButton({ cycleId, isLive }: Props) {
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const doStop = async () => {
    setConfirming(false);
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
        onClick={() => setConfirming(true)}
        disabled={pending || done}
        title="Stop the running optimizer at next round boundary"
      >
        {done ? "Stop flag written" : pending ? "Stopping…" : "Stop run"}
      </button>
      {err && <span className="stop-err">stop: {err}</span>}
      <Modal
        open={confirming}
        title={isLive ? "Stop this cycle?" : "Write stop.flag for this cycle?"}
        message={
          isLive
            ? `${cycleId} is currently running. Writing .runtime/stop.flag — the optimizer exits at the next round boundary. Round-in-progress measurements are preserved.`
            : `${cycleId} is not currently running. Writing the stop.flag now will cause the next \`optimize\` invocation against this cycle to halt immediately.`
        }
        actions={[
          { label: "Cancel", onClick: () => setConfirming(false) },
          {
            label: isLive ? "Stop run" : "Write flag",
            variant: "danger",
            onClick: () => void doStop(),
          },
        ]}
        onClose={() => setConfirming(false)}
      />
    </span>
  );
}
