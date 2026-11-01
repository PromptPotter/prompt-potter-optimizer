import { describe, expect, it } from "vitest";
import {
  DEFAULT_VIEWS,
  EMPTY_ADDRESS,
  formatAddress,
  parseAddress,
  type Address,
  type View,
} from "../address";

// Pinned literals: changing an address string breaks every link anyone saved.

const roundTrip = (a: Address): Address | null => parseAddress(formatAddress(a));
const PATH = [{ campaignId: "justlogic__cf67b3", cycleId: "cycle_ee7bb41bbde0" }];
const view = (family: View["family"], key: string | null = null): View => ({ family, key });

describe("formatAddress", () => {
  // `workspace.tsx` drops the hash on this equality.
  it("writes the empty address for following the default view", () => {
    expect(formatAddress({ subject: null, views: DEFAULT_VIEWS })).toBe(EMPTY_ADDRESS);
    expect(EMPTY_ADDRESS).toBe("#/");
  });

  it("addresses a check-in by its draft id, and a fresh one as new", () => {
    expect(formatAddress({ subject: { draftId: "d_6553261b89ac48e0" }, views: DEFAULT_VIEWS })).toBe(
      "#/d/d_6553261b89ac48e0",
    );
    expect(formatAddress({ subject: { draftId: null }, views: DEFAULT_VIEWS })).toBe("#/new");
    expect(parseAddress("#/d")).toBeNull();
  });

  it("names the open views in the order opened while following", () => {
    expect(formatAddress({ subject: null, views: [view("samples"), view("spend")] })).toBe(
      "#/samples/spend",
    );
  });

  it("strips the cycle_ prefix every minter emits, and omits the default view", () => {
    expect(formatAddress({ subject: { path: PATH }, views: DEFAULT_VIEWS })).toBe(
      "#/c/justlogic__cf67b3/ee7bb41bbde0",
    );
  });

  it("appends hops in pairs, then the candidate, the moment and the views", () => {
    expect(
      formatAddress({
        subject: {
          path: [
            { campaignId: "pp-self__aa11bb", cycleId: "cycle_outer0000" },
            { campaignId: "justlogic__cc22dd", cycleId: "cycle_inner0000" },
          ],
          candidateId: "sp_9f2",
          at: 512,
        },
        views: [view("chat"), view("samples", "ab12")],
      }),
    ).toBe("#/c/pp-self__aa11bb/outer0000/justlogic__cc22dd/inner0000/k/sp_9f2/t/512/chat/samples=ab12");
  });
});

describe("parseAddress round trip", () => {
  const cases: Array<[string, Address]> = [
    ["following, default view", { subject: null, views: DEFAULT_VIEWS }],
    ["following, a keyed view", { subject: null, views: [view("account", "storage")] }],
    ["a check-in", { subject: { draftId: "d_6553261b89ac48e0" }, views: DEFAULT_VIEWS }],
    ["a fresh check-in", { subject: { draftId: null }, views: DEFAULT_VIEWS }],
    [
      "pinned, a candidate, a moment at offset zero and a cell",
      {
        subject: { path: PATH, candidateId: "sp_9f2a1c", at: 0 },
        views: [view("samples", "9f2a1c0b7d3e4f5a.ab12cd34ef56"), view("files")],
      },
    ],
    [
      "an inner hop and two views",
      {
        subject: {
          path: [
            { campaignId: "pp-self__aa11bb", cycleId: "cycle_outer0000" },
            { campaignId: "justlogic__cc22dd", cycleId: "cycle_inner0000" },
          ],
          candidateId: null,
          at: null,
        },
        views: [view("compare"), view("chat")],
      },
    ],
    [
      "a fork cycle id",
      {
        subject: {
          path: [{ campaignId: "justlogic__cf67b3", cycleId: "cycle_ee7bb41_fork_9a2f" }],
          candidateId: null,
          at: null,
        },
        views: [view("timeline"), view("leader")],
      },
    ],
    ["a check-in's node", { subject: { draftId: "d_6553261b89ac48e0" }, views: [view("chat"), view("node", "target.llm_only")] }],
  ];

  for (const [name, address] of cases) {
    it(name, () => expect(roundTrip(address)).toEqual(address));
  }
});

describe("parseAddress tolerates what a person types", () => {
  it("reads a bare hash as following the default view", () => {
    for (const hash of ["#", "", "#/"]) {
      expect(parseAddress(hash)).toEqual({ subject: null, views: DEFAULT_VIEWS });
    }
  });

  it("restores the cycle_ prefix", () => {
    expect(parseAddress("#/c/a__b/deadbeef")).toEqual({
      subject: { path: [{ campaignId: "a__b", cycleId: "cycle_deadbeef" }], candidateId: null, at: null },
      views: DEFAULT_VIEWS,
    });
  });
});

describe("parseAddress refuses what is not an address", () => {
  const bad = [
    "#/c",
    "#/c/a__b",
    "#/c/a__b/deadbeef/justlogic__cc22dd",
    "#/c/a__b/deadbeef/k",
    "#/c/a__b/deadbeef/t",
    "#/c/a__b/deadbeef/t/-4",
    "#/c/a__b/deadbeef/t/12/k/sp_9f2", // the moment follows the candidate
    "#/t/12", // a followed view has no moment
    "#/c/a__b/deadbeef/samples/leftover",
    "#/c/a__b/../samples",
    "#/c/a__b/dead beef",
    "#/samples/samples", // one family never shows twice
    "#/samples=a=b",
    "#/samples=..",
    "#/nosuchview",
    "#/node=llm_only", // a node is scoped
    "#/node=judge.llm_only",
    "#/new/d",
  ];
  for (const hash of bad) {
    it(`rejects ${hash || "(empty)"}`, () => expect(parseAddress(hash)).toBeNull());
  }
});
