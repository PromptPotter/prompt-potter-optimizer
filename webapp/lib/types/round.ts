import type { ArmRow } from "./candidate";

export type {
  RoundResult,
  ScoreboardRow,
  ServedRound,
} from "@/lib/api/types";

export type RoundCandidates = Map<number, ArmRow[]>;
