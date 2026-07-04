"use client";
// L4 champion registry — the ranked table of candidate meta-prompt states,
// reduced fresh server-side from the tenant's on-disk pp-self cycles. One-shot
// fetch (not the 2 s poll): the L4 Lab is a dev-only on-demand surface.
//
// `n_cycles_scanned === 0` is the whitelabel gate: a tenant with no pp-self
// campaigns (every end-user) gets an empty registry, so the Lab tab stays hidden.

import { useFetch } from "@/lib/hooks/useFetch";
import { fetchChampionRegistry, type ChampionRegistryResponse } from "@/lib/api";

export function useChampionRegistry(): {
  registry: ChampionRegistryResponse | null;
  loading: boolean;
  error: string | null;
} {
  const { data, loading, error } = useFetch<ChampionRegistryResponse>(
    (signal) => fetchChampionRegistry(signal),
    [],
  );
  return { registry: data, loading, error };
}

// The whitelabel dev-gate: show the L4 Lab only when the tenant has pp-self
// cycles on disk. End-users cannot mint pp-self, so this is always false for them.
export function hasL4Data(registry: ChampionRegistryResponse | null): boolean {
  return !!registry && registry.n_cycles_scanned > 0;
}
