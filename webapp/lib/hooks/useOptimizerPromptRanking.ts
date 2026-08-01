"use client";
// The ranked table of edits to the optimizer's own prompts,
// reduced fresh server-side from the tenant's on-disk pp-self cycles. One-shot
// fetch (not the 2 s poll): it feeds the outer-loop dashboard boxes on demand.
//
// Pass `enabled=false` (the dashboard passes `isOuterSelfOpt`) so the fetch fires
// only when viewing the outer pp-self loop — a normal or inner-loop view never
// hits the route.

import { useFetch } from "@/lib/hooks/useFetch";
import { fetchOptimizerPromptRanking, type OptimizerPromptRanking } from "@/lib/api";

export function useOptimizerPromptRanking(enabled: boolean): {
  registry: OptimizerPromptRanking | null;
  loading: boolean;
  error: string | null;
} {
  const { data, loading, error } = useFetch<OptimizerPromptRanking>(
    enabled ? (signal) => fetchOptimizerPromptRanking(signal) : null,
    [enabled],
  );
  return { registry: data, loading, error };
}
