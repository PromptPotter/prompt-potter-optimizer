"use client";
// Cross-surface store for the served mask (lens / What-If) divergence overlay.
//
// The lineage card is the single fetcher of `/lineage?mask=…` — it owns the
// divergence projection. But the divergence is a property of the *record*, not
// of the tree widget: the per-round samples axis wants to show it too ("which
// rounds would a different scorer have diverged at?"). Rather than fetch the
// endpoint twice, the lineage card PUBLISHES its served overlay here and the
// round-axis surfaces (RoundTabsStrip, RoundSamplesView) SUBSCRIBE. One fetch,
// one source of truth, rendered — never recomputed (R-36).
//
// Both publisher (FamilyTree) and subscribers are co-mounted on the dashboard
// tab, so the store is always fresh when the round axis reads it.

import { useSyncExternalStore } from "react";
import type { LineageDivergence } from "@/lib/api/types";

interface MaskState {
  // First node per branch where the masked criterion flips the recorded winner.
  divergences: LineageDivergence[];
  // Node keys (`{cycle_id}::r{round}`) of the counterfactual descendant subtree.
  divergent: string[];
  // A mask is driving the projection (drives the red tag on subscriber surfaces).
  maskActive: boolean;
  // Short human label for the active lens ("What-If", "Accuracy", "No abort", …).
  maskLabel: string;
}

let state: MaskState = {
  divergences: [],
  divergent: [],
  maskActive: false,
  maskLabel: "",
};

const listeners = new Set<() => void>();

function emit(): void {
  for (const l of listeners) l();
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

function getSnapshot(): MaskState {
  return state;
}

function sameStrings(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) return false;
  return true;
}

function sameDivergences(a: LineageDivergence[], b: LineageDivergence[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) {
    if (a[i].node_key !== b[i].node_key) return false;
    if (a[i].alternative_candidate_id !== b[i].alternative_candidate_id) return false;
  }
  return true;
}

// Publish the served overlay. Content-guarded so a bare 2 s poll that re-fetches
// identical divergences doesn't churn every subscriber's render.
export function setMaskState(next: MaskState): void {
  if (
    state.maskActive === next.maskActive &&
    state.maskLabel === next.maskLabel &&
    sameStrings(state.divergent, next.divergent) &&
    sameDivergences(state.divergences, next.divergences)
  ) {
    return;
  }
  state = next;
  emit();
}

export function useMaskState(): MaskState {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

// Per-cycle divergence facts for the round axis — which rounds of `cycleId` are
// a divergence point vs. inside the counterfactual subtree. Pure derivation over
// the published overlay; both surfaces share it so they mark identically.
export function divergenceRoundsFor(
  state: MaskState,
  cycleId: string | null,
): { points: ReadonlySet<number>; subtree: ReadonlySet<number> } {
  const points = new Set<number>();
  const subtree = new Set<number>();
  if (!cycleId) return { points, subtree };
  for (const d of state.divergences) {
    if (d.cycle_id === cycleId) points.add(d.round);
  }
  const prefix = `${cycleId}::r`;
  for (const key of state.divergent) {
    if (key.startsWith(prefix)) {
      const r = Number(key.slice(prefix.length));
      if (Number.isFinite(r)) subtree.add(r);
    }
  }
  return { points, subtree };
}
