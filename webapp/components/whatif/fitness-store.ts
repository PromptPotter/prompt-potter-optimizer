"use client";
// Cross-mount store for FitnessPanel toggles + selected evaluators.
//
// Both ChatPane (New Job tab) and DashboardPane (View Results) render their
// own <FitnessPanel/>, but only one tab is mounted at a time. Local
// component state would reset every tab swap. This module-scoped store
// keeps the chip toggles and what-if selection coherent across remounts.

import { useSyncExternalStore } from "react";

interface FitnessState {
  showComposite: boolean;
  showWhatIf: boolean;
  selected: Set<string>;
  // Once the panel has seeded `selected` from the active formula it sets
  // this so subsequent remounts don't overwrite operator picks.
  seeded: boolean;
}

let state: FitnessState = {
  showComposite: false,
  showWhatIf: false,
  selected: new Set<string>(),
  seeded: false,
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
