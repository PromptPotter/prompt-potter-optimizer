"use client";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import type { SelectedCandidate } from "@/lib/types";

// One shared selection store for the dashboard. Three axes:
//
//   - candidate — which searchpoint is the operator inspecting
//   - round     — which L1-round are the lineage / fitness / samples
//                 surfaces scoped to (null = follow live in-flight)
//   - node      — which node is open for drill, AND which canvas it was
//                 clicked on (see SelectedNode)
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

// Which canvas a node was selected on. Load-bearing: a node ID alone does NOT
// identify a node, because the two canvases are NOT disjoint namespaces. The
// `promptpotter-self` TARGET pipeline declares nodes named `l1_generate` /
// `l1_critique` / `l2_context` / `l3_plan` — byte-identical to the OPTIMIZER's
// own node ids. Under an id-only selection, clicking `l1_generate` on the chat
// hero (a target node) also opened OptimizerNodeDetail for the optimizer's
// unrelated `l1_generate` on the Dashboard. The clicked canvas is the only thing
// that disambiguates them, so the writer records it rather than a reader
// guessing from the name.
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
  // Atomic candidate+round write. Passing null clears both axes.
  setSelectionForCandidate: (c: SelectedCandidate | null) => void;
  // Round-axis write. Clears any candidate selection whose round
  // differs — a stranded highlight on the wrong round is never the
  // user's intent.
  setSelectionForRound: (r: number | null) => void;
  // Node write — the caller names the canvas it is writing from. Independent
  // of the candidate/round pair.
  setSelectionForNode: (n: SelectedNode | null) => void;
  // Fixed-sample-set write. Passing null returns the fitness bars to
  // per-candidate-own-samples mode. Independent of the other axes.
  setSelectionForSampleSet: (ids: number[] | null) => void;
}

const SelectionCtx = createContext<Ctx | null>(null);

// Provider lives high in AppShell (above the shell) so selection persists across
// Chat/Dashboard/Files tab switches. It is keyed on the VIEWED LEAF hop, the same
// cycle the inspector, the samples panes and `round_NNNN.json` re-root to — the
// axes below scope those surfaces, so they have to agree on which cycle they mean.
// Keyed on the root hop instead, drilling into an L4 inner loop left the outer
// cycle's candidate selected against the inner cycle's rounds.
//
// Cycle change drops the node and sample-set axes outright: a node would mismatch
// the new cycle's pipeline, and a sample set is a list of ids picked from the old
// one. The candidate survives only if it NAMES the incoming cycle — i.e. it was
// picked for the cycle being navigated to, which is exactly the cross-cycle click
// (a sidebar candidate row, an off-lane forest node) that this clear used to eat.
// A candidate from any other cycle points into the wrong tree and still goes.
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
    // Round follows the candidate it belongs to — kept together, or dropped
    // together. A surviving candidate with a cleared round would scope the
    // samples view off the round that produced it.
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
    // Drop candidate when its round no longer matches; r=null (follow
    // live) leaves the candidate alone so a live-mode click on the
    // round-tabs strip doesn't blow away a still-relevant selection.
    if (r != null) {
      setCandidate((prev) => (prev && prev.round !== r ? null : prev));
    }
  }, []);

  const setSelectionForNode = useCallback((n: SelectedNode | null) => {
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
