"use client";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import type { SelectedCandidate } from "@/lib/types";

// The dashboard's INSPECTION axes: candidate, round (null = follow live), node and sampleSet.
// A candidate implies its round, so the pair is written only through the helpers below.

export type { SelectedCandidate };

// A node id alone is ambiguous: the `promptpotter-self` TARGET pipeline declares node ids
// byte-identical to the OPTIMIZER's own, so the writer records the canvas it was clicked on.
export type NodeScope = "optimizer" | "target";

export interface SelectedNode {
  id: string;
  scope: NodeScope;
}

interface Ctx {
  candidate: SelectedCandidate | null;
  round: number | null;
  node: SelectedNode | null;
  sampleSet: number[] | null;
  setSelectionForCandidate: (c: SelectedCandidate | null) => void;
  // Clears a candidate whose round differs.
  setSelectionForRound: (r: number | null) => void;
  setSelectionForNode: (n: SelectedNode | null) => void;
  setSelectionForSampleSet: (ids: number[] | null) => void;
}

const SelectionCtx = createContext<Ctx | null>(null);

// Keyed on the VIEWED LEAF hop, the cycle the inspector, samples panes and round file re-root to.
// NOT restored from view memory: a `SelectedCandidate` carries `is_winner` and `accuracy`, and a
// restored one is a measurement claim the operator would read as current.
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
  const [prevCycle, setPrevCycle] = useState(cycleId);
  if (cycleId !== prevCycle) {
    setPrevCycle(cycleId);
    // A candidate survives only if it NAMES the incoming cycle — the cross-cycle click (a sidebar
    // candidate row, an off-lane forest node) — and its round goes with it.
    const kept = candidate && candidate.cycle_id === cycleId ? candidate : null;
    setCandidate(kept);
    setRound(kept ? kept.round : null);
    setNode(null);
    setSampleSet(null);
  }

  const setSelectionForCandidate = useCallback(
    (c: SelectedCandidate | null) => {
      setCandidate(c);
      setRound(c ? c.round : null);
    },
    [],
  );

  const setSelectionForRound = useCallback((r: number | null) => {
    setRound(r);
    // r=null (follow live) leaves a still-relevant candidate alone.
    if (r != null) {
      setCandidate((prev) => (prev && prev.round !== r ? null : prev));
    }
  }, []);

  const setSelectionForNode = useCallback((n: SelectedNode | null) => {
    setNode(n);
  }, []);

  const setSelectionForSampleSet = useCallback((ids: number[] | null) => {
    // `null` = no pick (the served set); `[]` is distinct — the picker open with nothing selected.
    setSampleSet(ids);
  }, []);

  return (
    <SelectionCtx.Provider
      value={{
        candidate,
        round,
        node,
        sampleSet,
        setSelectionForCandidate,
        setSelectionForRound,
        setSelectionForNode,
        setSelectionForSampleSet,
      }}
    >
      {children}
    </SelectionCtx.Provider>
  );
}

export function useSelection(): Ctx {
  const c = useContext(SelectionCtx);
  if (!c) throw new Error("useSelection must be used inside SelectionProvider");
  return c;
}
