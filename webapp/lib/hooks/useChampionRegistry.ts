"use client";
// L4 champion registry — the ranked table of candidate meta-prompt states,
// reduced fresh server-side from the tenant's on-disk pp-self cycles. One-shot
// fetch (not the 2 s poll): it feeds the outer-loop dashboard boxes on demand.
//
// Pass `enabled=false` (the dashboard passes `isOuterSelfOpt`) so the fetch fires
// only when viewing the outer pp-self loop — a normal or inner-loop view never
// hits the route.

import { useFetch } from "@/lib/hooks/useFetch";
import { fetchChampionRegistry, type ChampionRegistry } from "@/lib/api";

export function useChampionRegistry(enabled: boolean): {
  registry: ChampionRegistry | null;
  loading: boolean;
  error: string | null;
} {
  const { data, loading, error } = useFetch<ChampionRegistry>(
    enabled ? (signal) => fetchChampionRegistry(signal) : null,
    [enabled],
  );
  return { registry: data, loading, error };
}
