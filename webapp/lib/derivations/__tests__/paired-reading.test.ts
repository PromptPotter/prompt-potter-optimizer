import { describe, expect, it } from "vitest";
import { READING_STATE_INFO } from "@/lib/api/types";
import { wordKeys } from "@/lib/api/words";
import { pairedReading, unreadOverlap } from "@/lib/test-fixtures";
import { liftOf, readPaired } from "../paired-reading";

describe("readPaired", () => {
  it("reads a read pair as its lift over the cells both scored", () => {
    const pair = readPaired(pairedReading(0.12, [0.01, 0.23], { rateA: 0.5 }));
    expect(pair).toMatchObject({ read: true, cells: 10 });
  });

  const UNREAD = wordKeys(READING_STATE_INFO).filter((s) => s !== "read");

  it.each(UNREAD)("says why a %s pair was not read, in the served words", (state) => {
    const reading = { ...unreadOverlap().lead, state };
    const { label, sentence } = READING_STATE_INFO[state];
    expect(readPaired(reading)).toEqual({ read: false, label, sentence });
    expect(liftOf(reading)).toBeNull();
  });
});
