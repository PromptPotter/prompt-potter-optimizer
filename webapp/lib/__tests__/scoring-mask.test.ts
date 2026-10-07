import { describe, expect, it } from "vitest";
import { NO_DIALS, dialsOf, dialsText, lensOf, servedMask } from "../scoring-mask";

describe("dials — the one spelling the draft, the scoring block and the lens share", () => {
  it("drops a dial at 0 and orders by the served vocabulary, so one set is one string", () => {
    expect(dialsText({ tokens: 0.08, latency: 0.1, cost: 0 })).toBe("latency=0.1,tokens=0.08");
    expect(dialsText({})).toBe("");
  });

  it("drops a name that is not a dialable term rather than sending it", () => {
    expect(dialsText({ fitness: 0.5, nonsense: 0.2, cached: 0.1 })).toBe("cached=0.1");
  });

  it("reads back what the server canonicalized, skipping a malformed pair", () => {
    expect(dialsOf("tokens=0.08, latency=0.1")).toEqual({ tokens: 0.08, latency: 0.1 });
    expect(dialsOf("tokens=,=0.2,cost=x")).toEqual({});
    expect(dialsOf("")).toEqual({});
  });
});

describe("lensOf", () => {
  it("mints a `dials:` lens and never a formula — the server owns the anchors", () => {
    expect(lensOf({ kind: "dials", weights: { tokens: 0.08 } })).toBe("dials:tokens=0.08");
  });

  it("is null when nothing is asked: every dial off, a blank expression, no mask", () => {
    expect(lensOf(NO_DIALS)).toBeNull();
    expect(lensOf({ kind: "dials", weights: { tokens: 0 } })).toBeNull();
    expect(lensOf({ kind: "expression", lens: "  " })).toBeNull();
    expect(lensOf(null)).toBeNull();
  });

  it("passes an expression through verbatim", () => {
    expect(lensOf({ kind: "expression", lens: " abort:floor " })).toBe("abort:floor");
  });
});

describe("servedMask", () => {
  it("is the served dials where the formula is the anchored shape", () => {
    expect(servedMask("fitness * (0.92 + 0.08 * 692.0 / max(692.0, tokens))", { tokens: 0.08 })).toEqual({
      kind: "dials",
      weights: { tokens: 0.08 },
    });
  });

  it("is the formula verbatim where no dials were served, and no dials before run init", () => {
    expect(servedMask("fitness - 0.1 * degraded", null)).toEqual({
      kind: "expression",
      lens: "score:fitness - 0.1 * degraded",
    });
    expect(servedMask(null, null)).toBe(NO_DIALS);
  });
});
