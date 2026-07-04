"use client";
// L4 champion registry — the ranked table of candidate meta-prompt states,
// reduced fresh server-side from the tenant's on-disk pp-self cycles. One-shot
// fetch (not the 2 s poll): the L4 Lab is a dev-only on-demand surface.
//
// Visibility is gated by the `L4_LAB_CAP` capability off `/auth/me` (the dev-only
// whitelabel gate), NOT by whether data exists — so a developer with zero pp-self
// cycles still opens the Lab (to its empty state), and no end-user ever hits the
// route. Pass `enabled=false` for a non-dev identity so the fetch never fires (the
// route 404s without the capability anyway).

import { useFetch } from "@/lib/hooks/useFetch";
import { fetchChampionRegistry, type ChampionRegistryResponse } from "@/lib/api";

export function useChampionRegistry(enabled: boolean): {
  registry: ChampionRegistryResponse | null;
  loading: boolean;
  error: string | null;
} {
  const { data, loading, error } = useFetch<ChampionRegistryResponse>(
    enabled ? (signal) => fetchChampionRegistry(signal) : null,
    [enabled],
  );
  return { registry: data, loading, error };
}
