"use client";
import { createContext, useContext, useState, type ReactNode } from "react";

// One shared candidate-selection slot. Every panel that shows or surfaces
// a candidate (LineageTree, FitnessPanel, ScoringInspector, future
// Workflow per-candidate detail) reads + writes through this context so
// clicking in any of them highlights the same row everywhere.
//
// `candidate_id` matches LineageTree's fallback (`r{round}_{idx}` when the
// round file doesn't carry an explicit id) — FitnessPanel uses the same
// composition so the two surfaces line up bar-for-stub.
export interface SelectedCandidate {
  round: number;
  candidate_id: string;
  label: string;
  accuracy: number | null;
  is_winner: boolean;
}

interface Ctx {
  selected: SelectedCandidate | null;
  setSelected: (c: SelectedCandidate | null) => void;
  // Workflow node selection — separate slot from candidate because the
  // two can coexist (operator can pick a candidate AND a specific node
  // to drill into within that searchpoint). SharedInspector reads both.
  node: string | null;
  setNode: (n: string | null) => void;
}

const SelectionCtx = createContext<Ctx | null>(null);

// Provider lives high in DashboardPane (above the shell) so selection
// persists across Chat/Dashboard/Files tab switches. Auto-clears when the
// operator picks a different cycle: a stale `candidate_id` would point
// into the wrong cycle's tree, and a stale `node` would mismatch the new
// cycle's pipeline.
export function SelectionProvider({
  cycleId,
  children,
}: {
  cycleId: string | null;
  children: ReactNode;
}) {
  const [selected, setSelected] = useState<SelectedCandidate | null>(null);
  const [node, setNode] = useState<string | null>(null);
  const [prevCycle, setPrevCycle] = useState(cycleId);
  if (cycleId !== prevCycle) {
    setPrevCycle(cycleId);
    setSelected(null);
    setNode(null);
  }
  return (
    <SelectionCtx.Provider value={{ selected, setSelected, node, setNode }}>
      {children}
    </SelectionCtx.Provider>
  );
}

export function useSelection(): Ctx {
  const c = useContext(SelectionCtx);
  if (!c) throw new Error("useSelection must be used inside SelectionProvider");
  return c;
}
