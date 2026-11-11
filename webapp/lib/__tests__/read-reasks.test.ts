import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cellsRead,
  couponRead,
  dashboardRead,
  evidenceRead,
  hierarchyRead,
  userSettingsRead,
} from "@/lib/api/reads";
import { RECONNECT_POLL_MS } from "@/lib/cadence";
import { clearReadCache, subscribe, type ReadDescriptor } from "@/lib/read-cache";
import { couponStatus, dash } from "@/lib/test-fixtures";

// The browser's `EventSource`, driven by the test: the server's frames, a drop, a refusal.
class FakeFeed {
  static CLOSED = 2;
  static opened: FakeFeed[] = [];
  readyState = 1;
  onmessage: ((ev: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(readonly url: string) {
    FakeFeed.opened.push(this);
  }
  close(): void {
    this.readyState = FakeFeed.CLOSED;
  }
  frame(subject: string, version: string): void {
    this.onmessage?.({ data: JSON.stringify({ subject, version }) } as MessageEvent<string>);
  }
}

const top = [{ campaignId: "c1", cycleId: "root" }];
const inner = [...top, { campaignId: "i1", cycleId: "x" }];
const otherInner = [{ campaignId: "c2", cycleId: "root" }, { campaignId: "i2", cycleId: "y" }];

const NAMES = ["hierarchy", "top", "inner", "other", "evidence", "settled", "cells", "coupon", "settings"] as const;
type Name = (typeof NAMES)[number];

function nameOf(url: string): Name {
  const path = new URL(url, "http://x").pathname;
  if (path.endsWith("/hierarchy")) return "hierarchy";
  if (path.endsWith("/dashboard")) {
    const descend = new URL(url, "http://x").searchParams.get("descend");
    return descend === null ? "top" : descend.startsWith("i1") ? "inner" : "other";
  }
  if (path.endsWith("/evidence")) return url.includes("live") ? "evidence" : "settled";
  if (path.includes("/cells")) return "cells";
  if (path.endsWith("/auth/coupon")) return "coupon";
  if (path.endsWith("/auth/user-settings")) return "settings";
  throw new Error(`unrouted: ${url}`);
}

describe("the feed re-asks exactly the reads whose subject moved, and no read asks on a clock", () => {
  let asked: Record<Name, number>;
  const mounted: (() => void)[] = [];
  const once = Object.fromEntries(NAMES.map((n) => [n, 1])) as Record<Name, number>;

  beforeEach(() => {
    FakeFeed.opened = [];
    vi.stubGlobal("EventSource", FakeFeed);
    clearReadCache(true);
    vi.useFakeTimers();
    asked = Object.fromEntries(NAMES.map((n) => [n, 0])) as Record<Name, number>;
    vi.stubGlobal("fetch", async (url: string) => {
      const name = nameOf(url);
      asked[name] += 1;
      if (name === "coupon") return Response.json(couponStatus());
      if (name === "top" || name === "inner" || name === "other") return Response.json(dash({}));
      return Response.json({ entries: [] });
    });
  });
  afterEach(() => {
    for (const unwatch of mounted.splice(0)) unwatch();
    clearReadCache(true);
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  const settle = () => vi.advanceTimersByTimeAsync(0);
  const feed = (): FakeFeed => {
    const last = FakeFeed.opened.at(-1);
    if (!last) throw new Error("no feed opened");
    return last;
  };
  const mount = async () => {
    const reads: ReadDescriptor<unknown>[] = [
      hierarchyRead(),
      dashboardRead(top, null),
      dashboardRead(inner, null),
      dashboardRead(otherInner, null),
      evidenceRead(["campaign:live"], {}, true),
      evidenceRead(["campaign:done"], {}, false),
      cellsRead("ds", top, "campaign", null, {}, null),
      couponRead(),
      userSettingsRead(),
    ].map((read) => read as unknown as ReadDescriptor<unknown>);
    for (const read of reads) mounted.push(subscribe(() => read, () => {}));
    await settle();
    // The hello: each subject at its version moves nothing, every read having asked on its mount.
    for (const subject of ["account", "store:", "store:c1::root", "store:c2::root", "machine"]) {
      feed().frame(subject, "v1");
    }
    await settle();
  };

  it("subscribes once, and a subject's first version re-asks nothing", async () => {
    await mount();
    expect(FakeFeed.opened).toHaveLength(1);
    expect(asked).toEqual(once);
  });

  it("re-asks every read of the account's store on its move, a settled evidence and the settings not", async () => {
    await mount();
    feed().frame("store:", "v2");
    await settle();
    expect(asked).toEqual({ ...once, hierarchy: 2, top: 2, evidence: 2, cells: 2, coupon: 2 });
  });

  it("re-asks an inner run's dashboard on its own sandbox's move alone", async () => {
    await mount();
    feed().frame("store:c1::root", "v2");
    await settle();
    expect(asked).toEqual({ ...once, hierarchy: 2, top: 2, inner: 2, evidence: 2, cells: 2, coupon: 2 });
  });

  it("re-asks the account's own reads on what it filed, and nothing a run moves", async () => {
    await mount();
    feed().frame("account", "v2");
    await settle();
    expect(asked).toEqual({ ...once, settings: 2 });
  });

  it("is a hint, never the body: a repeated or replayed version re-asks nothing", async () => {
    await mount();
    feed().frame("store:", "v1");
    feed().frame("account", "v1");
    await settle();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(asked).toEqual(once);
  });

  it("reopens a refused feed on the reconnect beat, and its hello moves only what moved meanwhile", async () => {
    await mount();
    const refused = feed();
    refused.close();
    refused.onerror?.();
    await vi.advanceTimersByTimeAsync(RECONNECT_POLL_MS);
    expect(FakeFeed.opened).toHaveLength(2);
    feed().frame("account", "v1");
    feed().frame("store:", "v3");
    await settle();
    expect(asked).toEqual({ ...once, hierarchy: 2, top: 2, evidence: 2, cells: 2, coupon: 2 });
  });
});
