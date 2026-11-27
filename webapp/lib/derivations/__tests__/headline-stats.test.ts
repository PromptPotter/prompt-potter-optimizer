import { describe, expect, it } from "vitest";
import { primaryMetric } from "@/lib/derivations";

describe("primaryMetric", () => {
  it("prefers the metric the campaign elects on", () => {
    expect(primaryMetric(new Set(["fitness", "composite"]), "composite")).toBe("composite");
  });

  it("falls back to canonical order when the elected metric is not shown", () => {
    expect(primaryMetric(new Set(["fitness"]), "composite")).toBe("fitness");
    expect(primaryMetric(new Set(["composite"]), "fitness")).toBe("composite");
    expect(primaryMetric(new Set())).toBe("fitness");
  });
});
