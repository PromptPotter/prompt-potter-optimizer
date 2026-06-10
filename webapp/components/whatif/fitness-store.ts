"use client";
// Cross-mount store for FitnessPanel toggles + selected evaluators.
//
// Both ChatPane (New Job tab) and AppShell (View Results) render their
// own <FitnessPanel/>, but only one tab is mounted at a time. Local
// component state would reset every tab swap. This module-scoped store
// keeps the chip toggles and what-if selection coherent across remounts.
//
// Selection seeding is scoped to the cycle: `seededForCycle` records which
// cycle the current `selected` set was seeded for. When the cycle changes
// (operator re-`init`s, or webapp polls a fresh active session), the panel
// re-seeds against the new cycle's formula instead of inheriting the prior
// cycle's picks.

import { useSyncExternalStore } from "react";

interface FitnessState {
  showComposite: boolean;
  showWhatIf: boolean;
  selected: Set<string>;
  // Per-evaluator what-if weight (the slider value). Seeded from the realized
  // composite formula's coefficients per cycle; the operator reweights to
  // explore. Keyed by evaluator display name; missing ⇒ DEFAULT_WHATIF_WEIGHT.
  weights: Record<string, number>;
  seededForCycle: string | null;
}

let state: FitnessState = {
  showComposite: false,
  showWhatIf: false,
  selected: new Set<string>(),
  weights: {},
  seededForCycle: null,
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

function getSnapshot(): FitnessState {
  return state;
}

export function setFitnessState(patch: Partial<FitnessState>): void {
  state = { ...state, ...patch };
  emit();
}

export function useFitnessState(): FitnessState {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
