import { READING_STATE_INFO } from "@/lib/api/types.generated";
import type { MeasuredLift, PairedReading } from "@/lib/api/types";
import { fmtLift, fmtUnit } from "@/lib/format";

type PairRead =
  | { read: true; lift: MeasuredLift; cells: number }
  | { read: false; label: string; sentence: string };

export function readPaired(reading: PairedReading): PairRead {
  const { headline, coverage, state } = reading;
  if (headline !== null && coverage !== null) {
    return { read: true, lift: headline, cells: coverage.scored };
  }
  const { label, sentence } = READING_STATE_INFO[state];
  return { read: false, label, sentence };
}

export function liftOf(reading: PairedReading | null): MeasuredLift | null {
  if (reading === null) return null;
  const pair = readPaired(reading);
  return pair.read ? pair.lift : null;
}

export type PairFacet = "rates" | "lift";

export function pairText(lift: MeasuredLift, facet: PairFacet): string {
  const { unit } = lift.measurand;
  if (facet === "lift") return fmtLift(unit, lift.estimate.value);
  return `${fmtUnit(unit, lift.rate_a)} → ${fmtUnit(unit, lift.rate_b)}`;
}
