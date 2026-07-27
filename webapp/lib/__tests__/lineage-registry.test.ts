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

  it("retires a gone key from live() while its subscriber stays mounted", () => {
    // The failure this prevents: a sidebar row keeps naming a campaign that was
    // deleted, so the key stays subscribed and the tick re-asks a 404 every
    // interval, forever. Subscription and existence are different questions.
    const reg = createRegistry(() => {});
    reg.subscribe("k", PATH);
    reg.setEtag("k", 'W/"x"');

    reg.markGone("k");
    expect(reg.live()).toEqual([]);
    expect(reg.isGone("k")).toBe(true);
    // Still subscribed — the component did not unmount.
    expect(reg.has("k")).toBe(true);
    // The validator went with it; a body we will never fetch cannot leave an ETag
    // behind to 304 against.
    expect(reg.etag("k")).toBe(null);
  });

  it("does not resurrect a gone key on a re-tick, but clears the mark on unsubscribe", () => {
    const reg = createRegistry(() => {});
    const un = reg.subscribe("k", PATH);
    reg.markGone("k");
    // A second subscriber (another surface opening the same address) must not
    // un-kill it — the address is still gone.
    const un2 = reg.subscribe("k", PATH);
    expect(reg.live()).toEqual([]);

    // But once nobody holds it, the mark dies with the body: a later subscribe is a
    // fresh question, and a leaked mark would make it silently unfetchable.
    un();
    un2();
    reg.subscribe("k", PATH);
    expect(reg.isGone("k")).toBe(false);
    expect(reg.live()).toHaveLength(1);
  });

  it("ignores markGone for a key nobody subscribes", () => {
    const reg = createRegistry(() => {});
    reg.markGone("ghost");
    expect(reg.isGone("ghost")).toBe(false);
  });
});
