import { useMemo } from "react";
import type { ArmReading, RoundView, ServedArm } from "@/lib/api/types";
import { roundRead } from "../api";
import {
  armObserveConfig,
  armSearchPoint,
  armVerdicts,
  groupByRound,
  liveCandidates,
  roundCandidates,
  servedArm,
  type CandidateSearchPoint,
  type CandidateVerdict,
  type ObserveConfig,
} from "../derivations";
import type { CyclePath } from "../ids";
import { useDashboard } from "../poll";
import type { ReadResult } from "../read-cache";
import type { ArmRow } from "../types";
import { useMomentAt } from "../workspace";
import { readyData, useRead } from "./useRead";

interface Round {
  // The round no file holds yet (`round_axis.unfiled`): its arms are the dashboard's, and `read` is idle.
  unfiled: boolean;
  read: ReadResult<RoundView>;
  rows: ArmRow[];
  cfg: (key: string | null, display: string, nodeId?: string | null) => ObserveConfig | null;
  seed: (key: string) => CandidateSearchPoint | null;
  reading: (key: string | null) => ArmReading | null;
  verdicts: Map<string, CandidateVerdict>;
}

const NO_ROWS: ArmRow[] = [];
const NO_ARMS: ServedArm[] = [];

export function useRound(path: CyclePath | null, round: number | null): Round {
  // Another cycle's round reads its dashboard once; the viewed one rides the viewed poll.
  const { dash } = useDashboard(path);
  const unfiled = round != null && round === (dash?.round_axis.unfiled ?? null);
  const at = useMomentAt(path);
  const read = useRead(path && round != null && !unfiled ? roundRead(path, round, at) : null);
  const doc = readyData(read);
  // Both halves serve one `ServedArm` shape; a filed round's arms do not re-derive every poll.
  const liveArms = unfiled ? liveCandidates(dash) : null;
  const arms = liveArms ?? doc?.candidates ?? NO_ARMS;

  const rows = useMemo(() => {
    if (round == null) return NO_ROWS;
    return groupByRound(roundCandidates(dash)).get(round) ?? NO_ROWS;
  }, [dash, round]);

  const answers = useMemo<Pick<Round, "cfg" | "seed" | "reading" | "verdicts">>(
    () => ({
      cfg: (key, display, nodeId) => armObserveConfig(servedArm(arms, key), display, nodeId),
      seed: (key) => armSearchPoint(servedArm(arms, key)),
      reading: (key) => servedArm(arms, key)?.reading ?? null,
      verdicts: armVerdicts(arms),
    }),
    [arms],
  );
  return useMemo(() => ({ unfiled, read, rows, ...answers }), [unfiled, read, rows, answers]);
}
