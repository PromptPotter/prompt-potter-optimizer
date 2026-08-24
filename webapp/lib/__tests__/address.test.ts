import { describe, expect, it } from "vitest";
import { EMPTY_ADDRESS, formatAddress, parseAddress, type Address } from "../address";

// The address is the one thing a person copies out of this app, and the one thing a
// reload has to reconstruct exactly. Both directions are pinned here: what an address
// LOOKS like (a change to those strings breaks every link anyone saved) and that every
// arm survives the round trip.

const roundTrip = (a: Address): Address | null => parseAddress(formatAddress(a));

describe("formatAddress", () => {
  // The writer in `workspace.tsx` compares against EMPTY_ADDRESS to decide whether to
  // drop the hash entirely, so this equality is load-bearing, not decorative.
  it("writes the empty address for following the default view", () => {
    expect(formatAddress({ kind: "follow", tab: "chat" })).toBe(EMPTY_ADDRESS);
    expect(EMPTY_ADDRESS).toBe("#/");
  });

  it("names a non-default view while following", () => {
    expect(formatAddress({ kind: "follow", tab: "dashboard" })).toBe("#/dashboard");
  });

  it("strips the cycle_ prefix every minter emits", () => {
    expect(
      formatAddress({
        kind: "cycle",
        path: [{ campaignId: "justlogic__cf67b3", cycleId: "cycle_ee7bb41bbde0" }],
        tab: "dashboard",
        candidateId: null,
      }),
    ).toBe("#/c/justlogic__cf67b3/ee7bb41bbde0/dashboard");
  });

  it("omits the default view, so the common address stays short", () => {
    expect(
      formatAddress({
        kind: "cycle",
        path: [{ campaignId: "justlogic__cf67b3", cycleId: "cycle_ee7bb41bbde0" }],
        tab: "chat",
        candidateId: null,
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
        candidateId: "sp_9f2",
      }),
    ).toBe("#/c/pp-self__aa11bb/outer0000/justlogic__cc22dd/inner0000/dashboard/k/sp_9f2");
  });

  it("addresses an account pane", () => {
    expect(formatAddress({ kind: "account", pane: "activity" })).toBe("#/account/activity");
  });
});

describe("parseAddress round trip", () => {
  const cases: Array<[string, Address]> = [
    ["following, default view", { kind: "follow", tab: "chat" }],
    ["following, explicit view", { kind: "follow", tab: "files" }],
    [
      "pinned, default view",
      {
        kind: "cycle",
        path: [{ campaignId: "justlogic__cf67b3", cycleId: "cycle_ee7bb41bbde0" }],
        tab: "chat",
        candidateId: null,
      },
    ],
    [
      "pinned, explicit view",
      {
        kind: "cycle",
        path: [{ campaignId: "justlogic__cf67b3", cycleId: "cycle_ee7bb41bbde0" }],
        tab: "compare",
        candidateId: null,
      },
    ],
    [
      "pinned with a parked candidate",
      {
        kind: "cycle",
        path: [{ campaignId: "justlogic__cf67b3", cycleId: "cycle_ee7bb41bbde0" }],
        tab: "dashboard",
        candidateId: "sp_9f2a1c",
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
        tab: "verify",
        candidateId: null,
      },
    ],
    [
      // The sibling separators are suffixes ON TOP of the prefix, so the strip has to
      // leave them intact — a fork that came back as its parent would re-root the whole
      // dashboard onto the wrong cycle, silently.
      "a fork cycle id",
      {
        kind: "cycle",
        path: [{ campaignId: "justlogic__cf67b3", cycleId: "cycle_ee7bb41_fork_9a2f" }],
        tab: "dashboard",
        candidateId: null,
      },
    ],
    [
      "a check-in cycle id",
      {
        kind: "cycle",
        path: [{ campaignId: "justlogic__cf67b3", cycleId: "cycle_chk_a1b2c3d4e5f6" }],
        tab: "chat",
        candidateId: null,
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
    expect(parseAddress("#")).toEqual({ kind: "follow", tab: "chat" });
    expect(parseAddress("")).toEqual({ kind: "follow", tab: "chat" });
    expect(parseAddress("#/")).toEqual({ kind: "follow", tab: "chat" });
  });

  it("defaults the account pane when none is named", () => {
    expect(parseAddress("#/account")).toEqual({ kind: "account", pane: "profile" });
  });

  it("restores the cycle_ prefix", () => {
    expect(parseAddress("#/c/a__b/deadbeef")).toEqual({
      kind: "cycle",
      path: [{ campaignId: "a__b", cycleId: "cycle_deadbeef" }],
      tab: "chat",
      candidateId: null,
    });
  });
});

describe("parseAddress refuses what is not an address", () => {
  // Null, never a throw and never a half-address: the caller keeps the view it had.
  const bad = [
    "#/c", // named a cycle and gave none
    "#/c/a__b", // a hop missing its cycle
    "#/c/a__b/deadbeef/justlogic__cc22dd", // a second hop missing its cycle
    "#/c/a__b/deadbeef/k", // `k` with no candidate
    "#/c/a__b/deadbeef/dashboard/leftover", // trailing junk
    "#/c/a__b/../dashboard", // traversal segment
    "#/c/a__b/dead beef", // not a valid id component
    "#/account/billing", // not a pane we have
    "#/account/activity/extra",
    "#/nosuchview",
    "#/dashboard/extra",
  ];
  for (const hash of bad) {
    it(`rejects ${hash || "(empty)"}`, () => expect(parseAddress(hash)).toBeNull());
  }
});
