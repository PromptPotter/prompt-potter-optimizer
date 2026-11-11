import { commandRead } from "@/lib/api/reads";
import type { CycleHop } from "@/lib/api/types";
import { readyData, useRead } from "./useRead";

// The cycle a queued launch minted, followed on the feed by its command; null until a slot mints it.
export function useMintedHop(commandId: string | null): CycleHop | null {
  const reading = readyData(useRead(commandId !== null ? commandRead(commandId) : null));
  const minted = reading?.launches?.find((launch) => launch.hop !== null);
  return minted ? minted.hop : null;
}
