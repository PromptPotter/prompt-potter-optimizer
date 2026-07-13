"use client";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import type { SelectedCandidate } from "@/lib/types";

// One shared selection store for the dashboard. Three axes:
//
//   - candidate — which searchpoint is the operator inspecting
//   - round     — which L1-round are the lineage / fitness / samples
//                 surfaces scoped to (null = follow live in-flight)
//   - node      — which optimizer node is open for drill (workflow detail)
//   - sampleSet — a fixed set of sample ids to recompute the per-candidate
//                 fitness bars over (null = each bar over its own samples).
//                 Populated by the Sample-trajectory "Steps" view; consumed
//                 by the candidates card's "fixed sample set" mode. Independent axis.
//
// The candidate and round axes are coupled: a candidate selection
// implies a round (the candidate's). Writes go through the helpers
// below so the pair stays coherent — no orphan `setRound` that
// strands the prior candidate on a different round, no orphan
// `setSelected` that leaves the samples view scoped elsewhere.

export type { SelectedCandidate };

interface Ctx {
  candidate: SelectedCandidate | null;
  round: number | null;
  node: string | null;
  sampleSet: number[] | null;
  // Atomic candidate+round write. Passing null clears both axes.
  setSelectionForCandidate: (c: SelectedCandidate | null) => void;
  // Round-axis write. Clears any candidate selection whose round
  // differs — a stranded highlight on the wrong round is never the
  // user's intent.
  setSelectionForRound: (r: number | null) => void;
  // Workflow-node write. Independent of the candidate/round pair.
  setSelectionForNode: (n: string | null) => void;
  // Fixed-sample-set write. Passing null returns the fitness bars to
  // per-candidate-own-samples mode. Independent of the other axes.
  setSelectionForSampleSet: (ids: number[] | null) => void;
}

const SelectionCtx = createContext<Ctx | null>(null);

// Provider lives high in AppShell (above the shell) so selection
// persists across Chat/Dashboard/Files tab switches. Auto-clears when
// the operator picks a different cycle: a stale candidate_id would
// point into the wrong cycle's tree, a stale node would mismatch the
// new cycle's pipeline, and a stale round would scope drills into
// the wrong cycle's round_NNNN.json.
export function SelectionProvider({
  cycleId,
  children,
}: {
  cycleId: string | null;
  children: ReactNode;
}) {
  const [candidate, setCandidate] = useState<SelectedCandidate | null>(null);
  const [round, setRound] = useState<number | null>(null);
  const [node, setNode] = useState<string | null>(null);
  const [sampleSet, setSampleSet] = useState<number[] | null>(null);
  const [prevCycle, setPrevCycle] = useState(cycleId);
  if (cycleId !== prevCycle) {
    setPrevCycle(cycleId);
    setCandidate(null);
    setRound(null);
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
    // Drop candidate when its round no longer matches; r=null (follow
    // live) leaves the candidate alone so a live-mode click on the
    // round-tabs strip doesn't blow away a still-relevant selection.
    if (r != null) {
      setCandidate((prev) => (prev && prev.round !== r ? null : prev));
    }
  }, []);

  const setSelectionForNode = useCallback((n: string | null) => {
    setNode(n);
  }, []);

  const setSelectionForSampleSet = useCallback((ids: number[] | null) => {
    // `null` = mode off (per-candidate-own-samples). An empty array is a
    // DISTINCT state: mode on, nothing selected — the chart blanks and the
    // operator builds the set up sample-by-sample. Only the panel's own
    // "Sample set" chip flips back to null.
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
