import {
  READING_STATE_KINDS,
  READING_STATE_LABELS,
  READING_STATE_SENTENCES,
  type ReadingStateKind,
} from "@/lib/api/types.generated";
import type { LiftEstimate, MeasuredLift, PairedReading } from "@/lib/api/types";
import { fmtLevel, fmtSigned } from "@/lib/format";

type PairRead =
  | { read: true; lift: MeasuredLift; cells: number }
  | { read: false; kind: ReadingStateKind; label: string; sentence: string };

export function readPaired(reading: PairedReading): PairRead {
  const { headline, coverage, state } = reading;
  if (headline !== null && coverage !== null) {
    return { read: true, lift: headline, cells: coverage.scored };
  }
  return {
    read: false,
    kind: READING_STATE_KINDS[state],
    label: READING_STATE_LABELS[state],
    sentence: READING_STATE_SENTENCES[state],
  };
}

export function liftOf(reading: PairedReading | null): LiftEstimate | null {
  if (reading === null) return null;
  const pair = readPaired(reading);
  return pair.read ? pair.lift.estimate : null;
}

export type PairFacet = "rates" | "lift";

export function fmtLift(lift: MeasuredLift, facet: PairFacet): string {
  if (facet === "lift") return fmtSigned(lift.estimate.value);
  const { unit } = lift.measurand;
  return `${fmtLevel(unit, lift.rate_a)} → ${fmtLevel(unit, lift.rate_b)}`;
}

export function fmtPaired(reading: PairedReading, facet: PairFacet): string {
  const pair = readPaired(reading);
  return pair.read ? fmtLift(pair.lift, facet) : pair.label;
}
