import { describe, expect, it } from "vitest";
import {
  DEFAULT_VIEWS,
  EMPTY_ADDRESS,
  NO_KEYS,
  formatAddress,
  parseAddress,
  type Address,
  type RefusedAddress,
  type View,
} from "../address";

// Pinned literals: changing an address string breaks every link anyone saved.

const roundTrip = (a: Address): Address | RefusedAddress => parseAddress(formatAddress(a));
const MALFORMED: RefusedAddress = { refused: "malformed" };
const PATH = [{ campaignId: "justlogic__cf67b3", cycleId: "cycle_ee7bb41bbde0" }];
const INNER = [
  { campaignId: "pp-self__aa11bb", cycleId: "cycle_outer0000" },
  { campaignId: "justlogic__cc22dd", cycleId: "cycle_inner0000" },
];
const view = (family: View["family"], key: string | null = null): View => ({ family, key });
const at = (subject: Address["subject"], views: readonly View[] = DEFAULT_VIEWS, keys = NO_KEYS): Address => ({
  subject,
  views,
  keys,
});

describe("formatAddress", () => {
  // `workspace.tsx` drops the hash on this equality.
  it("writes the empty address for following the default view", () => {
    expect(formatAddress(at(null))).toBe(EMPTY_ADDRESS);
    expect(EMPTY_ADDRESS).toBe("#/");
  });

  it("addresses a check-in by its draft id, and a fresh one as new", () => {
    expect(formatAddress(at({ draftId: "d_6553261b89ac48e0" }))).toBe("#/d/d_6553261b89ac48e0");
    expect(formatAddress(at({ draftId: null }))).toBe("#/new");
    expect(parseAddress("#/d")).toEqual(MALFORMED);
  });

  it("names the open views in the order opened while following", () => {
    expect(formatAddress(at(null, [view("samples"), view("spend")]))).toBe("#/samples/spend");
  });

  it("spells a course as the served hierarchy address, and omits the default view", () => {
    expect(formatAddress(at({ path: PATH }))).toBe("#/justlogic__cf67b3::cycle_ee7bb41bbde0");
  });

  it("writes the subject's hops and pick, the views, then the moment and the view keys", () => {
    expect(
      formatAddress(
        at({ path: INNER, pick: { round: 3, arm: "sp_9f2" }, at: 512 }, [view("chat"), view("samples", "ab12")], {
          round: 2,
          samples: [1, 4],
          observe: "best",
        }),
      ),
    ).toBe(
      "#/pp-self__aa11bb::cycle_outer0000~justlogic__cc22dd::cycle_inner0000/r3/sp_9f2/chat/samples=ab12" +
        ";at=512;round=2;samples=1,4;observe=best",
    );
  });

  it("keeps the view keys while following", () => {
    expect(formatAddress(at(null, DEFAULT_VIEWS, { ...NO_KEYS, samples: [] }))).toBe("#/;samples=");
  });
});

describe("parseAddress round trip", () => {
  const cases: Array<[string, Address]> = [
    ["following, default view", at(null)],
    ["following, a keyed view", at(null, [view("account", "storage")])],
    ["following, a round and an open picker", at(null, DEFAULT_VIEWS, { ...NO_KEYS, round: 4, samples: [] })],
    ["a check-in", at({ draftId: "d_6553261b89ac48e0" })],
    ["a fresh check-in", at({ draftId: null })],
    [
      "pinned, a candidate, a moment at offset zero and a cell",
      at({ path: PATH, pick: { round: 3, arm: "sp_9f2a1c" }, at: 0 }, [
        view("samples", "9f2a1c0b7d3e4f5a.ab12cd34ef56"),
        view("files"),
      ]),
    ],
    ["an inner hop and two views", at({ path: INNER, pick: null, at: null }, [view("compare"), view("chat")])],
    [
      "a fork cycle id and its view keys",
      at(
        { path: [{ campaignId: "justlogic__cf67b3", cycleId: "cycle_ee7bb41_fork_9a2f" }], pick: null, at: null },
        [view("timeline"), view("leader")],
        { round: null, samples: [2, 7], observe: "latest" },
      ),
    ],
    ["a check-in's node", at({ draftId: "d_6553261b89ac48e0" }, [view("chat"), view("node", "target.llm_only")])],
  ];

  for (const [name, address] of cases) {
    it(name, () => expect(roundTrip(address)).toEqual(address));
  }
});

describe("parseAddress tolerates what a person types", () => {
  it("reads a bare hash as following the default view", () => {
    for (const hash of ["#", "", "#/"]) {
      expect(parseAddress(hash)).toEqual(at(null));
    }
  });
});

describe("parseAddress refuses what is not an address", () => {
  const bad = [
    "#/a__b",
    "#/a__b::cycle_x/r3", // a pick names its arm; a round alone is `;round=`
    "#/a__b::cycle_x/sp_9f2",
    "#/a__b::cycle_x/r3/sp_9f2/leftover",
    "#/samples/a__b::cycle_x", // the subject leads
    "#/a__b::cycle_x;at=-4",
    "#/;at=12", // a followed view has no moment
    "#/;round=1;round=2",
    "#/;samples=1,,2",
    "#/;observe=nowhere",
    "#/;nosuchkey=1",
    "#/a__b::..",
    "#/a__b::dead beef",
    "#/samples/samples", // one family never shows twice
    "#/samples=a=b",
    "#/samples=..",
    "#/nosuchview",
    "#/node=llm_only", // a node is scoped
    "#/node=judge.llm_only",
    "#/new/d",
  ];
  for (const hash of bad) {
    it(`rejects ${hash || "(empty)"}`, () => expect(parseAddress(hash)).toEqual(MALFORMED));
  }

  // A link copied before the hierarchy grammar names its run another way: refused as retired, so
  // the workspace says so instead of opening the followed run in its place.
  for (const hash of ["#/c/justlogic__cf67b3/ee7bb41bbde0", "#/c/justlogic__cf67b3/ee7bb41bbde0/k/r3/sp_9f2/t/512"]) {
    it(`refuses ${hash} as an earlier version's link`, () => expect(parseAddress(hash)).toEqual({ refused: "retired" }));
  }
});
