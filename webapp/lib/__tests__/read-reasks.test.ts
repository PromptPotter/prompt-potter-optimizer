import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cellsRead,
  couponRead,
  dashboardRead,
  evidenceRead,
  hierarchyRead,
  onViewedPhaseChange,
} from "@/lib/api/reads";
import type { HierarchyResponse, RunPhase, UnstartedCampaign } from "@/lib/api/types";
import { DASHBOARD_LIVE_POLL_MS, RECONNECT_POLL_MS, REGISTRY_POLL_MS } from "@/lib/cadence";
import { viewedIntervalMs } from "@/lib/poll";
import { clearReadCache, invalidateReads, subscribe, type ReadDescriptor } from "@/lib/read-cache";
import {
  couponStatus,
  dash,
  hierarchyState,
  lifetimeSpend,
  subtreeCampaign,
  subtreeCourse,
} from "@/lib/test-fixtures";

const viewed = [{ campaignId: "c1", cycleId: "root" }];
const another = [{ campaignId: "c2", cycleId: "root" }];

// The viewed root beside a sibling fork, another campaign's course, and an open check-in.
function listing({
  rootTokens = 100,
  rootPhase = "terminal",
  forkTokens = 100,
  otherTokens = 100,
  checkinUsd = 0,
}: { rootTokens?: number; rootPhase?: RunPhase; forkTokens?: number; otherTokens?: number; checkinUsd?: number } = {}): HierarchyResponse {
  const course = (address: string, tokens: number, mint: "root" | "fork" = "root") =>
    subtreeCourse({ address }, { mint, spend: { ...subtreeCourse({ address }).facts.spend, metered_tokens: tokens } });
  const root = {
    ...course("c1::root", rootTokens),
    state: hierarchyState({ status: { run_phase: rootPhase, stop_reason: null, mark: "unknown" } }),
  };
  const checkin: UnstartedCampaign = {
    address: "d1",
    parent: null,
    label: "d1",
    tags: [],
    figures: [],
    lines: [],
    level: "unstarted",
    span: { opened_at: "", last_at: "" },
    facts: { spend_lifetime: lifetimeSpend({ billed_usd: checkinUsd }) },
  };
  return {
    entries: [
      checkin,
      subtreeCampaign({ address: "c1", children: [root, course("c1::fork", forkTokens, "fork")] }),
      subtreeCampaign({ address: "c2", children: [course("c2::root", otherTokens)] }),
    ],
  };
}

const SETTLED = dash({ run_phase: "terminal" });

const NAMES = ["hierarchy", "dashboard", "evidence", "cells", "coupon"] as const;
type Name = (typeof NAMES)[number];

describe("a read re-asks on its caller's interval, and on a change the body it reads off shows", () => {
  let served = listing();
  let board = SETTLED;
  let anotherBoard = SETTLED;
  let asked: Record<Name, number>;
  const mounted: (() => void)[] = [];

  beforeEach(() => {
    clearReadCache(true);
    vi.useFakeTimers();
    vi.setSystemTime(10_000);
    served = listing();
    board = SETTLED;
    anotherBoard = SETTLED;
    asked = { hierarchy: 0, dashboard: 0, evidence: 0, cells: 0, coupon: 0 };
    vi.stubGlobal("fetch", async (url: string) => {
      const path = new URL(url, "http://x").pathname;
      const name = NAMES.find((n) => path.includes(n === "coupon" ? "/auth/coupon" : `/${n}`));
      if (name === undefined) throw new Error(`unrouted: ${url}`);
      asked[name] += 1;
      if (name === "hierarchy") return Response.json(served);
      if (name === "dashboard") return Response.json(path.includes("/c2/") ? anotherBoard : board);
      if (name === "coupon") return Response.json(couponStatus());
      return Response.json({});
    });
  });
  afterEach(() => {
    for (const unwatch of mounted.splice(0)) unwatch();
    clearReadCache(true);
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  // The listing, the viewed cycle's dashboard at `dashboardMs` as `useDashboard` reads it, and
  // three reads asked once.
  const mount = async (dashboardMs: number | null = null) => {
    const reads: ReadDescriptor<unknown>[] = [
      hierarchyRead(),
      { ...dashboardRead(viewed, null, dashboardMs), onChange: onViewedPhaseChange },
      evidenceRead(["campaign:c1"], {}, null),
      cellsRead("ds", viewed, "campaign", null, {}, null, null),
      couponRead(),
    ].map((read) => read as unknown as ReadDescriptor<unknown>);
    for (const read of reads) mounted.push(subscribe(() => read, () => {}));
    await settle();
  };
  const settle = () => vi.advanceTimersByTimeAsync(0);
  const beat = () => vi.advanceTimersByTimeAsync(REGISTRY_POLL_MS);
  const once = { hierarchy: 1, dashboard: 1, evidence: 1, cells: 1, coupon: 1 };

  it("re-asks the account's money once on a moved bill, an open check-in's included", async () => {
    await mount();
    expect(asked).toEqual(once);
    served = listing({ checkinUsd: 0.25 });
    await beat();
    await beat();
    expect(asked).toEqual({ ...once, hierarchy: 3, coupon: 2 });
  });

  it("re-asks nothing on the listing's tokens, rounds or phase", async () => {
    await mount();
    served = listing({ rootTokens: 250, forkTokens: 250, otherTokens: 250, rootPhase: "running" });
    await beat();
    expect(asked).toEqual({ ...once, hierarchy: 2 });
  });

  it("re-asks every mounted read on a dashboard's phase change", async () => {
    await mount();
    board = dash({ run_phase: "running" });
    invalidateReads("dashboard");
    await settle();
    await settle();
    expect(asked).toEqual({ hierarchy: 2, dashboard: 3, evidence: 2, cells: 2, coupon: 2 });
  });

  it("re-asks nothing on another cycle's phase change", async () => {
    await mount();
    const read = dashboardRead(another, null, null) as unknown as ReadDescriptor<unknown>;
    mounted.push(subscribe(() => read, () => {}));
    await settle();
    anotherBoard = dash({ run_phase: "running" });
    invalidateReads("dashboard");
    await settle();
    await settle();
    expect(asked).toEqual({ ...once, dashboard: 4 });
  });

  it("beats the viewed cycle slow on a replayed moment or a lost read", () => {
    expect([viewedIntervalMs(null, false), viewedIntervalMs(7, false), viewedIntervalMs(null, true)]).toEqual([
      DASHBOARD_LIVE_POLL_MS,
      RECONNECT_POLL_MS,
      RECONNECT_POLL_MS,
    ]);
  });

  it("polls a settled dashboard at the interval its caller names", async () => {
    await mount(DASHBOARD_LIVE_POLL_MS);
    await vi.advanceTimersByTimeAsync(DASHBOARD_LIVE_POLL_MS);
    expect(asked).toEqual({ ...once, dashboard: 2 });
  });
});
