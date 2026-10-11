import { describe, expect, it } from "vitest";
import { EMPTY_ADDRESS, formatAddress, parseAddress, type Address } from "../address";

// Pinned literals: changing an address string breaks every link anyone saved.

const roundTrip = (a: Address): Address | null => parseAddress(formatAddress(a));

describe("formatAddress", () => {
  // `workspace.tsx` drops the hash on this equality.
  it("writes the empty address for following the default view", () => {
    expect(formatAddress({ kind: "follow", tab: "chat", cell: null })).toBe(EMPTY_ADDRESS);
    expect(EMPTY_ADDRESS).toBe("#/");
  });

  it("names a non-default view while following", () => {
    expect(formatAddress({ kind: "follow", tab: "dashboard", cell: null })).toBe("#/dashboard");
  });

  it("strips the cycle_ prefix every minter emits", () => {
    expect(
      formatAddress({
        kind: "cycle",
        path: [{ campaignId: "justlogic__cf67b3", cycleId: "cycle_ee7bb41bbde0" }],
        tab: "dashboard",
        candidateId: null, at: null, cell: null,
      }),
    ).toBe("#/c/justlogic__cf67b3/ee7bb41bbde0/dashboard");
  });

  it("omits the default view, so the common address stays short", () => {
    expect(
      formatAddress({
        kind: "cycle",
        path: [{ campaignId: "justlogic__cf67b3", cycleId: "cycle_ee7bb41bbde0" }],
        tab: "chat",
        candidateId: null, at: null, cell: null,
      }),
    ).toBe("#/c/justlogic__cf67b3/ee7bb41bbde0");
  });

  it("appends hops in pairs and the candidate last", () => {
    expect(
      formatAddress({
        kind: "cycle",
        path: [
          { campaignId: "pp-self__aa11bb", cycleId: "cycle_outer0000" },
          { campaignId: "justlogic__cc22dd", cycleId: "cycle_inner0000" },
        ],
        tab: "dashboard",
        candidateId: "sp_9f2", at: null, cell: null,
      }),
    ).toBe("#/c/pp-self__aa11bb/outer0000/justlogic__cc22dd/inner0000/dashboard/k/sp_9f2");
  });

  it("writes the moment after the candidate, and names the default view before it", () => {
    const path = [{ campaignId: "justlogic__cf67b3", cycleId: "cycle_ee7bb41bbde0" }];
    expect(
      formatAddress({ kind: "cycle", path, tab: "chat", candidateId: null, at: 512, cell: null }),
    ).toBe("#/c/justlogic__cf67b3/ee7bb41bbde0/chat/t/512");
    expect(
      formatAddress({
        kind: "cycle",
        path,
        tab: "dashboard",
        candidateId: "sp_9f2",
        at: 512,
        cell: { answer: "ab12" },
      }),
    ).toBe("#/c/justlogic__cf67b3/ee7bb41bbde0/dashboard/k/sp_9f2/t/512/x/ab12");
  });

  it("addresses an account pane", () => {
    expect(formatAddress({ kind: "account", pane: "activity" })).toBe("#/account/activity");
  });
});

describe("parseAddress round trip", () => {
  const cases: Array<[string, Address]> = [
    ["following, default view", { kind: "follow", tab: "chat", cell: null }],
    ["following, explicit view", { kind: "follow", tab: "files", cell: null }],
    [
      "pinned, default view",
      {
        kind: "cycle",
        path: [{ campaignId: "justlogic__cf67b3", cycleId: "cycle_ee7bb41bbde0" }],
        tab: "chat",
        candidateId: null, at: null, cell: null,
      },
    ],
    [
      "pinned, explicit view",
      {
        kind: "cycle",
        path: [{ campaignId: "justlogic__cf67b3", cycleId: "cycle_ee7bb41bbde0" }],
        tab: "compare",
        candidateId: null, at: null, cell: null,
      },
    ],
    [
      "pinned with a parked candidate",
      {
        kind: "cycle",
        path: [{ campaignId: "justlogic__cf67b3", cycleId: "cycle_ee7bb41bbde0" }],
        tab: "dashboard",
        candidateId: "sp_9f2a1c", at: null, cell: null,
      },
    ],
    [
      "pinned, a replayed moment",
      {
        kind: "cycle",
        path: [{ campaignId: "justlogic__cf67b3", cycleId: "cycle_ee7bb41bbde0" }],
        tab: "dashboard",
        candidateId: null, at: 4096, cell: null,
      },
    ],
    [
      "pinned, a candidate, a moment at offset zero and a cell",
      {
        kind: "cycle",
        path: [{ campaignId: "justlogic__cf67b3", cycleId: "cycle_ee7bb41bbde0" }],
        tab: "chat",
        candidateId: "sp_9f2a1c",
        at: 0,
        cell: { answer: "9f2a1c0b7d3e4f5a.ab12cd34ef56" },
      },
    ],
    [
      "an inner hop",
      {
        kind: "cycle",
        path: [
          { campaignId: "pp-self__aa11bb", cycleId: "cycle_outer0000" },
          { campaignId: "justlogic__cc22dd", cycleId: "cycle_inner0000" },
        ],
        tab: "compare",
        candidateId: null, at: null, cell: null,
      },
    ],
    [
      "a fork cycle id",
      {
        kind: "cycle",
        path: [{ campaignId: "justlogic__cf67b3", cycleId: "cycle_ee7bb41_fork_9a2f" }],
        tab: "dashboard",
        candidateId: null, at: null, cell: null,
      },
    ],
    [
      "a check-in cycle id",
      {
        kind: "cycle",
        path: [{ campaignId: "justlogic__cf67b3", cycleId: "cycle_chk_a1b2c3d4e5f6" }],
        tab: "chat",
        candidateId: null, at: null, cell: null,
      },
    ],
    [
      "following, a cell open on the default view",
      { kind: "follow", tab: "chat", cell: { answer: "9f2a1c0b7d3e4f5a.ab12cd34ef56" } },
    ],
    [
      "pinned, a parked candidate and a cell open on the default view",
      {
        kind: "cycle",
        path: [{ campaignId: "justlogic__cf67b3", cycleId: "cycle_ee7bb41bbde0" }],
        tab: "chat",
        candidateId: "sp_9f2a1c",
        at: null,
        cell: { answer: "9f2a1c0b7d3e4f5a.ab12cd34ef56" },
      },
    ],
    [
      "pinned, a cell open on an explicit view",
      {
        kind: "cycle",
        path: [{ campaignId: "justlogic__cf67b3", cycleId: "cycle_ee7bb41bbde0" }],
        tab: "measurements",
        candidateId: null,
        at: null,
        cell: { answer: "9f2a1c0b7d3e4f5a.ab12cd34ef56" },
      },
    ],
    ["an account pane", { kind: "account", pane: "storage" }],
  ];

  for (const [name, address] of cases) {
    it(name, () => expect(roundTrip(address)).toEqual(address));
  }
});

describe("parseAddress tolerates what a person types", () => {
  it("reads a bare hash as following", () => {
    expect(parseAddress("#")).toEqual({ kind: "follow", tab: "chat", cell: null });
    expect(parseAddress("")).toEqual({ kind: "follow", tab: "chat", cell: null });
    expect(parseAddress("#/")).toEqual({ kind: "follow", tab: "chat", cell: null });
  });

  it("defaults the account pane when none is named", () => {
    expect(parseAddress("#/account")).toEqual({ kind: "account", pane: "profile" });
  });

  it("restores the cycle_ prefix", () => {
    expect(parseAddress("#/c/a__b/deadbeef")).toEqual({
      kind: "cycle",
      path: [{ campaignId: "a__b", cycleId: "cycle_deadbeef" }],
      tab: "chat",
      candidateId: null, at: null, cell: null,
    });
  });
});

describe("parseAddress refuses what is not an address", () => {
  const bad = [
    "#/c",
    "#/c/a__b",
    "#/c/a__b/deadbeef/justlogic__cc22dd",
    "#/c/a__b/deadbeef/k",
    "#/c/a__b/deadbeef/dashboard/t",
    "#/c/a__b/deadbeef/dashboard/t/-4",
    "#/c/a__b/deadbeef/dashboard/t/12/k/sp_9f2", // the moment follows the candidate
    "#/dashboard/t/12", // a followed view has no moment
    "#/c/a__b/deadbeef/dashboard/leftover",
    "#/c/a__b/../dashboard",
    "#/c/a__b/dead beef",
    "#/account/billing",
    "#/account/activity/extra",
    "#/nosuchview",
    "#/dashboard/extra",
  ];
  for (const hash of bad) {
    it(`rejects ${hash || "(empty)"}`, () => expect(parseAddress(hash)).toBeNull());
  }
});
