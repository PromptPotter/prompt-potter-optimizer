import { describe, expect, it } from "vitest";
import { costSeries, roundCosts } from "../spend";
import type { KindSpend, MeteredSpend } from "@/lib/api/types";
import { dash } from "@/lib/test-fixtures";

function kind(billed: number): KindSpend {
  return {
    counted: true,
    sent: true,
    metered_usd: billed,
    billed_usd: billed,
    rate_priced_usd: 0,
    incurred_usd: billed,
    tokens: 0,
    prefix: { state: "unreported", share: null, badge: "c?" },
  };
}

function metered(
  kinds: Record<string, KindSpend>,
  over: Partial<MeteredSpend> = {},
): MeteredSpend {
  return {
    meter: "bill",
    metered_usd: 0,
    billed_usd: 0,
    rate_priced_usd: 0,
    incurred_usd: 0,
    billed_tokens: 0,
    kinds,
    replay_share: null,
    bill: "$0.0000",
    rate_priced_line: null,
    replay_line: "The search incurred nothing, so no share of it replayed",
    buckets_line: "",
    ...over,
  };
}

describe("costSeries over rounds carrying different kinds", () => {
  it("keys each series by kind, so a later round's dollars never land under another label", () => {
    // `dashboard.json` is served VERBATIM, so round 0 can lack a kind round 1 carries.
    const snapshot = dash({
      spend_metered_by_round: {
        "0": metered({ backend: kind(0.5) }),
        "1": metered({ backend: kind(0.4), judge: kind(0.02) }),
      },
    });

    const series = costSeries(roundCosts(snapshot));
    expect(series.map((s) => s.key)).toEqual(["backend", "judge"]);
    expect(series.find((s) => s.key === "judge")?.data).toEqual([null, 0.02]);
    // A kind's ink is its place in the served order, not among the kinds this run carries.
    expect(series.map((s) => s.ink)).toEqual([0, 2]);
    expect(series.find((s) => s.key === "backend")?.data).toEqual([0.5, 0.4]);
  });
});
