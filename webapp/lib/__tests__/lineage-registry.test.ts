import { describe, expect, it } from "vitest";
import { createRegistry } from "@/lib/lineage-registry";
import type { CyclePath } from "@/lib/ids";

// The ref-count registry behind `lib/lineage.tsx`. Its failure modes are silent: an ETag
// that outlives its dropped body replays a validator for a tree we no longer hold, and the
// next 304 confirms an entry that renders nothing — no error, just an empty forest.

const PATH: CyclePath = [{ campaignId: "camp", cycleId: "cycle_root" }];

describe("createRegistry", () => {
  it("drops the body and the ETag exactly when the last subscriber leaves", () => {
    const dropped: string[] = [];
    const reg = createRegistry((k) => dropped.push(k));
    const un1 = reg.subscribe("k", PATH);
    const un2 = reg.subscribe("k", PATH);
    reg.setEtag("k", 'W/"x"');

    un1();
    expect(reg.has("k")).toBe(true);
    expect(reg.etag("k")).toBe('W/"x"');
    expect(dropped).toEqual([]);

    un2();
    // `has()` flipping false is the tick's mid-flight guard: a response landing after this
    // moment must not resurrect the key.
    expect(reg.has("k")).toBe(false);
    expect(reg.etag("k")).toBe(null);
    expect(dropped).toEqual(["k"]);
  });

  it("latches the fetch spec from the subscriber that names it", () => {
    // The provider supplies the mask; an address-only subscriber of the same key must not
    // erase it — the key encodes the mask, so all subscribers of one key name one fetch.
    const reg = createRegistry(() => {});
    reg.subscribe("k", PATH, { lens: "score:accuracy", samples: [1, 2] });
    reg.subscribe("k", PATH);
    expect(reg.live()).toEqual([
      ["k", { path: PATH, opts: { lens: "score:accuracy", samples: [1, 2] } }],
    ]);
  });

  it("bumps the version on every membership change so the poll revalidates", () => {
    const reg = createRegistry(() => {});
    const v0 = reg.version();
    const un = reg.subscribe("k", PATH);
    expect(reg.version()).toBeGreaterThan(v0);
    const v1 = reg.version();
    un();
    expect(reg.version()).toBeGreaterThan(v1);
  });
});
