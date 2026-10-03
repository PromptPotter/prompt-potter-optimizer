import { describe, expect, it } from "vitest";
import { createRegistry } from "@/lib/lineage-registry";
import type { CyclePath } from "@/lib/ids";

const PATH: CyclePath = [{ campaignId: "camp", cycleId: "cycle_root" }];

describe("createRegistry", () => {
  it("drops the body and the ETag exactly when the last subscriber leaves", () => {
    const dropped: string[] = [];
    const reg = createRegistry((k) => dropped.push(k));
    const un1 = reg.subscribe("k", PATH);
    const un2 = reg.subscribe("k", PATH);
    reg.setEtag("k", 'W/"x"');

    un1();
    expect(reg.spec("k")).not.toBeNull();
    expect(reg.etag("k")).toBe('W/"x"');
    expect(dropped).toEqual([]);

    un2();
    // `spec()` going null is the tick's mid-flight guard: a response landing after this
    // moment must not resurrect the key.
    expect(reg.spec("k")).toBeNull();
    expect(reg.etag("k")).toBe(null);
    expect(dropped).toEqual(["k"]);
  });

  it("latches the fetch spec from the subscriber that names it", () => {
    const reg = createRegistry(() => {});
    reg.subscribe("k", PATH, { lens: "score:accuracy", samples: [1, 2] });
    reg.subscribe("k", PATH);
    expect(reg.spec("k")).toEqual({
      path: PATH,
      opts: { lens: "score:accuracy", samples: [1, 2] },
    });
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

  it("retires a gone key from live() while its subscriber stays mounted", () => {
    const reg = createRegistry(() => {});
    reg.subscribe("k", PATH);
    reg.setEtag("k", 'W/"x"');

    reg.markGone("k");
    expect(reg.liveKeys()).toEqual([]);
    expect(reg.isGone("k")).toBe(true);
    expect(reg.spec("k")).not.toBeNull();
    expect(reg.etag("k")).toBe(null);
  });

  it("does not resurrect a gone key on a re-tick, but clears the mark on unsubscribe", () => {
    const reg = createRegistry(() => {});
    const un = reg.subscribe("k", PATH);
    reg.markGone("k");
    // A second subscriber must not un-kill it — the address is still gone.
    const un2 = reg.subscribe("k", PATH);
    expect(reg.liveKeys()).toEqual([]);

    un();
    un2();
    reg.subscribe("k", PATH);
    expect(reg.isGone("k")).toBe(false);
    expect(reg.liveKeys()).toEqual(["k"]);
  });

  it("ignores markGone for a key nobody subscribes", () => {
    const reg = createRegistry(() => {});
    reg.markGone("ghost");
    expect(reg.isGone("ghost")).toBe(false);
  });
});
