import { describe, expect, it } from "vitest";
import {
  READING_STATE_KINDS,
  READING_STATE_LABELS,
  READING_STATE_SENTENCES,
  type ReadingState,
} from "@/lib/api/types.generated";
import { pairedReading, unreadOverlap } from "@/lib/test-fixtures";
import { fmtPaired, liftOf, readPaired } from "../paired-reading";

describe("readPaired", () => {
  it("reads a read pair as its lift over the cells both scored", () => {
    const pair = readPaired(pairedReading(0.12, [0.01, 0.23], { rateA: 0.5 }));
    expect(pair).toMatchObject({ read: true, cells: 10 });
    expect(fmtPaired(pairedReading(0.12, [0.01, 0.23], { rateA: 0.5 }), "rates")).toBe("50% → 62%");
    expect(fmtPaired(pairedReading(0.12, [0.01, 0.23], { rateA: 0.5 }), "lift")).toBe("+0.120");
  });

  const UNREAD = (Object.keys(READING_STATE_KINDS) as ReadingState[]).filter((s) => s !== "read");

  it.each(UNREAD)("says why a %s pair was not read, in the served words", (state) => {
    const reading = { ...unreadOverlap().lead, state };
    expect(readPaired(reading)).toEqual({
      read: false,
      kind: READING_STATE_KINDS[state],
      label: READING_STATE_LABELS[state],
      sentence: READING_STATE_SENTENCES[state],
    });
    expect(fmtPaired(reading, "rates")).toBe(READING_STATE_LABELS[state]);
    expect(liftOf(reading)).toBeNull();
  });
});
