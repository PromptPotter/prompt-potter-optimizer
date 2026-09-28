import { describe, expect, it } from "vitest";
import { compareItems, sharedComparableNote } from "../compare-items";
import type { Evidence, HeadToHeadRow, SubjectReading } from "@/lib/api/types";

// One list serves every subject kind: a campaign item's bench numbers are its served head-to-head
// row, a searchpoint carries none, and an unread channel keeps its card with no reading and no ink.
function reading(key: string, kind: SubjectReading["kind"]): SubjectReading {
  return { key, kind } as SubjectReading;
}

function row(subject: string, optimizer: string): HeadToHeadRow {
  return { subject, optimizer } as HeadToHeadRow;
}

describe("compareItems", () => {
  it("lists campaign items off the served head-to-head rows, in the channels' order", () => {
    const evidence = {
      subjects: [
        reading("campaign:potter", "campaign"),
        reading("course:capo/c1", "course"),
        reading("candidate:potter/c0/k", "candidate"),
      ],
      head_to_head: { rows: [row("campaign:potter", "potter"), row("course:capo/c1", "capo")] },
    } as unknown as Evidence;
    const items = compareItems(evidence, [
      { rootCampaignId: "capo", subject: "course:capo/c1" },
      { rootCampaignId: "potter", subject: "candidate:potter/c0/k" },
      { rootCampaignId: "gone", subject: "campaign:gone" },
      { rootCampaignId: "potter", subject: "campaign:potter" },
    ]);
    expect(items.map((i) => [i.slot, i.headline?.optimizer ?? null])).toEqual([
      [1, "capo"],
      [2, null],
      [null, null],
      [0, "potter"],
    ]);
    expect(items[2]?.reading).toBeNull();
  });

  it("carries no headline where the read served no head-to-head", () => {
    const evidence = {
      subjects: [reading("campaign:potter", "campaign")],
      head_to_head: null,
    } as unknown as Evidence;
    const [item] = compareItems(evidence, [{ rootCampaignId: "potter", subject: "campaign:potter" }]);
    expect(item?.headline).toBeNull();
    expect(item?.slot).toBe(0);
  });
});

describe("sharedComparableNote", () => {
  const off = (key: string, note: string) =>
    ({ key, kind: "campaign", comparable: false, comparable_note: note }) as SubjectReading;
  const items = (...readings: (SubjectReading | null)[]) =>
    readings.map((reading) => ({
      channel: { rootCampaignId: "", subject: "" },
      reading,
      slot: null,
      headline: null,
    }));

  it("lifts a note every read item shares, skipping unread ones", () => {
    expect(sharedComparableNote(items(off("a", "n"), null, off("b", "n")))).toBe("n");
  });

  it("stays on the items when one reads on the shared ruler or the notes differ", () => {
    const on = { key: "c", kind: "campaign", comparable: true, comparable_note: "" };
    expect(sharedComparableNote(items(off("a", "n"), on as SubjectReading))).toBeNull();
    expect(sharedComparableNote(items(off("a", "n"), off("b", "m")))).toBeNull();
  });
});
