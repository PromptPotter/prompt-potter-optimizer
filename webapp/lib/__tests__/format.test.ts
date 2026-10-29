import { describe, expect, it } from "vitest";
import { fmtInterval, fmtUnit, PLACEHOLDER, sideTone } from "../format";

describe("sideTone", () => {
  it("inks a served side, and an interval spanning 0 like one that was never tested", () => {
    expect(sideTone("above")).toBe("l4-eff-pos");
    expect(sideTone("below")).toBe("l4-eff-neg");
    expect(sideTone("spans")).toBe("l4-eff-flat");
    expect(sideTone(null)).toBe("l4-eff-flat");
  });
});

describe("fmtUnit", () => {
  it("formats by the served unit, level and metric alike", () => {
    expect(fmtUnit("rate", 0.456)).toBe("46%");
    expect(fmtUnit("score", 0.456)).toBe("0.46");
    expect(fmtUnit("theta", -0.456)).toBe("θ -0.46");
    expect(fmtUnit("level", 0.4567)).toBe("0.457");
    expect(fmtUnit("delta", 0.1)).toBe("+0.100");
  });

  it("renders an absent or non-finite value as the placeholder, never a number", () => {
    expect(fmtUnit("rate", null)).toBe(PLACEHOLDER);
    expect(fmtUnit("level", undefined)).toBe(PLACEHOLDER);
    expect(fmtUnit("delta", Number.NaN)).toBe(PLACEHOLDER);
  });
});

describe("fmtInterval", () => {
  it("brackets both ends in the unit, and an open end is the placeholder", () => {
    expect(fmtInterval("delta", -0.05, 0.1)).toBe("[-0.050, +0.100]");
    expect(fmtInterval("rate", 0.4, null)).toBe(PLACEHOLDER);
  });
});
