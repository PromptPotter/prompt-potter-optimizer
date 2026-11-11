import { useMemo } from "react";
import type { ObserveState } from "@/lib/derivations";
import { useWorkspace } from "@/lib/workspace";

// The view keys riding the hash beside the pick (`workspace.tsx::keys`), under their readers' names.
interface Selection {
  // null follows live; a pick selects its own round.
  round: number | null;
  // `null` is no pick (the served set); `[]` is the picker open with nothing selected.
  sampleSet: number[] | null;
  // `null` follows `resolveObserveSubject`'s own rule.
  observe: ObserveState | null;
  setObserve: (s: ObserveState) => void;
  setSelectionForRound: (r: number | null) => void;
  setSelectionForSampleSet: (ids: number[] | null) => void;
}

export function useSelection(): Selection {
  const { keys, pick, setViewKeys } = useWorkspace();
  const round = keys.round ?? pick?.round ?? null;
  return useMemo<Selection>(
    () => ({
      round,
      sampleSet: keys.samples,
      observe: keys.observe,
      setObserve: (observe) => setViewKeys({ observe }),
      setSelectionForRound: (r) => setViewKeys({ round: r, observe: null }),
      setSelectionForSampleSet: (samples) => setViewKeys({ samples }),
    }),
    [round, keys.samples, keys.observe, setViewKeys],
  );
}
