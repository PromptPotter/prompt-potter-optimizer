import { describe, expect, it } from "vitest";
import { costSeries, roundCosts } from "../spend";
import type { KindSpend, MeteredSpend } from "@/lib/api/types";
import type { DashboardSnapshot } from "@/lib/poll";

function kind(billed: number): KindSpend {
  return {
    counted: true,
    metered_usd: billed,
    billed_usd: billed,
    incurred_usd: billed,
    tokens: 0,
    cache_share: null,
    cache_write_tokens: 0,
    rate_known: true,
  };
}

function metered(kinds: Record<string, KindSpend>): MeteredSpend {
  return {
    meter: "bill",
    usd: 0,
    tokens: 0,
    billed_usd: 0,
    incurred_usd: 0,
    billed_tokens: 0,
    unpriced_tokens: 0,
    kinds,
    replay_share: null,
  } as MeteredSpend;
}

describe("costSeries over rounds carrying different kinds", () => {
  it("keys each series by kind, so a later round's dollars never land under another label", () => {
    // `dashboard.json` is served VERBATIM, so round 0 can lack a kind round 1 carries. Built by
    // position off round 0, round 1's judge bill would paint under the next label or vanish.
    const snapshot = {
      spend_metered_by_round: {
        "0": metered({ backend: kind(0.5) }),
        "1": metered({ backend: kind(0.4), judge: kind(0.02) }),
      },
    } as unknown as DashboardSnapshot;

    const series = costSeries(roundCosts(snapshot));
    expect(series.map((s) => s.key)).toEqual(["backend", "judge"]);
    expect(series.find((s) => s.key === "judge")?.data).toEqual([null, 0.02]);
    expect(series.find((s) => s.key === "backend")?.data).toEqual([0.5, 0.4]);
  });
});
