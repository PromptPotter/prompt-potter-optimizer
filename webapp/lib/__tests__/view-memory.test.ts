import { describe, expect, it } from "vitest";
import { emptyView, pruneStore, viewMemoryCodec, type CampaignView } from "@/lib/view-memory";

const DAY = 24 * 60 * 60 * 1000;
const NOW = 1_800_000_000_000;

const view = (over: Partial<CampaignView> = {}): CampaignView => ({
  ...emptyView(),
  at: NOW,
  ...over,
});

describe("pruneStore", () => {
  it("keeps a record from within the window", () => {
    const store = { a: view({ at: NOW - 13 * DAY }) };
    expect(Object.keys(pruneStore(store, NOW))).toEqual(["a"]);
  });

  it("drops a record past the 14-day window", () => {
    const store = { a: view({ at: NOW - 15 * DAY }) };
    expect(pruneStore(store, NOW)).toEqual({});
  });

  it("drops a record written by an older version rather than migrating it", () => {
    const store = { a: view({ v: 0 }) };
    expect(pruneStore(store, NOW)).toEqual({});
  });

  it("evicts least-recently-viewed first once over the cap", () => {
    const store: Record<string, CampaignView> = {};
    // 30 campaigns, oldest first — `c0` is the least recent.
    for (let i = 0; i < 30; i++) store[`c${i}`] = view({ at: NOW - (30 - i) * 1000 });
    const kept = Object.keys(pruneStore(store, NOW));
    expect(kept).toHaveLength(24);
    expect(kept).not.toContain("c0");
    expect(kept).toContain("c29");
  });

  it("survives a malformed entry instead of throwing the whole store away", () => {
    const store = { a: view(), bad: null } as unknown as Record<string, CampaignView>;
    expect(Object.keys(pruneStore(store, NOW))).toEqual(["a"]);
  });
});

describe("viewMemoryCodec", () => {
  it("round-trips a record", () => {
    const store = { a: view({ toggled: ["course:x::y"], expandedLanes: ["x::y|cand"] }) };
    const back = viewMemoryCodec.deserialize(viewMemoryCodec.serialize(store));
    expect(back.a?.toggled).toEqual(["course:x::y"]);
    expect(back.a?.expandedLanes).toEqual(["x::y|cand"]);
  });

  it("returns an empty store for a non-object blob rather than crashing the app", () => {
    // Storage is operator-writable: a hand-edited value degrades to defaults, never an error.
    expect(viewMemoryCodec.deserialize("[]")).toEqual({});
    expect(viewMemoryCodec.deserialize("null")).toEqual({});
    expect(viewMemoryCodec.deserialize('"nope"')).toEqual({});
  });

  it("stores no measurement — every persisted field is an id, a flag, or a UI key", () => {
    // A restored view never renders a number read as current: `ScoringInspector` renders
    // `is_winner`, so only the navigation axis is remembered.
    const keys = Object.keys(emptyView()).sort();
    expect(keys).toEqual(
      [
        "at",
        "autoExpandedFor",
        "expandedLanes",
        "showForest",
        "toggled",
        "v",
        "viewedCandidateId",
        "viewedPath",
      ].sort(),
    );
  });
});
