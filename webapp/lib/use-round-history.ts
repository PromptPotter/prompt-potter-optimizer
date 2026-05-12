"use client";
import { useEffect, useState } from "react";
import { fetchCycleFile, fetchFiles } from "./api";

// Shape-agnostic round-file document. Every consumer casts to its own
// narrowed view at the use site; the hook is the single fetch path so the
// dashboard makes one set of round-file network calls instead of three
// (previously HeroSummary / TrendChart / FitnessPanel each ran the same
// listing + Promise.all independently).
export interface RoundFileDoc {
  round?: number;
  accuracy?: number;
  composite_fitness?: number;
  origin_accuracy?: number;
  scoreboard?: unknown[];
  results?: unknown[];
  candidate_scores?: unknown[];
  all_candidate_results?: Record<string, unknown>;
  [k: string]: unknown;
}

export function useRoundHistory(
  cycleId: string | null,
  refreshKey: number,
): RoundFileDoc[] {
  const [rounds, setRounds] = useState<RoundFileDoc[]>([]);

  // Cycle change ⇒ clear immediately so the prior cycle's docs can't linger
  // on-screen during the new fetch. Prev-prop pattern (React's documented
  // recipe) avoids a cascading render that `setState` in `useEffect` would
  // cost.
  const [prevCycleId, setPrevCycleId] = useState<string | null>(cycleId);
  if (cycleId !== prevCycleId) {
    setPrevCycleId(cycleId);
    setRounds([]);
  }

  useEffect(() => {
    if (!cycleId) return;
    let cancelled = false;
    (async () => {
      try {
        const listing = await fetchFiles(cycleId);
        const roundFiles = listing.entries
          .filter((e) => e.scope === "cycle" && /^rounds\/round_\d+\.json$/.test(e.path))
          .sort((a, b) => a.path.localeCompare(b.path));
        const fetched = await Promise.all(
          roundFiles.map(async (f) => {
            try {
              const r = await fetchCycleFile(cycleId, "cycle", f.path);
              return JSON.parse(r.content) as RoundFileDoc;
            } catch {
              return null;
            }
          }),
        );
        const valid = fetched
          .filter((p): p is RoundFileDoc => p !== null)
          .sort((a, b) => (a.round ?? 0) - (b.round ?? 0));
        if (!cancelled) setRounds(valid);
      } catch {
        /* listing failed — leave prior state, retry on next refreshKey */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cycleId, refreshKey]);

  return rounds;
}
