import { describe, expect, it } from "vitest";
import { primaryMetric } from "@/lib/derivations";

describe("primaryMetric", () => {
  it("prefers the metric the campaign elects on", () => {
    expect(primaryMetric(new Set(["accuracy", "ability"]), "ability")).toBe("ability");
    expect(primaryMetric(new Set(["accuracy", "composite"]), "composite")).toBe("composite");
  });

  it("falls back to canonical order when the elected metric is not shown", () => {
    expect(primaryMetric(new Set(["accuracy", "composite"]), "ability")).toBe("accuracy");
    expect(primaryMetric(new Set(["composite"]), "ability")).toBe("composite");
    expect(primaryMetric(new Set())).toBe("accuracy");
  });
});
