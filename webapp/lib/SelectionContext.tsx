"use client";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import type { ObserveState } from "@/lib/derivations";
import type { SelectedCandidate } from "@/lib/types";

export type { SelectedCandidate };

// A node id alone is ambiguous: `promptpotter-self`'s TARGET pipeline reuses the OPTIMIZER's node ids.
export type NodeScope = "optimizer" | "target";

export interface SelectedNode {
  id: string;
  scope: NodeScope;
}

interface Ctx {
  candidate: SelectedCandidate | null;
  // null follows live.
  round: number | null;
  node: SelectedNode | null;
  // `null` is no pick (the served set); `[]` is the picker open with nothing selected.
  sampleSet: number[] | null;
  // `null` follows `resolveObserveSubject`'s own rule.
  observe: ObserveState | null;
  setObserve: (s: ObserveState) => void;
  setSelectionForCandidate: (c: SelectedCandidate | null) => void;
  setSelectionForRound: (r: number | null) => void;
  setSelectionForNode: (n: SelectedNode | null) => void;
  setSelectionForSampleSet: (ids: number[] | null) => void;
}

const SelectionCtx = createContext<Ctx | null>(null);

export function SelectionProvider({
  cycleId,
  children,
}: {
  cycleId: string | null;
  children: ReactNode;
}) {
  const [candidate, setCandidate] = useState<SelectedCandidate | null>(null);
  const [round, setRound] = useState<number | null>(null);
  const [node, setNode] = useState<SelectedNode | null>(null);
  const [sampleSet, setSampleSet] = useState<number[] | null>(null);
  const [observe, setObserve] = useState<ObserveState | null>(null);
  const [prevCycle, setPrevCycle] = useState(cycleId);
  if (cycleId !== prevCycle) {
    setPrevCycle(cycleId);
    // A candidate naming the incoming cycle is the cross-cycle click that caused the switch.
    const kept = candidate && candidate.cycle_id === cycleId ? candidate : null;
    setCandidate(kept);
    setRound(kept ? kept.round : null);
    setNode(null);
    setSampleSet(null);
    setObserve(null);
  }

  const setSelectionForCandidate = useCallback(
    (c: SelectedCandidate | null) => {
      setCandidate(c);
      setRound(c ? c.round : null);
      setObserve(null);
    },
    [],
  );

  const setSelectionForRound = useCallback((r: number | null) => {
    setRound(r);
    setObserve(null);
    if (r != null) {
      setCandidate((prev) => (prev && prev.round !== r ? null : prev));
    }
  }, []);

  const setSelectionForNode = useCallback((n: SelectedNode | null) => {
    setNode(n);
  }, []);

  const setSelectionForSampleSet = useCallback((ids: number[] | null) => {
    setSampleSet(ids);
  }, []);

  const value = useMemo<Ctx>(
    () => ({
      candidate,
      round,
      node,
      sampleSet,
      observe,
      setObserve,
      setSelectionForCandidate,
      setSelectionForRound,
      setSelectionForNode,
      setSelectionForSampleSet,
    }),
    [
      candidate,
      round,
      node,
      sampleSet,
      observe,
      setSelectionForCandidate,
      setSelectionForRound,
      setSelectionForNode,
      setSelectionForSampleSet,
    ],
  );

  return <SelectionCtx.Provider value={value}>{children}</SelectionCtx.Provider>;
}

export function useSelection(): Ctx {
  const c = useContext(SelectionCtx);
  if (!c) throw new Error("useSelection must be used inside SelectionProvider");
  return c;
}
