"use client";
// The single "unfiled round → `dash`, filed round → round_NNNN.json" guard: it SELECTS one source,
// never merges. Viewed-cycle surfaces read `useRoundRows`; this takes a path for any other cycle.

import { roundOf, type DashboardSnapshot } from "@/lib/poll";
import { useRoundFile, type RoundFileState } from "@/lib/hooks/useRoundFile";
import type { CyclePath } from "@/lib/ids";
import type { RoundResult } from "@/lib/types";

interface RoundSourceState extends RoundFileState<RoundResult> {
  // No round file yet: read `dash`; `doc` stays null.
  unfiled: boolean;
}

// "Does a round FILE exist yet" — not equality with `current_round.round`, and not
// `measuredRoundNumbers`, which drops the empty rounds that closed before measuring and still get a file.
export function isRoundUnfiled(dash: DashboardSnapshot | null, round: number | null): boolean {
  const filed = (dash?.rounds ?? []).some((r) => r.round === round);
  return round != null && round === roundOf(dash) && !filed;
}

export function useRoundSource(
  path: CyclePath | null,
  round: number | null,
  dash: DashboardSnapshot | null,
): RoundSourceState {
  const unfiled = isRoundUnfiled(dash, round);
  const file = useRoundFile(unfiled ? null : path, round);
  return { ...file, unfiled };
}
