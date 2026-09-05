import { describe, expect, it } from "vitest";

import { cacheShare, foldStepTokens, prefixReading } from "@/lib/derivations";

// The browser half of `domain/spend.py::TokenAccount`. Both halves are pinned because the rule
// they carry — a replayed row reports no provider discount — was implemented five times by hand
// and got three of five right.
describe("cacheShare", () => {
  it("is null on a replay, whose counts are the banked row's", () => {
    expect(cacheShare(550, 1000, false)).toBe(0.55);
    expect(cacheShare(550, 1000, true)).toBeNull();
  });

  it("keeps a reported zero apart from no breakdown at all", () => {
    expect(cacheShare(0, 1000, false)).toBe(0);
    expect(cacheShare(null, 1000, false)).toBeNull();
    expect(cacheShare(undefined, 1000, false)).toBeNull();
  });

  it("is null without an input to divide by, never zero", () => {
    expect(cacheShare(550, 0, false)).toBeNull();
    expect(cacheShare(550, null, false)).toBeNull();
  });
});

// Four states, one decision. Pinned because every renderer used to suppress on `> 0`, which
// merges three of them into one blank — and on a live campaign the blank is ~91% of rows.
describe("prefixReading", () => {
  it("names the state each renderer used to render as nothing", () => {
    expect(prefixReading(0.39, false)).toMatchObject({ state: "discounted", label: "c39%" });
    expect(prefixReading(0, false)).toMatchObject({ state: "cold", share: 0, label: "c0%" });
    expect(prefixReading(null, false)).toMatchObject({ state: "unreported", label: "c?" });
  });

  it("stays silent on a replay, whose row already carries 📖", () => {
    expect(prefixReading(null, true)).toMatchObject({ state: "replayed", label: "" });
    // `cacheShare` folds a replay to null before this ever sees a number, so passing one is
    // impossible in practice — but `replayed` still wins, because the row reached no provider.
    expect(prefixReading(0.5, true).state).toBe("replayed");
  });

  it("carries the badge the terminal prints, byte for byte", () => {
    // `domain/rendering.py::prefix_reading` emits these same three strings; an operator reading
    // the tape and the sample row must not have to learn two vocabularies.
    expect([0.39, 0, null].map((s) => prefixReading(s, false).label)).toEqual([
      "c39%",
      "c0%",
      "c?",
    ]);
  });
});

describe("foldStepTokens", () => {
  it("sums BOTH sides of the ratio out of one fold", () => {
    const account = foldStepTokens({
      a: { input: 600, output: 10, cache_read: 300 },
      b: { input: 400, output: 5, cache_read: 100 },
    });
    // The defect this replaces took the numerator from here and the denominator from a top-level
    // `input_tokens` twin that no writer ever set, so every historical row divided by undefined.
    expect(account).toEqual({ input: 1000, output: 15, cacheRead: 400 });
    expect(cacheShare(account?.cacheRead, account?.input, false)).toBe(0.4);
  });

  it("reports no breakdown as null, so an old round reads unknown rather than 0%", () => {
    expect(foldStepTokens({ a: { input: 10, output: 1 } })?.cacheRead).toBeNull();
  });

  it("returns null where the row has no entries at all", () => {
    expect(foldStepTokens({})).toBeNull();
    expect(foldStepTokens(undefined)).toBeNull();
  });
});
