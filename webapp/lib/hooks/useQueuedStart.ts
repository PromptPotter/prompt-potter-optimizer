import { useMemo } from "react";
import { commandRead } from "@/lib/api/reads";
import type { CycleHop } from "@/lib/api/types";
import { readyData, useRead } from "./useRead";

// A queued Start as its command reads on the feed: the cycle a slot minted, else how it ended
// unminted (refused when its slot freed, or withdrawn by an expiry or another tab's cancel).
export type QueuedStart =
  | { stage: "waiting" }
  | { stage: "minted"; hop: CycleHop }
  | { stage: "ended"; said: string };

export function useQueuedStart(commandId: string | null): QueuedStart {
  const reading = readyData(useRead(commandId !== null ? commandRead(commandId) : null));
  return useMemo((): QueuedStart => {
    const hop = reading?.launches?.find((launch) => launch.hop !== null)?.hop;
    if (hop) return { stage: "minted", hop };
    // An unminted job holds no gate, so past `working` it has ended: the served words say how.
    if (reading && reading.status !== "working") return { stage: "ended", said: reading.said };
    return { stage: "waiting" };
  }, [reading]);
}
