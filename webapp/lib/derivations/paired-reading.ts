import {
  CELL_SET_NAME_WORDS,
  READING_STATE_INFO,
  type CellSet,
  type MeasuredLift,
  type PairedReading,
} from "@/lib/api/types";
import { fmtLift, fmtUnit } from "@/lib/format";

// A lift's one name, as the server words it (`paired_reading.py::lift_versus`): whom it is over and
// the sheet its reading was read on, so one pair read on two sheets never shares a name.
export function liftVersus(over: string, cells: CellSet | null | undefined): string {
  return cells == null ? `vs ${over}` : `vs ${over} ${CELL_SET_NAME_WORDS[cells.name]}`;
}

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
