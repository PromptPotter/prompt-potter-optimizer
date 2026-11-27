import { useMemo } from "react";
import type { ArmReading, RoundView, ServedArm } from "@/lib/api/types";
import { roundRead } from "../api";
import {
  armObserveConfig,
  armSearchPoint,
  servedArm,
  type CandidateSearchPoint,
  type ObserveConfig,
} from "../derivations";
import type { CyclePath } from "../ids";
import type { ReadResult } from "../read-cache";
import { useMomentAt } from "@/lib/state/workspace";
import { shownData, useRead } from "./useRead";

interface Round {
  // No file holds the round yet (`RoundView.unfiled`): its arms stream, and it has no verdict.
  unfiled: boolean;
  read: ReadResult<RoundView>;
  arms: ServedArm[];
  cfg: (key: string | null, display: string, nodeId?: string | null) => ObserveConfig | null;
  start: (key: string) => CandidateSearchPoint | null;
  reading: (key: string | null) => ArmReading | null;
}

const NO_ARMS: ServedArm[] = [];

// The round read alone, filed or in flight: the server merges the two into one series.
export function useRound(path: CyclePath | null, round: number | null): Round {
  const at = useMomentAt(path);
  const read = useRead(path && round != null ? roundRead(path, round, at) : null);
  const doc = shownData(read);
  const arms = doc?.candidates ?? NO_ARMS;
  const unfiled = doc?.unfiled ?? false;

  const answers = useMemo<Pick<Round, "cfg" | "start" | "reading">>(() => {
    // One start per arm per answer: an editor seeded off it keeps its rows across renders.
    const starts = new Map(arms.map((arm) => [arm.reading.arm.key, armSearchPoint(arm)]));
    return {
      cfg: (key, display, nodeId) => armObserveConfig(servedArm(arms, key), display, nodeId),
      start: (key) => starts.get(key) ?? null,
      reading: (key) => servedArm(arms, key)?.reading ?? null,
    };
  }, [arms]);
  return useMemo(() => ({ unfiled, read, arms, ...answers }), [unfiled, read, arms, answers]);
}
