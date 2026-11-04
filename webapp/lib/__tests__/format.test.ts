import { describe, expect, it } from "vitest";
import {
  fmtInterval,
  fmtLift,
  fmtLiftBand,
  fmtMetric,
  fmtUnit,
  PLACEHOLDER,
  sideTone,
} from "../format";

const NBSP = String.fromCharCode(0xa0);

describe("sideTone", () => {
  it("inks a served side, and an interval spanning 0 like one that was never tested", () => {
    expect(sideTone("above")).toBe("l4-eff tone-success");
    expect(sideTone("below")).toBe("l4-eff tone-danger");
    expect(sideTone("spans")).toBe("l4-eff tone-quiet");
    expect(sideTone(null)).toBe("l4-eff tone-quiet");
  });
});

describe("fmtUnit", () => {
  it("formats a level by the served unit table", () => {
    expect(fmtUnit("rate", 0.456)).toBe("46%");
    expect(fmtUnit("score", 0.456)).toBe("0.46");
    expect(fmtUnit("theta", -0.456)).toBe("θ -0.46");
    expect(fmtUnit("usd", 0.0123)).toBe("$0.0123");
    expect(fmtUnit("tokens", 999.4)).toBe("999 tok");
    expect(fmtUnit("tokens", 1155)).toBe("1.2k tok");
    expect(fmtUnit("tokens", 2_000_000)).toBe("2.0M tok");
  });

  it("renders an absent or non-finite value as the placeholder, never a number", () => {
    expect(fmtUnit("rate", null)).toBe(PLACEHOLDER);
    expect(fmtUnit("score", undefined)).toBe(PLACEHOLDER);
    expect(fmtUnit("seconds", Number.NaN)).toBe(PLACEHOLDER);
  });
});

describe("fmtInterval", () => {
  it("brackets both ends in the unit, and an open end is the placeholder", () => {
    expect(fmtInterval("score", 0.05, 0.1)).toBe("[0.05, 0.10]");
    expect(fmtInterval("rate", 0.4, null)).toBe(PLACEHOLDER);
  });
});

describe("fmtLift", () => {
  it("signs a lift in its unit's word: a rate's in points, never percent", () => {
    expect(fmtLift("rate", 0.05)).toBe(`+5.0${NBSP}pts`);
    expect(fmtLift("theta", -0.1234)).toBe(`-0.123${NBSP}logits`);
    expect(fmtLift("score", null)).toBe(PLACEHOLDER);
  });

  it("bands a lift with its bounds signed, each pair held together", () => {
    expect(fmtLiftBand("rate", { value: 0.05, ci_lo: 0.01, ci_hi: 0.09 })).toBe(
      `+5.0${NBSP}pts [+1.0,${NBSP}+9.0]`,
    );
  });

  it("signs a metric's value only where it is a lift", () => {
    expect(fmtMetric({ unit: "theta", lift: true }, 0.2)).toBe(`+0.200${NBSP}logits`);
    expect(fmtMetric({ unit: "theta", lift: false }, 0.2)).toBe("θ 0.20");
  });
});
