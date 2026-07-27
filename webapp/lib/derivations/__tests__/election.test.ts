import { describe, expect, it } from "vitest";
import { roundSizes, wasElected } from "../election";

describe("roundSizes", () => {
  it("counts arms per round", () => {
    const sizes = roundSizes([{ round: 0 }, { round: 1 }, { round: 1 }, { round: 2 }, { round: 1 }]);
    expect(sizes.get(0)).toBe(1);
    expect(sizes.get(1)).toBe(3);
    expect(sizes.get(2)).toBe(1);
  });

  it("treats a missing round as 0 rather than dropping the row", () => {
    expect(roundSizes([{}, { round: null }, { round: 0 }]).get(0)).toBe(3);
  });

  it("is empty for no rows", () => {
    expect(roundSizes([]).size).toBe(0);
  });
});

describe("wasElected", () => {
  it("is false for the sole arm of its round — round 0, whose arm is the origin", () => {
    expect(wasElected(true, 1)).toBe(false);
  });

  it("is true for a crown won over rivals", () => {
    expect(wasElected(true, 2)).toBe(true);
  });

  it("is false for anything that did not win, contested or not", () => {
    expect(wasElected(false, 4)).toBe(false);
    expect(wasElected(false, 1)).toBe(false);
  });
});
