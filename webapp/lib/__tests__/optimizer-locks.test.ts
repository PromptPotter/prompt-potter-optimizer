import { describe, expect, it } from "vitest";
import type { OptimizerLocks } from "../api";
import { lockedParams, primaryNode, thinkingLadder } from "../optimizer-locks";

// Mirrors a fresh TermNorm draft's `optimizer_locks`: llm_only clamped to
// reasoning_effort `low` (medium/high crossed out), model/provider forbidden.
const TERMNORM_LOCKS: OptimizerLocks = {
  pipeline: ["llm_only"],
  forbidden_axes: ["model", "provider"],
  nodes: {
    llm_only: {
      config: { reasoning_effort: "low", temperature: 0 },
      param_allowed_values: { reasoning_effort: ["none", "default", "low"] },
    },
  },
};

describe("thinkingLadder", () => {
  it("marks low active+allowed, medium/high crossed out", () => {
    const ladder = thinkingLadder(TERMNORM_LOCKS);
    expect(ladder.value).toBe("low");
    const byKey = Object.fromEntries(ladder.options.map((o) => [o.key, o]));
    expect(byKey.low).toEqual({ key: "low", active: true, allowed: true });
    expect(byKey.medium).toEqual({ key: "medium", active: false, allowed: false });
    expect(byKey.high).toEqual({ key: "high", active: false, allowed: false });
  });
});

describe("lockedParams", () => {
  it("locks forbidden axes + constrained params, frees the rest", () => {
    const params = lockedParams(TERMNORM_LOCKS);
    const byParam = Object.fromEntries(params.map((p) => [p.param, p]));
    // model is forbidden even though it's absent from config.
    expect(byParam.model).toMatchObject({ locked: true, reason: "forbidden" });
    expect(byParam.provider).toMatchObject({ locked: true, reason: "forbidden" });
    // reasoning_effort has an allow-list → constrained (optimizer can't escalate).
    expect(byParam.reasoning_effort).toMatchObject({ locked: true, reason: "constrained" });
    // temperature has no restriction → optimizer-free.
    expect(byParam.temperature).toMatchObject({ locked: false, reason: "free" });
  });
});

describe("primaryNode", () => {
  it("returns the first pipeline step with a locks entry", () => {
    expect(primaryNode(TERMNORM_LOCKS)).toBe("llm_only");
  });
});
