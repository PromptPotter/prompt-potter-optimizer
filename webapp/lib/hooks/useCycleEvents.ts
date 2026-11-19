import { useEffect, useState } from "react";
import { cyclePathUrl } from "@/lib/api";
import { openLiveStream } from "@/lib/live-stream";
import { decodeCyclePath, encodeCyclePath, pathLeaf, type CyclePath } from "@/lib/ids";
import { useWorkspace } from "@/lib/state/workspace";
import type { ActivityState, StampedEnvelope } from "@/lib/api/types";

interface CycleEventsState extends ActivityState {
  connected: boolean;
}

const NO_ACTIVITY: ActivityState = { status: null, notices: [], decision: null };

export function useCycleEvents(path: CyclePath | null): CycleEventsState {
  const [activity, setActivity] = useState<ActivityState>(NO_ACTIVITY);
  const [connected, setConnected] = useState(false);

  const key = path ? encodeCyclePath(path) : "";
  const [prevKey, setPrevKey] = useState(key);
  if (key !== prevKey) {
    setPrevKey(key);
    setActivity(NO_ACTIVITY);
    setConnected(false);
  }

  const goneAddress = useWorkspace().goneAddress;
  const addressGone = goneAddress?.cause === "gone" && goneAddress.address === key;

  // Keyed on the stable `key` string, not the path array's per-render identity.
  useEffect(() => {
    const p = decodeCyclePath(key);
    if (!p || addressGone) return;
    // A frame queued across a reconnect would paint and linger: this feed never re-validates.
    const expectedCycleId = pathLeaf(p).cycleId;
    return openLiveStream<StampedEnvelope>(
      cyclePathUrl("cycle-events", p, {}),
      { surface: "cycle-events", address: key },
      {
        connected: setConnected,
        frame: (env) => {
          if (env.cycle_id === expectedCycleId) setActivity(env.activity);
        },
      },
    );
  }, [key, addressGone]);

  return { ...activity, connected };
}
