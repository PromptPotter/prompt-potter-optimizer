import { describe, expect, it } from "vitest";
import { emptyView, pruneStore, viewMemoryCodec, type CampaignView } from "@/lib/view-memory";

// The pruning rules are the whole contract of view memory: what survives a reload, what
// quietly expires, and what a bumped version drops. They live in the codec so every read
// and every write applies them — a caller cannot forget, and there is no separate sweep to
// fall out of sync with.

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
    // "Recently" has to mean something: a 15-day-old expansion set describes a tree that
    // has since grown forks and inner runs, so restoring it misleads.
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
    // Storage is operator-writable and survives across versions; a hand-edited or
    // half-written value must degrade to defaults, never to a render error.
    expect(viewMemoryCodec.deserialize("[]")).toEqual({});
    expect(viewMemoryCodec.deserialize("null")).toEqual({});
    expect(viewMemoryCodec.deserialize('"nope"')).toEqual({});
  });

  it("stores no measurement — every persisted field is an id, a flag, or a UI key", () => {
    // The rule this file exists to hold: a restored view may never render a number the
    // operator reads as a current result. `ScoringInspector` renders `is_winner`, so the
    // inspected candidate is deliberately NOT here — only the navigation axis is.
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
