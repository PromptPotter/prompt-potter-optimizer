import { describe, expect, it } from "vitest";
import { degradedRoundNotices } from "../round-health";
import type { DegradationHealth } from "@/lib/api/types";
import { health, summaryRound } from "@/lib/test-fixtures";

const ADVICE =
  "web_search degraded on 25% of samples, all transient. The numbers are soft but " +
  "usable; no action needed if the next round comes back clean.";

const rounds = (...healths: (DegradationHealth | null)[]) =>
  healths.map((h, i) => summaryRound({ round: i + 1, health: h }));

describe("degradedRoundNotices", () => {
  it("returns nothing for null / empty / all-healthy", () => {
    expect(degradedRoundNotices(undefined)).toEqual([]);
    expect(degradedRoundNotices(rounds(health("healthy")))).toEqual([]);
    expect(degradedRoundNotices(rounds(null))).toEqual([]);
  });

  it("surfaces only `degraded` rounds — `critical` is owned by the banner", () => {
    const out = degradedRoundNotices(
      rounds(
        health("healthy"),
        health("degraded", { suggested_action: ADVICE }),
        health("critical", { suggested_action: ADVICE }),
      ),
    );
    expect(out).toHaveLength(1);
    expect(out[0]!.round).toBe(2);
    expect(out[0]!.detail).toBe(ADVICE);
  });
});
