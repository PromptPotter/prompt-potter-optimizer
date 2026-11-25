import { describe, expect, it } from "vitest";
import { labelStep, unclutteredLabels, type LabelBox } from "../chart";

const box = (x: number, rank: number, y = 0): LabelBox => ({ x, y, w: 20, h: 10, rank });

describe("unclutteredLabels", () => {
  it("draws the lower rank where two would touch, and never an Infinity rank", () => {
    expect(unclutteredLabels([box(0, 2), box(15, 1), box(100, Infinity)], 2)).toEqual([false, true, false]);
  });

  it("keeps two labels that sit apart on either axis", () => {
    expect(unclutteredLabels([box(0, 1), box(22, 1), box(0, 1, 12)], 2)).toEqual([true, true, true]);
  });
});

// Rounds 0..14, one label 8px wide centred every `pitch` px.
const rounds = (pitch: number) => Array.from({ length: 15 }, (_, r) => ({ x: r * pitch, w: 8, ordinal: r }));

describe("labelStep", () => {
  it("labels every ordinal where all fit", () => {
    expect(labelStep(rounds(20), 6)).toBe(1);
  });

  it("thins to the next step of 1, 2, 5, 10 at which the kept labels clear", () => {
    expect(labelStep(rounds(10), 6)).toBe(2);
    expect(labelStep(rounds(3), 6)).toBe(5);
    expect(labelStep(rounds(1), 6)).toBe(20);
  });

  it("reads 300 rounds at a phone's width as R0, R50, R100…", () => {
    const many = Array.from({ length: 300 }, (_, r) => ({ x: r * 1.1, w: 30, ordinal: r }));
    expect(labelStep(many, 6)).toBe(50);
  });

  it("spaces labels by their centres, not their indices", () => {
    // R0 alone at 2px, then R1 under the middle of a long round at 60px: both fit.
    expect(labelStep([{ x: 2, w: 8, ordinal: 0 }, { x: 60, w: 8, ordinal: 1 }], 6)).toBe(1);
  });
});
