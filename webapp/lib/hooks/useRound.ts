import { useMemo } from "react";
import type { ArmReading, RoundView, ServedDashboard } from "@/lib/api/types";
import { roundRead } from "../api";
import {
  candidateObserveConfig,
  candidateSearchPoint,
  candidateVerdicts,
  groupByRound,
  liveCandidateObserveConfig,
  liveCandidateSearchPoint,
  liveCandidateVerdicts,
  roundCandidates,
  scoreboardReading,
  type CandidateSearchPoint,
  type CandidateVerdict,
  type ObserveConfig,
} from "../derivations";
import type { CyclePath } from "../ids";
import { liveCandidate, roundOf, useDashboard } from "../poll";
import type { ReadResult } from "../read-cache";
import type { ArmRow } from "../types";
import { useMomentAt } from "../workspace";
import { readyData, useRead } from "./useRead";

interface Round {
  // The round still in flight has no file: everything below is the dashboard's, and `read` is idle.
  unfiled: boolean;
  read: ReadResult<RoundView>;
  rows: ArmRow[];
  // A key's config, seed and reading, each off the one half that holds the round.
  cfg: (key: string | null, display: string, nodeId?: string | null) => ObserveConfig | null;
  seed: (key: string) => CandidateSearchPoint | null;
  reading: (key: string | null) => ArmReading | null;
  verdicts: Map<string, CandidateVerdict>;
}

// Not `round_axis.completed`: it drops the empty rounds that closed before measuring yet have a file.
export function isRoundUnfiled(dash: ServedDashboard | null, round: number | null): boolean {
  const filed = (dash?.rounds ?? []).some((r) => r.round === round);
  return round != null && round === roundOf(dash) && !filed;
}

const NO_ROWS: ArmRow[] = [];

export function useRound(path: CyclePath | null, round: number | null): Round {
  // Another cycle's round reads its dashboard once; the viewed one rides the viewed poll.
  const { dash } = useDashboard(path, null);
  const unfiled = isRoundUnfiled(dash, round);
  const at = useMomentAt(path);
  const read = useRead(path && round != null && !unfiled ? roundRead(path, round, at) : null);
  const doc = readyData(read);
  // Null while filed, so the answers below do not re-derive every poll.
  const live = unfiled ? dash : null;

  const rows = useMemo(() => {
    if (round == null) return NO_ROWS;
    return groupByRound(roundCandidates(dash)).get(round) ?? NO_ROWS;
  }, [dash, round]);

  const answers = useMemo<Pick<Round, "cfg" | "seed" | "reading" | "verdicts">>(
    () => ({
      cfg: (key, display, nodeId) =>
        unfiled
          ? liveCandidateObserveConfig(live, key, display, nodeId)
          : candidateObserveConfig(doc, key, display, nodeId),
      seed: (key) => (unfiled ? liveCandidateSearchPoint(live, key) : candidateSearchPoint(doc, key)),
      reading: (key) =>
        key === null
          ? null
          : unfiled
            ? (liveCandidate(live, key)?.reading ?? null)
            : scoreboardReading(doc, key),
      verdicts: unfiled ? liveCandidateVerdicts(live) : candidateVerdicts(doc),
    }),
    [unfiled, live, doc],
  );
  return useMemo(() => ({ unfiled, read, rows, ...answers }), [unfiled, read, rows, answers]);
}
