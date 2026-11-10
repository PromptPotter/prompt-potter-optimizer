import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import type { ObserveState } from "@/lib/derivations";
import { useWorkspace } from "@/lib/workspace";

// The candidate pick is the workspace's (`workspace.tsx::pick`, in the hash); this holds what
// rides beside it on the viewed leaf.
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

const SelectionContext = createContext<Selection | null>(null);

export function SelectionProvider({
  cycleId,
  children,
}: {
  cycleId: string | null;
  children: ReactNode;
}) {
  const { pick, pickCandidate } = useWorkspace();
  const [round, setRound] = useState<number | null>(pick?.round ?? null);
  const [sampleSet, setSampleSet] = useState<number[] | null>(null);
  const [observe, setObserve] = useState<ObserveState | null>(null);
  const [prevCycle, setPrevCycle] = useState(cycleId);
  const [prevPick, setPrevPick] = useState(pick);
  if (cycleId !== prevCycle) {
    setPrevCycle(cycleId);
    setPrevPick(pick);
    setRound(pick?.round ?? null);
    setSampleSet(null);
    setObserve(null);
  } else if (pick?.address !== prevPick?.address) {
    setPrevPick(pick);
    // An unpick drops the round the pick had selected, never one the operator moved to.
    setRound((r) => (pick ? pick.round : r === prevPick?.round ? null : r));
    setObserve(null);
  }

  const setSelectionForRound = useCallback(
    (r: number | null) => {
      setRound(r);
      setObserve(null);
      if (r != null && pick && pick.round !== r) pickCandidate(null);
    },
    [pick, pickCandidate],
  );

  const setSelectionForSampleSet = useCallback((ids: number[] | null) => {
    setSampleSet(ids);
  }, []);

  const value = useMemo<Selection>(
    () => ({
      round,
      sampleSet,
      observe,
      setObserve,
      setSelectionForRound,
      setSelectionForSampleSet,
    }),
    [round, sampleSet, observe, setSelectionForRound, setSelectionForSampleSet],
  );

  return <SelectionContext.Provider value={value}>{children}</SelectionContext.Provider>;
}

export function useSelection(): Selection {
  const c = useContext(SelectionContext);
  if (!c) throw new Error("useSelection must be used inside SelectionProvider");
  return c;
}
