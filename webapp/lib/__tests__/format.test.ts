import { describe, expect, it } from "vitest";
import { sideTone } from "../format";

describe("sideTone", () => {
  it("inks a served side, and an interval spanning 0 like one that was never tested", () => {
    expect(sideTone("above")).toBe("l4-eff-pos");
    expect(sideTone("below")).toBe("l4-eff-neg");
    expect(sideTone("spans")).toBe("l4-eff-flat");
    expect(sideTone(null)).toBe("l4-eff-flat");
  });
});
