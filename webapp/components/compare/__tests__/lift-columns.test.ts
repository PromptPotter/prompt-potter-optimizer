import { describe, expect, it } from "vitest";
import { pairedReading, unreadOverlap } from "@/lib/test-fixtures";
import { pairedCells, withReadRank } from "../lift-columns";

describe("withReadRank", () => {
  it("ranks the read pairs in served order and skips an unread one", () => {
    const read = pairedReading(0.12, [0.01, 0.23]);
    const unread = unreadOverlap().lead;
    const rows = [read, unread, read].map((reading) => pairedCells(reading));
    expect(withReadRank(rows).map((r) => r.rank)).toEqual([1, null, 2]);
  });
});
